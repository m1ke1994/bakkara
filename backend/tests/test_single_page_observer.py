"""Prevent the historical-recovery worker from opening a second live tab."""
import asyncio
from collections import deque

from app.browser import BrowserService, keep_single_browser_page


class DummyPage:
    def __init__(self, url):
        self.url = url
        self.closed = False
    def is_closed(self):
        return self.closed
    async def close(self):
        self.closed = True


class DummyContext:
    def __init__(self, pages):
        self.pages = pages
        self.created = []
    async def new_page(self):
        page = DummyPage("about:blank")
        self.pages.append(page)
        self.created.append(page)
        return page


def test_single_tab_reuses_correct_persisted_page_and_closes_rest():
    async def run():
        target = "https://example.test/ru/live/baccarat/tournament"
        spare, live, extra = (DummyPage("about:blank"), DummyPage(target),
                              DummyPage("https://example.test/baccarat/old"))
        context = DummyContext([spare, live, extra])
        chosen = await keep_single_browser_page(context, target)
        assert chosen is live
        assert spare.closed and extra.closed and not live.closed
        assert context.created == []
    asyncio.run(run())


def test_empty_context_creates_only_one_page():
    async def run():
        context = DummyContext([])
        page = await keep_single_browser_page(context, "https://example.test/baccarat")
        assert context.created == [page] and not page.closed
    asyncio.run(run())


def test_live_observation_never_spawns_history_repair_worker():
    async def run():
        service = object.__new__(BrowserService)
        seen = []
        async def live():
            seen.append("live")
        async def forbidden():
            raise AssertionError("Extra page must not open during scanning")
        service._observe_live = live
        service._recover_history = forbidden
        await service._observe()
        assert seen == ["live"]
    asyncio.run(run())


def test_unexpected_page_closes_only_when_scanning_active():
    async def run():
        service = object.__new__(BrowserService)
        main = DummyPage("https://example.test/baccarat/current")
        extra = DummyPage("https://example.test/baccarat/extra")
        service.page = main
        service.events = deque(maxlen=10)
        service.state_version = 0
        service.observation_task = None
        service._on_context_page(extra)
        await asyncio.sleep(0)
        assert not extra.closed
        active = asyncio.create_task(asyncio.Event().wait())
        service.observation_task = active
        try:
            service._on_context_page(main)
            service._on_context_page(extra)
            for _ in range(20):
                if extra.closed:
                    break
                await asyncio.sleep(.01)
            assert extra.closed and not main.closed
            assert "лишняя вкладка" in service.events[0]["message"]
        finally:
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
    asyncio.run(run())


def test_observer_closes_tabs_left_open_before_scan():
    async def run():
        service = object.__new__(BrowserService)
        main = DummyPage("https://example.test/baccarat/current")
        extra = DummyPage("https://example.test/baccarat/old")
        service.page = main
        service.context = DummyContext([main, extra])
        service.events = deque(maxlen=10)
        service.state_version = 0
        await service._enforce_single_observer_page()
        assert extra.closed
        assert not main.closed
        assert service.context.created == []
    asyncio.run(run())
