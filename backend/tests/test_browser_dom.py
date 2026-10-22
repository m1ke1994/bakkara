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
        assert lifecycle == ["OPENING", "OBSERVING", "CARDS_COMPLETE", "FINALIZED"]
        assert service.current_observation["lifecycle_state"] == "FINALIZED"
        assert not any("PREDICTION_RESULT" in event["message"] for event in service.events)

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


def test_baccarat_round_timer_is_read_from_visible_dom_and_not_status():
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content("""
                  <style>.ui-game-timer__label { display:block; }</style>
                  <span class="ui-caption ui-game-timer__label">00:17</span>
                  <div class="baccarat-player baccarat__player">
                    <span class="baccarat-player__name">Игрок</span>
                    <ul class="baccarat-player__cards">
                      <li class="baccarat-player__card-box suit-clubs">
                        <span class="baccarat-card__rank">10</span></li>
                      <li class="baccarat-player__card-box suit-spades">
                        <span class="baccarat-card__rank">2</span></li>
                    </ul>
                  </div>
                """)
                result = await service._inspect_game_dom("Игрок", "Банкир")
                assert result["round_timer_text"] == "00:17"
                assert result["finished"] is False
                await page.locator(".ui-game-timer__label").evaluate(
                    "node => node.textContent = '00:00'"
                )
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["round_timer_text"] == "00:00"
            finally:
                await browser.close()
    asyncio.run(run())


def test_two_card_round_countdown_zero_is_verified_and_saved(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, card_poll_seconds=0.1,
            scan_incomplete_timeout_seconds=8,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **_kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "two-timer.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "two-timer"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        cards = [
            {"position": 1, "rank": "10", "suit": "clubs"},
            {"position": 2, "rank": "2", "suit": "spades"},
        ]
        reads = 0
        async def inspect(_player, _banker):
            nonlocal reads
            reads += 1
            timer = "00:03" if reads == 1 else "00:02" if reads == 2 else "00:00"
            return {
                "cards": cards, "round_timer_text": timer,
                "pending_card_slots": 0,
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 2},
                "finished": False, "status_text": "", "cause": "fixture",
            }
        service._inspect_game_dom = inspect
        game = Game("two-timer", "https://example.test/two-timer", "",
                    "915", "Идёт", 0)
        processed = set()
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, {}), timeout=6
        )
        assert processed == {"two-timer"}
        saved = service.store.get("two-timer")
        assert saved["cards"] == cards
        assert saved["player_hand_final"] is True
        assert saved["final_snapshot_reverified"] is True
        assert saved["finished_confirmed"] is False
        assert saved["calculation_status"] == "complete"
        assert reads >= 4
        assert "таймер" in saved["reason"]
    asyncio.run(run())


def test_delayed_third_card_after_zero_prevents_early_two_card_finish(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, card_poll_seconds=0.1,
            scan_incomplete_timeout_seconds=8,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **_kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "third-timer.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "third-timer"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        two = [
            {"position": 1, "rank": "10", "suit": "clubs"},
            {"position": 2, "rank": "2", "suit": "spades"},
        ]
        third = {"position": 3, "rank": "A", "suit": "hearts"}
        reads = 0
        async def inspect(_player, _banker):
            nonlocal reads
            reads += 1
            timer = "00:02" if reads == 1 else "00:00"
            cards = two if reads < 6 else two + [third]
            return {"cards": cards, "round_timer_text": timer,
                    "pending_card_slots": 0,
                    "counts": {"player_fields": 1, "player_cards_containers": 1,
                               "player_card_boxes": len(cards)},
                    "finished": False, "status_text": "", "cause": "fixture"}
        service._inspect_game_dom = inspect
        game = Game("third-timer", "https://example.test/third-timer", "",
                    "916", "Идёт", 0)
        processed = set()
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, {}), timeout=6
        )
        assert processed == {"third-timer"}
        saved = service.store.get("third-timer")
        assert saved["cards"] == two + [third]
        assert saved["calculation_status"] == "complete"
    asyncio.run(run())


def test_visible_baccarat_victory_caption_is_terminal_only_for_player_widget():
    """The live skin uses a result caption instead of 'Игра завершена'."""
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content("""
                  <span class="ui-caption ui-game-timer__label">00:17</span>
                  <div class="baccarat-player baccarat__player">
                    <span class="baccarat-player__name">Игрок</span>
                    <ul class="baccarat-player__cards">
                      <li class="baccarat-player__card-box suit-clubs">
                        <span class="baccarat-card__rank">Q</span></li>
                      <li class="baccarat-player__card-box suit-hearts">
                        <span class="baccarat-card__rank">10</span></li>
                    </ul>
                  </div>
                  <span id="outcome" class="ui-caption ui-caption--weight-700
                    ui-caption--uppercase baccarat-panel__caption">Победа</span>
                """)
                result = await service._inspect_game_dom("Игрок", "Банкир")
                assert result["finished"] is True
                assert result["baccarat_result_visible"] is True
                assert result["finish_flag_text"] == "Победа"
                assert result["round_timer_text"] == "00:17"
                assert len(result["cards"]) == 2
                await page.locator("#outcome").evaluate("el => el.textContent = 'Ничья'")
                tie = await service._inspect_game_dom("Игрок", "Банкир")
                assert tie["finished"] is True
                assert tie["finish_flag_text"] == "Ничья"
                # Reset the test latch to verify that unrelated text alone
                # is never counted as an outcome.
                await page.evaluate("window.__bakkaraOutcomeLatch?.observer?.disconnect(); delete window.__bakkaraOutcomeLatch")
                await page.locator("#outcome").evaluate("el => el.textContent = 'Ход: Игрок'")
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["finished"] is False
                await page.locator("#outcome").evaluate("el => { el.textContent='Победа'; el.style.display='none'; }")
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["finished"] is False
                await page.locator("#outcome").evaluate("el => { el.style.display=''; el.className='random-market-caption'; }")
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["finished"] is False
                await page.locator("#outcome").evaluate("el => el.className='baccarat-panel__caption'")
                await page.locator(".baccarat-player").evaluate("el => el.remove()")
                # Outcome caption is still explicit terminal evidence after
                # the bookmaker removes the Player widget.
                assert (await service._inspect_game_dom("Игрок", "Банкир"))["finished"] is True
            finally:
                await browser.close()
    asyncio.run(run())


def test_victory_with_two_cards_finalizes_without_waiting_45_seconds(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings,
            card_poll_seconds=0.1,
            scan_incomplete_timeout_seconds=45,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "victory-final.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "victory-two"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        cards = [
            {"position": 1, "rank": "Q", "suit": "clubs"},
            {"position": 2, "rank": "10", "suit": "hearts"},
        ]
        calls = 0
        async def inspect(_player, _banker):
            nonlocal calls
            calls += 1
            final = calls >= 2
            return {
                "cards": cards,
                "round_timer_text": "00:17",
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 2},
                "pending_card_slots": 0,
                "finished": final,
                "baccarat_result_visible": final,
                "finish_flag_text": "Победа" if final else "",
                "status_text": "", "cause": "fixture",
            }
        service._inspect_game_dom = inspect
        game = Game("victory-two", "https://example.test/victory-two",
                    "", "1017", "Идёт", 0)
        processed = set()
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, {}),
            timeout=5,
        )
        assert processed == {"victory-two"}
        saved = service.store.get("victory-two")
        assert saved["cards"] == cards
        assert saved["player_hand_final"] is True
        assert saved["final_snapshot_reverified"] is True
        assert saved["calculation_status"] == "complete"
        assert saved["finished_confirmed"] is True
        assert "Победа" in saved["reason"]
        assert service.observation_status == "SAVED"
        assert any("обнаружено «Победа»" in e["message"] for e in service.events)
        assert calls >= 3
    asyncio.run(run())


def test_victory_does_not_finalize_two_cards_when_third_box_is_pending(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings,
            card_poll_seconds=0.1,
            scan_incomplete_timeout_seconds=6,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "victory-three.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "victory-three"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        cards = [
            {"position": 1, "rank": "Q", "suit": "clubs"},
            {"position": 2, "rank": "10", "suit": "hearts"},
        ]
        third = {"position": 3, "rank": "A", "suit": "diamonds"}
        reads = 0
        async def inspect(_player, _banker):
            nonlocal reads
            reads += 1
            complete = reads >= 4
            return {
                "cards": cards + [third] if complete else cards,
                "round_timer_text": "00:00",
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 3},
                "pending_card_slots": 0 if complete else 1,
                "finished": True,
                "baccarat_result_visible": True,
                "finish_flag_text": "Победа",
                "status_text": "", "cause": "fixture",
            }
        service._inspect_game_dom = inspect
        game = Game("victory-three", "https://example.test/victory-three",
                    "", "1018", "Идёт", 0)
        processed = set()
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, {}), timeout=5
        )
        saved = service.store.get("victory-three")
        assert processed == {"victory-three"}
        assert saved["cards"] == cards + [third]
        assert saved["calculation_status"] == "complete"
    asyncio.run(run())


def test_victory_two_cards_with_empty_third_placeholder_is_final(tmp_path, monkeypatch):
    """Live screenshot: two Player cards + one empty decorative third slot."""
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings,
            card_poll_seconds=0.1, scan_incomplete_timeout_seconds=45,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "victory-placeholder.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "victory-placeholder"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        cards = [
            {"position": 1, "rank": "Q", "suit": "clubs"},
            {"position": 2, "rank": "10", "suit": "hearts"},
        ]
        reads = 0
        async def inspect(_player, _banker):
            nonlocal reads
            reads += 1
            return {
                "cards": cards,
                "round_timer_text": "00:01",
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 3},
                "pending_card_slots": 1,  # Third box remains blank.
                "finished": True,
                "baccarat_result_visible": True,
                "finish_flag_text": "Победа",
                "status_text": "", "cause": "fixture",
            }
        service._inspect_game_dom = inspect
        game = Game("victory-placeholder", "https://example.test/victory-placeholder",
                    "", "1017", "Идёт", 0)
        processed = set()
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, {}), timeout=5,
        )
        assert processed == {"victory-placeholder"}
        saved = service.store.get("victory-placeholder")
        assert saved["cards"] == cards
        assert saved["player_hand_final"] is True
        assert saved["calculation_status"] == "complete"
        assert saved["final_snapshot_reverified"] is True
        assert service.current_observation["raw_pending_card_slots"] == 1
        assert service.current_observation["pending_card_slots"] == 0
        assert service.observation_status == "SAVED"
        assert reads >= 3
    asyncio.run(run())


def test_countdown_aware_timeout_covers_prestart_and_final_cards():
    from app.browser import scan_timeout_budget
    from app.observation import Game
    scheduled = Game("1028", "https://example.test/1028", "00:28", "1028",
                     "", 1, player_score="0", banker_score="0")
    assert scan_timeout_budget(scheduled, 45) == 83
    scheduled.time = "00:27"
    assert scan_timeout_budget(scheduled, 45) == 82
    scheduled.time = "03:27"
    assert scan_timeout_budget(scheduled, 45) == 150
    # Production #468: countdown disappeared in the list. A 45s timeout
    # abandoned a live game with TWO real cards and 4 seconds remaining.
    scheduled.time = "not available"
    assert scan_timeout_budget(scheduled, 45) == 105
    scheduled.status = "Завершена"
    assert scan_timeout_budget(scheduled, 45) == 45
    # Explicit tiny budgets in isolated tests remain tiny.
    scheduled.status = "Идёт"
    assert scan_timeout_budget(scheduled, 5) == 5


def test_expired_event_screen_detected_without_player_cards():
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content("""
                    <div class="market-grid__game-over-panel">
                      <strong>Событие завершено</strong>
                      <span>Выбранная игра уже состоялась</span>
                    </div>
                """)
                out = await service._inspect_game_dom("Игрок", "Банкир")
                assert out["expired_screen"] is True
                assert out["cards"] == []
                assert out["finished"] is False
            finally:
                await browser.close()
    asyncio.run(run())


def test_expired_screen_does_not_block_observer_45_seconds(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, card_poll_seconds=0.1,
            scan_incomplete_timeout_seconds=45,
            finished_recovery_timeout_seconds=12,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **_kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "expired-screen.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "expired"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        async def inspect(_player, _banker):
            return {"cards": [], "counts": {"player_fields": 0,
                    "player_cards_containers": 0, "player_card_boxes": 0},
                    "pending_card_slots": 0,
                    "expired_screen": True,
                    "round_timer_text": "",
                    "finished": False,
                    "status_text": "", "cause": "event finished"}
        service._inspect_game_dom = inspect
        game = Game("expired", "https://example.test/expired", "", "1027",
                    "Завершена", 0, finished=True)
        processed, retry = set(), {}
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, retry), timeout=7,
        )
        assert processed == set()
        assert service.observation_status == "INCOMPLETE"
        assert "expired" in retry
        assert "Событие завершено" in service.observation_diagnostic
        assert service.store.get("expired")["scan_result"] == "SCAN_TIMEOUT"
    asyncio.run(run())


def test_transient_victory_badge_survives_reread_and_two_cached_cards_finish(tmp_path, monkeypatch):
    """Real failure: result appears for one frame and Player DOM vanishes."""
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, card_poll_seconds=0.1,
            scan_incomplete_timeout_seconds=45,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **_kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "transient-victory.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "transient-405"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        cards = [
            {"position": 1, "rank": "K", "suit": "clubs"},
            {"position": 2, "rank": "7", "suit": "diamonds"},
        ]
        count = 0
        async def inspect(_player, _banker):
            nonlocal count
            count += 1
            # First 2 polls: Player cards, no result. Poll 3: a visible
            # victory banner, poll 4: banner gone (the scanner rereads).
            # All subsequent polls: Baccarat player widget removed.
            if count <= 4:
                return {
                    "cards": cards, "round_timer_text": "00:05",
                    "counts": {"player_fields": 1, "player_cards_containers": 1,
                               "player_card_boxes": 3},
                    "pending_card_slots": 1,
                    "finished": count == 3,
                    "baccarat_result_visible": count == 3,
                    "finish_flag_text": "Победа" if count == 3 else "",
                    "status_text": "", "cause": "transient banner",
                }
            return {
                "cards": [], "round_timer_text": "",
                "counts": {"player_fields": 0, "player_cards_containers": 0,
                           "player_card_boxes": 0},
                "pending_card_slots": 0, "finished": False,
                "baccarat_result_visible": False, "finish_flag_text": "",
                "status_text": "", "cause": "result DOM vanished",
            }
        service._inspect_game_dom = inspect
        game = Game("transient-405", "https://example.test/transient-405",
                    "", "405", "Идёт", 0)
        processed, retry = set(), {}
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, retry), timeout=4
        )
        saved = service.store.get("transient-405")
        assert processed == {"transient-405"}
        assert saved["cards"] == cards
        assert saved["calculation_status"] == "complete"
        assert saved["player_hand_final"] is True
        assert saved["finished_confirmed"] is True
        assert saved["final_snapshot_reverified"] is True
        assert saved["scan_result"] == "CARDS_CONFIRMED"
        assert service.observation_status == "SAVED"
        assert count >= 5
    asyncio.run(run())


def test_dom_mutation_latches_transient_result_between_browser_polls():
    """The visible Baccarat outcome may disappear before the next read."""
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content("""
                  <div class="baccarat-player baccarat__player">
                    <span class="baccarat-player__name">Игрок</span>
                    <ul class="baccarat-player__cards">
                      <li class="baccarat-player__card-box suit-clubs">
                        <span class="baccarat-card__rank">Q</span></li>
                      <li class="baccarat-player__card-box suit-hearts">
                        <span class="baccarat-card__rank">10</span></li>
                    </ul>
                  </div>
                  <span id="outcome" class="baccarat-panel__caption">Ход: Игрок</span>
                """)
                assert not (await service._inspect_game_dom("Игрок", "Банкир"))["finished"]
                # Transition happens between calls to _inspect_game_dom,
                # but is a separate DOM task, observable by MutationObserver.
                await page.locator("#outcome").evaluate("el => el.textContent = 'Победа'")
                await page.locator("#outcome").evaluate("el => el.remove()")
                outcome = await service._inspect_game_dom("Игрок", "Банкир")
                assert outcome["finished"] is True
                assert outcome["baccarat_result_visible"] is True
                assert outcome["finish_flag_text"] == "Победа"
                assert [c["rank"] for c in outcome["cards"]] == ["Q", "10"]
            finally:
                await browser.close()
    asyncio.run(run())


def test_visible_result_without_readable_cards_does_not_hold_next_round_one_minute(tmp_path, monkeypatch):
    """An incomplete final DOM must be released promptly for the next hand."""
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings,
            card_poll_seconds=0.1, scan_incomplete_timeout_seconds=90,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **_kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "terminal-incomplete.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "result-incomplete"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        async def inspect(*_args):
            return {
                "cards": [{"position": 1, "rank": "9", "suit": "spades"}],
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 3},
                "pending_card_slots": 2, "finished": True,
                "baccarat_result_visible": True,
                "finish_flag_text": "Ничья", "status_text": "",
                "cause": "result shown but card positions missing",
            }
        service._inspect_game_dom = inspect
        game = Game("result-incomplete", "https://example.test/result-incomplete",
                    "", "431", "Идёт", 0)
        processed, retry = set(), {}
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, retry), timeout=6.5
        )
        assert not processed
        assert service.observation_status == "INCOMPLETE"
        assert "result-incomplete" in retry
        record = service.store.get("result-incomplete")
        assert record["cards"][0]["rank"] == "9"
        assert record["player_hand_final"] is False
        assert record["scan_result"] == "SCAN_TIMEOUT"
    asyncio.run(run())


def test_real_winner_caption_banker_and_two_player_cards_with_empty_third_slot():
    """Screenshot #453: ПОБЕДА: Банкир; Player cards A♥ 3♣; 3rd box empty."""
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content("""
                  <div class="baccarat-player baccarat__player">
                    <span class="baccarat-player__name">Игрок</span>
                    <ul class="baccarat-player__cards">
                      <li class="baccarat-player__card-box">
                        <div class="card-value">A</div>
                        <svg><use href="#cardSuit-hearts"></use></svg>
                      </li>
                      <li class="baccarat-player__card-box">
                        <strong>3</strong><span>♣</span>
                      </li>
                      <li class="baccarat-player__card-box">
                        <span class="baccarat-card__back"></span>
                      </li>
                    </ul>
                  </div>
                  <span class="baccarat-panel__caption">ПОБЕДА: Банкир</span>
                """)
                result = await service._inspect_game_dom("Игрок", "Банкир")
                assert result["baccarat_result_visible"]
                assert result["finished"]
                assert result["finish_flag_text"] == "ПОБЕДА: Банкир"
                assert result["cards"] == [
                    {"position": 1, "rank": "A", "suit": "hearts"},
                    {"position": 2, "rank": "3", "suit": "clubs"},
                ]
                assert result["counts"]["player_card_boxes"] == 3
                assert result["pending_card_slots"] == 1
            finally:
                await browser.close()
    asyncio.run(run())


def test_winner_label_does_not_match_non_result_text():
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content(
                    '<div class="baccarat-panel__caption">Победа ожидается</div>'
                )
                result = await service._inspect_game_dom("Игрок", "Банкир")
                assert not result["finished"]
                assert not result["baccarat_result_visible"]
            finally:
                await browser.close()
    asyncio.run(run())


def test_explicit_finished_with_two_player_cards_and_third_empty_slot(tmp_path, monkeypatch):
    """A real end caption counts even when winner-specific text is hidden."""
    async def run():
        monkeypatch.setattr(browser_module, "settings", replace(
            browser_module.settings, card_poll_seconds=0.1,
            scan_incomplete_timeout_seconds=6,
        ))
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **_kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "finish-two-placeholder.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = {"game_id": "end-two"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        async def authorized():
            return True
        service._probe_observation_auth_locked = authorized
        cards = [
            {"position": 1, "rank": "6", "suit": "diamonds"},
            {"position": 2, "rank": "Q", "suit": "diamonds"},
        ]
        async def inspect(*_):
            return {
                "cards": cards, "round_timer_text": "",
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 3},
                "pending_card_slots": 1,
                "finished": True, "baccarat_result_visible": False,
                "finish_flag_text": "Игра завершена", "status_text": "",
                "cause": "explicit final result, two real cards, empty third",
            }
        service._inspect_game_dom = inspect
        game = Game("end-two", "https://example.test/end-two", "",
                    "468", "Идёт", 0)
        processed, retry = set(), {}
        await asyncio.wait_for(service._scan_selected_game(
            game, [], processed, retry), timeout=4)
        assert processed == {"end-two"}
        record = service.store.get("end-two")
        assert record["player_hand_final"] is True
        assert record["final_snapshot_reverified"] is True
        assert record["cards"] == cards
        assert record["scan_result"] == "CARDS_CONFIRMED"
    asyncio.run(run())
