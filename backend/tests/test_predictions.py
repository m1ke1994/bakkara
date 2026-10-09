import sqlite3

from app.observation import Game, ObservationStore, evaluate_prediction


TABLE = "https://example.test/ru/live/baccarat/2050671-baccara"


def complete_source(store, game_id="opaque-source-id", round_number="616", position=0):
    game = Game(game_id, f"{TABLE}/{game_id}-player-banker", "", round_number, "Завершена", position)
    cards = [
        {"position": 1, "rank": "9", "suit": "hearts"},
        {"position": 2, "rank": "2", "suit": "clubs"},
    ]
    store.upsert(game, cards, "CONFIRMED", "final", "CARDS_CONFIRMED",
                 finished_confirmed=True, final_snapshot_reverified=True)
    return game


def target_game(game_id="opaque-target-id", round_number="617", position=1):
    return Game(game_id, f"{TABLE}/{game_id}-player-banker", "", round_number, "Идёт", position)


def save_target(store, game, cards, *, reverified=True):
    store.upsert(game, cards, "CONFIRMED" if reverified else "PARTIAL", "target result",
                 "CARDS_CONFIRMED" if reverified else "CARDS_UNCONFIRMED",
                 finished_confirmed=True, final_snapshot_reverified=reverified)
    store.refresh_prediction_target(game.game_id)


def test_round_616_links_one_prediction_to_the_selected_round_617(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store)
    target = target_game()

    transition = store.link_active_transition(source.game_id, target)

    assert transition["source_round_number"] == "616"
    assert transition["target_round_number"] == "617"
    assert transition["source_game_id"] == "opaque-source-id"
    assert transition["target_game_id"] == "opaque-target-id"
    assert transition["predicted_suit"] == "spades"
    assert transition["prediction_result"] == "PENDING"


def test_spade_forecast_wins_when_any_confirmed_player_card_is_spades(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store)
    target = target_game()
    store.link_active_transition(source.game_id, target)

    save_target(store, target, [
        {"position": 1, "rank": "9", "suit": "hearts"},
        {"position": 2, "rank": "2", "suit": "clubs"},
        {"position": 3, "rank": "7", "suit": "spades"},
    ])

    row = store.get(target.game_id)
    assert row["prediction_result"] == "WIN"
    assert row["source_game_id"] == source.game_id
    assert len(row["actual_player_cards"]) == 3
    assert store.get(source.game_id)["prediction_hit"] is True


def test_spade_forecast_loses_only_against_complete_confirmed_cards(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store)
    target = target_game()
    store.link_active_transition(source.game_id, target)

    save_target(store, target, [
        {"position": 1, "rank": "A", "suit": "hearts"},
        {"position": 2, "rank": "8", "suit": "diamonds"},
        {"position": 3, "rank": "J", "suit": "clubs"},
    ])

    assert store.get(target.game_id)["prediction_result"] == "LOSE"
    assert store.statistics()["losses"] == 1


def test_duplicate_matching_suits_are_still_one_win():
    cards = [
        {"rank": "3", "suit": "spades"},
        {"rank": "4", "suit": "spades"},
    ]
    assert evaluate_prediction("♠", cards, finished_confirmed=True,
                               final_snapshot_reverified=True) == "WIN"


def test_incomplete_final_snapshot_is_unknown_not_lose(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store)
    target = target_game()
    store.link_active_transition(source.game_id, target)

    save_target(store, target, [
        {"position": 1, "rank": "A", "suit": "hearts"},
        {"position": 2, "rank": "8", "suit": "diamonds"},
    ], reverified=False)

    assert store.get(target.game_id)["prediction_result"] == "UNKNOWN"
    assert store.statistics()["checked_count"] == 0


def test_target_without_incoming_prediction_has_no_win_or_lose(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    target = target_game()
    save_target(store, target, [
        {"position": 1, "rank": "3", "suit": "spades"},
        {"position": 2, "rank": "K", "suit": "hearts"},
    ])

    row = store.get(target.game_id)
    assert "prediction_result" not in row
    assert "predicted_suit" not in row
    assert store.statistics()["checked_count"] == 0


def test_round_gap_does_not_block_selected_transition_or_following_forecast(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store, round_number="616")
    skipped_target = target_game("opaque-618", "618", 1)

    transition = store.link_active_transition(source.game_id, skipped_target)
    assert transition["prediction_result"] == "PENDING"
    assert transition["target_round_number"] == "618"
    save_target(store, skipped_target, [
        {"position": 1, "rank": "3", "suit": "spades"},
        {"position": 2, "rank": "K", "suit": "hearts"},
    ])
    assert store.get(skipped_target.game_id)["prediction_result"] == "WIN"

    next_target = target_game("opaque-619", "619", 2)
    next_transition = store.link_active_transition(skipped_target.game_id, next_target)
    assert next_transition["source_game_id"] == skipped_target.game_id
    assert next_transition["target_game_id"] == next_target.game_id
    assert next_transition["prediction_result"] == "PENDING"


def test_counter_reset_and_missing_round_do_not_block_active_transition(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store, "source-999", "999", 0)
    reset = target_game("target-1", "1", 1)
    assert store.link_active_transition(source.game_id, reset)["prediction_result"] == "PENDING"

    other_source = complete_source(store, "source-616", "616", 2)
    missing = target_game("target-no-round", "", 3)
    transition = store.link_active_transition(other_source.game_id, missing)
    assert transition["prediction_result"] == "PENDING"
    assert transition["target_round_number"] is None


def test_forecast_is_not_created_from_history_before_scanner_selects_a_target(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store)

    assert "outgoing_prediction_result" not in store.get(source.game_id)

    later = target_game("later-game", "617", 1)
    store.upsert(later, [], "PARTIAL", "not linked", "CARDS_NOT_FOUND")
    assert "prediction_result" not in store.get(later.game_id)
    assert store.link_active_transition(source.game_id, later)["prediction_result"] == "PENDING"


def test_old_non_null_target_schema_migrates_without_losing_transitions(tmp_path):
    path = tmp_path / "old-schema.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE observations (
            game_id TEXT PRIMARY KEY,url TEXT NOT NULL,round_number TEXT,discovered_at TEXT NOT NULL,
            finished_at TEXT,cards_json TEXT NOT NULL,completeness TEXT NOT NULL,reason TEXT)""")
        db.execute("""CREATE TABLE predictions (
            source_game_id TEXT PRIMARY KEY,source_round_number TEXT,target_game_id TEXT NOT NULL UNIQUE,
            target_round_number TEXT,predicted_suit TEXT,actual_player_cards TEXT,prediction_result TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,checked_at TEXT)""")
        db.execute("""INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   ("source", "616", "target", "617", "spades", None, "PENDING", "waiting", "2026-10-09T10:00:00+00:00", None))

    ObservationStore(path)
    with sqlite3.connect(path) as db:
        target_column = next(row for row in db.execute("PRAGMA table_info(predictions)") if row[1] == "target_game_id")
        preserved = db.execute("SELECT source_game_id,target_game_id,prediction_result FROM predictions").fetchone()
    assert target_column[3] == 0
    assert preserved == ("source", "target", "PENDING")


def test_round_617_can_form_a_fresh_forecast_for_round_618(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store, "opaque-617", "617", 0)
    target = target_game("opaque-618", "618", 1)

    transition = store.link_active_transition(source.game_id, target)

    assert transition["source_round_number"] == "617"
    assert transition["target_round_number"] == "618"
    assert transition["prediction_result"] == "PENDING"


def test_active_scanner_sequence_ignores_round_gaps_and_continues_after_lose(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source_a = complete_source(store, "scan-a", "616", 0)
    target_b = target_game("scan-b", "652", 1)

    transition_ab = store.link_active_transition(source_a.game_id, target_b)
    assert transition_ab["prediction_result"] == "PENDING"
    assert transition_ab["target_round_number"] == "652"
    assert store.link_active_transition(source_a.game_id, target_b)["target_game_id"] == "scan-b"

    save_target(store, target_b, [
        {"position": 1, "rank": "9", "suit": "hearts"},
        {"position": 2, "rank": "2", "suit": "clubs"},
    ])
    assert store.get(target_b.game_id)["prediction_result"] == "LOSE"

    target_c = target_game("scan-c", "", 2)
    transition_bc = store.link_active_transition(target_b.game_id, target_c)
    assert transition_bc["prediction_result"] == "PENDING"
    assert transition_bc["source_game_id"] == "scan-b"
    assert transition_bc["target_round_number"] is None
    save_target(store, target_c, [
        {"position": 1, "rank": "7", "suit": "spades"},
        {"position": 2, "rank": "A", "suit": "hearts"},
    ])
    assert store.get(target_c.game_id)["prediction_result"] == "WIN"
    assert store.statistics()["wins"] == 1
    assert store.statistics()["losses"] == 1


def test_screenshot_sequence_a_to_b_win_then_b_forecasts_diamonds_for_c(tmp_path):
    store = ObservationStore(tmp_path / "screenshot-sequence.sqlite3")
    game_a = Game("screen-a", f"{TABLE}/759966370-player-banker", "00:14", "751", "Завершена", 0)
    store.upsert(game_a, [
        {"position": 1, "rank": "10", "suit": "spades"},
        {"position": 2, "rank": "5", "suit": "spades"},
        {"position": 3, "rank": "8", "suit": "clubs"},
    ], "CONFIRMED", "final A", "CARDS_CONFIRMED", finished_confirmed=True,
       final_snapshot_reverified=True, player_hand_final=True)
    assert store.get(game_a.game_id)["calculated_suit"] == "spades"

    game_b = Game("screen-b", f"{TABLE}/759966371-player-banker", "00:15", "752", "Завершена", 1)
    linked_ab = store.link_active_transition(game_a.game_id, game_b)
    assert linked_ab["target_game_id"] == game_b.game_id
    assert linked_ab["predicted_suit"] == "spades"
    save_target(store, game_b, [
        {"position": 1, "rank": "8", "suit": "spades"},
        {"position": 2, "rank": "10", "suit": "clubs"},
    ])
    row_b = store.get(game_b.game_id)
    assert row_b["incoming_prediction"] == "spades"
    assert row_b["prediction_result"] == "WIN"
    assert row_b["calculated_suit"] == "diamonds"
    assert row_b["outgoing_prediction"] == "diamonds"

    game_c = Game("screen-c", f"{TABLE}/759966372-player-banker", "00:16", "753", "Идёт", 2)
    linked_bc = store.link_active_transition(game_b.game_id, game_c)
    assert linked_bc["source_game_id"] == game_b.game_id
    assert linked_bc["target_game_id"] == game_c.game_id
    assert linked_bc["predicted_suit"] == "diamonds"
    assert linked_bc["prediction_result"] == "PENDING"


def test_active_chain_break_is_not_carried_to_a_later_game(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store, "scan-source", "616", 0)
    assert store.record_unresolved_transition(source.game_id, "missed game")

    later = target_game("later-selected", "700", 9)
    assert store.link_active_transition(source.game_id, later) is None
    assert store.get(later.game_id) is None
    assert store.get(source.game_id)["outgoing_prediction_result"] == "UNKNOWN"
    assert store.statistics()["checked_count"] == 0


def test_transition_history_survives_store_restart(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = ObservationStore(path)
    source = complete_source(store)
    target = target_game()
    store.link_active_transition(source.game_id, target)
    save_target(store, target, [
        {"position": 1, "rank": "3", "suit": "spades"},
        {"position": 2, "rank": "K", "suit": "hearts"},
    ])

    reloaded = ObservationStore(path)
    row = reloaded.get(target.game_id)
    assert row["prediction_result"] == "WIN"
    assert row["predicted_suit"] == "spades"
    assert reloaded.statistics()["wins"] == 1


def test_repeated_list_and_result_do_not_duplicate_or_overwrite_win(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_source(store)
    target = target_game()
    assert store.link_active_transition(source.game_id, target)["target_game_id"] == target.game_id
    assert store.link_active_transition(source.game_id, target)["target_game_id"] == target.game_id
    save_target(store, target, [
        {"position": 1, "rank": "3", "suit": "spades"},
        {"position": 2, "rank": "K", "suit": "hearts"},
    ])
    save_target(store, target, [
        {"position": 1, "rank": "A", "suit": "hearts"},
        {"position": 2, "rank": "8", "suit": "diamonds"},
    ])

    assert store.get(target.game_id)["prediction_result"] == "WIN"
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 1


def test_stats_exclude_pending_unknown_and_skipped_but_break_streaks(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    statuses = ["WIN", "LOSE", "LOSE", "UNKNOWN", "WIN", "LOSE", "PENDING", "PENDING"]
    with sqlite3.connect(store.path) as db:
        for index, status in enumerate(statuses):
            timestamp = f"2026-10-09T10:00:{index:02d}+00:00"
            db.execute("""INSERT INTO predictions(
                source_game_id,target_game_id,prediction_result,reason,created_at,checked_at)
                VALUES(?,?,?,?,?,?)""",
                (f"source-{index}", f"target-{index}", status, "test", timestamp,
                 None if status == "PENDING" else timestamp))

    stats = ObservationStore(store.path).statistics()
    assert stats == {
        "checked_count": 5,
        "wins": 2,
        "losses": 3,
        "win_rate": 40.0,
        "current_win_streak": 0,
        "current_lose_streak": 1,
        "max_lose_streak": 2,
    }
