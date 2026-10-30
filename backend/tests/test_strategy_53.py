"""Regression tests: isolated «53%» signal, frozen 16-card map."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.cycle18 import (STRATEGY_53, Cycle18Engine, predict_target,
                         facecard_53_analysis, strategy_offsets, strategy_warmup)
from app.strategy_53 import MAP_53, map_facecard_53

BASE = datetime(2026, 10, 18, tzinfo=timezone.utc)
NAMES = {"♥": "hearts", "♦": "diamonds", "♣": "clubs", "♠": "spades"}


def cards(items):
    return [{"position": i + 1, "rank": r, "suit": NAMES[s]}
            for i, (r, s) in enumerate(items)]


def row(number, items=None, *, outcome="PLAYER", verified=True):
    stamp = (BASE + timedelta(minutes=number)).isoformat()
    return {
        "round_number": str(number), "game_id": str(number),
        "finished_at": stamp if verified else None, "observed_at": stamp,
        "final_snapshot_reverified": 1 if verified else 0,
        "player_hand_final": 1 if verified else 0, "baccarat_outcome": outcome,
        "banker_hand_final": 1 if outcome in ("PLAYER", "BANKER", "DRAW") else 0,
        "calculation_status": "not_calculated",
        "cards_json": json.dumps(cards(items or [("2", "♥"), ("4", "♦")])),
    }


def history():
    sources = {n: row(n) for n in range(1, 41)}
    sources[40] = row(40, [("A", "♥"), ("4", "♦")])
    return sources


@pytest.mark.parametrize("key,target", list(MAP_53.items()))
def test_all_sixteen_assignments(key, target):
    r, s = key
    got = map_facecard_53(cards([(r, s), ("2", "♦")]))
    assert got["predicted_suit"] == NAMES[target]
    assert not got["mirrored"]


def test_middle_of_three_mirrors_output_only_and_unique_face_required():
    result = map_facecard_53(cards([("2", "♦"), ("A", "♥"), ("4", "♠")]))
    assert result["predicted_suit"] == "diamonds"  # A♥ -> ♣ -> ♦
    assert result["mirrored"]
    assert map_facecard_53(cards([("A", "♥"), ("2", "♦"), ("4", "♠")]))["predicted_suit"] == "clubs"
    assert map_facecard_53(cards([("A", "♥"), ("K", "♠")])) is None
    assert map_facecard_53(cards([("2", "♥"), ("4", "♣")])) is None


def test_next_forecast_requires_exact_40_prior_complete_hands():
    h = history()
    assert strategy_warmup(STRATEGY_53) == 40
    assert strategy_offsets(STRATEGY_53) == tuple(range(1, 41))
    x = predict_target(41, h, STRATEGY_53)
    assert x["predicted_suit"] == "clubs"
    assert x["source_round_number"] == 40
    assert x["votes"][0]["card"] == "A♥"
    del h[20]
    assert predict_target(41, h, STRATEGY_53) is None
    assert 20 in facecard_53_analysis(41, h)["missing_source_round_numbers"]


def test_pause_four_rounds_after_verified_draw():
    h = history()
    h[37]["baccarat_outcome"] = "DRAW"  # N-4: skip.
    assert predict_target(41, h, STRATEGY_53) is None
    h[37]["baccarat_outcome"] = "PLAYER"
    h[36]["baccarat_outcome"] = "DRAW"  # N-5: allowed.
    assert predict_target(41, h, STRATEGY_53)["predicted_suit"] == "clubs"
    h[40]["baccarat_outcome"] = ""  # Missing Banker outcome => preview only.
    assert predict_target(41, h, STRATEGY_53) is None
    partial = facecard_53_analysis(41, h)
    assert partial["provisional_suit"] == "clubs"
    assert not partial["draw_filter_verified"]
    assert partial["unverified_outcome_round_numbers"] == [40]
    h[39]["baccarat_outcome"] = "DRAW"
    assert predict_target(41, h, STRATEGY_53) is None


def test_legacy_caption_only_outcome_is_not_a_verified_draw_filter():
    h = history()
    h[40]["banker_hand_final"] = 0
    assert predict_target(41, h, STRATEGY_53) is None
    assert facecard_53_analysis(41, h)["unverified_outcome_round_numbers"] == [40]


def test_unconfirmed_hand_or_time_gap_blocks_signal():
    h = history()
    h[40]["final_snapshot_reverified"] = 0
    assert predict_target(41, h, STRATEGY_53) is None
    h = history()
    h[27]["finished_at"] = (BASE + timedelta(minutes=27, seconds=190)).isoformat()
    assert predict_target(41, h, STRATEGY_53) is None


def test_unknown_banker_outcomes_block_signal_but_expose_provisional_suit():
    sources = history()
    for item in sources.values():
        item["baccarat_outcome"] = ""
    assert predict_target(41, sources, STRATEGY_53) is None
    preview = facecard_53_analysis(41, sources)
    assert preview["provisional_suit"] == "clubs"
    assert preview["unverified_outcome_round_numbers"] == [40, 39, 38, 37]
    assert not preview["draw_filter_verified"]
    sources[37]["baccarat_outcome"] = "DRAW"
    assert predict_target(41, sources, STRATEGY_53) is None


def test_real_card_examples_from_30_october():
    sources = history()
    sources[40] = row(40, [("J", "♣"), ("2", "♠"), ("8", "♠")], outcome="")
    result = facecard_53_analysis(41, sources)
    assert result["provisional_suit"] == "clubs"
    sources[40] = row(40, [("3", "♥"), ("J", "♣"), ("7", "♥")], outcome="")
    result = facecard_53_analysis(41, sources)
    assert result["provisional_suit"] == "diamonds"
    assert result["mirrored"] is True


def test_draw_pause_is_exactly_four_physical_rounds_and_restarts_on_new_draw():
    sources = {n: row(n) for n in range(1, 46)}
    for n in range(40, 46):
        sources[n] = row(n, [("A", "♥"), ("4", "♦")])
    sources[40]["baccarat_outcome"] = "DRAW"
    for target in range(41, 45):
        analysis = facecard_53_analysis(target, sources)
        assert analysis["pause_remaining"] == 45 - target
        assert predict_target(target, sources, STRATEGY_53) is None
    assert predict_target(45, sources, STRATEGY_53) is not None
    sources[42]["baccarat_outcome"] = "DRAW"
    for target in range(43, 47):
        assert predict_target(target, sources, STRATEGY_53) is None


def test_independent_strategy_selection_survives_restart(tmp_path):
    path = tmp_path / "observation.sqlite3"
    engine = Cycle18Engine(path)
    assert any(o["id"] == STRATEGY_53 for o in engine.get_strategy()["options"])
    engine.save_strategy(STRATEGY_53)
    assert Cycle18Engine(path).get_strategy()["selected"] == STRATEGY_53
    engine.start_session()
    with pytest.raises(RuntimeError):
        engine.save_strategy("n18")
    engine.stop_session()
    engine.save_strategy("n18")
    assert engine.get_strategy()["selected"] == "n18"
