"""Cycle 18 uses 20 final hands per session, never shifting physical N-18 sources."""
import sqlite3
from datetime import datetime, timedelta, timezone

from app.cycle18 import Cycle18Engine, following, round_number, is_next, order_physical_rounds, preview_two_rounds
from app.observation import Game, ObservationStore


def add_round(store, number, *, cards=None):
    game_id = f"round-{number}"
    game = Game(game_id, f"https://example.test/baccarat/{number}-player-banker",
                "00:00", str(number), "Завершена", number)
    store.upsert(
        game,
        cards or [
            {"position": 1, "rank": "9", "suit": "hearts"},
            {"position": 2, "rank": "3", "suit": "clubs"},
        ],
        "CONFIRMED", "Подтверждено", "CARDS_CONFIRMED",
        finished_confirmed=True, final_snapshot_reverified=True,
        player_hand_final=True,
    )
    return game


def test_round_wrap_and_unknown_are_not_guessed():
    assert round_number("1440") == 1440
    assert round_number("not-known") is None
    assert round_number("0") is None
    assert following(1440) == 1


def test_twenty_new_consecutive_hands_then_predict_21(tmp_path):
    path = tmp_path / "observations.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    assert engine.refresh()["warmup_count"] == 0
    engine.start_session()
    for number in range(1, 20):
        add_round(store, number)
        result = engine.refresh()
        assert result["warmup_count"] == number
        assert result["ready"] is False
        assert result["upcoming"] == []

    add_round(store, 20)
    result = engine.refresh()
    assert result["warmup_count"] == 20
    assert result["ready"] is True
    assert len(result["upcoming"]) == 1
    prediction = result["upcoming"][0]
    assert prediction["source_round_number"] == 3
    assert prediction["target_round_number"] == 21
    assert prediction["predicted_suit"] == "spades"

    result = engine.refresh({"game_id": "round-21", "round_number": "21"})
    assert result["current"]["predicted_suit"] == "spades"
    assert result["current"]["result"] == "PENDING"

    add_round(store, 21, cards=[
        {"position": 1, "rank": "7", "suit": "spades"},
        {"position": 2, "rank": "2", "suit": "clubs"},
    ])
    result = engine.refresh()
    assert result["statistics"]["wins"] == 1
    assert result["statistics"]["losses"] == 0
    assert result["statistics"]["checked_count"] == 1
    assert result["upcoming"][-1]["target_round_number"] == 22
    assert engine.refresh()["statistics"]["wins"] == 1


def test_observation_restart_clears_warmup_not_saved_observations(tmp_path):
    path = tmp_path / "observations.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(1, 21):
        add_round(store, number)
    assert engine.refresh()["ready"] is True
    engine.stop_session()
    assert engine.refresh()["ready"] is False
    engine.start_session()
    new = engine.refresh()
    assert new["warmup_count"] == 0
    assert new["statistics"]["checked_count"] == 0
    add_round(store, 21)
    assert engine.refresh()["warmup_count"] == 1


def test_missing_round_does_not_reset_warmup_and_duplicate_does_not_increment(tmp_path):
    path = tmp_path / "observations.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(1, 20):
        add_round(store, number)
    assert engine.refresh()["warmup_count"] == 19
    add_round(store, 21)
    assert engine.refresh()["warmup_count"] == 20
    assert engine.refresh()["ready"] is True
    add_round(store, 21)
    assert engine.refresh()["warmup_count"] == 20


def test_incomplete_hands_do_not_count_or_create_false_losses(tmp_path):
    path = tmp_path / "observations.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(1, 21):
        add_round(store, number)
    engine.refresh()
    game = Game("round-21", "https://example.test/baccarat/21-player-banker",
                "00:00", "21", "Идёт", 21)
    store.upsert(game, [], "PARTIAL", "Нет карт", "CARDS_NOT_FOUND")
    pending = engine.refresh()
    assert pending["current"] is None
    assert pending["statistics"]["checked_count"] == 0
    store.upsert(game, [], "PARTIAL", "Неподтверждённый финал",
                 "CARDS_UNCONFIRMED", finished_confirmed=True, final_snapshot_reverified=False)
    result = engine.refresh()
    assert result["statistics"]["losses"] == 0
    assert result["statistics"]["checked_count"] == 0


def test_missing_round_keeps_its_slot_after_warmup_and_does_not_shift_n_minus_18(tmp_path):
    path = tmp_path / "history.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(1, 21):
        add_round(store, number)
    assert engine.refresh()["ready"]

    missing = Game("round-21", "https://example.test/baccarat/21-player-banker",
                   "00:00", "21", "Не удалось определить", 21)
    store.upsert(missing, [], "PARTIAL", "Карты не прочитаны", "CARDS_NOT_FOUND")
    add_round(store, 22)
    state = engine.refresh()
    assert state["ready"], "Once collected, 20-hand warmup does not reset because of missing cards"
    assert state["warmup_count"] == 20
    assert state["missing_count"] == 1
    assert state["missing_hands"][0]["round_number"] == "21"
    assert any(p["target_round_number"] == 23 and
               p["source_round_number"] == 5 for p in state["upcoming"])
    assert all(p["source_round_number"] != 6 for p in state["upcoming"]
               if p["target_round_number"] == 23)
    # No retroactive WIN/LOSE: #22 is already complete by the time the
    # first forecast for it is published.
    assert state["statistics"]["checked_count"] == 0
    assert state["statistics"]["losses"] + state["statistics"]["wins"] == 0


def test_unknown_forecast_resolves_to_win_after_real_cards_recovered(tmp_path):
    path = tmp_path / "history.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(1, 21):
        add_round(store, number)
    state = engine.refresh()
    assert state["upcoming"][-1]["target_round_number"] == 21

    game = Game("round-21", "https://example.test/baccarat/21-player-banker",
                "00:00", "21", "Завершена", 21)
    store.upsert(game, [], "PARTIAL", "Карты не прочитаны",
                 "CARDS_UNCONFIRMED", finished_confirmed=True,
                 final_snapshot_reverified=False)
    unknown = engine.refresh()
    assert unknown["statistics"]["losses"] == 0
    assert unknown["statistics"]["checked_count"] == 0

    add_round(store, 21, cards=[
        {"position": 1, "rank": "7", "suit": "spades"},
        {"position": 2, "rank": "2", "suit": "clubs"},
    ])
    recovered = engine.refresh()
    assert recovered["statistics"]["wins"] == 1
    assert recovered["statistics"]["losses"] == 0
    assert recovered["missing_count"] == 0


def test_list_registers_unknown_and_preserves_earlier_captured_cards(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = ObservationStore(path)
    first = Game("table-21", "https://example.test/baccarat/21-player-banker",
                 "", "21", "", 0)
    second = Game("table-22", "https://example.test/baccarat/22-player-banker",
                  "", "22", "", 1)
    store.register_discovered_games([first, second])
    assert store.get("table-21")["cards"] == []
    assert store.get("table-22")["round_number"] == "22"
    add_round(store, 1)
    assert store.get("round-1")["calculation_status"] == "complete"
    store.register_discovered_games([first, second])
    assert store.get("round-1")["calculation_status"] == "complete"


def test_warmup_shows_captured_two_cards_separately_from_final_hands(tmp_path):
    """Reproduces 0/20 despite two real saved hands and four list shells."""
    path = tmp_path / "rounds.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    games = [
        Game(f"round-{n}", f"https://example.test/baccarat/{n}-player-banker",
             "", str(n), "", n)
        for n in range(1000, 1006)
    ]
    store.register_discovered_games(games)
    initial = engine.refresh()
    assert initial["discovered_count"] == 6
    assert initial["warmup_count"] == 0

    for game in games[:2]:
        store.upsert(game, [
            {"position": 1, "rank": "10", "suit": "diamonds"},
            {"position": 2, "rank": "9", "suit": "spades"},
        ], "PARTIAL", "Пока нет финального подтверждения", "CARDS_FOUND")

    waiting = engine.refresh()
    assert waiting["captured_count"] == 2
    assert waiting["warmup_count"] == 0
    assert waiting["confirmed_warmup_count"] == 0
    assert waiting["ready"] is False
    assert waiting["missing_count"] == 6
    assert waiting["missing_hands"][0]["cards_count"] == 2

    for game in games[:2]:
        store.upsert(game, [
            {"position": 1, "rank": "10", "suit": "diamonds"},
            {"position": 2, "rank": "9", "suit": "spades"},
        ], "CONFIRMED", "Финальный набор проверен", "CARDS_CONFIRMED",
            player_hand_final=True, final_snapshot_reverified=True)
    confirmed = engine.refresh()
    assert confirmed["warmup_count"] == 2
    assert confirmed["confirmed_warmup_count"] == 2
    assert confirmed["verified_count"] == 2
    assert confirmed["ready"] is False


def test_warmup_counts_other_real_completed_rounds_even_when_one_is_unreadable(tmp_path):
    path = tmp_path / "rounds.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(1, 20):
        add_round(store, number)
    store.register_discovered_games([
        Game("round-20", "https://example.test/baccarat/20-player-banker",
             "", "20", "Не удалось определить", 20)
    ])
    add_round(store, 21)
    result = engine.refresh()
    assert result["ready"] is True
    assert result["warmup_count"] == 20
    assert result["confirmed_warmup_count"] == 20
    # The missing physical round retains its own slot; it is never
    # substituted for a genuine source game N-18.
    assert result["missing_hands"][0]["round_number"] == "20"
    assert result["statistics"]["checked_count"] == 0




def test_first_twenty_are_observation_only_then_only_cycle18_wins_and_losses(tmp_path):
    path = tmp_path / "strict-warmup.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(100, 120):
        add_round(store, number)
        result = engine.refresh()
        assert result["warmup_count"] == number - 99
        assert result["statistics"]["checked_count"] == 0
        assert result["statistics"]["wins"] == 0
        assert result["statistics"]["losses"] == 0
        assert result["recent"] == []
        assert "warmup_results" not in result
        assert "warmup_statistics" not in result
        if number < 119:
            assert result["ready"] is False
            assert result["upcoming"] == []
    assert result["ready"] is True
    upcoming = result["upcoming"]
    assert len(upcoming) == 1
    assert upcoming[0]["source_round_number"] == 102
    assert upcoming[0]["target_round_number"] == 120
    assert upcoming[0]["predicted_suit"] == "spades"

    # First post-warmup round was forecast before its final cards appeared.
    add_round(store, 120, cards=[
        {"position": 1, "rank": "K", "suit": "spades"},
        {"position": 2, "rank": "5", "suit": "hearts"},
    ])
    outcome = engine.refresh()
    assert outcome["statistics"]["checked_count"] == 1
    assert outcome["statistics"]["wins"] == 1
    assert [(p["target_round_number"], p["result"]) for p in outcome["recent"]] == [(120, "WIN")]


def test_pending_future_placeholders_do_not_advance_or_reset_preparation(tmp_path):
    path = tmp_path / "prefetched.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    rows = [
        Game(f"future-{n}", f"https://example.test/{n}", "00:30", str(n), "", n)
        for n in range(1, 25)
    ]
    store.register_discovered_games(rows)
    assert engine.refresh()["warmup_count"] == 0
    for n in range(1, 19):
        store.upsert(rows[n - 1], [
            {"position": 1, "rank": "10", "suit": "hearts"},
            {"position": 2, "rank": "2", "suit": "spades"},
        ], "CONFIRMED", "final", "CARDS_CONFIRMED",
           player_hand_final=True, final_snapshot_reverified=True)
    state = engine.refresh()
    assert state["warmup_count"] == 18
    assert state["statistics"]["checked_count"] == 0
    assert state["ready"] is False
    for n in range(19, 21):
        game = rows[n - 1]
        store.upsert(game, [
            {"position": 1, "rank": "10", "suit": "hearts"},
            {"position": 2, "rank": "2", "suit": "spades"},
        ], "CONFIRMED", "final", "CARDS_CONFIRMED",
           player_hand_final=True, final_snapshot_reverified=True)
    complete = engine.refresh()
    assert complete["ready"] is True
    assert complete["warmup_count"] == 20
    assert complete["statistics"]["checked_count"] == 0
    assert 21 in [forecast["target_round_number"] for forecast in complete["recent"]]
    assert all(p["result"] == "PENDING" for p in complete["recent"])


def test_duplicate_rounds_cannot_be_counted_to_complete_warmup(tmp_path):
    path = tmp_path / "conflicting.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for n in range(1, 21):
        add_round(store, n)
    duplicated = Game("second-11", "https://example.test/second-11", "",
                      "11", "Завершена", 11)
    store.upsert(duplicated, [
        {"position": 1, "rank": "2", "suit": "clubs"},
        {"position": 2, "rank": "3", "suit": "clubs"},
    ], "CONFIRMED", "duplicate", "CARDS_CONFIRMED",
       player_hand_final=True, final_snapshot_reverified=True)
    result = engine.refresh()
    assert result["ready"] is False
    assert result["statistics"]["checked_count"] == 0
    assert result["upcoming"] == []


def test_missing_after_nineteen_keeps_nineteen_completed_hands(tmp_path):
    path = tmp_path / "missing-20.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for n in range(1, 20):
        add_round(store, n)
    missing = Game("round-20", "https://example.test/20", "", "20", "", 20)
    store.register_discovered_games([missing])
    add_round(store, 21)
    state = engine.refresh()
    assert state["warmup_count"] == 20
    assert state["ready"] is True
    assert state["recent"] == []
    store.upsert(missing, [
        {"position": 1, "rank": "9", "suit": "hearts"},
        {"position": 2, "rank": "3", "suit": "clubs"},
    ], "CONFIRMED", "finalized", "CARDS_CONFIRMED",
       player_hand_final=True, final_snapshot_reverified=True)
    recovered = engine.refresh()
    assert recovered["ready"] is True
    assert recovered["warmup_count"] == 20
    assert recovered["statistics"]["checked_count"] == 0


def test_cycle18_does_not_announce_results_for_round_completed_before_prediction(tmp_path):
    path = tmp_path / "never-backdate.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for n in range(1, 20):
        add_round(store, n)
    hand20 = Game("round-20", "https://example.test/baccarat/20",
                  "", "20", "Идёт", 20)
    store.register_discovered_games([hand20])
    add_round(store, 21, cards=[
        {"position": 1, "rank": "7", "suit": "spades"},
        {"position": 2, "rank": "4", "suit": "clubs"},
    ])
    assert engine.refresh()["statistics"]["checked_count"] == 0
    store.upsert(hand20, [
        {"position": 1, "rank": "9", "suit": "hearts"},
        {"position": 2, "rank": "3", "suit": "clubs"},
    ], "CONFIRMED", "final", "CARDS_CONFIRMED",
       player_hand_final=True, final_snapshot_reverified=True)
    late = engine.refresh()
    assert late["ready"] is True
    assert late["statistics"]["checked_count"] == 0
    assert late["statistics"]["wins"] == 0
    assert late["statistics"]["losses"] == 0
    assert any(p["target_round_number"] == 21 and
               p["result"] == "SKIPPED" and p["mode"] == "MISSED"
               for p in late["recent"])
    assert engine.refresh()["statistics"]["checked_count"] == 0


def test_1122_to_1128_counts_seven_even_if_future_games_were_discovered_first(tmp_path):
    """Regression for the live screenshot: 7 completed rows but preparation 3/20."""
    path = tmp_path / "out-of-order-discovery.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    games = {
        number: Game(f"live-{number}", f"https://example.test/{number}",
                     "00:21", str(number), "", number - 1122)
        for number in range(1122, 1132)
    }
    # The bookmaker pre-registers multiple future rounds, in arbitrary DOM order.
    store.register_discovered_games([games[n] for n in (
        1125, 1128, 1130, 1122, 1127, 1123, 1129, 1124, 1131, 1126,
    )])
    now = datetime.now(timezone.utc)
    # Deliberately shuffle discovered_at timestamps independently of round order.
    with sqlite3.connect(path) as db:
        for i, n in enumerate((1125, 1128, 1130, 1122, 1127,
                               1123, 1129, 1124, 1131, 1126)):
            db.execute(
                "UPDATE observations SET discovered_at=? WHERE game_id=?",
                ((now + timedelta(seconds=i + 1)).isoformat(), games[n].game_id),
            )
    for n in range(1122, 1129):
        store.upsert(games[n], [
            {"position": 1, "rank": "10", "suit": "diamonds"},
            {"position": 2, "rank": "2", "suit": "spades"},
        ], "CONFIRMED", "Финальные карты", "CARDS_CONFIRMED",
            player_hand_final=True, final_snapshot_reverified=True)
    result = engine.refresh()
    assert result["captured_count"] == 7
    assert result["verified_count"] == 7
    assert result["warmup_count"] == 7
    assert result["confirmed_warmup_count"] == 7
    assert result["ready"] is False
    assert result["upcoming"] == []
    assert result["recent"] == []
    assert result["statistics"]["checked_count"] == 0


def test_cycle18_sorting_crosses_midnight_round_1440_to_1():
    numbers = [2, 1439, 1, 1440, 3]
    rows = [{
        "game_id": f"game-{n}", "round_number": str(n),
        "discovered_at": f"2026-10-21T21:00:{i:02d}+00:00",
    } for i, n in enumerate(numbers)]
    assert [int(row["round_number"]) for row in order_physical_rounds(rows)] == [
        1439, 1440, 1, 2, 3,
    ]


def test_first_twenty_with_future_prefetch_does_not_fabricate_extra_forecasts(tmp_path):
    path = tmp_path / "no-phantom-forecasts.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    games = [Game(f"live-{n}", f"https://example.test/{n}", "00:30", str(n), "", n)
             for n in range(1122, 1146)]
    store.register_discovered_games(games[::-1])
    for game in games[:20]:
        store.upsert(game, [
            {"position": 1, "rank": "K", "suit": "clubs"},
            {"position": 2, "rank": "4", "suit": "diamonds"},
        ], "CONFIRMED", "Карты подтверждены", "CARDS_CONFIRMED",
            player_hand_final=True, final_snapshot_reverified=True)
    result = engine.refresh()
    assert result["warmup_count"] == 20
    assert result["ready"] is True
    future = result["recent"]
    assert len(future) == 1
    assert future[0]["target_round_number"] == 1142
    assert future[0]["source_round_number"] == 1124
    assert future[0]["result"] == "PENDING"
    assert result["statistics"]["checked_count"] == 0


def test_prelisted_next_round_is_not_mistaken_for_an_already_finalized_hand():
    """Observed_at exists even for the empty future list row, before the prior hand ends."""
    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc)
    previous = {
        "round_number": "1128",
        "discovered_at": (end - timedelta(minutes=2)).isoformat(),
        "observed_at": end.isoformat(),
        "finished_at": end.isoformat(),
        "player_hand_final": 1,
        "final_snapshot_reverified": 1,
    }
    future = {
        "round_number": "1129",
        "discovered_at": (end - timedelta(minutes=1)).isoformat(),
        "observed_at": (end - timedelta(minutes=1)).isoformat(),
        "finished_at": None,
        "player_hand_final": 0,
        "final_snapshot_reverified": None,
    }
    assert is_next(previous, future)


def test_screenshot_six_final_hands_are_six_of_twenty_despite_number_gaps(tmp_path):
    """Actual screenshot: saved 359 360 361 365 366 368, not one of twenty."""
    path = tmp_path / "snapshot-359-368.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for n in (359, 360, 361, 365, 366, 368):
        add_round(store, n)
    for n in (362, 363, 364, 367):
        store.register_discovered_games([Game(
            f"missing-{n}", f"https://example.test/baccarat/{n}",
            "", str(n), "", n
        )])
    for n in (369, 370, 371):
        store.register_discovered_games([Game(
            f"future-{n}", f"https://example.test/baccarat/{n}",
            "00:30", str(n), "", n
        )])
    state = engine.refresh()
    assert state["verified_count"] == 6
    assert state["warmup_count"] == 6
    assert state["confirmed_warmup_count"] == 6
    assert state["captured_count"] == 6
    assert state["ready"] is False
    assert state["statistics"]["checked_count"] == 0
    assert state["recent"] == []
    # Continue collecting later hands without resetting on the gaps.
    for n in range(369, 383):
        # Reuse the discovered future row IDs instead of creating duplicates.
        if n in (369, 370, 371):
            game = Game(f"future-{n}", f"https://example.test/baccarat/{n}",
                        "", str(n), "Завершена", n)
            store.upsert(game, [
                {"position": 1, "rank": "9", "suit": "hearts"},
                {"position": 2, "rank": "3", "suit": "clubs"},
            ], "CONFIRMED", "final", "CARDS_CONFIRMED",
                finished_confirmed=True, final_snapshot_reverified=True,
                player_hand_final=True)
        else:
            add_round(store, n)
    ready = engine.refresh()
    assert ready["warmup_count"] == 20
    assert ready["ready"] is True
    assert len(ready["upcoming"]) == 1
    assert ready["upcoming"][0]["source_round_number"] == 365
    assert ready["upcoming"][0]["target_round_number"] == 383
    assert ready["statistics"]["checked_count"] == 0


def test_missing_actual_n_minus_18_source_is_never_replaced_by_saved_neighbor(tmp_path):
    path = tmp_path / "source-absent.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    # 20 real hands ending at 382, but no physically numbered #365.
    for n in (359, 360, 361, *range(366, 383)):
        add_round(store, n)
    result = engine.refresh()
    assert result["ready"] is True
    assert result["warmup_count"] == 20
    assert result["upcoming"] == []  # #383 needs #365, which is missing.
    add_round(store, 383)
    next_result = engine.refresh()
    assert any(p["target_round_number"] == 384 and
               p["source_round_number"] == 366 for p in next_result["upcoming"])
    assert all(p["target_round_number"] != 383 for p in next_result["recent"])
    assert next_result["statistics"]["checked_count"] == 0


def test_cycle18_previews_two_real_rounds_and_never_substitutes_missing_source(tmp_path):
    path = tmp_path / "preview.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(580, 600):
        add_round(store, number)
    # N=600 => source=582, N+1=601 => source=583
    result = engine.refresh({"game_id": "future-600", "round_number": "600"})
    assert result["ready"]
    assert [p["target_round_number"] for p in result["live_preview"]] == [600, 601]
    assert [p["source_round_number"] for p in result["live_preview"]] == [582, 583]
    assert [p["predicted_suit"] for p in result["live_preview"]] == ["spades", "spades"]
    assert all(p["available"] for p in result["live_preview"])
    assert result["statistics"]["checked_count"] == 0
    assert engine.refresh()["live_preview"] == []

    # If #583 was never conclusively collected, source for #601 must be
    # unavailable; using #584 would shift the cycle and is forbidden.
    path2 = tmp_path / "preview-missing.sqlite3"
    second_store, second_engine = ObservationStore(path2), Cycle18Engine(path2)
    second_engine.start_session()
    for number in (*range(580, 583), *range(584, 601)):
        add_round(second_store, number)
    missed = second_engine.refresh({"game_id": "future-600", "round_number": "600"})
    assert missed["ready"]
    assert missed["live_preview"][0]["available"] is True
    assert missed["live_preview"][1]["source_round_number"] == 583
    assert missed["live_preview"][1]["predicted_suit"] is None
    assert missed["live_preview"][1]["available"] is False


def test_preview_handles_1440_to_1_without_modifying_saved_forecasts():
    def verified(rank):
        return {
            "finished_at": "2026-10-22T10:00:00+00:00",
            "player_hand_final": 1,
            "final_snapshot_reverified": 1,
            "calculation_status": "complete",
            "cards_json": '[{"position":1,"rank":"' + rank + '","suit":"hearts"},'
                          '{"position":2,"rank":"3","suit":"clubs"}]',
        }
    previews = preview_two_rounds(1440, {1422: verified("9"), 1423: verified("8")})
    assert [(p["target_round_number"], p["source_round_number"])
            for p in previews] == [(1440, 1422), (1, 1423)]
    assert all(p["predicted_suit"] == "spades" for p in previews)


def test_observation_history_returns_two_hundred_completed_rounds(tmp_path):
    store = ObservationStore(tmp_path / "history-200.sqlite3")
    for number in range(1, 211):
        add_round(store, number)
    rows = store.history(limit=200)
    assert len(rows) == 200
    assert len({row["game_id"] for row in rows}) == 200
    assert all(len(row["player_cards"]) == 2 for row in rows)


def test_ten_losses_keep_step_eleven_when_future_rounds_were_discovered_out_of_order(tmp_path):
    """Prelisted game timestamps must not split an otherwise continuous N-18 run."""
    path = tmp_path / "reordered-prelisted-steps.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(1, 21):
        add_round(store, number)
        engine.refresh()

    # Every source round #3..#13 computes spades from hearts + clubs.
    # Targets #21..#30 have no spades, #31 contains spades: 10 LOSE, then WIN.
    for number in range(21, 32):
        target_cards = [
            {"position": 1, "rank": "6", "suit": "hearts"},
            {"position": 2, "rank": "4", "suit": "clubs"},
        ] if number < 31 else [
            {"position": 1, "rank": "9", "suit": "spades"},
            {"position": 2, "rank": "3", "suit": "clubs"},
        ]
        add_round(store, number, cards=target_cards)
        engine.refresh()

    # The site may register a future target earlier than an older target.
    # Reverse their discovery timestamps while leaving finalization times
    # and numbered hand identities untouched.
    future = datetime.now(timezone.utc) + timedelta(minutes=2)
    with sqlite3.connect(path) as db:
        for number in range(21, 32):
            db.execute("UPDATE observations SET discovered_at=? WHERE game_id=?",
                       ((future - timedelta(seconds=3 * (number - 21))).isoformat(),
                        f"round-{number}"))

    state = engine.refresh()
    recent = {row["target_round_number"]: row for row in state["recent"]}
    assert [recent[number]["result"] for number in range(21, 31)] == ["LOSE"] * 10
    assert [recent[number]["step"] for number in range(21, 32)] == list(range(1, 12))
    assert recent[31]["result"] == "WIN"
    assert state["statistics"]["max_lose_streak"] == 10
    assert state["statistics"]["wins_by_step"]["11"] == 1
    assert state["statistics"]["next_step"] == 1


def test_october_22_rounds_817_to_827_match_exact_n_minus_18_rule():
    """Audit actual Player suit sequences: no hidden 6/7-step win assumption."""
    from app.suit_calculator import calculate_suit
    from app.observation import evaluate_prediction

    source_hands = {
        799: ("♥", "♦"), 800: ("♣", "♥"), 801: ("♥", "♦"),
        802: ("♣", "♣", "♣"), 803: ("♣", "♦", "♦"),
        804: ("♥", "♥"), 805: ("♣", "♠", "♠"),
        806: ("♦", "♥", "♥"), 807: ("♠", "♠"),
        808: ("♥", "♥"), 809: ("♦", "♣", "♠"),
    }
    actual_hands = {
        817: ("♣", "♣", "♠"), 818: ("♦", "♥"),
        819: ("♥", "♥"), 820: ("♥", "♠"),
        821: ("♣", "♦"), 822: ("♠", "♠"),
        823: ("♠", "♦"), 824: ("♥", "♥", "♥"),
        825: ("♠", "♠"), 826: ("♥", "♥", "♠"),
        827: ("♣", "♣", "♣"),
    }
    checked = []
    for target in range(817, 828):
        source = target - 18
        expected = calculate_suit(source_hands[source])
        cards = [
            {"position": i, "rank": "A", "suit": suit}
            for i, suit in enumerate(actual_hands[target], start=1)
        ]
        checked.append(evaluate_prediction(
            expected, cards, finished_confirmed=True, final_snapshot_reverified=True,
        ))
    assert checked == ["LOSE"] * 10 + ["WIN"]



def test_voting_strategy_is_persisted_and_uses_five_exact_sources(tmp_path):
    path = tmp_path / "voting.sqlite3"
    store = ObservationStore(path)
    engine = Cycle18Engine(path)
    from app.cycle18 import STRATEGY_VOTE
    assert engine.get_strategy()["selected"] == "n18"
    engine.save_strategy(STRATEGY_VOTE)
    assert Cycle18Engine(path).get_strategy()["selected"] == STRATEGY_VOTE
    engine.start_session()

    # Each of the five original-table votes uses different source hands.
    # n41: n38 and n31 vote clubs; n17 and n10 vote hearts;
    # n1 votes diamonds -> tie 2:2 resolved in favour of hearts.
    for number in range(1, 41):
        if number in (38, 31):
            suits = ("clubs", "spades")  # clubs
        elif number in (17, 10):
            suits = ("spades", "spades")  # hearts
        elif number == 1:
            suits = ("hearts", "hearts")  # diamonds
        else:
            suits = ("hearts", "clubs")
        add_round(store, number, cards=[
            {"position": 1, "rank": "9", "suit": suits[0]},
            {"position": 2, "rank": "3", "suit": suits[1]},
        ])
    unlinked = engine.refresh()
    assert len(unlinked["upcoming"]) == 1
    assert unlinked["upcoming"][0]["target_round_number"] == 41
    assert unlinked["upcoming"][0]["source_round_numbers"] == [38, 31, 17, 10, 1]
    result = engine.refresh({"game_id": "round-41", "round_number": "41"})
    assert result["strategy"] == STRATEGY_VOTE
    assert result["warmup_required"] == 40
    assert result["warmup_count"] == 40
    assert result["ready"]
    current = result["live_preview"][0]
    assert current["source_round_numbers"] == [38, 31, 17, 10, 1]
    assert current["predicted_suit"] == "hearts"
    assert [v["suit"] for v in current["votes"]] == [
        "clubs", "clubs", "hearts", "hearts", "diamonds",
    ]
    assert result["current"]["target_round_number"] == 41
    assert result["current"]["source_round_numbers"] == [38, 31, 17, 10, 1]
    add_round(store, 41, cards=[
        {"position": 1, "rank": "A", "suit": "hearts"},
        {"position": 2, "rank": "2", "suit": "diamonds"},
    ])
    assert engine.refresh()["statistics"]["wins"] == 1


def test_voting_missing_one_exact_source_never_uses_neighbour(tmp_path):
    from app.cycle18 import STRATEGY_VOTE
    path = tmp_path / "missing-voter.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.save_strategy(STRATEGY_VOTE)
    engine.start_session()
    for number in range(1, 43):
        if number != 3:
            add_round(store, number)
    result = engine.refresh({"game_id": "future-43", "round_number": "43"})
    assert result["ready"]
    # 43-40 = 3 (missing), whereas #4 exists. This must NOT be shifted.
    assert result["live_preview"][0]["source_round_numbers"] == [40, 33, 19, 12, 3]
    assert result["live_preview"][0]["available"] is False
    assert result["live_preview"][0]["missing_sources"] == [
        {"offset": 40, "round_number": 3, "reason": "отсутствует в истории"}]
    assert all(row["target_round_number"] != 43 for row in result["upcoming"])


def test_preview_explains_unconfirmed_exact_voter_without_shifting_source():
    from app.cycle18 import STRATEGY_VOTE
    incomplete = {
        "finished_at": None, "player_hand_final": 0,
        "final_snapshot_reverified": None, "calculation_status": "incomplete",
        "cards_json": "[]", "reason": "Третья карта ещё закрыта",
    }
    preview = preview_two_rounds(
        945, {}, STRATEGY_VOTE, {921: [incomplete]})[0]
    assert preview["available"] is False
    assert preview["source_round_numbers"] == [942, 935, 921, 914, 905]
    assert preview["missing_sources"][2] == {
        "offset": 24, "round_number": 921,
        "reason": "финальная рука Игрока не подтверждена (Третья карта ещё закрыта)",
    }


def test_strategy_can_only_change_between_sessions(tmp_path):
    from app.cycle18 import STRATEGY_VOTE
    import pytest
    path = tmp_path / "choose.sqlite3"
    ObservationStore(path)
    engine = Cycle18Engine(path)
    with pytest.raises(ValueError):
        engine.save_strategy("unknown")
    engine.start_session()
    with pytest.raises(RuntimeError, match="Остановите"):
        engine.save_strategy(STRATEGY_VOTE)
    assert engine.refresh()["strategy"] == "n18"
    engine.stop_session()
    assert engine.save_strategy(STRATEGY_VOTE)["selected"] == STRATEGY_VOTE
    engine.start_session()
    assert engine.refresh()["strategy"] == STRATEGY_VOTE


def test_voting_missing_target_does_not_reset_loss_step(tmp_path):
    from app.cycle18 import STRATEGY_VOTE
    path = tmp_path / "skip-vote.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.save_strategy(STRATEGY_VOTE)
    engine.start_session()
    for number in range(1, 42):
        if number != 3:
            add_round(store, number)
    engine.refresh()  # #42 is predicted using all five valid source rounds.
    add_round(store, 42, cards=[
        {"position": 1, "rank": "9", "suit": "hearts"},
        {"position": 2, "rank": "2", "suit": "diamonds"},
    ])  # all five votes say spades -> LOSE
    engine.refresh()
    add_round(store, 43)  # #43 had no N−40 source (#3), so no signal.
    engine.refresh()  # #44's N−40 source is #4 and all votes are present.
    add_round(store, 44, cards=[
        {"position": 1, "rank": "9", "suit": "spades"},
        {"position": 2, "rank": "2", "suit": "hearts"},
    ])
    result = engine.refresh()
    assert result["statistics"]["checked_count"] == 2
    assert result["statistics"]["losses"] == 1
    assert result["statistics"]["wins"] == 1
    assert result["statistics"]["wins_by_step"] == {"2": 1}
    assert all(item["target_round_number"] != 43 for item in result["recent"])
