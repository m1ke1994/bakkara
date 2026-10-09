import asyncio
import logging
import re
from collections import deque
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from .config import settings
from .observation import Game, ObservationStore, choose_next_game

log = logging.getLogger(__name__)


class BrowserService:
    """Owns one visible, persistent Playwright Chromium context."""

    def __init__(self) -> None:
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.status = "STOPPED"
        self.authorization = "UNKNOWN"
        self.diagnostic = "Browser has not been started."
        self.events: deque[dict[str, str]] = deque(maxlen=100)
        self.lock = asyncio.Lock()
        self.monitor_task: asyncio.Task[None] | None = None
        self.observation_task: asyncio.Task[None] | None = None
        self.observation_status = "STOPPED"
        self.current_observation: dict[str, Any] | None = None
        self.observation_diagnostic = "Observation has not started."
        self.store = ObservationStore(settings.observation_db)

    def event(self, level: str, message: str) -> None:
        # Never include page content, credentials, tokens, or cookies here.
        item = {"at": datetime.now(timezone.utc).isoformat(), "level": level, "message": message}
        self.events.appendleft(item)
        getattr(log, level.lower(), log.info)(message)

    async def start(self) -> None:
        async with self.lock:
            if self.context is not None:
                self.event("INFO", "Browser is already running.")
                return
            if not settings.target_url:
                self.status = "ERROR"
                self.diagnostic = "TARGET_URL is missing in .env."
                self.event("ERROR", self.diagnostic)
                return
            self.status = "STARTING"
            self.authorization = "UNKNOWN"
            self.event("INFO", "Starting visible Chromium with Bakkara's separate profile.")
            try:
                settings.profile_dir.mkdir(parents=True, exist_ok=True)
                self.playwright = await async_playwright().start()
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=str(settings.profile_dir), headless=False, viewport=None
                )
                self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
                await self.page.goto(settings.target_url, wait_until="domcontentloaded", timeout=45_000)
                self.status = "WAITING_AUTH"
                self.authorization = "UNCONFIRMED"
                self.diagnostic = "Target opened. Sign in manually in Chromium."
                self.event("INFO", "Target page opened; waiting for manual login.")
                self.monitor_task = asyncio.create_task(self._monitor_auth())
            except Exception as exc:
                await self._close_locked()
                self.status = "ERROR"
                self.diagnostic = f"Could not start browser: {type(exc).__name__}."
                self.event("ERROR", self.diagnostic)

    async def stop(self) -> None:
        async with self.lock:
            await self._close_locked()
            self.status = "STOPPED"
            self.authorization = "UNKNOWN"
            self.diagnostic = "Browser stopped."
            self.event("INFO", "Browser stopped.")

    async def start_observation(self) -> None:
        async with self.lock:
            if self.authorization != "AUTHORIZED" or self.page is None:
                self.observation_status = "WAITING_AUTH"
                self.event("WARNING", "Observation requires a manually authorized browser session.")
                return
            if self.observation_task and not self.observation_task.done():
                return
            self.observation_status = "WAITING_GAME"
            self.observation_diagnostic = "Waiting for a DOM-confirmed active Baccarat game."
            self.observation_task = asyncio.create_task(self._observe())
            self.event("INFO", "Read-only observation started.")

    async def stop_observation(self) -> None:
        task, self.observation_task = self.observation_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
        self.observation_status = "STOPPED"
        self.current_observation = None
        self.event("INFO", "Observation stopped.")

    async def _close_locked(self) -> None:
        await self.stop_observation()
        task, self.monitor_task = self.monitor_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        self.context = None
        self.page = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
        self.playwright = None

    async def _observe(self) -> None:
        """Navigate only between Baccarat list/game pages and read exposed DOM."""
        handled: set[str] = set()
        try:
            while self.context is not None and self.page is not None:
                if self.authorization != "AUTHORIZED":
                    self.observation_status = "WAITING_AUTH"
                    await asyncio.sleep(1)
                    continue
                self.observation_status = "WAITING_GAME"
                await self.page.goto(settings.game_list_url, wait_until="domcontentloaded", timeout=45_000)
                await self.page.locator("li.dashboard-champ-body").wait_for(timeout=15_000)
                raw_games = await self.page.locator("li.dashboard-game").evaluate_all("""nodes => nodes.map((n, position) => {
                  const text = s => n.querySelector(s)?.innerText?.trim() || '';
                  const href = n.querySelector('.dashboard-game-block__link')?.href || '';
                  const match = href.match(/\\/(\\d+)(?:-[^/?#]+)?(?:[?#].*)?$/);
                  return {game_id: match?.[1] || '', url: href, time: text('.dashboard-game-info__time'), round_number: text('.dashboard-game-info__period'), status: text('.dashboard-game-info__additional-info') || text('.dashboard-game-info__time'), position, active: Boolean(selector) && n.matches(selector)};
                })""", settings.game_active_selector)
                games = [Game(**item) for item in raw_games if item["game_id"]]
                if not settings.game_active_selector:
                    self.observation_diagnostic = f"Read {len(games)} Baccarat game rows, but GAME_ACTIVE_SELECTOR is not configured; no game was opened."
                    await asyncio.sleep(max(settings.observation_poll_seconds, 0.5))
                    continue
                game = choose_next_game(games, handled)
                if not game:
                    self.observation_diagnostic = "No DOM-confirmed active Baccarat game is available."
                    await asyncio.sleep(max(settings.observation_poll_seconds, 0.5)); continue
                handled.add(game.game_id)
                self.observation_status = "GAME_OPENED"
                await self.page.goto(game.url, wait_until="domcontentloaded", timeout=45_000)
                if self.page.url.rstrip("/") != game.url.rstrip("/"):
                    self.observation_diagnostic = "Selected game redirected before its page loaded."
                    continue
                await self.page.locator(".baccarat-player.baccarat__player").wait_for(timeout=15_000)
                self.current_observation = {"game_id": game.game_id, "url": game.url, "round_number": game.round_number, "cards": []}
                # Do not leave merely because two cards are present or an arbitrary
                # timeout elapsed.  A live-verified completion selector is required.
                while self.context is not None and self.page is not None:
                    if self.page.is_closed(): return
                    cards = await self.page.locator(".baccarat-player.baccarat__player .baccarat-player__card-box").evaluate_all("""nodes => nodes.map(n => {
                      const rank = n.querySelector('.baccarat-card__rank')?.innerText?.trim() || '';
                      const values = [n.className, ...[...n.querySelectorAll('svg')].map(s => s.getAttribute('data-v-ico') || '')].join(' ').toLowerCase();
                      const suit = ['hearts','diamonds','clubs','spades'].find(x => values.includes('suit-' + x) || values.includes('cardsuits|' + x)) || '';
                      return {rank: rank || null, suit: suit || null};
                    })""")
                    if cards:
                        self.current_observation["cards"] = cards
                        self.observation_status = "THREE_CARDS_VISIBLE" if len(cards) >= 3 else "TWO_CARDS_VISIBLE"
                        self.store.upsert(game, cards, "PARTIAL", "Round-end DOM marker is not yet configured.")
                    if settings.round_finished_selector and await self.page.locator(settings.round_finished_selector).count():
                        self.observation_status = "ROUND_FINISHED"
                        self.observation_diagnostic = "Round finished by the configured DOM marker."
                        self.store.upsert(game, cards, "CONFIRMED")
                        break
                    if not settings.round_finished_selector:
                        self.observation_diagnostic = "ROUND_FINISHED_SELECTOR is not configured; continuing to scan the current game."
                    await asyncio.sleep(max(settings.observation_poll_seconds, 0.5))
                self.observation_status = "RETURNING_TO_LIST"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.observation_status = "ERROR"
            self.event("WARNING", f"Observation paused: {type(exc).__name__}.")

    async def _monitor_auth(self) -> None:
        while self.context is not None and self.page is not None:
            try:
                authorized, diagnostic = await self._is_authorized()
                previous = self.authorization
                self.authorization = "AUTHORIZED" if authorized else "UNCONFIRMED"
                self.status = "AUTHORIZED" if authorized else "WAITING_AUTH"
                self.diagnostic = diagnostic
                if previous == "AUTHORIZED" and not authorized:
                    self.status = "AUTH_LOST"
                    self.event("WARNING", "Authorization is no longer confirmed; waiting for login.")
                elif previous != self.authorization:
                    self.event("INFO", "Authorization confirmed." if authorized else diagnostic)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = "WAITING_AUTH"
                self.authorization = "UNCONFIRMED"
                self.diagnostic = "Authorization check is temporarily unavailable."
            await asyncio.sleep(max(settings.auth_poll_seconds, 0.5))

    async def _is_authorized(self) -> tuple[bool, str]:
        if self.page is None or self.page.is_closed():
            return False, "Browser page is unavailable."
        # Independent copy of new_bot's manual-login check. Its authenticated
        # balance header renders an exact RUB currency marker; the selector
        # fallbacks support the current and legacy site layouts.
        selectors = tuple(dict.fromkeys(
            selector for selector in (settings.currency_selector, *settings.currency_selector_fallbacks)
            if selector
        ))
        if not selectors or not settings.currency_text:
            return False, "Authorization marker is not configured."
        currency_pattern = re.compile(
            rf"(?<![A-Za-zА-Яа-я0-9]){re.escape(settings.currency_text)}(?![A-Za-zА-Яа-я0-9])"
        )
        for selector in selectors:
            candidates = self.page.locator(selector)
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                try:
                    text = await candidate.inner_text()
                except Exception:
                    text = await candidate.text_content()
                if text and currency_pattern.search(text.strip()):
                    return True, "Authorization confirmed by the account-balance currency indicator."
                try:
                    if await candidate.get_by_text(settings.currency_text, exact=True).count():
                        return True, "Authorization confirmed by the account-balance currency indicator."
                except Exception:
                    pass
        return False, "Waiting for manual login."

    def snapshot(self) -> dict[str, Any]:
        return {"backend": "OK", "browser": self.status, "authorization": self.authorization,
                "diagnostic": self.diagnostic, "events": list(self.events),
                "observation": {"status": self.observation_status, "current": self.current_observation,
                                "diagnostic": self.observation_diagnostic, "history": self.store.history()}}


browser_service = BrowserService()
