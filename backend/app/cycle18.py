"""Read-only cycle-18 predictions with a 20-hand warm-up on every scan start.

The bookmaker controls are never accessed. All matching is based on confirmed
Player hands and explicit round numbers (not game IDs or list positions).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .observation import evaluate_prediction
from .suit_calculator import SUIT_SYMBOL_TO_NAME, calculate_cards_suit

CYCLE = 18
WARMUP = 20
ROUND_MAX = 1440
MAX_GAP_SECONDS = 600
STALE_SECONDS = 5400


def round_number(value: Any) -> int | None:
    value = str(value or "").strip()
    if not value.isdecimal():
        return None
    number = int(value)
    return number if 1 <= number <= ROUND_MAX else None


def following(number: int) -> int:
    return number % ROUND_MAX + 1


def moment(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def is_next(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    left, right = round_number(previous["round_number"]), round_number(current["round_number"])
    first, second = moment(previous["discovered_at"]), moment(current["discovered_at"])
    if not left or not right or not first or not second:
        return False
    gap = (second - first).total_seconds()
    return following(left) == right and 0 < gap <= MAX_GAP_SECONDS


def verified_suit(row: dict[str, Any]) -> str | None:
    if not (row["finished_at"] or row["player_hand_final"]):
        return None
    if row["final_snapshot_reverified"] != 1 or row["calculation_status"] != "complete":
        return None
    try:
        cards = json.loads(row["cards_json"] or "[]")
    except (TypeError, ValueError):
        return None
    symbol = calculate_cards_suit(cards)
    return SUIT_SYMBOL_TO_NAME.get(symbol) if symbol else None


class Cycle18Engine:
    """A session is reset on every real observer start, not on each HTTP poll."""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cycle18_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                stopped_at TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS cycle18_forecasts (
                session_id INTEGER NOT NULL,
                source_game_id TEXT NOT NULL,
                anchor_game_id TEXT NOT NULL,
                source_round_number INTEGER NOT NULL,
                target_round_number INTEGER NOT NULL,
                target_game_id TEXT,
                predicted_suit TEXT NOT NULL,
                created_at TEXT NOT NULL,
                linked_at TEXT,
                checked_at TEXT,
                result TEXT NOT NULL DEFAULT 'PENDING',
                mode TEXT NOT NULL DEFAULT 'UNLINKED',
                actual_cards_json TEXT,
                reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (session_id, source_game_id),
                UNIQUE (session_id, target_game_id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_cycle18_forecast_target ON cycle18_forecasts(session_id,target_round_number)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_cycle18_forecast_status ON cycle18_forecasts(session_id,result)")

    def start_session(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cycle18_sessions SET stopped_at=? WHERE stopped_at IS NULL", (now,))
            cursor = db.execute("INSERT INTO cycle18_sessions(started_at) VALUES(?)", (now,))
            return int(cursor.lastrowid)

    def stop_session(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cycle18_sessions SET stopped_at=? WHERE stopped_at IS NULL",
                       (datetime.now(timezone.utc).isoformat(),))

    def clear(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM cycle18_forecasts")
            db.execute("DELETE FROM cycle18_sessions")

    def refresh(self, current: dict[str, Any] | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=3000")
            session = db.execute("SELECT * FROM cycle18_sessions ORDER BY id DESC LIMIT 1").fetchone()
            if not session:
                return self._empty()
            sid, started_at = int(session["id"]), moment(session["started_at"])
            stopped_at = moment(session["stopped_at"])
            rows = [dict(row) for row in db.execute("""
                SELECT game_id,round_number,discovered_at,finished_at,
                       cards_json,completeness,calculated_suit,calculation_status,
                       final_snapshot_reverified,player_hand_final
                FROM observations
                WHERE discovered_at>=?
                ORDER BY discovered_at,game_id
            """, (session["started_at"],)).fetchall()]
            by_id = {row["game_id"]: row for row in rows}
            completed = [row for row in rows if verified_suit(row)]
            # A gap resets the warm-up. Deduplication is automatic: game_id is
            # the PRIMARY KEY in observations.
            chain: list[dict[str, Any]] = []
            warmup_count = 0
            for hand in completed:
                chain = chain + [hand] if chain and is_next(chain[-1], hand) else [hand]
                warmup_count = min(WARMUP, len(chain))
                if stopped_at or len(chain) < WARMUP:
                    continue
                # After hands #1..#20 are confirmed, source #3 forecasts #21.
                # For later targets the source advances one game at a time.
                source = chain[-CYCLE]
                predicted = verified_suit(source)
                if not predicted:
                    continue
                db.execute("""INSERT OR IGNORE INTO cycle18_forecasts
                    (session_id,source_game_id,anchor_game_id,source_round_number,
                     target_round_number,predicted_suit,created_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (sid, source["game_id"], hand["game_id"],
                     round_number(source["round_number"]),
                     following(round_number(hand["round_number"])), predicted,
                     now.isoformat()))

            # Link each prediction to the exact numbered next hand. Never infer
            # the target ID from round number or list position.
            targets = rows[:]
            if current and current.get("game_id") not in by_id and current.get("game_id"):
                targets.append({
                    "game_id": current["game_id"],
                    "round_number": current.get("round_number"),
                    "discovered_at": now.isoformat(),
                    "finished_at": None,
                    "player_hand_final": 0,
                    "final_snapshot_reverified": None,
                    "cards_json": "[]",
                })
            forecasts = [dict(x) for x in db.execute(
                "SELECT * FROM cycle18_forecasts WHERE session_id=? ORDER BY created_at,source_game_id",
                (sid,)).fetchall()]
            for prediction in forecasts:
                if prediction["result"] != "PENDING" or prediction["target_game_id"]:
                    continue
                anchor = by_id.get(prediction["anchor_game_id"])
                if not anchor:
                    continue
                anchor_time = moment(anchor["discovered_at"])
                if anchor_time is None:
                    continue
                matching = []
                for target in targets:
                    if (round_number(target.get("round_number")) != prediction["target_round_number"]
                            or target["game_id"] == anchor["game_id"]):
                        continue
                    target_time = moment(target["discovered_at"])
                    if not target_time:
                        continue
                    gap = (target_time - anchor_time).total_seconds()
                    if 0 < gap <= MAX_GAP_SECONDS:
                        matching.append((target_time, target))
                if matching:
                    target = min(matching, key=lambda pair: pair[0])[1]
                    # If target cards are already finalized when discovered,
                    # this is historical reconstruction, not a live prediction.
                    live = not (target.get("finished_at") or target.get("player_hand_final"))
                    db.execute("""UPDATE cycle18_forecasts
                        SET target_game_id=?, linked_at=?, mode=?,
                            reason='Сопоставлено по номеру целевой раздачи'
                        WHERE session_id=? AND source_game_id=? AND target_game_id IS NULL""",
                        (target["game_id"], now.isoformat(), "LIVE" if live else "BACKFILL",
                         sid, prediction["source_game_id"]))
                elif (now - anchor_time).total_seconds() > STALE_SECONDS:
                    db.execute("""UPDATE cycle18_forecasts SET result='UNKNOWN', checked_at=?,
                        reason='Целевая раздача не найдена: срок ожидания истёк'
                        WHERE session_id=? AND source_game_id=? AND target_game_id IS NULL""",
                        (now.isoformat(), sid, prediction["source_game_id"]))

            pending = [dict(x) for x in db.execute("""
                SELECT * FROM cycle18_forecasts
                WHERE session_id=? AND target_game_id IS NOT NULL AND result='PENDING'
            """, (sid,)).fetchall()]
            for prediction in pending:
                target = by_id.get(prediction["target_game_id"])
                if not target:
                    continue
                try:
                    cards = json.loads(target["cards_json"] or "[]")
                except (TypeError, ValueError):
                    cards = []
                result = evaluate_prediction(
                    prediction["predicted_suit"], cards,
                    finished_confirmed=bool(target["finished_at"]),
                    player_hand_final=bool(target["player_hand_final"]),
                    final_snapshot_reverified=target["final_snapshot_reverified"] == 1,
                )
                if result == "PENDING":
                    continue
                db.execute("""UPDATE cycle18_forecasts SET result=?,checked_at=?,
                    actual_cards_json=?,reason=?
                    WHERE session_id=? AND source_game_id=? AND result='PENDING'""",
                    (result, now.isoformat(), json.dumps(cards, ensure_ascii=False),
                     "Проверено по финальным картам Игрока" if result in ("WIN", "LOSE")
                     else "Нет достоверных финальных карт Игрока",
                     sid, prediction["source_game_id"]))
            forecasts = [dict(x) for x in db.execute("""
                SELECT p.*, o.discovered_at AS target_discovered_at
                FROM cycle18_forecasts p
                LEFT JOIN observations o ON o.game_id=p.target_game_id
                WHERE p.session_id=?
                ORDER BY COALESCE(o.discovered_at,p.linked_at,p.created_at),p.target_round_number
            """, (sid,)).fetchall()]

        checked = [row for row in forecasts if row["result"] in ("WIN", "LOSE")]
        wins = sum(row["result"] == "WIN" for row in checked)
        losing = max_losing = 0
        steps: dict[str, int] = {}
        previous = None
        for row in forecasts:
            if not row["target_game_id"] or row["result"] not in ("WIN", "LOSE"):
                if row["result"] == "UNKNOWN":
                    losing = 0
                    previous = None
                continue
            if previous and (following(previous["target_round_number"]) != row["target_round_number"]
                             or not self._close_in_time(previous, row)):
                losing = 0
            if row["result"] == "LOSE":
                losing += 1
                max_losing = max(max_losing, losing)
            else:
                step = str(losing + 1)
                steps[step] = steps.get(step, 0) + 1
                losing = 0
            previous = row
        active = next((row for row in forecasts if current and row["target_game_id"] == current.get("game_id")), None)
        pending_upcoming = [row for row in forecasts if row["result"] == "PENDING" and not row["target_game_id"]]
        return {
            "cycle": CYCLE, "warmup_required": WARMUP,
            "warmup_count": warmup_count, "ready": warmup_count >= WARMUP and not stopped_at,
            "session_id": sid, "session_started_at": session["started_at"],
            "session_active": stopped_at is None,
            "statistics": {
                "checked_count": len(checked), "wins": wins, "losses": len(checked) - wins,
                "win_rate": round(100 * wins / len(checked), 2) if checked else None,
                "max_lose_streak": max_losing, "current_lose_streak": losing,
                "next_step": losing + 1, "wins_by_step": steps,
                "live_checked": sum(row["mode"] == "LIVE" for row in checked),
                "backfill_checked": sum(row["mode"] == "BACKFILL" for row in checked),
            },
            "current": self._public(active) if active else None,
            "upcoming": [self._public(row) for row in pending_upcoming[-CYCLE:]],
            "recent": [self._public(row) for row in forecasts if row["target_game_id"]][-50:][::-1],
        }

    @staticmethod
    def _close_in_time(left: dict[str, Any], right: dict[str, Any]) -> bool:
        first, second = moment(left["target_discovered_at"]), moment(right["target_discovered_at"])
        return bool(first and second and 0 < (second - first).total_seconds() <= MAX_GAP_SECONDS)

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row.get(key) for key in (
            "source_game_id", "anchor_game_id", "source_round_number",
            "target_round_number", "target_game_id", "predicted_suit",
            "created_at", "linked_at", "checked_at", "result", "mode", "reason",
        )}

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "cycle": CYCLE, "warmup_required": WARMUP, "warmup_count": 0,
            "ready": False, "session_active": False, "session_id": None,
            "statistics": {
                "checked_count": 0, "wins": 0, "losses": 0, "win_rate": None,
                "max_lose_streak": 0, "current_lose_streak": 0, "next_step": 1,
                "wins_by_step": {}, "live_checked": 0, "backfill_checked": 0,
            }, "current": None, "upcoming": [], "recent": [],
        }
