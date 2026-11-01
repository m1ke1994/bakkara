"""Second Player card is the only signal source for this experimental strategy."""
import json
from decimal import Decimal

from app.cycle18 import (
    AVAILABLE_STRATEGIES, STRATEGY_SECOND_CARD, Cycle18Engine,
    predict_target, preview_two_rounds, second_card_analysis,
    source_verified, strategy_offsets, strategy_warmup,
)
from app.live_bets import LiveBets
from app.observation import Game, ObservationStore


def verified(number, suits, *, final=True):
    return {
        "game_id": f"game-{number}", "round_number": str(number),
        "finished_at": "2026-11-01T09:00:00+00:00" if final else None,
        "player_hand_final": int(final),
        "final_snapshot_reverified": 1 if final else None,
        "calculation_status": "not_calculated",
        "cards_json": json.dumps([
            {"position": i, "rank": "K" if i == 2 else "6", "suit": suit}
            for i, suit in enumerate(suits, 1)
        ]),
    }


def add_final(store, number, suits):
    game = Game(f"round-{number}", f"https://example.test/baccarat/{number}",
                "00:00", str(number), "Завершена", number)
    store.upsert(game, [
        {"position": i, "rank": "K" if i == 2 else "6", "suit": suit}
        for i, suit in enumerate(suits, 1)
    ], "CONFIRMED", "Подтверждено", "CARDS_CONFIRMED",
        finished_confirmed=True, final_snapshot_reverified=True,
        player_hand_final=True)


def test_registered_saved_and_exact_one_round_source(tmp_path):
    assert STRATEGY_SECOND_CARD in AVAILABLE_STRATEGIES
    assert strategy_warmup(STRATEGY_SECOND_CARD) == 40
    assert strategy_offsets(STRATEGY_SECOND_CARD) == (1,)
    engine = Cycle18Engine(tmp_path / "records.sqlite3")
    saved = engine.save_strategy(STRATEGY_SECOND_CARD)
    assert saved["selected"] == STRATEGY_SECOND_CARD
    assert any(x["id"] == STRATEGY_SECOND_CARD for x in saved["options"])
    assert Cycle18Engine(tmp_path / "records.sqlite3").get_strategy()["selected"] == STRATEGY_SECOND_CARD


def test_two_card_last_and_three_card_middle_are_used_without_suit_table():
    h = {40: verified(40, ["spades", "clubs"])}
    p = predict_target(41, h, STRATEGY_SECOND_CARD)
    assert p["predicted_suit"] == "clubs"
    assert p["source_round_numbers"] == [40]
    assert p["votes"][0]["card"] == "K♣"
    assert p["votes"][0]["card_position"] == 2
    h[40] = verified(40, ["spades", "diamonds", "hearts"])
    p = predict_target(41, h, STRATEGY_SECOND_CARD)
    assert p["predicted_suit"] == "diamonds"
    assert p["votes"][0]["card"] == "K♦"
    assert p["partial_vote"] is False
    assert source_verified(h[40], STRATEGY_SECOND_CARD)


def test_never_guess_from_unfinished_wrong_number_or_wrong_positions():
    h = {40: verified(40, ["spades", "clubs"])}
    assert predict_target(42, h, STRATEGY_SECOND_CARD) is None
    assert second_card_analysis(41, {40: {**h[40], "round_number": "39"}}) is None
    for changed in (
        {"finished_at": None, "player_hand_final": 0},
        {"final_snapshot_reverified": 0},
        {"cards_json": json.dumps([{"position": 1, "rank": "K", "suit": "clubs"}])},
        {"cards_json": json.dumps([{"position": 1, "rank": "K", "suit": "clubs"},
                                    {"position": 3, "rank": "2", "suit": "spades"}])},
    ):
        assert predict_target(41, {40: {**h[40], **changed}}, STRATEGY_SECOND_CARD) is None
    current, nxt = preview_two_rounds(41, h, STRATEGY_SECOND_CARD)
    assert current["predicted_suit"] == "clubs"
    assert not nxt["available"]
    assert nxt["missing_sources"][0]["round_number"] == 41


def test_wraparound_1440_to_one_and_no_banker_dependency():
    h = {1440: verified(1440, ["spades", "hearts", "clubs"])}
    h[1440]["banker_cards_json"] = json.dumps([{"suit": "diamonds"}])
    assert predict_target(1, h, STRATEGY_SECOND_CARD)["predicted_suit"] == "hearts"


def test_demo_confirmed_win_lose_and_live_ready_preview(tmp_path):
    path = tmp_path / "records.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.save_strategy(STRATEGY_SECOND_CARD)
    engine.start_session()
    for number in range(1, 40):
        add_final(store, number, ("hearts", "clubs"))
        state = engine.refresh()
        assert not state["ready"]
        assert state["upcoming"] == []
    add_final(store, 40, ("spades", "clubs"))
    state = engine.refresh()
    assert state["ready"]
    assert state["warmup_count"] == 40
    assert state["upcoming"][0]["target_round_number"] == 41
    assert state["upcoming"][0]["source_round_numbers"] == [40]
    assert state["upcoming"][0]["predicted_suit"] == "clubs"
    assert engine.refresh({"game_id": "round-41", "round_number": "41"})["live_preview"][0]["predicted_suit"] == "clubs"
    # After final cards, only actual confirmed cards settle this forecast.
    add_final(store, 41, ("hearts", "diamonds"))
    state = engine.refresh()
    assert state["statistics"]["checked_count"] == 1
    assert state["statistics"]["losses"] == 1
    assert state["statistics"]["next_step"] == 2
    assert state["upcoming"][0]["target_round_number"] == 42
    assert state["upcoming"][0]["predicted_suit"] == "diamonds"
    add_final(store, 42, ("diamonds", "spades", "hearts"))
    state = engine.refresh()
    assert state["statistics"]["wins"] == 1
    assert state["statistics"]["losses"] == 1
    assert state["statistics"]["next_step"] == 1


def test_live_reuses_existing_acceptance_and_settlement_gate(tmp_path):
    path = tmp_path / "live.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    ledger = LiveBets(path)
    engine.save_strategy(STRATEGY_SECOND_CARD)
    engine.start_session()
    for number in range(1, 41):
        add_final(store, number, ("hearts", "clubs"))
        engine.refresh()
    ledger.mode("LIVE")
    state = engine.refresh({"game_id": "round-41", "round_number": "41"})
    forecast = state["live_preview"][0]
    assert state["strategy"] == STRATEGY_SECOND_CARD
    assert state["ready"] and forecast["available"]
    assert forecast["predicted_suit"] == "clubs"
    # This verifies the persistent gate, not the external bookmaker coupon.
    assert ledger.reserve("round-41", "41", "clubs", Decimal("10"))
    assert not ledger.reserve("round-41", "41", "clubs", Decimal("10"))
    assert not ledger.reserve("round-42", "42", "clubs", Decimal("10"))
    ledger.update("round-41", "ACCEPTED")
    assert ledger.settle("round-41", "LOSE", 9)
    assert ledger.snapshot()["step"] == 2
    assert ledger.reserve("round-42", "42", "clubs", Decimal("20"))
