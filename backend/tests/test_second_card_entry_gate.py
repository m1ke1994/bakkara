"""Only the N-1 second-Player-card forecast may trigger this configurable LIVE entry."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.cycle18 import Cycle18Engine, STRATEGY_N18, STRATEGY_SECOND_CARD
from app.live_bets import LiveBets
from app.observation import Game, ObservationStore
from app.strategy_eight import evaluate_eight


def history(results, *, numbers=None):
    base = datetime(2026, 11, 3, tzinfo=timezone.utc)
    numbers = numbers or list(range(1, len(results) + 1))
    return [{
        "target_game_id": f"game-{number}",
        "target_round_number": number,
        "predicted_suit": "hearts",
        "created_at": (base + timedelta(minutes=index, seconds=-20)).isoformat(),
        "target_finished_at": (base + timedelta(minutes=index)).isoformat(),
        "result": result,
        "mode": "LIVE",
    } for index, (number, result) in enumerate(zip(numbers, results), start=1)]


def accepted(number, result, step, *, status="ACCEPTED"):
    base = datetime(2026, 11, 3, tzinfo=timezone.utc)
    return {
        "game_id": f"game-{number}",
        "signal_source": "STRATEGY", "single_test_run_id": None,
        "status": status, "result": result, "step": step, "suit": "hearts",
        "created_at": (base + timedelta(minutes=number, seconds=-10)).isoformat(),
    }


def hand(store, number, suits):
    game = Game(f"round-{number}", f"https://example.test/baccarat/{number}",
                "00:00", str(number), "Завершена", number)
    store.upsert(game, [
        {"position": position, "rank": "K" if position == 2 else "6", "suit": suit}
        for position, suit in enumerate(suits, 1)
    ], "CONFIRMED", "Подтверждено", "CARDS_CONFIRMED",
        finished_confirmed=True, final_snapshot_reverified=True,
        player_hand_final=True)


def current(engine, number):
    return engine.refresh({"game_id": f"round-{number}", "round_number": str(number)})


def test_entry_after_three_virtual_losses_and_restart_after_live_win():
    rows = history(["LOSE", "LOSE", "LOSE", "LOSE", "WIN", "LOSE", "LOSE", "LOSE"])
    attempts = {"game-4": accepted(4, "LOSE", 1),
                "game-5": accepted(5, "WIN", 2)}
    gate = evaluate_eight(rows, attempts, mode="LIVE", wait_losses=3,
                          max_steps=8, strict_consecutive=True)
    assert not gate["before"]["game-3"]["bet_allowed"]
    assert gate["before"]["game-4"]["bet_allowed"]
    assert gate["before"]["game-4"]["step"] == 1
    assert gate["before"]["game-5"]["step"] == 2
    assert gate["before"]["game-6"]["phase"] == "WAITING"
    assert gate["before"]["game-8"]["bet_allowed"] is False
    assert gate["current"]["bet_allowed"]
    assert gate["current"]["step"] == 1
    assert gate["current"]["entries"] == 2
    assert gate["current"]["wins"] == 1


@pytest.mark.parametrize("results,numbers,expected", [
    (["LOSE", "LOSE", "LOSE", "LOSE"], [1, 2, 4, 5], 2),
    (["LOSE", "PENDING", "LOSE", "LOSE"], [1, 2, 3, 4], 2),
    (["LOSE", "WIN", "LOSE", "LOSE"], [1, 2, 3, 4], 2),
    (["LOSE", "LOSE", "UNKNOWN", "LOSE"], [1, 2, 3, 4], 1),
])
def test_only_consecutive_verified_losses_unlock_entry(results, numbers, expected):
    gate = evaluate_eight(history(results, numbers=numbers), {},
                          mode="LIVE", wait_losses=3, strict_consecutive=True)
    assert gate["current"]["virtual_losses"] == expected
    assert not gate["current"]["bet_allowed"]


def test_no_duplicate_or_unconfirmed_bets_and_no_retry_after_exhaustion():
    rows = history(["LOSE"] * 6, numbers=[1439, 1440, 1, 2, 3, 4])
    attempts = {"game-2": accepted(2, "LOSE", 1),
                "game-3": accepted(3, "LOSE", 2)}
    gate = evaluate_eight(rows, attempts, mode="LIVE", wait_losses=3,
                          max_steps=2, strict_consecutive=True)
    assert gate["before"]["game-2"]["bet_allowed"]
    assert gate["before"]["game-3"]["step"] == 2
    assert gate["current"]["phase"] == "EXHAUSTED"
    assert not gate["current"]["bet_allowed"]
    attempts["game-2"]["status"] = "UNKNOWN"
    gate = evaluate_eight(rows, attempts, mode="LIVE", wait_losses=3,
                          max_steps=2, strict_consecutive=True)
    assert gate["current"]["step"] == 1


def test_save_threshold_and_frozen_active_session(tmp_path):
    path = tmp_path / "saved.sqlite3"
    engine = Cycle18Engine(path)
    config = engine.save_strategy(STRATEGY_SECOND_CARD, entry_losses=3)
    assert config["selected"] == STRATEGY_SECOND_CARD
    assert config["entry_losses"] == 3
    assert Cycle18Engine(path).get_strategy()["entry_losses"] == 3
    original_session = engine.start_session()
    assert engine.refresh()["entry_losses"] == 3
    with pytest.raises(RuntimeError):
        engine.save_strategy(STRATEGY_SECOND_CARD, entry_losses=5)
    engine.stop_session()
    assert engine.save_strategy(STRATEGY_SECOND_CARD, entry_losses=5)["entry_losses"] == 5
    assert engine.start_session(resume=True) != original_session
    assert engine.refresh()["entry_losses"] == 5
    engine.stop_session()
    assert engine.save_strategy(STRATEGY_N18)["entry_losses"] == 0
    for invalid in (-1, 31, True, 2.5):
        with pytest.raises(ValueError):
            engine.save_strategy(STRATEGY_SECOND_CARD, entry_losses=invalid)


def test_live_uses_previous_second_card_after_wait_then_saved_stake_row(tmp_path):
    path = tmp_path / "hand.sqlite3"
    store = ObservationStore(path)
    engine = Cycle18Engine(path)
    ledger = LiveBets(path)
    engine.save_strategy(STRATEGY_SECOND_CARD, entry_losses=3)
    session_id = engine.start_session()
    ledger.mode("LIVE")

    hand(store, 1, ("spades", "clubs"))
    assert not current(engine, 2)["live_preview"][0]["available"]
    hand(store, 2, ("hearts", "diamonds"))  # #2 predicted clubs -> LOSE
    assert current(engine, 3)["entry_gate"]["virtual_losses"] == 1
    hand(store, 3, ("spades", "clubs"))  # #3 predicted diamonds -> LOSE
    assert current(engine, 4)["entry_gate"]["virtual_losses"] == 2
    hand(store, 4, ("hearts", "diamonds"))  # #4 predicted clubs -> LOSE
    fifth = current(engine, 5)
    assert fifth["entry_gate"]["virtual_losses"] == 3
    assert fifth["entry_gate"]["bet_allowed"]
    assert fifth["entry_gate"]["step"] == 1
    assert fifth["live_preview"][0]["available"]
    assert fifth["live_preview"][0]["predicted_suit"] == "diamonds"

    assert ledger.reserve("round-5", "5", "diamonds", Decimal("15"),
                          expected_step=1, strategy=STRATEGY_SECOND_CARD,
                          strategy_session_id=session_id)
    assert not ledger.reserve("round-5", "5", "diamonds", Decimal("15"))
    ledger.update("round-5", "ACCEPTED")
    hand(store, 5, ("spades", "clubs"))  # #5 predicted diamonds -> LOSE
    current(engine, 6)  # Confirm actual cards against the saved forecast
    assert ledger.settle("round-5", "LOSE", 8)
    sixth = current(engine, 6)
    assert sixth["entry_gate"]["step"] == 2
    assert sixth["entry_gate"]["bet_allowed"]
    assert sixth["live_preview"][0]["predicted_suit"] == "clubs"

    assert ledger.reserve("round-6", "6", "clubs", Decimal("30"),
                          expected_step=2, strategy=STRATEGY_SECOND_CARD,
                          strategy_session_id=session_id)
    ledger.update("round-6", "ACCEPTED")
    hand(store, 6, ("hearts", "clubs"))  # #6 predicted clubs -> WIN
    current(engine, 7)
    assert ledger.settle("round-6", "WIN", 8)
    seventh = current(engine, 7)
    assert seventh["entry_gate"]["phase"] == "WAITING"
    assert seventh["entry_gate"]["virtual_losses"] == 0
    assert seventh["entry_gate"]["step"] == 1
    assert not seventh["live_preview"][0]["available"]
    assert ledger.snapshot()["step"] == 1
