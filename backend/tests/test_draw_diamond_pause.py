"""Regression coverage for the optional DRAW + diamonds 12-round pause.

These tests never place bookmaker bets. Only exact final Player cards and
independently verified Baccarat outcomes can open a pause window.
"""
from app.cycle18 import (
    Cycle18Engine, STRATEGY_VOTE_DRAW_12, STRATEGY_VOTE,
    pause_for_round, draw_diamond_pause_triggers, predict_target,
)
from app.draw_rules import banker_card_count, resolved_baccarat_scores
from app.observation import Game, ObservationStore


def hand(*cards):
    return [{"position": i, "rank": rank, "suit": suit}
            for i, (rank, suit) in enumerate(cards, start=1)]


def add_round(store, n, cards=None, outcome=""):
    cards = cards or hand(("9", "hearts"), ("3", "clubs"))
    game = Game(f"round-{n}", f"https://example.test/baccarat/{n}",
                "00:00", str(n), "Завершена", n)
    store.upsert(game, cards, "CONFIRMED", "Итог подтверждён",
                 "CARDS_CONFIRMED", finished_confirmed=True,
                 final_snapshot_reverified=True, player_hand_final=True,
                 baccarat_outcome=outcome)
    return game


def test_draw_rules_verify_both_hands_and_refuse_incomplete():
    # Naturals: 8 vs 8, no third card for either player.
    two = hand(("8", "diamonds"), ("K", "hearts"))
    assert banker_card_count(two, ["Q", "8"]) == 2
    assert resolved_baccarat_scores(two, ["Q", "8"]) == (8, 8)
    # A Banker draw was necessary: the two-card initial 5 must draw to 6.
    other = hand(("6", "diamonds"), ("K", "clubs"))
    assert banker_card_count(other, ["5", "Q", "A"]) == 3
    assert resolved_baccarat_scores(other, ["5", "Q", "A"]) == (6, 6)
    assert resolved_baccarat_scores(other, ["5", "Q"]) is None
    # Player drew a third card, but Banker legitimately stood on 6.
    third = hand(("A", "diamonds"), ("2", "clubs"), ("3", "hearts"))
    assert resolved_baccarat_scores(third, ["K", "6"]) == (6, 6)


def test_filter_pause_is_exactly_12_physical_rounds_and_rollover_safe():
    for step in range(1, 13):
        target = (1438 + step - 1) % 1440 + 1
        result = pause_for_round(target, [1438])
        assert result is not None
        assert result["pause_remaining"] == 13 - step
    assert pause_for_round(1438, [1438]) is None
    assert pause_for_round(11, [1438]) is None


def test_unverified_zero_zero_and_diamond_without_draw_do_not_trigger(tmp_path):
    store, engine = ObservationStore(tmp_path / "rounds.sqlite3"), Cycle18Engine(tmp_path / "rounds.sqlite3")
    engine.save_strategy(STRATEGY_VOTE_DRAW_12)
    engine.start_session()
    for n in range(1, 41):
        if n == 40:
            # The pre-deal list score 0:0 cannot be used as a verified draw.
            cards = hand(("8", "diamonds"), ("K", "hearts"))
            game = Game("round-40", "https://example.test/baccarat/40",
                        "00:00", "40", "Завершена", 40,
                        player_score="0", banker_score="0")
            store.upsert(game, cards, "CONFIRMED", "Снимок",
                         "CARDS_CONFIRMED", finished_confirmed=True,
                         final_snapshot_reverified=True, player_hand_final=True)
        else:
            add_round(store, n)
    state = engine.refresh({"game_id": "next-41", "round_number": "41"})
    assert state["ready"]
    assert state["pause_triggers"] == []
    assert state["live_preview"][0]["available"]
    assert state["live_preview"][0].get("filter_paused") is not True


def test_verified_diamond_draw_blocks_next_twelve_and_resumes_at_thirteen(tmp_path):
    store, engine = ObservationStore(tmp_path / "rounds.sqlite3"), Cycle18Engine(tmp_path / "rounds.sqlite3")
    engine.save_strategy(STRATEGY_VOTE_DRAW_12)
    engine.start_session()
    for n in range(1, 41):
        add_round(store, n,
                  hand(("8", "diamonds"), ("K", "hearts")) if n == 40 else None,
                  "DRAW" if n == 40 else "")
    state = engine.refresh({"game_id": "future-41", "round_number": "41"})
    assert state["ready"] and state["pause_enabled"]
    assert state["pause_triggers"] == [40]
    assert state["live_preview"][0]["target_round_number"] == 41
    assert state["live_preview"][0]["filter_paused"] is True
    assert state["live_preview"][0]["available"] is False
    assert state["live_preview"][0]["pause_remaining"] == 12

    # A genuinely confirmed target remains available for virtual audit, but
    # must never become a stake step.
    add_round(store, 41)
    settled = engine.refresh()
    assert settled["filter_skipped_count"] >= 1
    assert settled["statistics"]["checked_count"] == 0
    assert settled["statistics"]["next_step"] == 1
    for n in range(42, 53):
        add_round(store, n,
                  hand(("8", "diamonds"), ("K", "hearts")) if n == 45 else None,
                  "DRAW" if n == 45 else "")
    state = engine.refresh({"game_id": "future-53", "round_number": "53"})
    assert state["pause_triggers"] == [40]  # pause is not extended by round 45
    assert state["live_preview"][0]["target_round_number"] == 53
    assert state["live_preview"][0]["available"] is True
    assert state["live_preview"][0].get("filter_paused") is not True


def test_filter_uses_same_actual_five_votes_as_plain_voting():
    # Both modes must make identical suit selections; filter is a betting gate.
    source = {"finished_at": "2026-10-26T10:00:00+00:00",
              "player_hand_final": 1, "final_snapshot_reverified": 1,
              "calculation_status": "complete", "cards_json": ""}
    from app.suit_calculator import calculate_cards_suit
    import json
    samples = {}
    for n in (1, 10, 17, 31, 38):
        sample = dict(source)
        sample["cards_json"] = json.dumps(hand(("8", "diamonds"), ("K", "hearts")))
        sample["calculated_suit"] = "diamonds"
        samples[n] = sample
    plain = predict_target(41, samples, STRATEGY_VOTE)
    filtered = predict_target(41, samples, STRATEGY_VOTE_DRAW_12)
    assert plain is not None and filtered is not None
    assert plain["predicted_suit"] == filtered["predicted_suit"]
    assert plain["votes"] == filtered["votes"]
