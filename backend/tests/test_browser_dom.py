import asyncio
from collections import deque
from dataclasses import replace

from playwright.async_api import async_playwright

import app.browser as browser_module
from app.browser import BrowserService
from app.observation import Game, ObservationStore


def test_chromium_reads_player_positions_and_exact_visible_finish_flag():
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content("""
                  <style>.baccarat-player,.baccarat-player__cards,.baccarat-player__card-box,
                    .ui-caption--size-xl.ui-caption--weight-700 { display:block; }</style>
                  <div class="baccarat-player baccarat__player">
                    <span class="baccarat-player__name">Банкир</span>
                    <ul class="baccarat-player__cards"><li class="baccarat-player__card-box suit-hearts">
                      <span class="baccarat-card__rank">A</span></li></ul>
                  </div>
                  <div class="baccarat-player baccarat__player">
                    <span class="baccarat-player__name">Игрок</span>
                    <ul class="baccarat-player__cards">
                      <li class="baccarat-player__card-box suit-spades"><span class="baccarat-card__rank">3</span></li>
                      <li class="baccarat-player__card-box suit-spades"><span class="baccarat-card__rank">K</span></li>
                      <li class="baccarat-player__card-box suit-clubs"><span class="baccarat-card__rank">J</span></li>
                    </ul>
                  </div>
                  <div id="finish" class="ui-caption--size-xl ui-caption--weight-700">Игра завершена</div>
                """)
                exact = await service._inspect_game_dom("Игрок", "Банкир")
                assert exact["finished"] is True
                assert exact["finish_flag_text"] == "Игра завершена"
                assert exact["cards"] == [
                    {"position": 1, "rank": "3", "suit": "spades"},
                    {"position": 2, "rank": "K", "suit": "spades"},
                    {"position": 3, "rank": "J", "suit": "clubs"},
                ]
                assert exact["counts"]["banker_names"] == 1
                await page.locator("#finish").evaluate("node => node.textContent = 'Ставки закрыты'")
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["finished"] is False
                await page.locator("#finish").evaluate("node => { node.textContent = 'Игра завершена'; node.style.display = 'none'; }")
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["finished"] is False
                await page.locator("#finish").evaluate("node => node.remove()")
                await page.evaluate("""() => {
                  const panel = document.createElement('div');
                  panel.className = 'market-grid__game-over-panel';
                  panel.textContent = 'Игра завершена';
                  document.body.append(panel);
                }""")
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["finished"] is False
                await page.evaluate("""() => {
                  const label = document.createElement('div');
                  label.className = 'ui-caption--size-xl ui-caption--weight-700';
                  label.textContent = 'Игра завершена';
                  document.body.append(label);
                }""")
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["finished"] is True
            finally:
                await browser.close()

    asyncio.run(run())


def test_observer_confirms_stable_third_card_and_saves_without_waiting_for_game_finish(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings,
            game_page_wait_seconds=0,
            game_finish_timeout_seconds=5,
            observation_poll_seconds=0.25,
            card_poll_seconds=0.1,
        ))

        holder = {}

        class Page:
            url = "about:blank"

            async def goto(self, url, **kwargs):
                if url.endswith("/flow-1"):
                    service = holder["service"]
                    assert service.current_observation["game_id"] == "flow-1"
                    assert service.current_observation["round_number"] == "652"
                    assert service.store.get("flow-1") is None
                    assert service.store.history() == []
                self.url = url

        service = object.__new__(BrowserService)
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "flow.sqlite3")
        holder["service"] = service
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "flow-1"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service.observation_phase = ""
        service._probe_observation_auth_locked = lambda: _resolved(True)

        def snapshot(cards=(), *, finished=False):
            return {
                "url": "about:blank",
                "counts": {
                    "player_fields": int(bool(cards)),
                    "player_cards_containers": int(bool(cards)),
                    "player_card_boxes": len(cards),
                },
                "cards": list(cards),
                "pending_card_slots": 0,
                "finished": finished,
                "finish_flag_text": "Игра завершена" if finished else "",
                "status_text": "Игра завершена" if finished else "",
                "cause": "fixture",
                "dom_fragment": "",
            }

        card1 = {"position": 1, "rank": "3", "suit": "spades"}
        card2 = {"position": 2, "rank": "K", "suit": "spades"}
        card3 = {"position": 3, "rank": "J", "suit": "clubs"}
        reads = iter([
            snapshot(),
            snapshot([card1, card2]),
            snapshot([card1, card2, card3]),
            snapshot([card1, card2, card3]),
        ])

        async def inspect(_player, _banker):
            return next(reads)

        service._inspect_game_dom = inspect
        game = Game("flow-1", "https://example.test/flow-1", "00:20", "652", "", 0)
        processed = set()
        await service._scan_selected_game(game, [], processed, {})

        assert processed == {"flow-1"}
        assert service.observation_status == "SAVED"
        saved = service.store.get("flow-1")
        assert saved["finished_confirmed"] is False
        assert saved["player_hand_final"] is True
        assert saved["final_snapshot_reverified"] is True
        assert saved["round_number"] == "652"
        assert saved["cards"] == [card1, card2, card3]
        lifecycle = [event["message"].split("state=", 1)[1].split()[0]
                     for event in reversed(service.events)
                     if event["message"].startswith("LIFECYCLE game_id=flow-1")]
        assert lifecycle == ["OPENING", "OBSERVING", "CARDS_COMPLETE", "FINALIZED", "PREDICTION_EVALUATED"]
        assert service.current_observation["lifecycle_state"] == "PREDICTION_EVALUATED"

    async def _resolved(value):
        return value

    asyncio.run(run())


def test_two_cards_are_final_only_after_explicit_finished_list_status(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_page_wait_seconds=30, game_finish_timeout_seconds=2,
            player_hand_timeout_seconds=2, card_poll_seconds=0.1,
        ))
        service = object.__new__(BrowserService)
        service.page = type("Page", (), {"url": "about:blank", "goto": lambda self, url, **kwargs: _set_url(self, url)})()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "two-final.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "two-final"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service._probe_observation_auth_locked = lambda: _resolved(True)
        cards = [
            {"position": 1, "rank": "A", "suit": "diamonds"},
            {"position": 2, "rank": "10", "suit": "clubs"},
        ]
        reads = 0

        async def inspect(_player, _banker):
            nonlocal reads
            reads += 1
            return {
                "url": "about:blank", "counts": {"player_fields": 1, "player_cards_containers": 1,
                    "player_card_boxes": 2}, "cards": cards, "pending_card_slots": 0,
                "finished": False, "status_text": "", "cause": "fixture", "dom_fragment": "",
            }

        service._inspect_game_dom = inspect
        game = Game("two-final", "https://example.test/two-final", "", "653", "Завершена", 0,
                    status_text="Игра завершена")
        processed = set()
        started = asyncio.get_running_loop().time()
        await service._scan_selected_game(game, [], processed, {})
        elapsed = asyncio.get_running_loop().time() - started

        saved = service.store.get("two-final")
        assert reads == 2  # one read plus a final stable reread, no timeout wait
        assert elapsed < 0.75  # GAME_PAGE_WAIT_SECONDS=30 is no longer a per-round delay
        assert processed == {"two-final"}
        assert saved["player_hand_final"] is True
        assert saved["finished_confirmed"] is True
        assert saved["calculation_status"] == "complete"

    async def _set_url(page, url):
        page.url = url

    async def _resolved(value):
        return value

    asyncio.run(run())


def test_disappearing_cards_keep_last_snapshot_but_never_confirm_or_lose(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_finish_timeout_seconds=2, player_hand_timeout_seconds=2,
            card_poll_seconds=0.1,
        ))
        service = object.__new__(BrowserService)
        service.page = type("Page", (), {"url": "about:blank", "goto": lambda self, url, **kwargs: _set_url(self, url)})()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "disappearing.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "target-disappears"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service._probe_observation_auth_locked = lambda: _resolved(True)
        source = Game("source", "https://example.test/source", "", "700", "Завершена", 0)
        service.store.upsert(source, [
            {"position": 1, "rank": "A", "suit": "spades"},
            {"position": 2, "rank": "K", "suit": "spades"},
        ], "CONFIRMED", "source", "CARDS_CONFIRMED", finished_confirmed=True,
           final_snapshot_reverified=True, player_hand_final=True)
        target = Game("target-disappears", "https://example.test/target-disappears", "", "701", "Идёт", 1)
        service.store.link_active_transition("source", target)
        cards = [
            {"position": 1, "rank": "A", "suit": "diamonds"},
            {"position": 2, "rank": "10", "suit": "clubs"},
        ]
        reads = iter([
            _snapshot(cards, finished=False),
            _snapshot([], finished=True),
            _snapshot([], finished=True),
        ])

        async def inspect(_player, _banker):
            return next(reads)

        service._inspect_game_dom = inspect
        await service._scan_selected_game(target, [], set(), {})

        record = service.store.get(target.game_id)
        assert record["cards"] == cards
        assert record["player_hand_final"] is False
        assert record["prediction_result"] == "UNKNOWN"
        assert record["prediction_result"] != "LOSE"

    def _snapshot(cards, *, finished):
        return {"url": "about:blank", "counts": {"player_fields": int(bool(cards)),
                "player_cards_containers": int(bool(cards)), "player_card_boxes": len(cards)},
            "cards": cards, "pending_card_slots": 0, "finished": finished,
            "finish_flag_text": "Игра завершена" if finished else "", "status_text": "",
            "cause": "fixture", "dom_fragment": ""}

    async def _set_url(page, url):
        page.url = url

    async def _resolved(value):
        return value

    asyncio.run(run())


def test_timeout_does_not_finalize_or_navigate_away_from_unconfirmed_two_card_hand(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_page_wait_seconds=30, game_finish_timeout_seconds=1,
            player_hand_timeout_seconds=1, card_poll_seconds=0.1, incomplete_retry_seconds=5,
        ))
        service = object.__new__(BrowserService)
        service.page = type("Page", (), {"url": "about:blank", "goto": lambda self, url, **kwargs: _set_url(self, url)})()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "two-pending.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "two-pending"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service._probe_observation_auth_locked = lambda: _resolved(True)
        cards = [
            {"position": 1, "rank": "A", "suit": "diamonds"},
            {"position": 2, "rank": "10", "suit": "clubs"},
        ]

        async def inspect(_player, _banker):
            return {"url": "about:blank", "counts": {"player_fields": 1, "player_cards_containers": 1,
                "player_card_boxes": 2}, "cards": cards, "pending_card_slots": 0,
                "finished": False, "status_text": "", "cause": "fixture", "dom_fragment": ""}

        service._inspect_game_dom = inspect
        game = Game("two-pending", "https://example.test/two-pending", "", "654", "Идёт", 0)
        processed = set()
        task = asyncio.create_task(service._scan_selected_game(game, [], processed, {}))
        try:
            async def timeout_diagnostic_seen():
                while not any("тайм-аут не завершает" in event["message"] for event in service.events):
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(timeout_diagnostic_seen(), timeout=3)
            saved = service.store.get("two-pending")
            assert saved["player_hand_final"] is False
            assert saved["finished_confirmed"] is False
            assert saved["calculation_status"] != "complete"
            assert service.observation_status == "WAITING_FOR_FINISH"
            assert processed == set()
            assert service.page.url.endswith("/two-pending")
            assert service.store.history() == []
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _set_url(page, url):
        page.url = url

    async def _resolved(value):
        return value

    asyncio.run(run())


def test_explicit_end_does_not_advance_until_player_hand_is_complete(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, game_finish_timeout_seconds=1, player_hand_timeout_seconds=1,
            card_poll_seconds=0.1,
        ))
        service = object.__new__(BrowserService)
        service.page = type("Page", (), {"url": "about:blank", "goto": lambda self, url, **kwargs: _set_url(self, url)})()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "finished-incomplete.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "finished-incomplete"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service._probe_observation_auth_locked = lambda: _resolved(True)
        partial = [{"position": 1, "rank": "A", "suit": "diamonds"}]

        async def inspect(_player, _banker):
            return {
                "url": "about:blank",
                "counts": {"player_fields": 1, "player_cards_containers": 1, "player_card_boxes": 3},
                "cards": partial, "pending_card_slots": 1, "finished": True,
                "finish_flag_text": "РРіСЂР° Р·Р°РІРµСЂС€РµРЅР°", "status_text": "РРіСЂР° Р·Р°РІРµСЂС€РµРЅР°",
                "cause": "one of three Player slots is still unreadable", "dom_fragment": "",
            }

        service._inspect_game_dom = inspect
        game = Game("finished-incomplete", "https://example.test/finished-incomplete", "", "655", "", 0)
        processed = set()
        task = asyncio.create_task(service._scan_selected_game(game, [], processed, {}))
        try:
            async def terminal_seen_but_unconfirmed():
                while True:
                    saved = service.store.get("finished-incomplete")
                    if (service.observation_status == "GAME_FINISHED" and saved
                            and saved["finished_confirmed"]):
                        return
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(terminal_seen_but_unconfirmed(), timeout=3)
            assert not task.done()
            assert processed == set()
            assert service.page.url.endswith("/finished-incomplete")
            saved = service.store.get("finished-incomplete")
            assert saved["finished_confirmed"] is True
            assert saved["player_hand_final"] is False
            assert saved["cards"] == partial
            assert saved["calculation_status"] != "complete"
            assert service.current_observation["lifecycle_state"] == "OBSERVING"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _set_url(page, url):
        page.url = url

    async def _resolved(value):
        return value

    asyncio.run(run())
