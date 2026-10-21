"""Persistent, read-only N-18 Baccarat suit forecasts.

This engine does not place stakes or interact with the bookmaker.  It never
guesses a round number from a game ID or an arbitrary list position.
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
MAX_ROUND = 1440
MAX_AGE_SECONDS = 90 * 60


def round_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    number = int(text)
    return number if 1 <= number <= MAX_ROUND else None


def target_round(source: Any) -> int | None:
    number = round_int(source)
    return ((number - 1 + CYCLE) % MAX_ROUND) + 1 if number else None


def source_round(target: Any) -> int | None:
    number = round_int(target)
    return ((number - 1 - CYCLE) % MAX_ROUND) + 1 if number else None


def moment(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


class Cycle18Engine:
    """Independent table: the existing next-game predictions remain unchanged."""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cycle18_predictions (
                source_game_id TEXT PRIMARY KEY,
                source_round_number INTEGER NOT NULL,
                target_round_number INTEGER NOT NULL,
                target_game_id TEXT UNIQUE,
                predicted_suit TEXT NOT NULL,
                created_at TEXT NOT NULL,
                linked_at TEXT,
                checked_at TEXT,
                result TEXT NOT NULL DEFAULT 'PENDING',
                mode TEXT NOT NULL DEFAULT 'UNLINKED',
                actual_cards_json TEXT,
                reason TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_cycle18_target_round ON cycle18_predictions(target_round_number)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_cycle18_result ON cycle18_predictions(result)")

    def clear(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM cycle18_predictions")

    def refresh(self, current: dict[str, Any] | None = None) -> dict[str, Any]:
        """Register confirmed source hands, link exact N+18 targets, settle final cards.

        This method is idempotent across polling and process restarts.
        Existing completed results are never overwritten. Historical results
        are marked HISTORY, not misrepresented as prospective forecasts.
        """
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=3000")
            # Historical rows must have confirmed final Player cards.
            observations = [dict(row) for row in db.execute("""
                SELECT game_id,round_number,discovered_at,finished_at,
                       cards_json,completeness,calculated_suit,calculation_status,
                       final_snapshot_reverified,player_hand_final
                FROM observations ORDER BY discovered_at,game_id
            """).fetchall()]
            by_id = {row["game_id"]: row for row in observations}
            confirmed_sources: dict[int, list[dict[str, Any]]] = {}
            for row in observations:
                number = round_int(row["round_number"])
                if not number or row["calculation_status"] != "complete":
                    continue
                if not (row["finished_at"] or row["player_hand_final"]) or row["final_snapshot_reverified"] != 1:
                    continue
                try:
                    cards = json.loads(row["cards_json"] or "[]")
                except (ValueError, TypeError):
                    continue
                symbol = calculate_cards_suit(cards)
                predicted = SUIT_SYMBOL_TO_NAME.get(symbol) if symbol else None
                if not predicted:
                    continue
                confirmed_sources.setdefault(number, []).append(row)
                db.execute("""INSERT OR IGNORE INTO cycle18_predictions
                    (source_game_id,source_round_number,target_round_number,
                     predicted_suit,created_at)
                    VALUES (?,?,?,?,?)""",
                    (row["game_id"], number, target_round(number), predicted, now))

            # Current game can be discovered by Playwright before the observer
            # persists its cards, allowing a forecast to be shown in advance.
            targets = list(observations)
            if current and current.get("game_id") and current["game_id"] not in by_id:
                targets.append({
                    "game_id": current["game_id"],
                    "round_number": current.get("round_number"),
                    "discovered_at": now,
                    "finished_at": None,
                    "player_hand_final": 0,
                    "final_snapshot_reverified": None,
                    "cards_json": "[]",
                })
            for target in targets:
                target_number = round_int(target.get("round_number"))
                if target_number is None:
                    continue
                previous_number = source_round(target_number)
                target_time = moment(target.get("discovered_at"))
                if target_time is None:
                    continue
                sources = []
                for candidate in confirmed_sources.get(previous_number, []):
                    source_time = moment(candidate.get("discovered_at"))
                    if source_time is None or source_time >= target_time:
                        continue
                    age = (target_time - source_time).total_seconds()
                    if 0 < age <= MAX_AGE_SECONDS and candidate["game_id"] != target["game_id"]:
                        sources.append((source_time, candidate))
                if not sources:
                    continue
                src = max(sources, key=lambda x: x[0])[1]
                is_final = bool(target.get("finished_at") or target.get("player_hand_final"))
                mode = "HISTORY" if is_final else "LIVE"
                db.execute("""UPDATE cycle18_predictions
                    SET target_game_id=?, linked_at=?, mode=?,
                        reason='Точная связь по номеру раздачи N−18'
                    WHERE source_game_id=? AND target_game_id IS NULL""",
                    (target["game_id"], now, mode, src["game_id"]))

            # Resolve only linked targets. An unfinished or incomplete hand
            # remains PENDING/UNKNOWN: never count an absent card as LOSE.
            pending = db.execute("""SELECT source_game_id,target_game_id,predicted_suit
                FROM cycle18_predictions
                WHERE target_game_id IS NOT NULL AND result='PENDING'""").fetchall()
            for prediction in pending:
                target = by_id.get(prediction["target_game_id"])
                if target is None:
                    continue
                try:
                    actual = json.loads(target["cards_json"] or "[]")
                except (TypeError, ValueError):
                    actual = []
                outcome = evaluate_prediction(
                    prediction["predicted_suit"], actual,
                    finished_confirmed=bool(target["finished_at"]),
                    player_hand_final=bool(target["player_hand_final"]),
                    final_snapshot_reverified=target["final_snapshot_reverified"] == 1,
                )
                if outcome == "PENDING":
                    continue
                # UNKNOWN is terminal for a confirmed-but-unverifiable hand;
                # it does not contribute to the WIN/LOSE denominator.
                db.execute("""UPDATE cycle18_predictions SET result=?,checked_at=?,
                    actual_cards_json=?,reason=? WHERE source_game_id=? AND result='PENDING'""",
                    (outcome, now, json.dumps(actual, ensure_ascii=False),
                     "Проверено по окончательным картам Игрока" if outcome in ("WIN", "LOSE")
                     else "Не удалось подтвердить карты Игрока",
                     prediction["source_game_id"]))

            # Old cleared history cannot leave ghost forecasts.
            db.execute("""DELETE FROM cycle18_predictions
                WHERE source_game_id NOT IN (SELECT game_id FROM observations)""")
            results = [dict(row) for row in db.execute("""
                SELECT p.*, o.discovered_at AS target_discovered_at
                FROM cycle18_predictions p
                LEFT JOIN observations o ON o.game_id=p.target_game_id
                ORDER BY COALESCE(o.discovered_at,p.linked_at,p.created_at),p.target_round_number
            """).fetchall()]

        complete = [row for row in results if row["result"] in ("WIN", "LOSE") and row["target_game_id"]]
        wins = sum(row["result"] == "WIN" for row in complete)
        losses = len(complete) - wins
        history = [row for row in complete if row["mode"] == "HISTORY"]
        prospective = [row for row in complete if row["mode"] == "LIVE"]
        max_losses, loss_streak, current_step = 0, 0, 1
        step_wins: dict[int, int] = {}
        prior = None
        for row in results:
            if row["result"] not in ("WIN", "LOSE") or not row["target_game_id"]:
                if row["target_game_id"] and row["result"] == "UNKNOWN":
                    loss_streak = 0
                continue
            timestamp = moment(row.get("target_discovered_at"))
            if prior:
                prior_number, prior_time = prior
                gap = (timestamp - prior_time).total_seconds() if timestamp and prior_time else -1
                if (round_int(row["target_round_number"]) !=
                        ((prior_number % MAX_ROUND) + 1) or not (0 < gap <= 600)):
                    loss_streak = 0
            else:
                loss_streak = 0
            if row["result"] == "LOSE":
                loss_streak += 1
                max_losses = max(max_losses, loss_streak)
            else:
                step_wins[loss_streak + 1] = step_wins.get(loss_streak + 1, 0) + 1
                loss_streak = 0
            prior = (int(row["target_round_number"]), timestamp)
        current_step = loss_streak + 1
        active = None
        if current and current.get("game_id"):
            active = next((row for row in results
                           if row["target_game_id"] == current["game_id"]), None)
        # Only expose exact, already registered N+18 forecasts.
        upcoming = [row for row in results if not row["target_game_id"]]
        return {
            "cycle": CYCLE,
            "statistics": {
                "checked_count": len(complete), "wins": wins, "losses": losses,
                "win_rate": round(100 * wins / len(complete), 2) if complete else None,
                "max_lose_streak": max_losses,
                "current_lose_streak": loss_streak,
                "next_step": current_step,
                "wins_by_step": {str(k): v for k, v in sorted(step_wins.items())},
                "historical_checked": len(history),
                "prospective_checked": len(prospective),
            },
            "current": self._public(active) if active else None,
            "upcoming": [self._public(row) for row in upcoming[-CYCLE:]],
            "recent": [self._public(row) for row in results if row["target_game_id"]][-50:][::-1],
        }

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row.get(key) for key in (
            "source_game_id", "source_round_number", "target_game_id",
            "target_round_number", "predicted_suit", "created_at", "linked_at",
            "checked_at", "result", "mode", "reason"
        )}
