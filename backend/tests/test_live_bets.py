from decimal import Decimal
import sqlite3

import pytest

from app.live_bets import LiveBets
from app.live_executor import PlacementError, outcome_box
from app.browser import forecast_precedes_attempt


def test_live_attempt_is_durable_and_only_accepted_bet_moves_step(tmp_path):
    path = tmp_path / "live.sqlite3"
    bets = LiveBets(path)
    assert bets.reserve("game-1", "601", "clubs", Decimal("15"))
    assert not bets.reserve("game-1", "601", "clubs", Decimal("15"))
    assert not LiveBets(path).reserve("game-2", "602", "hearts", Decimal("30"))
    assert not bets.settle("game-1", "LOSE", 3)
    bets.update("game-1", "SUBMITTING")
    bets.update("game-1", "ACCEPTED")
    assert bets.settle("game-1", "LOSE", 3)
    assert not bets.settle("game-1", "LOSE", 3)
    assert bets.snapshot()["step"] == 2
    assert bets.reserve("game-2", "602", "hearts", Decimal("30"))
    bets.update("game-2", "ACCEPTED")
    assert bets.settle("game-2", "WIN", 3)
    assert bets.snapshot()["step"] == 1


def test_unknown_submission_blocks_new_attempt_after_restart(tmp_path):
    path = tmp_path / "live.sqlite3"
    bets = LiveBets(path)
    assert bets.reserve("game-1", "601", "clubs", Decimal("15"))
    bets.update("game-1", "UNKNOWN")
    restored = LiveBets(path)
    assert restored.snapshot()["blocked"]
    assert not restored.reserve("game-2", "602", "clubs", Decimal("15"))
    assert not restored.settle("game-1", "LOSE", 3)


def test_single_stake_test_limit_survives_result_and_restart(tmp_path):
    path = tmp_path / "live.sqlite3"
    bets = LiveBets(path)
    run_id = bets.begin_single_stake_test()
    assert LiveBets(path).begin_single_stake_test() == run_id
    assert not bets.reserve("wrong-amount", "600", "clubs", Decimal("11"))
    assert bets.reserve("game-1", "601", "clubs", Decimal("10"))
    bets.update("game-1", "SUBMITTING")
    bets.update("game-1", "ACCEPTED")
    assert bets.settle("game-1", "WIN", 3)
    restored = LiveBets(path)
    assert restored.snapshot()["single_test_run_id"] == run_id
    assert not restored.reserve("game-2", "602", "clubs", Decimal("10"))


def test_single_ten_ruble_test_preserves_existing_strategy_step(tmp_path):
    path = tmp_path / "live.sqlite3"
    bets = LiveBets(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE live_control SET step=2 WHERE id=1")
    bets.begin_single_stake_test()
    assert bets.reserve("test-1", "953", "diamonds", Decimal("10"),
                        signal_source="MANUAL_TEST")
    bets.update("test-1", "ACCEPTED")
    assert bets.settle("test-1", "LOSE", 8)
    restarted = LiveBets(path)
    assert restarted.snapshot()["step"] == 2
    assert restarted.snapshot()["blocked"]
    assert not restarted.reserve("test-2", "954", "diamonds", Decimal("10"))


def test_forecast_must_exist_before_accepted_attempt():
    assert forecast_precedes_attempt("2026-10-25T10:00:00.100000+00:00",
                                     "2026-10-25T10:00:00.200000+00:00")
    assert not forecast_precedes_attempt("2026-10-25T10:00:00.300000+00:00",
                                         "2026-10-25T10:00:00.200000+00:00")
    assert forecast_precedes_attempt("2026-10-25T10:00:00.300000+00:00",
                                     "2026-10-25 10:00:00")


def test_canvas_click_maps_to_coefficient_not_suit_label():
    labels = [{"text": name, "x": 20, "y": 40 + index * 30,
               "width": 40, "height": 12} for index, name in
              enumerate(("трефы", "черви", "бубны", "пики"))]
    odds = {label["text"]: {"x": 880, "y": label["y"], "width": 22,
                            "height": 12, "amount": 1.9}
            for label in labels}
    probe = {"ok": True, "marketTitles": [{"text": "Игрок получит карту", "y": 10}],
             "labels": labels, "oddsBySuit": odds,
             "canvas": {"width": 951, "height": 850}}
    assert outcome_box(probe, "clubs")["x"] == 880
    assert outcome_box(probe, "clubs")["odds"] == 1.9
    with pytest.raises(PlacementError):
        outcome_box({**probe, "oddsBySuit": {}}, "clubs")
    with pytest.raises(PlacementError):
        outcome_box({**probe, "labels": labels[:-1]}, "clubs")
    with pytest.raises(PlacementError):
        outcome_box({**probe, "marketTitles": []}, "clubs")
