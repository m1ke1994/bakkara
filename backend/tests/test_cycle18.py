"""Cycle 18 never borrows old games and waits for 20 new consecutive hands."""
from app.cycle18 import Cycle18Engine, following, round_number, is_next
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


def test_gap_restarts_warmup_and_duplicate_does_not_increment(tmp_path):
    path = tmp_path / "observations.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for number in range(1, 20):
        add_round(store, number)
    assert engine.refresh()["warmup_count"] == 19
    add_round(store, 21)
    assert engine.refresh()["warmup_count"] == 1
    add_round(store, 21)
    assert engine.refresh()["warmup_count"] == 1


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
    # Round 22 may itself WIN/LOSE against real source #4; missing #21
    # must not be recorded as a loss.
    assert state["statistics"]["checked_count"] == 1
    assert state["statistics"]["losses"] + state["statistics"]["wins"] == 1


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


def test_warmup_count_does_not_reach_twenty_by_skipping_unreadable_round(tmp_path):
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
    assert result["ready"] is False
    assert result["warmup_count"] == 1
    assert result["confirmed_warmup_count"] == 19
    # The missing real round is retained and no forecast is fabricated.
    assert result["missing_hands"][0]["round_number"] == "20"
    assert result["upcoming"] == []




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
    assert upcoming[0]["source_round_number"] == 103
    assert upcoming[0]["target_round_number"] == 121
    assert upcoming[0]["predicted_suit"] == "spades"

    # First post-warmup round was forecast before its final cards appeared.
    add_round(store, 121, cards=[
        {"position": 1, "rank": "K", "suit": "spades"},
        {"position": 2, "rank": "5", "suit": "hearts"},
    ])
    outcome = engine.refresh()
    assert outcome["statistics"]["checked_count"] == 1
    assert outcome["statistics"]["wins"] == 1
    assert [(p["target_round_number"], p["result"]) for p in outcome["recent"]] == [(121, "WIN")]


def test_pending_future_placeholders_do_not_advance_or_reset_preparation(tmp_path):
    path = tmp_path / "prefetched.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    rows = [
        Game(f"future-{n}", f"https://example.test/{n}", "00:30", str(n), "", n)
        for n in range(1, 25)
    ]
    store.register_discovered_games(rows)
    for n in range(1, 19):
        add_round(store, n)
    state = engine.refresh()
    assert state["warmup_count"] == 18
    assert state["statistics"]["checked_count"] == 0
    assert state["ready"] is False
    # A complete final hand must occupy the SAME game ID as its list entry
    # when it is known in advance. No duplicates can be silently ignored.
    for n in range(19, 21):
        game = rows[n - 1]
        store.upsert(game, [
            {"position": 1, "rank": "10", "suit": "hearts"},
            {"position": 2, "rank": "2", "suit": "spades"},
        ], "CONFIRMED", "final", "CARDS_CONFIRMED",
            player_hand_final=True, final_snapshot_reverified=True)
    # Earlier duplicates (1-18) intentionally have conflicting IDs:
    # this session cannot invent a unique 20-hand preparation.
    assert engine.refresh()["ready"] is False


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


def test_missing_after_nineteen_requires_new_continuous_run(tmp_path):
    path = tmp_path / "missing-20.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.start_session()
    for n in range(1, 20):
        add_round(store, n)
    missing = Game("round-20", "https://example.test/20", "", "20", "", 20)
    store.register_discovered_games([missing])
    add_round(store, 21)
    state = engine.refresh()
    assert state["warmup_count"] == 1
    assert state["ready"] is False
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
