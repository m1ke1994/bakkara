import asyncio
from collections import deque
from dataclasses import replace

import app.browser as browser_module
from app.browser import BrowserService
from app.observation import Game, ObservationStore


TABLE = "https://example.test/ru/live/baccarat/table"
LIST_URL = f"{TABLE}/live-list"


class _Locator:
    @property
    def first(self):
        return self

    async def wait_for(self, **_kwargs):
        return None

    async def evaluate_all(self, _expression):
        return ["fixture list markup"]


class _Page:
    def __init__(self):
        self.url = "about:blank"

    async def goto(self, url, **_kwargs):
        self.url = url

    async def wait_for_function(self, *_args, **_kwargs):
        return None

    def locator(self, _selector):
        return _Locator()

    def is_closed(self):
        return False


def _complete_source(store, round_number="616"):
    game = Game("source", f"{TABLE}/source-player-banker", "", round_number, "Завершена", 0)
    store.upsert(game, [
        {"position": 1, "rank": "3", "suit": "spades"},
        {"position": 2, "rank": "K", "suit": "spades"},
    ], "CONFIRMED", "final", "CARDS_CONFIRMED",
       finished_confirmed=True, final_snapshot_reverified=True)
    return game


def _service(store, selected):
    service = object.__new__(BrowserService)
    service.page = _Page()
    service.page_lock = asyncio.Lock()
    service.store = store
    service.events = deque(maxlen=100)
    service.current_observation = None
    service.observation_status = "WAITING_FOR_GAME"
    service.observation_diagnostic = ""
    service.observation_phase = ""
    service.game_diagnostics = []
    service._last_no_game_signature = None
    service._active_chain_source_id = "source"
    service._queued_games = []
    service._probe_observation_auth_locked = _resolved_true

    async def scan(chosen, *_args):
        selected.append(chosen)

    service._scan_selected_game = scan
    return service


async def _resolved_true():
    return True


def test_observer_links_forecast_to_the_next_row_it_selects_not_round_adjacency(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL, observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "chain.sqlite3")
        _complete_source(store)
        selected = []
        service = _service(store, selected)
        first = Game("target-b", f"{TABLE}/target-b-player-banker", "00:15", "617", "UPCOMING", 0,
                     player_score="0", banker_score="0", is_countdown_to_start=True)
        later = Game("target-c", f"{TABLE}/target-c-player-banker", "01:15", "618", "UPCOMING", 1,
                     player_score="0", banker_score="0", is_countdown_to_start=True)
        async def read_snapshot():
            return [first, later]
        service._read_game_list_snapshot = read_snapshot

        await service._observe_iteration({"source"}, {})

        assert [game.game_id for game in selected] == ["target-b"]
        assert store.pending_prediction_targets() == {"target-b"}
        assert store.get("source")["outgoing_target_game_id"] == "target-b"
        assert store.get("target-c") is None
        lifecycle = [event["message"] for event in service.events]
        assert any("game_id=target-b state=DISCOVERED" in message for message in lifecycle)
        assert any("game_id=target-b state=SELECTED" in message for message in lifecycle)
        assert service.current_observation["lifecycle_state"] == "SELECTED"

    asyncio.run(run())


def test_observer_skips_finished_game_and_breaks_forecast_chain_before_later_round(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL, observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "chain.sqlite3")
        _complete_source(store)
        selected = []
        service = _service(store, selected)
        missed = Game("missed", f"{TABLE}/missed-player-banker", "00:00", "617", "Завершена", 0,
                      status_text="Игра завершена", finished=True)
        target = Game("target", f"{TABLE}/target-player-banker", "00:15", "618", "UPCOMING", 1,
                      player_score="0", banker_score="0", is_countdown_to_start=True)
        async def read_snapshot():
            return [missed, target]
        service._read_game_list_snapshot = read_snapshot

        await service._observe_iteration({"source"}, {})

        assert [game.game_id for game in selected] == ["target"]
        assert not store.pending_prediction_targets()
        assert store.get("source")["outgoing_prediction_result"] == "UNKNOWN"

    asyncio.run(run())


def test_observer_breaks_chain_when_round_gap_cannot_be_restored(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL, observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "missing-gap.sqlite3")
        _complete_source(store, "705")
        selected = []
        service = _service(store, selected)
        target = Game("round-707", f"{TABLE}/round-707-player-banker", "00:15", "707", "UPCOMING", 0,
                      player_score="0", banker_score="0", is_countdown_to_start=True)
        async def read_snapshot():
            return [target]
        service._read_game_list_snapshot = read_snapshot

        await service._observe_iteration({"source"}, {})

        assert [game.game_id for game in selected] == ["round-707"]
        source = store.get("source")
        assert source["outgoing_prediction_result"] == "UNKNOWN"
        assert source["outgoing_target_game_id"] is None
        assert not store.pending_prediction_targets()

    asyncio.run(run())


def test_candidate_that_starts_before_opening_is_replaced_by_next_upcoming(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL, observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "preflight.sqlite3")
        selected = []
        service = _service(store, selected)
        first = Game("first", f"{TABLE}/first-player-banker", "00:01", "801", "UPCOMING", 0,
                     player_score="0", banker_score="0", is_countdown_to_start=True)
        first_started = Game("first", first.url, "00:00", "801", "STARTED", 0,
                             player_score="0", banker_score="0", started=True)
        second = Game("second", f"{TABLE}/second-player-banker", "00:15", "802", "UPCOMING", 1,
                      player_score="0", banker_score="0", is_countdown_to_start=True)
        snapshots = [[first, second], [first_started, second], [first_started, second]]

        async def read_snapshot():
            return snapshots.pop(0) if snapshots else [first_started, second]

        service._read_game_list_snapshot = read_snapshot

        await service._observe_iteration(set(), {})

        assert [game.game_id for game in selected] == ["second"]
        assert service.page.url == LIST_URL
        assert any("изменилась перед открытием" in event["message"] for event in service.events)

    asyncio.run(run())


def test_only_finished_started_and_unknown_rows_leave_observer_waiting(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL, observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "no-upcoming.sqlite3")
        selected = []
        service = _service(store, selected)
        games = [
            Game("finished", f"{TABLE}/finished-player-banker", "00:00", "811", "FINISHED", 0,
                 player_score="5", banker_score="8", finished=True),
            Game("started", f"{TABLE}/started-player-banker", "00:00", "812", "STARTED", 1,
                 player_score="0", banker_score="0", started=True),
            Game("unknown", f"{TABLE}/unknown-player-banker", "00:15", "813", "", 2,
                 player_score="0", banker_score="0"),
        ]

        async def read_snapshot():
            return games

        service._read_game_list_snapshot = read_snapshot

        await service._observe_iteration(set(), {})

        assert selected == []
        assert service.observation_status == "WAITING_FOR_GAME"
        assert "Игр до начала со счётом 0:0 пока нет" in service.observation_diagnostic
        assert service.page.url == LIST_URL
        assert store.history() == []
        assert service.current_observation is None
        assert service.game_diagnostics[0]["round_number"] == "811"
        assert service.game_diagnostics[2]["state"] == "UNKNOWN"
        assert service.game_diagnostics[2]["status_text"] == ""
        assert service.game_diagnostics[2]["is_countdown_to_start"] is False

    asyncio.run(run())
