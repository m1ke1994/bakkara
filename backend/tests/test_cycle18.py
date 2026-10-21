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
    assert waiting["warmup_count"] == 2
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
