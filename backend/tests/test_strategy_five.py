"""Regression tests for frozen Strategy 5 (reverse conversion on step three).

This strategy is selected explicitly; legacy strategies must remain unchanged.
No forecast is allowed to use Player cards from its future target round.
"""
import sqlite3

from app.cycle18 import Cycle18Engine, STRATEGY_FIVE, STRATEGY_VOTE
from app.observation import Game, ObservationStore
from app.suit_calculator import calculate_suit


def add_hand(store, number, suits=("hearts", "hearts")):
    game = Game(
        f"strategy-five-{number}",
        f"https://example.test/baccarat/{number}-player-banker",
        "00:00", str(number), "Завершена", number,
    )
    store.upsert(
        game,
        [{"position": index, "rank": "9", "suit": suit}
         for index, suit in enumerate(suits, 1)],
        "CONFIRMED", "Финальные карты", "CARDS_CONFIRMED",
        finished_confirmed=True, final_snapshot_reverified=True,
        player_hand_final=True,
    )
    return game


def start_ready(tmp_path, *, mixed_vote=False):
    db_path = tmp_path / "strategy-five.sqlite3"
    store, engine = ObservationStore(db_path), Cycle18Engine(db_path)
    options = engine.get_strategy()["options"]
    assert len(options) == 6
    assert options[-1]["id"] == STRATEGY_FIVE
    assert engine.save_strategy(STRATEGY_FIVE)["selected"] == STRATEGY_FIVE
    assert Cycle18Engine(db_path).get_strategy()["selected"] == STRATEGY_FIVE
    engine.start_session()
    # Source sets for targets 41,42,43 are disjoint.
    # These sources make target 43 a spade vote, targets 41,42 diamonds.
    spade_sources = {3, 12, 19, 33, 40} if mixed_vote else set()
    for number in range(1, 41):
        add_hand(store, number, ("diamonds", "spades") if number in spade_sources
                 else ("hearts", "hearts"))
    state = engine.refresh()
    assert state["ready"] is True
    assert state["warmup_required"] == 40
    assert state["upcoming"][0]["target_round_number"] == 41
    assert state["upcoming"][0]["predicted_suit"] == "diamonds"
    return store, engine


def read_forecast(engine, number):
    with sqlite3.connect(engine.path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM cycle18_forecasts WHERE target_round_number=?",
            (number,),
        ).fetchone()
        return dict(row) if row else None


def reach_third_step(store, engine):
    add_hand(store, 41)
    engine.refresh()
    assert read_forecast(engine, 41)["result"] == "LOSE"
    assert read_forecast(engine, 42)["predicted_suit"] == "diamonds"
    add_hand(store, 42)
    state = engine.refresh()
    assert read_forecast(engine, 42)["result"] == "LOSE"
    assert state["statistics"]["next_step"] == 3
    return state


def test_reverse_order_on_third_step_is_noncommutative_and_persistent(tmp_path):
    store, engine = start_ready(tmp_path, mixed_vote=True)
    reach_third_step(store, engine)
    row = read_forecast(engine, 43)
    assert row["base_predicted_suit"] == "spades"
    assert row["strategy_five_previous_suit"] == "diamonds"
    assert calculate_suit(("spades", "diamonds")) == "♥"
    assert calculate_suit(("diamonds", "spades")) == "♠"
    assert row["predicted_suit"] == "hearts"
    assert row["strategy_five_applied"] == 1
    assert engine.refresh()["upcoming"][0]["predicted_suit"] == "hearts"
    assert read_forecast(engine, 43)["predicted_suit"] == "hearts"
    current = engine.refresh({"game_id": "strategy-five-43", "round_number": "43"})
    preview = next(x for x in current["live_preview"] if x["phase"] == "CURRENT")
    assert preview["predicted_suit"] == "hearts"
    assert preview["base_predicted_suit"] == "spades"
    assert preview["strategy_five_applied"] is True
    add_hand(store, 43)
    state = engine.refresh()
    assert read_forecast(engine, 43)["result"] == "WIN"
    assert state["statistics"]["next_step"] == 1


def test_only_step_three_changes_and_step_four_returns_to_base(tmp_path):
    store, engine = start_ready(tmp_path)
    reach_third_step(store, engine)
    third = read_forecast(engine, 43)
    assert third["base_predicted_suit"] == "diamonds"
    assert third["predicted_suit"] == "hearts"
    assert third["strategy_five_applied"] == 1
    # Clubs do not match the transformed hearts; three consecutive losses.
    add_hand(store, 43, ("clubs", "clubs"))
    state = engine.refresh()
    assert read_forecast(engine, 43)["result"] == "LOSE"
    fourth = read_forecast(engine, 44)
    assert fourth["predicted_suit"] == "diamonds"
    assert fourth["base_predicted_suit"] == "diamonds"
    assert fourth["strategy_five_applied"] == 0
    assert state["statistics"]["next_step"] == 4
    add_hand(store, 44, ("clubs", "clubs"))
    engine.refresh()
    fifth = read_forecast(engine, 45)
    assert fifth["strategy_five_applied"] == 0


def test_no_retroactive_third_step_when_forecasts_were_not_published(tmp_path):
    store, engine = start_ready(tmp_path)
    # These hands were all finished before the engine published 41/42.
    # Late historical forecasts must NOT authorize a fabricated step 3.
    add_hand(store, 41)
    add_hand(store, 42)
    state = engine.refresh()
    third = read_forecast(engine, 43)
    assert third["predicted_suit"] == "diamonds"
    assert third["strategy_five_applied"] == 0
    assert state["statistics"]["wins"] == 0


def test_strategy_five_requires_all_five_verified_voters(tmp_path):
    path = tmp_path / "source-gap.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.save_strategy(STRATEGY_FIVE)
    engine.start_session()
    for number in range(1, 42):
        if number != 39:
            add_hand(store, number)
    state = engine.refresh()
    assert state["warmup_count"] == 40
    assert state["ready"]
    # Target 42 requires the unavailable N-3 = 39 source.
    assert not any(x["target_round_number"] == 42 for x in state["upcoming"])
    assert read_forecast(engine, 42) is None


def test_old_voting_is_still_selectable(tmp_path):
    path = tmp_path / "old-strategy.sqlite3"
    engine = Cycle18Engine(path)
    assert engine.save_strategy(STRATEGY_VOTE)["selected"] == STRATEGY_VOTE
    assert engine.get_strategy()["selected"] == STRATEGY_VOTE
