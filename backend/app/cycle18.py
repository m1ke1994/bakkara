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
from .suit_calculator import SUIT_SYMBOL_TO_NAME, calculate_cards_suit, calculate_suit, normalize_suit
from .strategy_53 import MAX_GAP_53, PAUSE_53, WARMUP_53, map_facecard_53

CYCLE = 18
WARMUP = 20
ROUND_MAX = 1440
MAX_GAP_SECONDS = 600
STALE_SECONDS = 5400

STRATEGY_N18 = "n18"
STRATEGY_VOTE = "vote_3_10_24_31_40"
STRATEGY_VOTE_DRAW_12 = "vote_3_10_24_31_40_draw_diamond_12"
STRATEGY_HOT_8 = "hot_suits_8_spread_6"
STRATEGY_FIVE = "vote_3_10_24_31_40_step3_reverse"
STRATEGY_53 = "facecards_53"
DRAW_DIAMOND_PAUSE_ROUNDS = 12
HOT_WINDOW = 8
HOT_MIN_SPREAD = 6
HOT_MAX_GAP_SECONDS = 185
VOTE_STRATEGIES = (STRATEGY_VOTE, STRATEGY_VOTE_DRAW_12, STRATEGY_FIVE)
AVAILABLE_STRATEGIES = (STRATEGY_N18,) + VOTE_STRATEGIES + (STRATEGY_HOT_8, STRATEGY_53)
VOTE_OFFSETS = (3, 10, 24, 31, 40)
SUIT_PRIORITY = ("hearts", "diamonds", "clubs", "spades")
STRATEGY_OPTIONS = (
    {"id": STRATEGY_N18, "label": "Цикл N−18", "description": "Один источник N−18, подготовка 20 рук"},
    {"id": STRATEGY_VOTE, "label": "Голосование N−3/10/24/31/40",
     "description": "Пять источников, большинство голосов, подготовка 40 рук"},
    {"id": STRATEGY_VOTE_DRAW_12, "label": "Голосование + ничья ♦ / пропуск 12",
     "description": "После подтверждённой ничьей с бубной Игрока пропустить 12 следующих раздач"},
    {"id": STRATEGY_HOT_8, "label": "Частые масти · 8 раздач / разница ≥6",
     "description": "Подсчёт реальных мастей восьми подряд завершённых рук; "
                    "сигнал только при разбросе частот от 6"},
    {"id": STRATEGY_FIVE, "label": "Стратегия 5 · обратный 3-й шаг",
     "description": "Голосование N−3/10/24/31/40; после двух подтверждённых LOSE "
                    "на 3-м шаге преобразовать прогноз 3-го + прогноз 2-го; затем обычный алгоритм"},
    {"id": STRATEGY_53, "label": "53%",
     "description": "16 соответствий A/K/Q/J; зеркало для средней из трёх; пропуск 4 игр после X; прогрев 40"},
)


def strategy_warmup(strategy: str) -> int:
    return WARMUP_53 if strategy == STRATEGY_53 else HOT_WINDOW if strategy == STRATEGY_HOT_8 else 40 if strategy in VOTE_STRATEGIES else WARMUP


def strategy_offsets(strategy: str) -> tuple[int, ...]:
    return tuple(range(1, WARMUP_53 + 1)) if strategy == STRATEGY_53 else tuple(range(1, HOT_WINDOW + 1)) if strategy == STRATEGY_HOT_8 else VOTE_OFFSETS if strategy in VOTE_STRATEGIES else (CYCLE,)


def pause_for_round(target_number: int, trigger_rounds: list[int]) -> dict[str, Any] | None:
    """Pause ONLY the next 12 physical rounds after a confirmed trigger."""
    for trigger in reversed(trigger_rounds):
        distance = (target_number - trigger) % ROUND_MAX
        if 1 <= distance <= DRAW_DIAMOND_PAUSE_ROUNDS:
            return {"trigger_round_number": trigger,
                    "pause_remaining": DRAW_DIAMOND_PAUSE_ROUNDS - distance + 1,
                    "reason": (f"Ничья с ♦ Игрока в №{trigger}: "
                               f"пропуск {distance}/{DRAW_DIAMOND_PAUSE_ROUNDS}")}
    return None


def draw_diamond_pause_triggers(rows: list[dict[str, Any]]) -> list[int]:
    """Do not infer draws from 0:0. Ignore triggers during a pause."""
    triggers: list[int] = []
    for row in rows:
        number = round_number(row.get("round_number"))
        if number is None or row.get("baccarat_outcome") != "DRAW":
            continue
        if verified_suit(row) is None or pause_for_round(number, triggers):
            continue
        if any(normalize_suit(card.get("suit")) == "♦" for card in captured_cards(row)):
            triggers.append(number)
    return triggers


def verified_player_cards(row: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A final, independently rechecked Player hand (without the suit table)."""
    if not row or not (row.get("finished_at") or row.get("player_hand_final")):
        return []
    if row.get("final_snapshot_reverified") != 1:
        return []
    return captured_cards(row)


def source_verified(row: dict[str, Any] | None, strategy: str) -> bool:
    """The hot-suit strategy must not depend on calculated_suit/suit_map."""
    if strategy in (STRATEGY_HOT_8, STRATEGY_53):
        return bool(verified_player_cards(row))
    return bool(verified_suit(row)) if row else False


def hot_window_analysis(number: int, sources: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Use ONLY eight actual previous consecutive Player hands.

    As in the frozen offline backtest, gaps in physical numbering, missing
    or partial results, and time gaps greater than 185 seconds invalidate
    the entire eight-hand window. No future or Banker cards are read.
    """
    offsets = strategy_offsets(STRATEGY_HOT_8)
    previous_numbers = [((number - offset - 1) % ROUND_MAX) + 1 for offset in offsets]
    missing = [n for n in previous_numbers
               if not verified_player_cards(sources.get(n))]
    if missing:
        return {"predicted_suit": None, "source_round_numbers": previous_numbers,
                "missing_source_round_numbers": missing,
                "reason": "Нет восьми подряд подтверждённых раздач"}
    chronological = [sources[n] for n in reversed(previous_numbers)]
    for left, right in zip(chronological, chronological[1:]):
        left_n, right_n = round_number(left.get("round_number")), round_number(right.get("round_number"))
        left_time = moment(left.get("finished_at") or left.get("observed_at"))
        right_time = moment(right.get("finished_at") or right.get("observed_at"))
        if (left_n is None or right_n != following(left_n)
                or left_time is None or right_time is None
                or not 0 <= (right_time - left_time).total_seconds() <= HOT_MAX_GAP_SECONDS):
            return {"predicted_suit": None, "source_round_numbers": previous_numbers,
                    "missing_source_round_numbers": [],
                    "reason": "Нарушена непрерывность восьми раздач (номер или время)"}
    counts = {suit: 0 for suit in SUIT_PRIORITY}
    votes = []
    for offset, source_number in zip(offsets, previous_numbers):
        cards = verified_player_cards(sources[source_number])
        suits = [SUIT_SYMBOL_TO_NAME[normalize_suit(card["suit"])] for card in cards]
        for suit in suits:
            counts[suit] += 1
        votes.append({"offset": offset, "round_number": source_number, "cards": suits})
    spread = max(counts.values()) - min(counts.values())
    predicted = (max(SUIT_PRIORITY, key=lambda suit: counts[suit])
                 if spread >= HOT_MIN_SPREAD else None)
    return {
        "predicted_suit": predicted,
        "source_round_number": previous_numbers[0],
        "source_round_numbers": previous_numbers,
        "votes": votes,
        "vote_counts": counts,
        "suit_counts": counts,
        "count_spread": spread,
        "min_spread": HOT_MIN_SPREAD,
        "vote_count": HOT_WINDOW,
        "partial_vote": False,
        "missing_source_round_numbers": [],
        "reason": "" if predicted else
                  f"Разница частот {spread} < {HOT_MIN_SPREAD}: пропуск",
    }


def facecard_53_analysis(number: int, sources: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Require four confirmed outcomes; expose an unknown case as preview only."""
    offsets = strategy_offsets(STRATEGY_53)
    previous = [((number - k - 1) % ROUND_MAX) + 1 for k in offsets]
    missing = [n for n in previous if not verified_player_cards(sources.get(n))]
    info = {"source_round_numbers": previous, "missing_source_round_numbers": missing}
    if missing:
        return {**info, "predicted_suit": None, "reason": "Нужны 40 подтверждённых подряд рук Игрока"}
    ordered = [sources[n] for n in reversed(previous)]
    for left, right in zip(ordered, ordered[1:]):
        # Banker's result can be saved after the next Player hand.
        # Use the verified Player observation time, not late Banker closure.
        first = moment(left.get("observed_at") or left.get("finished_at"))
        second = moment(right.get("observed_at") or right.get("finished_at"))
        if (first is None or second is None
                or following(round_number(left.get("round_number")) or 0) != round_number(right.get("round_number"))
                or not 0 <= (second - first).total_seconds() <= MAX_GAP_53):
            return {**info, "predicted_suit": None, "reason": "Разрыв номеров/времени в 40 раздачах"}
    unknown_draw_rounds = []
    for distance, source_number in enumerate(previous[:PAUSE_53], 1):
        source_row = sources[source_number]
        result = (source_row.get("baccarat_outcome")
                  if source_row.get("banker_hand_final") else "UNKNOWN")
        if result == "DRAW":
            return {**info, "predicted_suit": None,
                    "reason": f"После ничьей №{source_number} пропуск четырёх раздач (N−{distance})",
                     "pause_remaining": PAUSE_53 - distance + 1,
                     "draw_filter_verified": True,
                     "unverified_outcome_round_numbers": unknown_draw_rounds}
        if result not in ("PLAYER", "BANKER"):
            unknown_draw_rounds.append(source_number)
    trigger = map_facecard_53(verified_player_cards(sources[previous[0]]))
    if trigger is None:
        return {**info, "predicted_suit": None,
                "reason": "В предыдущей руке должна быть ровно одна A/K/Q/J"}
    if unknown_draw_rounds:
        return {**info, **trigger, "predicted_suit": None,
                "provisional_suit": trigger["predicted_suit"],
                "source_round_number": previous[0],
                "draw_filter_verified": False,
                "unverified_outcome_round_numbers": unknown_draw_rounds,
                "reason": "Ожидаем подтверждения исходов: №" + ", №".join(map(str, unknown_draw_rounds))}
    return {
        **info, **trigger,
        "source_round_number": previous[0],
        "unverified_outcome_round_numbers": unknown_draw_rounds,
        "draw_filter_verified": not unknown_draw_rounds,
        "votes": [{"offset": 1, "round_number": previous[0],
                   "suit": trigger["predicted_suit"], "card": trigger["trigger_card"],
                   "mirrored": trigger["mirrored"],
                   "unverified_outcome_round_numbers": unknown_draw_rounds}],
        "vote_counts": {}, "vote_count": 1, "partial_vote": False,
        "missing_source_round_numbers": [], "reason": "",
    }


def predict_target(number: int, sources: dict[int, dict[str, Any]],
                   strategy: str = STRATEGY_N18) -> dict[str, Any] | None:
    """One advance prediction from exact, verified physical source rounds.

    Majority uses the unmodified ordered 2/3-card suit table. Ties use
    hearts, diamonds, clubs, spades, fixed before observing the target.
    A missing source never gets replaced with a neighbouring round.
    Voting requires at least three verified sources and a strict majority
    of the available votes; fewer/ambiguous votes produce no signal.
    """
    if not 1 <= number <= ROUND_MAX:
        return None
    if strategy == STRATEGY_HOT_8:
        result = hot_window_analysis(number, sources)
        return result if result["predicted_suit"] else None
    if strategy == STRATEGY_53:
        result = facecard_53_analysis(number, sources)
        return result if result["predicted_suit"] else None
    votes = []
    missing = []
    offsets = strategy_offsets(strategy)
    for offset in offsets:
        source_number = ((number - offset - 1) % ROUND_MAX) + 1
        source = sources.get(source_number)
        suit = verified_suit(source) if source else None
        if suit is None:
            missing.append({"offset": offset, "round_number": source_number})
            continue
        votes.append({"offset": offset, "round_number": source_number, "suit": suit})
    if strategy == STRATEGY_N18:
        if len(votes) != 1:
            return None
    elif len(votes) < 3:
        return None
    # Frozen five-source rule: missing source means no Strategy 5 signal.
    if strategy == STRATEGY_FIVE and missing:
        return None
    counts = {suit: sum(vote["suit"] == suit for vote in votes) for suit in SUIT_PRIORITY}
    predicted = max(SUIT_PRIORITY, key=lambda suit: counts[suit])
    # Preserve the original five-way tie-break for the full strategy.
    # Partial voting is stricter: refuse signals without a real majority.
    if strategy in VOTE_STRATEGIES and missing and counts[predicted] <= len(votes) // 2:
        return None
    return {
        "predicted_suit": predicted,
        "source_round_number": votes[0]["round_number"],
        "source_round_numbers": [((number - offset - 1) % ROUND_MAX) + 1 for offset in offsets],
        "votes": votes,
        "vote_counts": counts,
        "vote_count": len(votes),
        "partial_vote": bool(missing),
        "missing_source_round_numbers": [item["round_number"] for item in missing],
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
                       strategy: str = STRATEGY_N18,
                       observed: dict[int, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
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
        missing_sources = []
        for offset, source_number in zip(strategy_offsets(strategy), source_numbers):
            if source_number in sources:
                continue
            records = (observed or {}).get(source_number, [])
            # N+1 depends on the cards of currently running N. Empty card
            # placeholders before N is dealt are expected, not corrupt data.
            # Never mark N as verified until its final Player hand is captured.
            if phase == "NEXT" and source_number == current_round and len(records) <= 1:
                detail = (f"текущая раздача №{source_number} ещё не подтверждена; "
                          f"для прогноза №{number} ждём её окончательные карты")
            elif not records:
                detail = "отсутствует в истории"
            elif len(records) > 1:
                detail = "неоднозначен: несколько игр с этим номером"
            else:
                record = records[0]
                detail = ("финальная рука Игрока не подтверждена"
                          if not source_verified(record, strategy) else "источник недоступен")
                reason = str(record.get("reason") or "").strip()
                if reason:
                    detail += f" ({reason})"
            missing_sources.append({"offset": offset, "round_number": source_number,
                                    "reason": detail})
        hot_state = (hot_window_analysis(number, sources) if strategy == STRATEGY_HOT_8
                     else facecard_53_analysis(number, sources) if strategy == STRATEGY_53 else {})
        previews.append({
            "phase": phase,
            "target_round_number": number,
            "source_round_number": source_numbers[0],
            "source_round_numbers": source_numbers,
            "predicted_suit": forecast["predicted_suit"] if forecast else None,
            "votes": forecast["votes"] if forecast else [],
            "available": forecast is not None,
            "partial_vote": bool(forecast and forecast["partial_vote"]),
            "vote_count": len(forecast["votes"]) if forecast else 0,
            "missing_sources": missing_sources,
            "suit_counts": hot_state.get("suit_counts"),
            "count_spread": hot_state.get("count_spread"),
            "skip_reason": hot_state.get("reason", ""),
            "provisional_suit": hot_state.get("provisional_suit"),
            "trigger_card": hot_state.get("trigger_card"),
            "mirrored": hot_state.get("mirrored", False),
            "pause_remaining": hot_state.get("pause_remaining", 0),
            "unverified_outcome_round_numbers": hot_state.get("unverified_outcome_round_numbers", []),
            "draw_filter_verified": hot_state.get("draw_filter_verified", True),
        })
    return previews



def strategy_five_past_outcome(db: sqlite3.Connection, sid: int, target: int,
                               confirmed: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    """Only previously published forecasts can count as a settled LOSE.

    The Player hand must have been independently confirmed after publication;
    historical backfills and guessed/missing cards are never counted.
    """
    records = db.execute(
        "SELECT * FROM cycle18_forecasts WHERE session_id=? AND target_round_number=?",
        (sid, target)).fetchall()
    if len(records) != 1:
        return None
    prediction = dict(records[0])
    hand = confirmed.get(target)
    if (not hand or prediction["mode"] in ("FILTER", "MISSED")
            or prediction["result"] not in ("PENDING", "WIN", "LOSE")
            or prediction["target_game_id"] not in (None, hand["game_id"])):
        return None
    created = moment(prediction["created_at"])
    finished = moment(hand.get("finished_at") or hand.get("observed_at"))
    if not created or not finished or created >= finished:
        return None
    result = evaluate_prediction(
        prediction["predicted_suit"], verified_player_cards(hand),
        finished_confirmed=bool(hand.get("finished_at")),
        player_hand_final=bool(hand.get("player_hand_final")),
        final_snapshot_reverified=hand["final_snapshot_reverified"] == 1,
    )
    if result not in ("WIN", "LOSE"):
        return None
    if prediction["result"] in ("WIN", "LOSE") and prediction["result"] != result:
        return None
    prediction["settled_result"] = result
    prediction["finished_moment"] = finished
    return prediction


def strategy_five_step_three(db: sqlite3.Connection, sid: int, target: int,
                             base_suit: str, confirmed: dict[int, dict[str, Any]]
                             ) -> tuple[str, bool, str | None]:
    """At step 3: transform [current base prediction, preceding base prediction].

    Only two timely, sequential, confirmed LOSE results authorize the
    intervention. The fourth and subsequent steps use the usual majority.
    """
    second_number = ((target - 2) % ROUND_MAX) + 1
    first_number = ((target - 3) % ROUND_MAX) + 1
    older_number = ((target - 4) % ROUND_MAX) + 1
    first = strategy_five_past_outcome(db, sid, first_number, confirmed)
    second = strategy_five_past_outcome(db, sid, second_number, confirmed)
    if not first or not second:
        return base_suit, False, None
    if first["settled_result"] != "LOSE" or second["settled_result"] != "LOSE":
        return base_suit, False, None
    if not 0 <= (second["finished_moment"] - first["finished_moment"]).total_seconds() <= 185:
        return base_suit, False, None
    older_recorded = db.execute(
        "SELECT 1 FROM cycle18_forecasts WHERE session_id=? AND target_round_number=?",
        (sid, older_number)).fetchone()
    if older_recorded:
        older = strategy_five_past_outcome(db, sid, older_number, confirmed)
        if older is None:
            return base_suit, False, None
        older_gap = (first["finished_moment"] - older["finished_moment"]).total_seconds()
        if 0 <= older_gap <= 185 and older["settled_result"] == "LOSE":
            return base_suit, False, None
    previous_base = second.get("base_predicted_suit") or second["predicted_suit"]
    symbol = calculate_suit((base_suit, previous_base))
    if symbol is None:
        return base_suit, False, None
    return SUIT_SYMBOL_TO_NAME[symbol], True, previous_base


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
            if "base_predicted_suit" not in forecast_columns:
                db.execute("ALTER TABLE cycle18_forecasts ADD COLUMN base_predicted_suit TEXT")
            if "strategy_five_applied" not in forecast_columns:
                db.execute("ALTER TABLE cycle18_forecasts ADD COLUMN strategy_five_applied INTEGER NOT NULL DEFAULT 0")
            if "strategy_five_previous_suit" not in forecast_columns:
                db.execute("ALTER TABLE cycle18_forecasts ADD COLUMN strategy_five_previous_suit TEXT")
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
        selected = row[0] if row and row[0] in AVAILABLE_STRATEGIES else STRATEGY_N18
        return {"selected": selected, "active": session[0] if session else None,
                "options": list(STRATEGY_OPTIONS)}

    def save_strategy(self, strategy: str) -> dict[str, Any]:
        if strategy not in AVAILABLE_STRATEGIES:
            raise ValueError("Неизвестный фильтр прогнозирования")
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM cycle18_sessions WHERE stopped_at IS NULL LIMIT 1").fetchone():
                raise RuntimeError("Остановите сканирование перед сменой фильтра")
            db.execute("UPDATE bot_strategy_settings SET strategy=? WHERE id=1", (strategy,))
        return self.get_strategy()

    def start_session(self, *, resume: bool = False) -> int:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            selected = db.execute("SELECT strategy FROM bot_strategy_settings WHERE id=1").fetchone()
            strategy = selected[0] if selected else STRATEGY_N18
            if resume:
                last = db.execute(
                    "SELECT id,strategy,stopped_at,started_at FROM cycle18_sessions "
                    "ORDER BY id DESC LIMIT 1").fetchone()
                if last and last[1] == strategy:
                    ended = moment(last[2])
                    # Reopening an existing session preserves its 40-hand
                    # preparation and forecasts without forging card data.
                    # Do not resume a session after a long gap.
                    if ended and 0 <= (now_dt - ended).total_seconds() <= STALE_SECONDS:
                        db.execute("UPDATE cycle18_sessions SET stopped_at=NULL WHERE id=?",
                                   (last[0],))
                        return int(last[0])
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
            strategy = session["strategy"] if session["strategy"] in AVAILABLE_STRATEGIES else STRATEGY_N18
            warmup_required = strategy_warmup(strategy)
            sid, started_at = int(session["id"]), moment(session["started_at"])
            stopped_at = moment(session["stopped_at"])
            rows = [dict(row) for row in db.execute("""
                SELECT game_id,round_number,discovered_at,finished_at,
                       cards_json,completeness,calculated_suit,calculation_status,
                        final_snapshot_reverified,player_hand_final,banker_hand_final,
                       scan_result,reason,last_scanned_at,observed_at,baccarat_outcome
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
            observed_by_number: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                number = round_number(row.get("round_number"))
                if number is not None:
                    physical_counts[number] = physical_counts.get(number, 0) + 1
                    observed_by_number.setdefault(number, []).append(row)
            # A skipped physical round remains in the database, but must not
            # erase confirmed observations already collected during warm-up.
            # Still require true, unique, FINAL Player hands for 20/20.
            valid_hands: dict[int, dict[str, Any]] = {
                number: row for row in rows
                if (number := round_number(row.get("round_number"))) is not None
                and physical_counts[number] == 1 and source_verified(row, strategy)
            }
            pause_triggers = (draw_diamond_pause_triggers(rows) if strategy == STRATEGY_VOTE_DRAW_12 else [])
            warmup_complete = False
            missing_hands: list[dict[str, Any]] = []
            verified_total = 0
            captured_total = 0
            for hand in rows:
                physical = round_number(hand.get("round_number"))
                unique = physical is not None and physical_counts[physical] == 1
                confirmed = source_verified(hand, strategy) if unique else False
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
                # Preparation counts final Player hands. For the hot strategy,
                # the actual signal additionally requires eight PHYSICALLY
                # consecutive recent hands with <=185s between their timestamps.
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
                    # No forecast on a missing source, non-consecutive hot window
                    # or hot-suit count spread below six. A skip never bets.
                    continue
                source_number = forecast["source_round_number"]
                source = valid_hands[source_number]
                pause = pause_for_round(target_number, pause_triggers)
                base_suit = forecast["predicted_suit"]
                chosen_suit, step_three, previous_base = (
                    strategy_five_step_three(db, sid, target_number, base_suit, valid_hands)
                    if strategy == STRATEGY_FIVE else (base_suit, False, None))
                db.execute("""INSERT OR IGNORE INTO cycle18_forecasts
                    (session_id,source_game_id,anchor_game_id,source_round_number,
                     target_round_number,predicted_suit,created_at,sources_json,votes_json,
                     mode,reason,base_predicted_suit,strategy_five_applied,
                     strategy_five_previous_suit)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, source["game_id"], hand["game_id"],
                     source_number, target_number, chosen_suit,
                     now.isoformat(),
                     json.dumps(forecast["source_round_numbers"]),
                     json.dumps(forecast["votes"], ensure_ascii=False),
                     "FILTER" if pause else "UNLINKED", pause["reason"] if pause else "",
                     base_suit if strategy == STRATEGY_FIVE else None,
                     int(step_three), previous_base))
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
                    # Future rounds can be listed >10 minutes early; unique physical
                    # round identity and same session prevent wrong linking.
                    if abs(gap) <= STALE_SECONDS:
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
                            (target["game_id"], now.isoformat(),
                             "FILTER" if prediction["mode"] == "FILTER" else
                             ("LIVE" if not final else "BACKFILL"),
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

        checked = [row for row in forecasts if row["result"] in ("WIN", "LOSE")
                   and row["mode"] != "FILTER"]
        wins = sum(row["result"] == "WIN" for row in checked)
        losing = max_losing = 0
        steps: dict[str, int] = {}
        previous = None
        for row in forecasts:
            if row["mode"] == "FILTER" or not row["target_game_id"] or row["result"] not in ("WIN", "LOSE"):
                if row["result"] == "UNKNOWN" and strategy == STRATEGY_N18:
                    losing = 0
                    previous = None
                # A missing vote source means no attempt, not a success. In
                # voting mode preserve the attempted-loss count over skipped
                # physical rounds, provided the time gap is still plausible.
                continue
            if previous and strategy not in (STRATEGY_HOT_8, STRATEGY_53) and (
                not self._close_in_time(previous, row)
                or (strategy in (STRATEGY_N18, STRATEGY_FIVE)
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
        previews = (preview_two_rounds(current_number, valid_hands, strategy,
                                       observed_by_number)
                    if warmup_complete and not stopped_at else [])
        if strategy == STRATEGY_FIVE:
            # LIVE preview must agree with the immutable saved strategy signal.
            saved_by_number = {row["target_round_number"]: row for row in forecasts
                               if row["result"] == "PENDING"}
            for preview in previews:
                saved = saved_by_number.get(preview["target_round_number"])
                if saved and preview["available"]:
                    preview["predicted_suit"] = saved["predicted_suit"]
                    preview["base_predicted_suit"] = saved.get("base_predicted_suit")
                    preview["strategy_five_applied"] = bool(saved.get("strategy_five_applied"))
                    preview["strategy_five_previous_suit"] = saved.get("strategy_five_previous_suit")
        if strategy == STRATEGY_VOTE_DRAW_12:
            for preview in previews:
                pause = pause_for_round(preview["target_round_number"], pause_triggers)
                if pause:
                    preview.update(available=False, filter_paused=True, **pause)
        active = next((row for row in forecasts if current and row["target_game_id"] == current.get("game_id")), None)
        pending_upcoming = [row for row in forecasts if row["result"] == "PENDING" and not row["target_game_id"]]
        audit_signature = (sid, strategy,
            tuple((row["game_id"], row["round_number"], row["cards_json"], row["calculation_status"],
                   row["final_snapshot_reverified"], row["player_hand_final"], row["finished_at"],
                    row.get("banker_hand_final"), row.get("baccarat_outcome")) for row in rows),
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
            "pause_enabled": strategy == STRATEGY_VOTE_DRAW_12,
            "pause_triggers": pause_triggers[-25:],
            "filter_skipped_count": sum(row["mode"] == "FILTER" for row in forecasts),
            "filter_skipped_wins": sum(row["mode"] == "FILTER" and row["result"] == "WIN" for row in forecasts),
            "filter_skipped_losses": sum(row["mode"] == "FILTER" and row["result"] == "LOSE" for row in forecasts),
            "unconfirmed_forecasts": [self._public(row) for row in forecasts
                if row["result"] in ("PENDING", "UNKNOWN", "SKIPPED")
                and row["mode"] != "FILTER"][-30:][::-1],
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
            "recent": [self._public(row) for row in forecasts if row["target_game_id"]][-300:][::-1],
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
            "base_predicted_suit", "strategy_five_applied", "strategy_five_previous_suit",
        )}
        try:
            sources = json.loads(row.get("sources_json") or "null")
            votes = json.loads(row.get("votes_json") or "null")
        except (ValueError, TypeError):
            sources, votes = None, None
        result["source_round_numbers"] = sources if isinstance(sources, list) else [row.get("source_round_number")]
        result["votes"] = votes if isinstance(votes, list) else []
        if result["votes"]:
            result["unverified_outcome_round_numbers"] = (
                result["votes"][0].get("unverified_outcome_round_numbers", []))
            result["draw_filter_verified"] = not bool(result["unverified_outcome_round_numbers"])
        if result["votes"] and isinstance(result["votes"][0].get("cards"), list):
            counts = {suit: 0 for suit in SUIT_PRIORITY}
            for vote in result["votes"]:
                for suit in vote.get("cards", []):
                    if suit in counts:
                        counts[suit] += 1
            result["suit_counts"] = counts
            result["count_spread"] = max(counts.values()) - min(counts.values())
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
            "pause_enabled": strategy == STRATEGY_VOTE_DRAW_12,
            "pause_triggers": [], "filter_skipped_count": 0,
            "filter_skipped_wins": 0, "filter_skipped_losses": 0,
            "unconfirmed_forecasts": [],
            "ready": False, "session_active": False, "session_id": None,
            "discovered_count": 0, "missing_hands": [], "missing_count": 0,
            "statistics": {
                "checked_count": 0, "wins": 0, "losses": 0, "win_rate": None,
                "max_lose_streak": 0, "current_lose_streak": 0, "next_step": 1,
                "wins_by_step": {}, "live_checked": 0, "backfill_checked": 0,
            }, "current": None, "live_preview": [], "upcoming": [], "recent": [],
        }

