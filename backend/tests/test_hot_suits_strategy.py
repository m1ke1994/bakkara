"""Frozen backtest rule: 8 completed Player hands, raw suit frequencies, spread >= 6.

This strategy is independent of the suit transformation table, N-18 and voting.
Only already revealed, final and uniquely numbered hands count as sources.
"""
import json
from datetime import datetime, timedelta, timezone

from app.cycle18 import (
    STRATEGY_HOT_8,
    STRATEGY_N18,
    STRATEGY_VOTE,
    STRATEGY_VOTE_DRAW_12,
    Cycle18Engine,
    hot_window_analysis,
    predict_target,
    preview_two_rounds,
    strategy_offsets,
    strategy_warmup,
)
from app.observation import Game, ObservationStore


BASE = datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc)
NAMES = ("hearts", "diamonds", "clubs", "spades")


def hand(number, suits, tick, *, final=True):
    return {
        "round_number": str(number),
        "game_id": f"test-{number}",
        "finished_at": (BASE + timedelta(seconds=tick)).isoformat() if final else None,
        "observed_at": (BASE + timedelta(seconds=tick)).isoformat(),
        "player_hand_final": 1 if final else 0,
        "final_snapshot_reverified": 1 if final else None,
        # Intentionally never calculated: this strategy MUST ignore suit_map.
        "calculation_status": "not_calculated",
        "cards_json": json.dumps([
            {"position": index, "rank": "A", "suit": suit}
            for index, suit in enumerate(suits, 1)
        ]),
    }


def source_window(numbers, all_suits=None, *, minutes=1):
    # 8 hands * 2 cards, hearts 8, diamonds 4, clubs 2, spades 2.
    cards = all_suits or (
        ["hearts"] * 8 + ["diamonds"] * 4 +
        ["clubs"] * 2 + ["spades"] * 2
    )
    assert len(numbers) == 8 and len(cards) == 16
    return {
        number: hand(number, cards[i * 2:i * 2 + 2], i * 60 * minutes)
        for i, number in enumerate(numbers)
    }


def test_four_strategies_and_raw_eight_hand_signal():
    assert strategy_warmup(STRATEGY_HOT_8) == 8
    assert strategy_offsets(STRATEGY_HOT_8) == tuple(range(1, 9))
    assert all(strategy_warmup(s) in (20, 40) for s in (
        STRATEGY_N18, STRATEGY_VOTE, STRATEGY_VOTE_DRAW_12))
    sources = source_window(list(range(101, 109)))
    result = predict_target(109, sources, STRATEGY_HOT_8)
    assert result["predicted_suit"] == "hearts"
    assert result["source_round_numbers"] == list(range(108, 100, -1))
    assert result["suit_counts"] == dict(
        hearts=8, diamonds=4, clubs=2, spades=2)
    assert result["count_spread"] == 6
    assert result["vote_count"] == 8
    assert result["votes"][0]["round_number"] == 108
    assert result["votes"][0]["cards"] == ["spades", "spades"]
    assert result["partial_vote"] is False


def test_low_spread_always_skips_without_advancing_a_step():
    sources = source_window(list(range(101, 109)),
                            ["hearts"] * 7 + ["diamonds"] * 4 +
                            ["clubs"] * 3 + ["spades"] * 2)
    assert predict_target(109, sources, STRATEGY_HOT_8) is None
    details = hot_window_analysis(109, sources)
    assert details["count_spread"] == 5
    assert "пропуск" in details["reason"]
    preview = preview_two_rounds(109, sources, STRATEGY_HOT_8)[0]
    assert preview["available"] is False
    assert preview["count_spread"] == 5
    assert preview["skip_reason"] == details["reason"]


def test_unconfirmed_gap_duplicate_and_185_second_limit():
    sources = source_window(list(range(101, 109)))
    del sources[104]
    assert predict_target(109, sources, STRATEGY_HOT_8) is None
    previews = preview_two_rounds(109, sources, STRATEGY_HOT_8)
    assert previews[0]["missing_sources"][0]["round_number"] == 104

    sources = source_window(list(range(101, 109)))
    sources[105]["player_hand_final"] = 0
    sources[105]["finished_at"] = None
    assert predict_target(109, sources, STRATEGY_HOT_8) is None

    sources = source_window(list(range(101, 109)))
    sources[105]["final_snapshot_reverified"] = None
    assert predict_target(109, sources, STRATEGY_HOT_8) is None

    # Numbered hand #105 cannot be filled with the completed #106's cards.
    sources = source_window(list(range(101, 109)))
    sources[105]["round_number"] = "106"
    assert predict_target(109, sources, STRATEGY_HOT_8) is None

    # Source #104 is 186s after #103, even though no numbers are missing.
    sources = source_window(list(range(101, 109)))
    for number in range(104, 109):
        ts = BASE + timedelta(seconds=(number - 101) * 60 + 126)
        sources[number]["finished_at"] = ts.isoformat()
    assert predict_target(109, sources, STRATEGY_HOT_8) is None


def test_wraparound_and_fixed_tie_priority():
    numbers = [1437, 1438, 1439, 1440, 1, 2, 3, 4]
    sources = source_window(numbers, ["clubs"] * 8 + ["spades"] * 8)
    out = predict_target(5, sources, STRATEGY_HOT_8)
    assert out["predicted_suit"] == "clubs"
    assert out["source_round_numbers"] == [4, 3, 2, 1, 1440, 1439, 1438, 1437]


def test_three_cards_are_counted_and_banker_is_ignored():
    sources = source_window(list(range(101, 109)))
    # Add a third verified card to every source hand; it contributes to count.
    for source in sources.values():
        cards = json.loads(source["cards_json"])
        cards.append({"position": 3, "rank": "K", "suit": "spades"})
        source["cards_json"] = json.dumps(cards)
        source["banker_cards_json"] = json.dumps([{"suit": "clubs"}] * 3)
    result = predict_target(109, sources, STRATEGY_HOT_8)
    assert result["suit_counts"]["spades"] == 10
    assert result["predicted_suit"] == "spades"


def add_final(store, number, suits):
    game = Game(f"round-{number}", f"https://example.test/baccarat/{number}",
                "00:00", str(number), "Завершена", number)
    store.upsert(game, [
        {"position": position, "rank": "A", "suit": suit}
        for position, suit in enumerate(suits, 1)
    ], "CONFIRMED", "Подтверждено", "CARDS_CONFIRMED",
        finished_confirmed=True, final_snapshot_reverified=True,
        player_hand_final=True)


def test_strategy_is_saved_and_first_signal_comes_after_eight_real_hands(tmp_path):
    path = tmp_path / "hot.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    assert len(engine.get_strategy()["options"]) == 5
    engine.save_strategy(STRATEGY_HOT_8)
    assert Cycle18Engine(path).get_strategy()["selected"] == STRATEGY_HOT_8
    engine.start_session()
    for number in range(1, 9):
        add_final(store, number, ("hearts", "hearts"))
        state = engine.refresh()
    assert state["ready"]
    assert state["warmup_required"] == 8
    assert state["warmup_count"] == 8
    assert state["upcoming"][0]["target_round_number"] == 9
    assert state["upcoming"][0]["predicted_suit"] == "hearts"
    assert state["upcoming"][0]["source_round_numbers"] == list(range(8, 0, -1))
    assert state["upcoming"][0]["suit_counts"]["hearts"] == 16
    add_final(store, 9, ("diamonds", "spades"))
    state = engine.refresh()
    assert state["statistics"]["checked_count"] == 1
    assert state["statistics"]["losses"] == 1
    assert state["statistics"]["next_step"] == 2
    assert any(item["target_round_number"] == 9 and item["result"] == "LOSE"
               for item in state["recent"])


def test_gap_skips_target_without_forging_a_forecast(tmp_path):
    path = tmp_path / "missing.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.save_strategy(STRATEGY_HOT_8)
    engine.start_session()
    for number in range(1, 10):
        if number != 5:
            add_final(store, number, ("hearts", "hearts"))
    state = engine.refresh({"game_id": "next-10", "round_number": "10"})
    assert state["ready"]
    assert not any(item["target_round_number"] == 10 for item in state["upcoming"])
    assert state["live_preview"][0]["available"] is False
    assert state["live_preview"][0]["missing_sources"][0]["round_number"] == 5
    # Once we have eight continuous hands again, forecasts resume.
    for number in range(10, 14):
        add_final(store, number, ("hearts", "hearts"))
    recovered = engine.refresh()
    assert any(item["target_round_number"] == 14 for item in recovered["upcoming"])


def test_skip_between_bets_keeps_previous_loss_step(tmp_path):
    path = tmp_path / "skip.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.save_strategy(STRATEGY_HOT_8)
    engine.start_session()
    for number in range(1, 9):
        add_final(store, number, ("hearts", "hearts"))
        engine.refresh()
    add_final(store, 9, ("diamonds", "spades"))
    state = engine.refresh()
    assert state["statistics"]["next_step"] == 2
    # This hand makes the hot suit of the last eight gradually disappear.
    # Every skipped physical round must leave the step unchanged.
    for number in range(10, 18):
        add_final(store, number, ("diamonds", "spades"))
        state = engine.refresh()
    preview = state["upcoming"][0] if state["upcoming"] else None
    # At least one intermediate forecast is deliberately absent.
    # All confirmed LOSE forecasts still count; missing signals never do.
    checked = state["statistics"]["checked_count"]
    actual = [row for row in state["recent"] if row["result"] in ("WIN", "LOSE")]
    assert checked == len(actual)
    assert state["statistics"]["next_step"] == 1 + state["statistics"]["current_lose_streak"]


def test_next_round_waits_for_current_real_cards_without_false_failure():
    """A prelisted N with three empty DOM slots cannot be a source for N+1."""
    sources = source_window(list(range(101, 109)))
    waiting = hand(109, [], 8 * 60, final=False)
    waiting["reason"] = "Карты раскрываются: прочитано 0, неподтверждённых позиций 3"
    current, following_round = preview_two_rounds(
        109, sources, STRATEGY_HOT_8, observed={109: [waiting]})
    assert current["available"] is True
    assert following_round["available"] is False
    assert following_round["missing_sources"][0]["round_number"] == 109
    assert "текущая раздача №109" in following_round["missing_sources"][0]["reason"]
    assert "окончательные карты" in following_round["missing_sources"][0]["reason"]
    assert "Карты раскрываются" not in following_round["missing_sources"][0]["reason"]
    # Completing N with a verified hand is the only way to unlock N+1.
    sources[109] = hand(109, ["hearts", "hearts"], 8 * 60)
    next_after_confirm = preview_two_rounds(109, sources, STRATEGY_HOT_8)[1]
    assert next_after_confirm["available"] is True
