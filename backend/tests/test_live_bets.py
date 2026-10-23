from decimal import Decimal

import pytest

from app.live_bets import LiveBets
from app.live_executor import PlacementError, outcome_box


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


def test_canvas_requires_market_and_four_unique_suits():
    labels = [{"text": name, "x": 20, "y": 30 + index * 20,
               "width": 40, "height": 12} for index, name in
              enumerate(("трефы", "черви", "бубны", "пики"))]
    probe = {"ok": True, "marketTitles": [{"text": "Игрок получит карту", "y": 10}],
             "labels": labels, "canvas": {"width": 951, "height": 850}}
    assert outcome_box(probe, "clubs")["y"] == 24
    with pytest.raises(PlacementError):
        outcome_box({**probe, "labels": labels[:-1]}, "clubs")
    with pytest.raises(PlacementError):
        outcome_box({**probe, "marketTitles": []}, "clubs")
