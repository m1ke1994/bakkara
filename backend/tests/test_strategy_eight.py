from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.live_bets import LiveBets
from app.observation import ObservationStore
from app.cycle18 import Cycle18Engine

from app.cycle18 import AVAILABLE_STRATEGIES, STRATEGY_EIGHT, strategy_offsets, strategy_warmup
from app.strategy_eight import MAX_STEPS, evaluate_eight


def history(outcomes):
    started = datetime(2026, 10, 31, tzinfo=timezone.utc)
    return [{
        "target_game_id": f"game-{index}",
        "target_round_number": index,
        "predicted_suit": "hearts",
        "created_at": (started + timedelta(minutes=index - 1, seconds=-20)).isoformat(),
        "target_finished_at": (started + timedelta(minutes=index - 1)).isoformat(),
        "result": result,
        "mode": "LIVE",
    } for index, result in enumerate(outcomes, start=1)]


def accepted(index, *, result, step):
    return {
        "game_id": f"game-{index}", "status": "ACCEPTED",
        "signal_source": "STRATEGY", "single_test_run_id": None,
        "result": result, "step": step, "suit": "hearts",
        "created_at": history(["LOSE"] * index)[-1]["target_finished_at"],
    }


def test_strategy_eight_inherits_exact_vote_sources_and_warmup():
    assert STRATEGY_EIGHT in AVAILABLE_STRATEGIES
    assert strategy_offsets(STRATEGY_EIGHT) == (3, 10, 24, 31, 40)
    assert strategy_warmup(STRATEGY_EIGHT) == 40
    assert MAX_STEPS == 9


def test_demo_entry_is_only_after_three_confirmed_virtual_losses():
    rows = history(["LOSE", "LOSE", "LOSE", "LOSE", "WIN", "LOSE"])
    status = evaluate_eight(rows, {}, mode="DEMO")
    assert status["before"]["game-1"]["bet_allowed"] is False
    assert status["before"]["game-3"]["bet_allowed"] is False
    assert status["before"]["game-4"]["bet_allowed"] is True
    assert status["before"]["game-4"]["step"] == 1
    assert status["before"]["game-5"]["step"] == 2
    assert status["before"]["game-6"]["phase"] == "WAITING"
    assert status["current"]["virtual_losses"] == 1
    assert status["current"]["wagers"] == 2
    assert status["current"]["wins"] == 1


def test_demo_nine_losses_stop_instead_of_auto_restarting():
    status = evaluate_eight(history(["LOSE"] * 14), {}, mode="DEMO")
    assert status["before"]["game-12"]["step"] == 9
    assert status["current"]["wagers"] == 9
    assert status["current"]["phase"] == "EXHAUSTED"
    assert status["current"]["bet_allowed"] is False


def test_skipped_and_unknown_forecasts_do_not_create_fake_loss():
    rows = history(["LOSE", "PENDING", "LOSE", "LOSE"])
    rows[2]["mode"] = "FILTER"
    status = evaluate_eight(rows, {}, mode="DEMO")
    assert status["current"]["phase"] == "WAITING"
    assert status["current"]["virtual_losses"] == 2


def test_live_rejected_wager_does_not_advance_and_accepted_win_resets():
    rows = history(["LOSE", "LOSE", "LOSE", "LOSE", "LOSE", "WIN", "LOSE"])
    attempts = {
        "game-4": {**accepted(4, result="LOSE", step=1), "status": "REJECTED"},
        "game-5": accepted(5, result="LOSE", step=1),
        "game-6": accepted(6, result="WIN", step=2),
    }
    status = evaluate_eight(rows, attempts, mode="LIVE")
    assert status["before"]["game-5"]["step"] == 1
    assert status["before"]["game-6"]["step"] == 2
    assert status["current"]["phase"] == "WAITING"
    assert status["current"]["virtual_losses"] == 1
    assert status["current"]["wagers"] == 2


def test_live_inconsistent_settlement_blocks_future_money():
    rows = history(["LOSE", "LOSE", "LOSE", "LOSE"])
    attempts = {"game-4": accepted(4, result="WIN", step=1)}
    status = evaluate_eight(rows, attempts, mode="LIVE")
    assert status["current"]["phase"] == "BLOCKED"
    assert not status["current"]["bet_allowed"]



def test_strategy_eight_can_refresh_without_live_tables(tmp_path):
    path = tmp_path / "only-observer.sqlite3"
    ObservationStore(path)
    engine = Cycle18Engine(path)
    engine.save_strategy(STRATEGY_EIGHT)
    engine.start_session()
    snapshot = engine.refresh()
    assert snapshot["strategy"] == STRATEGY_EIGHT
    assert snapshot["strategy_eight"]["phase"] == "WAITING"
    assert snapshot["strategy_eight"]["mode"] == "DEMO"
    assert snapshot["strategy_eight"]["demo_plan_ready"] is False


def test_live_attempts_persist_strategy_and_correct_step(tmp_path):
    ledger = LiveBets(tmp_path / "ledger.sqlite3")
    ledger.mode("LIVE")
    assert ledger.reserve("game-4", "4", "hearts", Decimal("15"),
                          expected_step=1, strategy=STRATEGY_EIGHT, strategy_session_id=7)
    saved = ledger.get_attempt("game-4")
    assert saved["strategy"] == STRATEGY_EIGHT
    assert saved["strategy_session_id"] == 7
    assert saved["step"] == 1
    ledger.update("game-4", "ACCEPTED")
    assert ledger.settle("game-4", "LOSE", 9)
    assert ledger.snapshot()["step"] == 2
    assert not ledger.settle("game-4", "WIN", 9)
