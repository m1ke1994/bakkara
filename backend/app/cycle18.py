"""Read-only cycle-18 predictions after 20 unique confirmed hands in a session.

The bookmaker controls are never accessed. All matching is based on confirmed
Player hands and explicit round numbers (not game IDs or list positions).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .history_audit import audit_history
from .observation import evaluate_prediction, valid_card_rank
from .suit_calculator import SUIT_SYMBOL_TO_NAME, calculate_cards_suit, normalize_suit

CYCLE = 18
WARMUP = 20
ROUND_MAX = 1440
MAX_GAP_SECONDS = 600
STALE_SECONDS = 5400

STRATEGY_N18 = "n18"
STRATEGY_VOTE = "vote_3_10_24_31_40"
VOTE_OFFSETS = (3, 10, 24, 31, 40)
SUIT_PRIORITY = ("hearts", "diamonds", "clubs", "spades")
STRATEGY_OPTIONS = (
    {"id": STRATEGY_N18, "label": "Цикл N−18", "description": "Один источник N−18, подготовка 20 рук"},
    {"id": STRATEGY_VOTE, "label": "Голосование N−3/10/24/31/40",
     "description": "Пять источников, большинство голосов, подготовка 40 рук"},
)


def strategy_warmup(strategy: str) -> int:
    return 40 if strategy == STRATEGY_VOTE else WARMUP


def strategy_offsets(strategy: str) -> tuple[int, ...]:
    return VOTE_OFFSETS if strategy == STRATEGY_VOTE else (CYCLE,)


def predict_target(number: int, sources: dict[int, dict[str, Any]],
                   strategy: str = STRATEGY_N18) -> dict[str, Any] | None:
    """One advance prediction from exact, verified physical source rounds.

    Majority uses the unmodified ordered 2/3-card suit table. Ties use
    hearts, diamonds, clubs, spades, fixed before observing the target.
    If any required source is absent, there is no signal at all.
    """
    if not 1 <= number <= ROUND_MAX:
        return None
    votes = []
    for offset in strategy_offsets(strategy):
        source_number = ((number - offset - 1) % ROUND_MAX) + 1
        source = sources.get(source_number)
        suit = verified_suit(source) if source else None
        if suit is None:
            return None
        votes.append({"offset": offset, "round_number": source_number, "suit": suit})
    counts = {suit: sum(vote["suit"] == suit for vote in votes) for suit in SUIT_PRIORITY}
    predicted = max(SUIT_PRIORITY, key=lambda suit: counts[suit])
    return {
        "predicted_suit": predicted,
        "source_round_number": votes[0]["round_number"],
        "source_round_numbers": [vote["round_number"] for vote in votes],
        "votes": votes,
        "vote_counts": counts,
    }


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


def order_physical_rounds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort a session by Baccarat round sequence, never list discovery time.

    The live list pre-registers future round IDs. Their discovered_at values
    can be earlier than the final cards of previous rounds. Choose the start
    immediately after the largest circular gap among physical round numbers;
    this also correctly orders 1439,1440,1,2.
    """
    numbers = sorted({value for row in rows
                      if (value := round_number(row.get("round_number"))) is not None})
    if not numbers:
        return rows[:]
    if len(numbers) == 1:
        origin = numbers[0]
    else:
        gaps = [((numbers[(i + 1) % len(numbers)] - number) % ROUND_MAX, i)
                for i, number in enumerate(numbers)]
        _gap, index = max(gaps, key=lambda item: item[0])
        origin = numbers[(index + 1) % len(numbers)]
    return sorted(rows, key=lambda row: (
        (round_number(row.get("round_number")) - origin) % ROUND_MAX
            if round_number(row.get("round_number")) is not None else ROUND_MAX + 1,
        str(row.get("discovered_at") or ""),
        str(row.get("game_id") or ""),
    ))


def is_next(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    left, right = round_number(previous["round_number"]), round_number(current["round_number"])
    if not left or not right or following(left) != right:
        return False
    # Confirmed rounds use the moment their actual cards became final. For
    # prelisted future slots use discovery time and allow an earlier discovery
    # than the finalization of the previous game (normal for a live list).
    # The store sets observed_at even on a pre-discovered EMPTY future game.
    # A timestamp is a final-hand timestamp only if real cards are confirmed.
    # Otherwise it is merely the time the future row appeared in the list.
    first_confirmed = bool(previous.get("finished_at") or previous.get("player_hand_final")) and previous.get("final_snapshot_reverified") == 1
    second_confirmed = bool(current.get("finished_at") or current.get("player_hand_final")) and current.get("final_snapshot_reverified") == 1
    first_final = (moment(previous.get("finished_at") or previous.get("observed_at"))
                   if first_confirmed else None)
    second_final = (moment(current.get("finished_at") or current.get("observed_at"))
                    if second_confirmed else None)
    first = first_final or moment(previous.get("discovered_at"))
    second = second_final or moment(current.get("discovered_at"))
    if not first or not second:
        return False
    gap = (second - first).total_seconds()
    if first_final and second_final:
        return 0 <= gap <= MAX_GAP_SECONDS
    return abs(gap) <= MAX_GAP_SECONDS


def captured_cards(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Cards actually captured from Player; a list placeholder has no cards.

    This is only a progress measure. Two visible cards aren't necessarily a
    final hand until the observer confirms the end of the round.
    """
    try:
        cards = json.loads(row.get("cards_json") or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(cards, list) or len(cards) not in (2, 3):
        return []
    positions = [card.get("position") for card in cards if isinstance(card, dict)]
    if (positions != list(range(1, len(cards) + 1))
            or any(not valid_card_rank(str(card.get("rank", "")))
                   or normalize_suit(card.get("suit")) is None for card in cards)):
        return []
    return cards


def verified_suit(row: dict[str, Any]) -> str | None:
    if not (row["finished_at"] or row["player_hand_final"]):
        return None
    if row["final_snapshot_reverified"] != 1 or row["calculation_status"] != "complete":
        return None
    try:
        cards = json.loads(row["cards_json"] or "[]")
    except (TypeError, ValueError):
        return None
    if not captured_cards(row):
        return None
    symbol = calculate_cards_suit(cards)
    return SUIT_SYMBOL_TO_NAME.get(symbol) if symbol else None


def preview_two_rounds(current_round: int | None, sources: dict[int, dict[str, Any]],
                       strategy: str = STRATEGY_N18) -> list[dict[str, Any]]:
    """Preview physical N and N+1 strictly from verified N-18 sources.

    This is presentation ONLY. It must not insert a forecast, announce
    WIN/LOSE, or shift its source to the 18th row when a hand is missing.
    """
    if current_round is None or not 1 <= current_round <= ROUND_MAX:
        return []
    previews = []
    for phase, number in (("CURRENT", current_round), ("NEXT", following(current_round))):
        source_numbers = [((number - offset - 1) % ROUND_MAX) + 1
                          for offset in strategy_offsets(strategy)]
        forecast = predict_target(number, sources, strategy)
        previews.append({
            "phase": phase,
            "target_round_number": number,
            "source_round_number": source_numbers[0],
            "source_round_numbers": source_numbers,
            "predicted_suit": forecast["predicted_suit"] if forecast else None,
            "votes": forecast["votes"] if forecast else [],
            "available": forecast is not None,
        })
    return previews


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
            # Both columns are additive migrations: existing N−18 sessions and
            # their historical forecast rows remain readable.
            session_columns = {row[1] for row in db.execute("PRAGMA table_info(cycle18_sessions)")}
            if "strategy" not in session_columns:
                db.execute("ALTER TABLE cycle18_sessions ADD COLUMN strategy TEXT NOT NULL DEFAULT 'n18'")
            forecast_columns = {row[1] for row in db.execute("PRAGMA table_info(cycle18_forecasts)")}
            if "sources_json" not in forecast_columns:
                db.execute("ALTER TABLE cycle18_forecasts ADD COLUMN sources_json TEXT")
            if "votes_json" not in forecast_columns:
                db.execute("ALTER TABLE cycle18_forecasts ADD COLUMN votes_json TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS bot_strategy_settings (
                id INTEGER PRIMARY KEY CHECK(id=1),
                strategy TEXT NOT NULL
            )""")
            db.execute("INSERT OR IGNORE INTO bot_strategy_settings(id,strategy) VALUES(1,?)",
                       (STRATEGY_N18,))
            db.execute("CREATE INDEX IF NOT EXISTS idx_cycle18_forecast_target ON cycle18_forecasts(session_id,target_round_number)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_cycle18_forecast_status ON cycle18_forecasts(session_id,result)")

    def get_strategy(self) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT strategy FROM bot_strategy_settings WHERE id=1").fetchone()
            session = db.execute("SELECT strategy FROM cycle18_sessions WHERE stopped_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        selected = row[0] if row and row[0] in (STRATEGY_N18, STRATEGY_VOTE) else STRATEGY_N18
        return {"selected": selected, "active": session[0] if session else None,
                "options": list(STRATEGY_OPTIONS)}

    def save_strategy(self, strategy: str) -> dict[str, Any]:
        if strategy not in (STRATEGY_N18, STRATEGY_VOTE):
            raise ValueError("Неизвестный фильтр прогнозирования")
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM cycle18_sessions WHERE stopped_at IS NULL LIMIT 1").fetchone():
                raise RuntimeError("Остановите сканирование перед сменой фильтра")
            db.execute("UPDATE bot_strategy_settings SET strategy=? WHERE id=1", (strategy,))
        return self.get_strategy()

    def start_session(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            selected = db.execute("SELECT strategy FROM bot_strategy_settings WHERE id=1").fetchone()
            strategy = selected[0] if selected else STRATEGY_N18
            db.execute("UPDATE cycle18_sessions SET stopped_at=? WHERE stopped_at IS NULL", (now,))
            cursor = db.execute("INSERT INTO cycle18_sessions(started_at,strategy) VALUES(?,?)",
                                (now, strategy))
            return int(cursor.lastrowid)

    def stop_session(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE cycle18_sessions SET stopped_at=? WHERE stopped_at IS NULL",
                       (datetime.now(timezone.utc).isoformat(),))

    def clear(self) -> None:
        with sqlite3.connect(self.path) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='cycle18_history_audit'").fetchone():
                db.execute("DELETE FROM cycle18_history_audit")
            db.execute("DELETE FROM cycle18_forecasts")
            db.execute("DELETE FROM cycle18_sessions")

    def refresh(self, current: dict[str, Any] | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=3000")
            session = db.execute("SELECT * FROM cycle18_sessions ORDER BY id DESC LIMIT 1").fetchone()
            if not session:
                return self._empty(self.get_strategy()["selected"])
            strategy = session["strategy"] if session["strategy"] in (STRATEGY_N18, STRATEGY_VOTE) else STRATEGY_N18
            warmup_required = strategy_warmup(strategy)
            sid, started_at = int(session["id"]), moment(session["started_at"])
            stopped_at = moment(session["stopped_at"])
            rows = [dict(row) for row in db.execute("""
                SELECT game_id,round_number,discovered_at,finished_at,
                       cards_json,completeness,calculated_suit,calculation_status,
                       final_snapshot_reverified,player_hand_final,
                       scan_result,reason,last_scanned_at,observed_at
                FROM observations
                WHERE discovered_at>=?
                ORDER BY discovered_at,list_position,game_id
            """, (session["started_at"],)).fetchall()]
            # IDs arrive in discovery order, which is NOT the order of the
            # physical Baccarat rounds (future games appear in the list).
            rows = order_physical_rounds(rows)
            by_id = {row["game_id"]: row for row in rows}
            # A unique, finalized physical round is the ONLY preparation unit.
            # List placeholders and partial two-card snapshots are never counted.
            # Duplicate round identities in one session are ambiguous, so
            # neither copy can advance the warm-up or create a forecast.
            physical_counts: dict[int, int] = {}
            for row in rows:
                number = round_number(row.get("round_number"))
                if number is not None:
                    physical_counts[number] = physical_counts.get(number, 0) + 1
            # A skipped physical round remains in the database, but must not
            # erase confirmed observations already collected during warm-up.
            # Still require true, unique, FINAL Player hands for 20/20.
            valid_hands: dict[int, dict[str, Any]] = {
                number: row for row in rows
                if (number := round_number(row.get("round_number"))) is not None
                and physical_counts[number] == 1 and verified_suit(row)
            }
            warmup_complete = False
            missing_hands: list[dict[str, Any]] = []
            verified_total = 0
            captured_total = 0
            for hand in rows:
                physical = round_number(hand.get("round_number"))
                unique = physical is not None and physical_counts[physical] == 1
                confirmed = verified_suit(hand) if unique else None
                has_cards = bool(captured_cards(hand))
                verified_total += bool(confirmed)
                captured_total += has_cards
                if not confirmed:
                    missing_hands.append({
                        "game_id": hand["game_id"],
                        "round_number": hand["round_number"],
                        "cards_count": len(json.loads(hand["cards_json"] or "[]")),
                        "calculation_status": hand["calculation_status"],
                        "scan_result": hand.get("scan_result", "DISCOVERED"),
                        "reason": hand.get("reason", ""),
                    })
                # Warm-up counts unique final Player hands, not the length of
                # the LAST run of uninterrupted round numbers. A temporary
                # unavailable round stays at its real number in physical history.
                if verified_total >= warmup_required:
                    warmup_complete = True
                if stopped_at or not warmup_complete or not confirmed:
                    continue
                hand_number = round_number(hand["round_number"])
                if hand_number is None:
                    continue
                target_number = following(hand_number)
                forecast = predict_target(target_number, valid_hands, strategy)
                if forecast is None:
                    # An absent N−18 source, or ANY of the five voter sources,
                    # suppresses the signal instead of shifting to a neighbour.
                    continue
                source_number = forecast["source_round_number"]
                source = valid_hands[source_number]
                db.execute("""INSERT OR IGNORE INTO cycle18_forecasts
                    (session_id,source_game_id,anchor_game_id,source_round_number,
                     target_round_number,predicted_suit,created_at,sources_json,votes_json)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (sid, source["game_id"], hand["game_id"],
                     source_number, target_number, forecast["predicted_suit"],
                     now.isoformat(),
                     json.dumps(forecast["source_round_numbers"]),
                     json.dumps(forecast["votes"], ensure_ascii=False)))
            # A completed hand always keeps its credit in the current session.
            # Neither missing physical round numbers nor future list shells
            # reset the counter. Old sessions are excluded by started_at.
            warmup_count = min(warmup_required, verified_total)
            confirmed_warmup_count = warmup_count

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
                    # The next game may already have been registered while
                    # its predecessor was still running: that is not a gap.
                    if abs(gap) <= MAX_GAP_SECONDS:
                        matching.append((target_time, target))
                if len(matching) == 1:
                    target = matching[0][1]
                    final = bool(target.get("finished_at") or target.get("player_hand_final"))
                    # Never manufacture a WIN/LOSE for a forecast created
                    # AFTER this target already reached its final hand.
                    # This matters when the 20th warmup hand is recovered late.
                    final_time = moment(target.get("finished_at") or target.get("observed_at"))
                    forecast_time = moment(prediction["created_at"])
                    missed = bool(final and final_time and forecast_time
                                  and final_time <= forecast_time)
                    if missed:
                        db.execute("""UPDATE cycle18_forecasts
                            SET target_game_id=?, linked_at=?, mode='MISSED',
                                result='SKIPPED', checked_at=?,
                                reason='Раздача завершилась до появления прогноза'
                            WHERE session_id=? AND source_game_id=? AND target_game_id IS NULL""",
                            (target["game_id"], now.isoformat(), now.isoformat(),
                             sid, prediction["source_game_id"]))
                    else:
                        db.execute("""UPDATE cycle18_forecasts
                            SET target_game_id=?, linked_at=?, mode=?,
                                reason='Сопоставлено по номеру целевой раздачи'
                            WHERE session_id=? AND source_game_id=? AND target_game_id IS NULL""",
                            (target["game_id"], now.isoformat(), "LIVE" if not final else "BACKFILL",
                             sid, prediction["source_game_id"]))
                elif (now - anchor_time).total_seconds() > STALE_SECONDS:
                    db.execute("""UPDATE cycle18_forecasts SET result='UNKNOWN', checked_at=?,
                        reason='Целевая раздача не найдена: срок ожидания истёк'
                        WHERE session_id=? AND source_game_id=? AND target_game_id IS NULL""",
                        (now.isoformat(), sid, prediction["source_game_id"]))

            pending = [dict(x) for x in db.execute("""
                SELECT * FROM cycle18_forecasts
                WHERE session_id=? AND target_game_id IS NOT NULL AND result IN ('PENDING','UNKNOWN')
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
                if result == "PENDING" or (result == "UNKNOWN" and prediction["result"] == "UNKNOWN"):
                    continue
                db.execute("""UPDATE cycle18_forecasts SET result=?,checked_at=?,
                    actual_cards_json=?,reason=?
                    WHERE session_id=? AND source_game_id=? AND result IN ('PENDING','UNKNOWN')""",
                    (result, now.isoformat(), json.dumps(cards, ensure_ascii=False),
                     "Проверено по финальным картам Игрока" if result in ("WIN", "LOSE")
                     else "Нет достоверных финальных карт Игрока",
                     sid, prediction["source_game_id"]))
            forecasts = [dict(x) for x in db.execute("""
                SELECT p.*, o.discovered_at AS target_discovered_at,
                       o.finished_at AS target_finished_at,
                       o.observed_at AS target_observed_at,
                       o.player_hand_final AS target_player_hand_final
                FROM cycle18_forecasts p
                LEFT JOIN observations o ON o.game_id=p.target_game_id
                WHERE p.session_id=?
            """, (sid,)).fetchall()]
            # The live list can discover a future game before its predecessor.
            # Discovery order must never determine the martingale step. Walk
            # confirmed forecasts in physical round order (including 1440->1).
            forecasts = order_physical_rounds([
                {**row, "round_number": str(row["target_round_number"]),
                 "discovered_at": row["target_discovered_at"] or row["created_at"]}
                for row in forecasts
            ])

        checked = [row for row in forecasts if row["result"] in ("WIN", "LOSE")]
        wins = sum(row["result"] == "WIN" for row in checked)
        losing = max_losing = 0
        steps: dict[str, int] = {}
        previous = None
        for row in forecasts:
            if not row["target_game_id"] or row["result"] not in ("WIN", "LOSE"):
                if row["result"] == "UNKNOWN" and strategy == STRATEGY_N18:
                    losing = 0
                    previous = None
                # A missing vote source means no attempt, not a success. In
                # voting mode preserve the attempted-loss count over skipped
                # physical rounds, provided the time gap is still plausible.
                continue
            if previous and (
                not self._close_in_time(previous, row)
                or (strategy == STRATEGY_N18
                    and following(previous["target_round_number"]) != row["target_round_number"])
            ):
                losing = 0
            row["step"] = losing + 1
            if row["result"] == "LOSE":
                losing += 1
                max_losing = max(max_losing, losing)
            else:
                step = str(losing + 1)
                steps[step] = steps.get(step, 0) + 1
                losing = 0
            previous = row
        current_number = round_number(current.get("round_number")) if current else None
        previews = preview_two_rounds(current_number, valid_hands, strategy) if warmup_complete and not stopped_at else []
        active = next((row for row in forecasts if current and row["target_game_id"] == current.get("game_id")), None)
        pending_upcoming = [row for row in forecasts if row["result"] == "PENDING" and not row["target_game_id"]]
        audit_signature = (sid, strategy,
            tuple((row["game_id"], row["round_number"], row["cards_json"], row["calculation_status"],
                   row["final_snapshot_reverified"], row["player_hand_final"], row["finished_at"]) for row in rows),
            tuple((row["target_game_id"], row["predicted_suit"], row["result"]) for row in forecasts))
        if audit_signature != getattr(self, "_audit_signature", None):
            self._historical_audit = audit_history(self.path, sid, rows, strategy)
            self._audit_signature = audit_signature
        return {
            "cycle": CYCLE if strategy == STRATEGY_N18 else None,
            "strategy": strategy,
            "strategy_label": next(item["label"] for item in STRATEGY_OPTIONS if item["id"] == strategy),
            "warmup_required": warmup_required,
            "warmup_count": warmup_count,
            "confirmed_warmup_count": confirmed_warmup_count,
            "captured_count": captured_total, "verified_count": verified_total,
            "ready": warmup_complete and not stopped_at,
            "discovered_count": len(rows), "missing_hands": missing_hands[-30:],
            "missing_count": len(missing_hands),
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
            "live_preview": previews,
            "upcoming": [self._public(row) for row in pending_upcoming[-max(strategy_offsets(strategy)):][::-1]],
            "historical_audit": self._historical_audit,
            "recent": [self._public(row) for row in forecasts if row["target_game_id"]][-200:][::-1],
        }

    @staticmethod
    def _close_in_time(left: dict[str, Any], right: dict[str, Any]) -> bool:
        # Discovery is a pre-listing timestamp, NOT the moment the hand
        # finished. Prefer an actual completion timestamp to avoid breaking a
        # consecutive loss streak when future rounds are listed out of order.
        first = moment(left.get("target_finished_at")) or (
            moment(left.get("target_observed_at")) if left.get("target_player_hand_final") else None)
        second = moment(right.get("target_finished_at")) or (
            moment(right.get("target_observed_at")) if right.get("target_player_hand_final") else None)
        return bool(first and second and 0 <= (second - first).total_seconds() <= MAX_GAP_SECONDS)

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        result = {key: row.get(key) for key in (
            "source_game_id", "anchor_game_id", "source_round_number",
            "target_round_number", "target_game_id", "predicted_suit",
            "created_at", "linked_at", "checked_at", "result", "mode", "reason", "step",
        )}
        try:
            sources = json.loads(row.get("sources_json") or "null")
            votes = json.loads(row.get("votes_json") or "null")
        except (ValueError, TypeError):
            sources, votes = None, None
        result["source_round_numbers"] = sources if isinstance(sources, list) else [row.get("source_round_number")]
        result["votes"] = votes if isinstance(votes, list) else []
        return result

    @staticmethod
    def _empty(strategy: str = STRATEGY_N18) -> dict[str, Any]:
        return {
            "cycle": CYCLE if strategy == STRATEGY_N18 else None,
            "strategy": strategy,
            "strategy_label": next(item["label"] for item in STRATEGY_OPTIONS if item["id"] == strategy),
            "warmup_required": strategy_warmup(strategy), "warmup_count": 0,
            "confirmed_warmup_count": 0,
            "captured_count": 0, "verified_count": 0,
            "ready": False, "session_active": False, "session_id": None,
            "discovered_count": 0, "missing_hands": [], "missing_count": 0,
            "statistics": {
                "checked_count": 0, "wins": 0, "losses": 0, "win_rate": None,
                "max_lose_streak": 0, "current_lose_streak": 0, "next_step": 1,
                "wins_by_step": {}, "live_checked": 0, "backfill_checked": 0,
            }, "current": None, "live_preview": [], "upcoming": [], "recent": [],
        }

