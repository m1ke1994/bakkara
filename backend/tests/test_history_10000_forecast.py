"""The extended archive must expose original forecasts, not hindsight guesses."""
import sqlite3
from datetime import datetime, timedelta, timezone

from app.cycle18 import Cycle18Engine, STRATEGY_EIGHT
from app.live_bets import LiveBets
from app.observation import ObservationStore


def make_archive(tmp_path, count=1250):
    path = tmp_path / "many-hands.sqlite3"
    store = ObservationStore(path)
    engine = Cycle18Engine(path)
    LiveBets(path)
    start = datetime(2026, 10, 30, tzinfo=timezone.utc)
    with sqlite3.connect(path) as db:
        db.executemany(
            """INSERT INTO observations
               (game_id, url, round_number, discovered_at, finished_at, observed_at,
                cards_json, completeness, player_hand_final, final_snapshot_reverified,
                calculated_suit, calculation_status)
               VALUES (?,?,?,?,?,?,?,'CONFIRMED',1,1,'hearts','complete')""",
            [(
                f"round-{i}", f"https://example.invalid/baccara/{i}", str(i),
                (start + timedelta(minutes=i - 1)).isoformat(),
                (start + timedelta(minutes=i)).isoformat(),
                (start + timedelta(minutes=i)).isoformat(),
                '[{"rank":"A","suit":"hearts"},{"rank":"2","suit":"spades"}]'
            ) for i in range(1, count + 1)]
        )
        cursor = db.execute(
            "INSERT INTO cycle18_sessions(started_at,stopped_at,strategy) VALUES(?,?,?)",
            (start.isoformat(), (start + timedelta(days=2)).isoformat(), STRATEGY_EIGHT)
        )
        sid = cursor.lastrowid
        # Exactly linked, saved predictions: one real, one discovered after
        # the round ended, and one virtual skip. The same ID must be used.
        for i, predicted, mode, minutes_offset in [
            (1, "spades", "LIVE", -0.5),
            (2, "diamonds", "BACKFILL", 2),
            (3, "clubs", "FILTER", -0.5),
        ]:
            db.execute(
                """INSERT INTO cycle18_forecasts
                   (session_id,source_game_id,anchor_game_id,source_round_number,
                    target_round_number,target_game_id,predicted_suit,created_at,
                    result,mode)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (sid, f"source-{i}", f"anchor-{i}", i - 1, i, f"round-{i}",
                 predicted, (start + timedelta(minutes=i + minutes_offset)).isoformat(),
                 "WIN" if i == 3 else "LOSE", mode)
            )
        db.execute(
            """INSERT INTO predictions
               (source_game_id,source_round_number,target_game_id,target_round_number,
                predicted_suit,prediction_result,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            ("round-3", "3", "round-4", "4", "diamonds", "WIN",
             (start + timedelta(minutes=3, seconds=30)).isoformat())
        )
        db.execute(
            """INSERT INTO live_attempts
               (game_id,round_number,suit,amount,step,status,result,signal_source)
               VALUES('round-1','1','spades','15',1,'ACCEPTED','LOSE','STRATEGY')"""
        )
    return store


def test_history_returns_over_1000_hands_without_sqlite_variable_error(tmp_path):
    store = make_archive(tmp_path)
    history = store.history(limit=10_000)
    assert len(history) == 1250
    assert history[0]["game_id"] == "round-1250"
    assert history[-1]["game_id"] == "round-1"
    assert len({row["game_id"] for row in history}) == 1250


def test_history_uses_saved_pre_hand_predictions_and_separates_live_bet(tmp_path):
    store = make_archive(tmp_path)
    history = {item["game_id"]: item for item in store.history(limit=10_000)}

    original = history["round-1"]
    assert original["calculated_suit"] == "hearts"
    assert original["forecast_suit"] == "spades"
    assert original["forecast_source"] == "STRATEGY"
    assert original["forecast_strategy"] == STRATEGY_EIGHT
    assert original["forecast_result"] == "LOSE"
    assert original["bet_suit"] == "spades"
    assert original["bet_status"] == "ACCEPTED"
    assert original["bet_step"] == 1
    assert original["bet_amount"] == "15"

    hindsight = history["round-2"]
    assert hindsight["forecast_suit"] is None  # Late BACKFILL cannot masquerade as pre-hand signal.
    assert hindsight["bet_suit"] is None

    skipped = history["round-3"]
    assert skipped["forecast_suit"] == "clubs"
    assert skipped["forecast_mode"] == "FILTER"
    assert skipped["bet_suit"] is None

    legacy = history["round-4"]
    assert legacy["forecast_suit"] == "diamonds"
    assert legacy["forecast_source"] == "LEGACY"
    assert legacy["forecast_strategy"] is None

    unseen = history["round-5"]
    assert unseen["forecast_suit"] is None
    assert unseen["forecast_result"] is None
