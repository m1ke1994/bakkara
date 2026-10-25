import asyncio
from collections import deque
from dataclasses import replace

import pytest
import app.browser as browser_module
from app.browser import BrowserService
from app.observation import Game, ObservationStore


@pytest.fixture(autouse=True)
def enable_retired_linking_for_compatibility_tests(monkeypatch):
    # This module's earlier tests explicitly exercise the archived N-1
    # browser-to-browser links. Production defaults to cycle18-only.
    monkeypatch.setattr(browser_module, "LEGACY_NEXT_GAME_MODE", True)


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
        assert store.get("target-c")["scan_result"] == "DISCOVERED"
        assert store.get("target-c")["cards"] == []
        lifecycle = [event["message"] for event in service.events]
        assert any("game_id=target-b state=DISCOVERED" in message for message in lifecycle)
        assert any("game_id=target-b state=SELECTED" in message for message in lifecycle)
        assert service.current_observation["lifecycle_state"] == "SELECTED"

    asyncio.run(run())


def test_observer_prioritizes_upcoming_over_finished_recovery(tmp_path, monkeypatch):
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
        # The legacy next-game chain cannot jump from 616 to 618, but
        # the physical 617 remains reserved for later suit recovery.
        assert store.pending_prediction_targets() == set()
        assert store.get("source")["outgoing_prediction_result"] == "UNKNOWN"
        assert store.get("missed")["scan_result"] == "DISCOVERED"

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

        assert [game.game_id for game in selected] == ["first"]
        assert service.page.url == LIST_URL
        assert service.current_observation["round_number"] == "801"

    asyncio.run(run())


def test_started_unknown_and_finished_rows_are_prioritized_by_live_status(tmp_path, monkeypatch):
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

        assert [game.game_id for game in selected] == ["started"]
        assert service.page.url == LIST_URL
        assert store.history() == []
        assert service.current_observation["game_id"] == "started"
        assert service.game_diagnostics[0]["round_number"] == "811"
        assert service.game_diagnostics[2]["state"] == "UNKNOWN"
        assert "откроем игру" in service.game_diagnostics[2]["reason"]
        await service._observe_iteration({"started"}, {})
        await service._observe_iteration({"started", "unknown"}, {})
        # After observing #813, never rewind to completed #811.
        assert [game.game_id for game in selected] == ["started", "unknown"]

    asyncio.run(run())


def test_unknown_game_does_not_block_new_game_after_a_retry(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL, observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "unknown-retry.sqlite3")
        selected = []
        service = _service(store, selected)
        games = [
            Game("u1", f"{TABLE}/u1-player-banker", "", "840", "", 0),
            Game("u2", f"{TABLE}/u2-player-banker", "", "841", "", 1),
        ]
        async def read_snapshot():
            return games
        service._read_game_list_snapshot = read_snapshot
        await service._observe_iteration(set(), {})
        assert [g.game_id for g in selected] == ["u1"]
        retry = {"u1": asyncio.get_running_loop().time() + 60}
        await service._observe_iteration(set(), retry)
        assert [g.game_id for g in selected] == ["u1", "u2"]
        assert service.observation_status != "ERROR"
    asyncio.run(run())


def test_finished_game_without_cards_yields_and_preserves_retry(tmp_path, monkeypatch):
    """A permanently empty Player widget must not pin the observer forever."""
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings,
            finished_recovery_timeout_seconds=0.1,
            card_poll_seconds=0.1,
            incomplete_retry_seconds=30,
        ))
        store = ObservationStore(tmp_path / "timeout.sqlite3")
        service = _service(store, [])
        game = Game("finished-no-cards", f"{TABLE}/finished-no-cards-player-banker",
                    "", "850", "Завершена", 0, finished=True)
        calls = []

        async def save_attempt(*args, **kwargs):
            calls.append(kwargs)
            return True

        async def inspect(*_args):
            return {
                "cards": [], "pending_card_slots": 0, "counts": {},
                "cause": "Поле Игрока не загрузилось",
                "finished": False, "status_text": "",
            }

        service._remember_game_scan = save_attempt
        service._inspect_game_dom = inspect
        processed = set()
        retry = {}
        await asyncio.wait_for(
            BrowserService._scan_selected_game(service, game, [], processed, retry),
            timeout=8,
        )
        assert calls
        assert processed == set()
        assert "finished-no-cards" in retry
        assert service.observation_status == "INCOMPLETE"
        assert service.current_observation["scan_result"] == "SCAN_TIMEOUT"
        assert "продолжаем" in service.observation_diagnostic
    asyncio.run(run())


def test_selects_live_and_nearest_hand_before_expired_recovery(tmp_path, monkeypatch):
    from app.observation import choose_observation_game
    old = Game("old", f"{TABLE}/old-player-banker", "00:01", "1027", "Завершена", 0,
               finished=True, player_score="4", banker_score="5")
    live = Game("live", f"{TABLE}/live-player-banker", "00:20", "1028", "", 1,
                player_score="2", banker_score="3")
    soon = Game("soon", f"{TABLE}/soon-player-banker", "00:27", "1029", "", 2,
                player_score="0", banker_score="0")
    far = Game("far", f"{TABLE}/far-player-banker", "03:27", "1032", "", 3,
               player_score="0", banker_score="0")
    assert choose_observation_game([old, live, soon, far], set()).game_id == "live"
    assert choose_observation_game([old, live, soon, far], {"live"}).game_id == "soon"
    assert choose_observation_game([old, live, soon, far], {"live", "soon"}).game_id == "old"
    assert choose_observation_game([old, live, soon, far], {"live", "soon", "old"}) is None
    assert choose_observation_game([old, far], {"old"}) is None
    assert choose_observation_game([old], set()).game_id == "old"


def test_unknown_without_timer_can_still_be_opened_as_last_resort():
    from app.observation import choose_observation_game
    no_status = Game("unknown", f"{TABLE}/unknown-player-banker",
                     "", "840", "", 0)
    assert choose_observation_game([no_status], set()).game_id == "unknown"


def test_default_cycle18_mode_never_links_legacy_prediction(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "LEGACY_NEXT_GAME_MODE", False)
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL, observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "cycle18-only.sqlite3")
        _complete_source(store)
        selected = []
        service = _service(store, selected)
        target = Game("round-617", f"{TABLE}/round-617-player-banker",
                      "00:15", "617", "UPCOMING", 0,
                      player_score="0", banker_score="0", is_countdown_to_start=True)
        async def read_snapshot():
            return [target]
        service._read_game_list_snapshot = read_snapshot
        await service._observe_iteration({"source"}, {})
        assert [game.game_id for game in selected] == ["round-617"]
        assert store.pending_prediction_targets() == set()
        assert store.get("source").get("outgoing_target_game_id") is None
        assert not any("PREDICTION_RESULT" in e["message"] for e in service.events)
    asyncio.run(run())


def test_after_round_400_prioritizes_401_not_stale_399_or_finished_400():
    from app.observation import choose_observation_game
    games = [
        Game("stale399", f"{TABLE}/399", "00:02", "399", "STARTED", 0,
             started=True, player_score="3", banker_score="5"),
        Game("done400", f"{TABLE}/400", "", "400", "Завершена", 1,
             finished=True),
        Game("next401", f"{TABLE}/401", "00:19", "401", "UPCOMING", 2,
             player_score="0", banker_score="0", is_countdown_to_start=True),
        Game("later402", f"{TABLE}/402", "00:52", "402", "UPCOMING", 3,
             player_score="0", banker_score="0", is_countdown_to_start=True),
    ]
    choose = choose_observation_game
    assert choose(games, set(), after_round=400).round_number == "401"
    assert choose(games, {"next401"}, after_round=400).round_number == "402"
    assert choose(games[:2], set(), after_round=400) is None


def test_after_round_1440_selects_round_1():
    from app.observation import choose_observation_game
    before = Game("last1440", f"{TABLE}/1440", "00:00", "1440",
                  "Завершена", 0, finished=True)
    after = Game("next1", f"{TABLE}/1", "00:12", "1", "UPCOMING", 1,
                 player_score="0", banker_score="0", is_countdown_to_start=True)
    assert choose_observation_game([before, after], set(), after_round=1440).game_id == "next1"


def test_next_round_scan_skips_archived_recovery_when_401_not_yet_listed(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL, observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "avoid-archive.sqlite3")
        old = Game("old399", f"{TABLE}/399", "00:00", "399", "Завершена", 0,
                   finished=True, player_score="2", banker_score="3")
        store.register_discovered_games([old])
        selected = []
        service = _service(store, selected)
        service._last_selected_round_number = 400
        service._read_game_list_snapshot = lambda: None
        async def snapshots():
            return [old]
        service._read_game_list_snapshot = snapshots
        await service._observe_iteration(set(), {})
        assert selected == []
        assert service.observation_status == "WAITING_FOR_GAME"
        assert service._last_selected_round_number == 400
    asyncio.run(run())


def test_next_round_selected_even_if_timer_is_over_one_minute_or_just_finished():
    from app.observation import choose_observation_game
    old = Game("r430", f"{TABLE}/430", "00:00", "430", "Завершена",
               0, finished=True)
    next_future = Game("r431", f"{TABLE}/431", "01:43", "431",
                       "UPCOMING", 1, player_score="0", banker_score="0",
                       is_countdown_to_start=True)
    later = Game("r432", f"{TABLE}/432", "00:08", "432",
                 "UPCOMING", 2, player_score="0", banker_score="0",
                 is_countdown_to_start=True)
    choose = choose_observation_game
    assert choose([old, next_future, later], set(), after_round=430).game_id == "r431"
    next_future.finished = True
    next_future.status = "Завершена"
    # Finished rounds are recovered by the background history reader.
    assert choose([old, next_future, later], set(), after_round=430).game_id == "r432"
    assert choose([old, next_future, later], {"r431"}, after_round=430).game_id == "r432"


def test_round_number_survives_transient_site_hydration_for_exact_game_id(tmp_path):
    async def run():
        store = ObservationStore(tmp_path / "round-cache.sqlite3")
        r431 = Game("763888699", f"{TABLE}/763888699-player-banker",
                    "00:10", "431", "", 0)
        r432 = Game("763888916", f"{TABLE}/763888916-player-banker",
                    "00:52", "432", "", 1)
        store.register_discovered_games([r431, r432])
        store.update_game_list_metadata([r431, r432])
        service = _service(store, [])
        class Page:
            def __init__(self):
                self.hydrated = True
            async def evaluate(self, _script):
                return [
                    {"absolute_url": g.url, "game_id": g.game_id,
                     "round_number": int(g.round_number) if self.hydrated else None,
                     "timer": g.time, "dom_position": i + 1}
                    for i, g in enumerate((r431, r432))
                ] + [
                    {"absolute_url": f"{TABLE}/763889247-player-banker",
                     "game_id": "763889247", "round_number": None,
                     "dom_position": 3}
                ]
        page = Page()
        service.page = page
        first = await service._read_game_list_snapshot()
        assert [g.round_number for g in first] == ["431", "432", ""]
        page.hydrated = False
        second = await service._read_game_list_snapshot()
        assert [g.round_number for g in second] == ["431", "432", ""]
        assert "идентичному game_id" in second[1].round_number_diagnostic
        # A different game ID never inherits a neighbouring game's number.
        assert second[2].round_number == ""
        # Memory may be lost during restart; the physical number survives
        # by matching the original ID in SQLite.
        another = _service(store, [])
        another.page = page
        assert [g.round_number for g in await another._read_game_list_snapshot()] == [
            "431", "432", ""]
    asyncio.run(run())


def test_fast_handoff_opens_three_sequential_games_without_reloading_list(tmp_path, monkeypatch):
    """Only one list hydration for known #500 -> #501 -> #502."""
    async def run():
        monkeypatch.setattr(browser_module, "LEGACY_NEXT_GAME_MODE", False)
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_list_url=LIST_URL,
            observation_poll_seconds=0,
        ))
        store = ObservationStore(tmp_path / "fast-handoff.sqlite3")
        selected = []
        service = _service(store, selected)
        service._last_selected_round_number = None
        games = [
            Game("r500", f"{TABLE}/500-player-banker", "00:15", "500",
                 "Идёт", 0, started=True, player_score="2", banker_score="3"),
            Game("r501", f"{TABLE}/501-player-banker", "00:45", "501",
                 "Ожидается", 1, player_score="0", banker_score="0",
                 is_countdown_to_start=True),
            Game("r502", f"{TABLE}/502-player-banker", "01:30", "502",
                 "Ожидается", 2, player_score="0", banker_score="0",
                 is_countdown_to_start=True),
        ]
        reads = []
        async def read_snapshot():
            reads.append("read")
            return games
        service._read_game_list_snapshot = read_snapshot
        goto_urls = []
        original_goto = service.page.goto
        async def counted_goto(url, **kwargs):
            goto_urls.append(url)
            await original_goto(url, **kwargs)
        service.page.goto = counted_goto
        processed, retry = set(), {}
        await service._observe_iteration(processed, retry)
        assert [g.round_number for g in selected] == ["500"]
        reads_after_first = len(reads)
        assert reads_after_first >= 2
        assert goto_urls == [LIST_URL]
        await service._observe_iteration(processed, retry)
        await service._observe_iteration(processed, retry)
        assert [g.round_number for g in selected] == ["500", "501", "502"]
        assert len(reads) == reads_after_first
        assert goto_urls == [LIST_URL]
        # When no known #503 URL exists, fall back to a fresh list read;
        # never invent a URL or skip a physical number.
        await service._observe_iteration(processed, retry)
        assert len(reads) > reads_after_first
    asyncio.run(run())


def test_fast_handoff_cache_rejects_ambiguous_and_missing_round_urls():
    from app.browser import prefetched_round_urls, pop_prefetched_successor
    games = [
        Game("g1440", f"{TABLE}/1440", "", "1440", "Идёт", 0),
        Game("g1", f"{TABLE}/1", "00:19", "1", "Ожидается", 1,
             player_score="0", banker_score="0", is_countdown_to_start=True),
        Game("g1-duplicate", f"{TABLE}/1-copy", "", "1", "Ожидается", 2),
        Game("unknown", f"{TABLE}/missing", "", "", "Ожидается", 3),
    ]
    cache = prefetched_round_urls(games)
    assert set(cache) == {1440}
    assert pop_prefetched_successor(cache, 1440, set()) is None
    valid = prefetched_round_urls(games[:2])
    assert pop_prefetched_successor(valid, 1440, set()).game_id == "g1"
    assert pop_prefetched_successor(valid, 1440, set()) is None
    valid = prefetched_round_urls(games[:2])
    assert pop_prefetched_successor(valid, 1440, {"g1"}) is None
