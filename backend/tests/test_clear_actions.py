import asyncio
import sqlite3
from collections import deque

from app.browser import BrowserService
from app.main import app
from app.observation import Game, ObservationStore


TABLE = "https://example.test/ru/live/baccarat/table"


def complete_game(store, game_id="source"):
    game = Game(game_id, f"{TABLE}/{game_id}-player-banker", "", "616", "finished", 0)
    cards = [
        {"position": 1, "rank": "3", "suit": "spades"},
        {"position": 2, "rank": "K", "suit": "spades"},
    ]
    store.upsert(game, cards, "CONFIRMED", "test diagnostic", "CARDS_CONFIRMED",
                 finished_confirmed=True, final_snapshot_reverified=True)
    return game


def service_with_store(path):
    service = object.__new__(BrowserService)
    service.store = ObservationStore(path)
    service.events = deque([{"at": "now", "level": "INFO", "message": "test event"}], maxlen=100)
    service.lock = asyncio.Lock()
    service.observation_task = None
    service._active_chain_source_id = "source"
    service.current_observation = {"game_id": "source"}
    service.game_diagnostics = [{"game_id": "source"}]
    service.observation_status = "WAITING_FOR_GAME"
    service.observation_diagnostic = "running"
    service.status = "AUTHORIZED"
    service.authorization = "AUTHORIZED"
    service.diagnostic = "signed in"
    return service


def test_store_clear_deletes_only_observations_and_predictions(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    source = complete_game(store)
    target = Game("target", f"{TABLE}/target-player-banker", "", "652", "live", 1)
    store.link_active_transition(source.game_id, target)
    store.upsert(target, [], "PARTIAL", "diagnostic", "SCANNING")
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS user_settings (key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT OR REPLACE INTO user_settings VALUES('theme','dark')")

    store.clear_all()

    assert store.history() == []
    assert store.statistics() == {
        "checked_count": 0, "wins": 0, "losses": 0, "win_rate": None,
        "current_win_streak": 0, "current_lose_streak": 0, "max_lose_streak": 0,
    }
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
        assert db.execute("SELECT value FROM user_settings WHERE key='theme'").fetchone()[0] == "dark"


def test_clear_history_waits_for_scan_to_stop_and_preserves_authorization(tmp_path):
    async def run():
        service = service_with_store(tmp_path / "history.sqlite3")
        complete_game(service.store)
        cancelled = asyncio.Event()
        started = asyncio.Event()

        async def writer():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        service.observation_task = asyncio.create_task(writer())
        await started.wait()
        snapshot = await service.clear_history()

        assert cancelled.is_set()
        assert service.observation_task is None
        assert service.store.history() == []
        assert service.current_observation is None
        assert service.game_diagnostics == []
        assert service._active_chain_source_id is None
        assert service.observation_status == "STOPPED"
        assert service.authorization == "AUTHORIZED"
        assert service.status == "AUTHORIZED"
        assert snapshot["observation"]["history"] == []

    asyncio.run(run())


def test_clear_logs_changes_only_the_event_buffer(tmp_path):
    service = service_with_store(tmp_path / "history.sqlite3")
    complete_game(service.store)
    original_current = service.current_observation
    original_chain = service._active_chain_source_id
    service.observation_task = marker = object()

    snapshot = service.clear_logs()

    assert service.events == deque([], maxlen=100)
    assert service.store.get("source") is not None
    assert service.current_observation is original_current
    assert service._active_chain_source_id == original_chain
    assert service.observation_task is marker
    assert service.authorization == "AUTHORIZED"
    assert snapshot["events"] == []


def test_stopping_scan_marks_unassigned_outgoing_forecast_unknown(tmp_path):
    async def run():
        service = service_with_store(tmp_path / "history.sqlite3")
        complete_game(service.store)
        await service.stop_observation()

        assert service._active_chain_source_id is None
        assert service.store.get("source")["outgoing_prediction_result"] == "UNKNOWN"
        assert service.authorization == "AUTHORIZED"

    asyncio.run(run())


def test_clear_actions_have_distinct_delete_api_routes():
    routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", set())}
    assert ("/api/observations", "DELETE") in routes
    assert ("/api/logs", "DELETE") in routes
