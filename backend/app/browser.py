import asyncio
import json
import logging
import re
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError, async_playwright

from .config import settings
from .auth_state import authorization_allows_observation, classify_auth_probe
from .observation import (
    Game,
    ObservationStore,
    choose_next_game,
    classify_game_status,
    classify_game,
    is_finished_status,
    is_same_upcoming_game,
    classify_player_scan,
    merge_card_snapshots,
    parse_game_list_html,
)

log = logging.getLogger(__name__)


GAME_LIST_SNAPSHOT_SCRIPT = r"""() => {
  const selector = '.dashboard-game-block__link[href]';
  const links = [...document.querySelectorAll(selector)];
  const matchesGame = url => /\/baccarat\/|baccara/i.test(url.pathname);
  const result = [];
  for (const link of links) {
    let url;
    try { url = new URL(link.href, document.baseURI); } catch { continue; }
    if (!matchesGame(url)) continue;

    // Climb to the nearest shared ancestor containing exactly this game link
    // and its info. The visual block itself is not always the list-row root.
    const ancestors = [];
    for (let node = link.parentElement; node && node !== document.body; node = node.parentElement) {
      const rowLinks = [...node.querySelectorAll(selector)];
      if (rowLinks.length === 1 && rowLinks[0] === link) ancestors.push(node);
    }
    const hasInfo = node => node.querySelector(
      '.dashboard-game-info__additional-info, .dashboard-game-info__time, .dashboard-game-info__period'
    );
    const row = ancestors.find(node => hasInfo(node))
      || ancestors.find(node => node.matches('li.dashboard-game'))
      || ancestors.find(node => node.matches('.dashboard-game-block.dashboard-game__block'))
      || ancestors[0]
      || link.parentElement;
    const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
    const additionalNodes = [...(row?.querySelectorAll('.dashboard-game-info__additional-info') || [])];
    const additional = additionalNodes.map(text).find(value => /^\d+$/.test(value)) || '';
    const timer = text(row?.querySelector('.dashboard-game-info__time'));
    const statusCandidates = [
      ...[...(row?.querySelectorAll('.dashboard-game-info__period, [class*="status"], [data-test*="status"]') || [])]
        .map(text),
    ];
    const statusText = statusCandidates.find(value =>
      /заверш|оконч|ид[её]т|в игре|live|проводится|ожида|предстоит|ставки до начала/i.test(value)
    ) || '';
    const probeNodes = [...(row?.querySelectorAll(
      '[class*="info"], [class*="time"], [class*="period"], [class*="round"], [data-test*="game"]'
    ) || [])].slice(0, 14);
    const numericNodes = [...(row?.querySelectorAll('*') || [])].filter(node => {
      const value = text(node);
      return /^\d{1,4}$/.test(value) && node.children.length === 0;
    }).slice(0, 8);
    const diagnosticFragment = JSON.stringify({
      numeric_candidates: numericNodes.map(node => ({
        tag: node.tagName.toLowerCase(), class: typeof node.className === 'string' ? node.className : '',
        data_test: node.getAttribute('data-test') || '', text: text(node),
        html: (node.outerHTML || '').slice(0, 250),
      })),
      row_text: (row?.innerText || text(row)).slice(0, 900),
      candidates: probeNodes.map(node => ({
        tag: node.tagName.toLowerCase(), class: typeof node.className === 'string' ? node.className : '',
        data_test: node.getAttribute('data-test') || '', text: text(node).slice(0, 80),
        html: (node.outerHTML || '').slice(0, 240),
      })),
      row_head: (row?.outerHTML || '').slice(0, 400),
    }).slice(0, 3500);
    const id = url.pathname.match(/\/(\d+)(?:-[^/]+)?\/?$/)?.[1] || '';
    const position = result.length;
    result.push({
      href: link.getAttribute('href') || link.href,
      absolute_url: url.href,
      game_id: id,
      round_number: additional || null,
      timer: timer || null,
      status_text: statusText,
      dom_position: position,
      selector_counts: {
        row_tag: row?.tagName?.toLowerCase() || '',
        row_class: typeof row?.className === 'string' ? row.className : '',
        game_links: row?.querySelectorAll(selector).length || 0,
        additional_info: additionalNodes.length,
        game_time: row?.querySelectorAll('.dashboard-game-info__time').length || 0,
        game_period: row?.querySelectorAll('.dashboard-game-info__period').length || 0,
      },
      row_html: diagnosticFragment,
    });
  }
  return result;
}"""


GAME_LIST_BLOCK_SNAPSHOT_SCRIPT = r"""() => {
  const blockSelector = '.dashboard-game-block.dashboard-game__block';
  const linkSelector = '.dashboard-game-block__link[href]';
  const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
  const matchingBlocks = [...document.querySelectorAll(blockSelector)];
  // Older list builds omit the wrapper classes but still have one game per
  // .dashboard-game list item. Keep that layout readable without mixing rows.
  const blocks = matchingBlocks.length ? matchingBlocks : [...document.querySelectorAll('li.dashboard-game')];
  const result = [];

  for (const [blockIndex, block] of blocks.entries()) {
    const exactBlock = block.matches(blockSelector);
    // A node belongs to the closest matching game block, preventing nested
    // blocks from borrowing a neighboring game's link or round number.
    const owned = selector => [...block.querySelectorAll(selector)]
      .filter(node => exactBlock ? node.closest(blockSelector) === block : !node.closest(blockSelector));
    const links = owned(linkSelector);
    if (links.length !== 1) continue;
    const link = links[0];
    const href = link.getAttribute('href') || '';
    const idMatch = href.match(/\/(\d+)-player-banker(?:[/?#]|$)/i);
    if (!idMatch) continue;

    let absoluteUrl;
    try { absoluteUrl = new URL(href, document.baseURI).href; } catch { continue; }
    if (!/\/baccarat\/|baccara/i.test(absoluteUrl)) continue;

    const roundNode = owned('.dashboard-game-info__additional-info')[0] || null;
    const roundText = text(roundNode);
    const timerNode = owned('.dashboard-game-info__time')[0] || null;
    const timer = text(timerNode);
    const periodNode = owned('.dashboard-game-info__period')[0] || null;
    const stateTextPattern = /(?:\b(?:finished|ended|live|in progress|started|countdown|pre[\s_-]?match|before[\s_-]?start|not[\s_-]?started)\b|игра\s+(?:завершена|окончена)|завершена|окончена|в игре|ид[её]т|начал[ао]сь|ожидает\s+начала|до\s+начала|предстоит|обратн\w*\s+отсч[её]т)/i;
    const statusCandidates = [
      text(periodNode),
      ...owned('[class*="status"], [data-test*="status"]').map(text),
      ...owned('*').filter(node => !node.children.length && stateTextPattern.test(text(node))).map(text),
    ].filter((value, index, values) => value && !/^\d+$/.test(value) && values.indexOf(value) === index);
    const statusText = statusCandidates[0] || '';
    const rowStateNodes = [
      block,
      ...owned('[class], [data-state], [data-status], [aria-label], [title]').slice(0, 60),
    ];
    const statusEvidence = rowStateNodes.flatMap(node => [
      typeof node.className === 'string' ? node.className : '',
      ...['data-state', 'data-status', 'aria-label', 'title']
        .map(name => node.getAttribute(name) || ''),
    ]).filter(Boolean).join(' ').slice(0, 1500);
    const stateNodes = [];
    for (let node = timerNode; node; node = node.parentElement) {
      stateNodes.push(node);
      if (node === block || stateNodes.length >= 8) break;
    }
    const timerEvidence = stateNodes.flatMap(node => node ? [
      typeof node.className === 'string' ? node.className : '',
      ...['aria-label', 'title', 'data-state', 'data-status', 'data-test']
        .map(name => node.getAttribute(name) || ''),
    ] : []).filter(Boolean).join(' ').slice(0, 500);
    const stateEvidence = `${statusCandidates.join(' ')} ${timerEvidence}`.toLowerCase();
    const combinedStateEvidence = [stateEvidence, statusEvidence].join(' ').toLowerCase();
    const isCountdownToStart = Boolean(timerNode && timer.trim())
      && /countdown|pre[\s_-]?match|before[\s_-]?start|not[\s_-]?started|ожидает\s+начала|до\s+начала|предстоит|обратн\w*\s+отсч[её]т/i.test(combinedStateEvidence);
    const finished = /(?:игра\s+)?(?:завершена|окончена)|\b(?:finished|ended)\b/i.test(combinedStateEvidence)
      && !/\bне\s+(?:завершена|окончена)|\bnot[\s_-]+(?:finished|ended)\b/i.test(combinedStateEvidence);
    const started = /\b(?:live|in progress|started)\b|в игре|ид[её]т|начал[ао]сь/i.test(combinedStateEvidence)
      && !/\bnot[\s_-]+started\b|не\s+(?:начал[ао]сь|в игре)/i.test(combinedStateEvidence);
    const teamNames = owned('.ui-game-scores__item--total ~ .ui-team-score-name, .ui-team-score-name')
      .map(text).map(value => value.toLocaleLowerCase());
    const scores = owned('.ui-game-scores__item--total .ui-game-scores__num').map(text);
    const playerIndex = teamNames.findIndex(value => /^(?:игрок|player)$/.test(value));
    const bankerIndex = teamNames.findIndex(value => /^(?:банкир|banker)$/.test(value));
    const playerScore = scores.length === teamNames.length && playerIndex >= 0 ? scores[playerIndex] : null;
    const bankerScore = scores.length === teamNames.length && bankerIndex >= 0 ? scores[bankerIndex] : null;

    result.push({
      href,
      absolute_url: absoluteUrl,
      game_id: idMatch[1],
      round_number: /^\d+$/.test(roundText) ? Number(roundText) : null,
      timer: timer || null,
      status_text: statusText,
      period_text: text(periodNode),
      timer_evidence: timerEvidence,
      status_evidence: statusEvidence,
      finished,
      started,
      is_countdown_to_start: isCountdownToStart,
      player_score: /^\d+$/.test(playerScore || '') ? Number(playerScore) : null,
      banker_score: /^\d+$/.test(bankerScore || '') ? Number(bankerScore) : null,
      position: blockIndex + 1,
      dom_position: blockIndex + 1,
      selector_counts: {
        row_tag: block.tagName.toLowerCase(),
        row_class: typeof block.className === 'string' ? block.className : '',
        game_links: links.length,
        additional_info: owned('.dashboard-game-info__additional-info').length,
        game_time: owned('.dashboard-game-info__time').length,
        game_period: owned('.dashboard-game-info__period').length,
        score_nodes: scores.length,
        team_names: teamNames.length,
      },
      row_html: (block.outerHTML || '').slice(0, 3500),
    });
  }
  return result;
}"""


class BrowserService:
    """Owns one visible, persistent Playwright Chromium context."""

    def __init__(self) -> None:
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.status = "STOPPED"
        self.authorization = "UNKNOWN"
        self.diagnostic = "Браузер ещё не запущен."
        self.events: deque[dict[str, str]] = deque(maxlen=100)
        self.lock = asyncio.Lock()
        self.page_lock = asyncio.Lock()
        self.monitor_task: asyncio.Task[None] | None = None
        self.observation_task: asyncio.Task[None] | None = None
        self.observation_status = "STOPPED"
        self.current_observation: dict[str, Any] | None = None
        self.observation_diagnostic = "Наблюдение ещё не запускалось."
        self.observation_phase = "список игр"
        self.game_diagnostics: list[dict[str, str]] = []
        self._last_no_game_signature: tuple[Any, ...] | None = None
        self._active_chain_source_id: str | None = None
        self._queued_games: list[Game] = []
        self._lifecycle_states: dict[str, str] = {}
        self.state_version = 0
        self.state_epoch = uuid.uuid4().hex
        self.store = ObservationStore(settings.observation_db)

    def event(self, level: str, message: str) -> None:
        # Never include page content, credentials, tokens, or cookies here.
        item = {"at": datetime.now(timezone.utc).isoformat(), "level": level, "message": message}
        self.events.appendleft(item)
        self.state_version = getattr(self, "state_version", 0) + 1
        getattr(log, level.lower(), log.info)(message)

    def _set_lifecycle_state(self, game_id: str, state: str, detail: str = "") -> None:
        """Record monotonic, game-ID-keyed lifecycle transitions for diagnostics."""
        order = {
            "DISCOVERED": 0,
            "SELECTED": 1,
            "OPENING": 2,
            "OBSERVING": 3,
            "CARDS_COMPLETE": 4,
            "FINALIZED": 5,
            "PREDICTION_EVALUATED": 6,
            "NEXT_GAME": 7,
        }
        states = getattr(self, "_lifecycle_states", None)
        if states is None:
            states = self._lifecycle_states = {}
        previous = states.get(game_id)
        if state not in order or (previous in order and order[state] <= order[previous]):
            return
        states[game_id] = state
        current = getattr(self, "current_observation", None)
        if current and str(current.get("game_id")) == game_id and current.get("lifecycle_state") != state:
            current["lifecycle_state"] = state
            self.state_version = getattr(self, "state_version", 0) + 1
        suffix = f" {detail}" if detail else ""
        self.event("INFO", f"LIFECYCLE game_id={game_id} state={state}{suffix}")

    async def start(self) -> None:
        async with self.lock:
            if self.context is not None:
                self.event("INFO", "Браузер уже запущен.")
                return
            if not settings.target_url:
                self.status = "ERROR"
                self.diagnostic = "В настройках не указан адрес целевой страницы."
                self.event("ERROR", self.diagnostic)
                return
            self.status = "STARTING"
            self.authorization = "UNKNOWN"
            self.event("INFO", "Запускаем видимый Chromium с отдельным профилем Bakkara.")
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
                self.diagnostic = "Страница открыта. Войдите вручную в окне Chromium."
                self.event("INFO", "Целевая страница открыта; ожидаем вход вручную.")
                self.monitor_task = asyncio.create_task(self._monitor_auth())
            except Exception as exc:
                await self._close_locked()
                self.status = "ERROR"
                self.diagnostic = "Не удалось запустить браузер. Подробности записаны в технический журнал."
                self.event("ERROR", self.diagnostic)
                log.exception("Не удалось запустить браузер (%s).", type(exc).__name__)

    async def stop(self) -> None:
        async with self.lock:
            await self._close_locked()
            self.status = "STOPPED"
            self.authorization = "UNKNOWN"
            self.diagnostic = "Браузер остановлен."
            self.event("INFO", "Браузер остановлен.")

    async def start_observation(self) -> None:
        async with self.lock:
            if not authorization_allows_observation(self.authorization) or self.page is None:
                self.observation_status = "WAITING_AUTH"
                self.observation_diagnostic = "Для сканирования войдите на сайт в открытом окне браузера."
                self.event("WARNING", self.observation_diagnostic)
                return
            if self.observation_task and not self.observation_task.done():
                return
            # A new scan session never inherits a forecast chain from history.
            self._active_chain_source_id = None
            self._queued_games.clear()
            self.observation_status = "WAITING_FOR_GAME"
            self.observation_diagnostic = "Ищем ближайшую игру баккары."
            self.observation_task = asyncio.create_task(self._observe())
            self.event("INFO", "Наблюдение запущено.")

    async def stop_observation(self) -> None:
        task, self.observation_task = self.observation_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        source_id, self._active_chain_source_id = getattr(self, "_active_chain_source_id", None), None
        if source_id:
            self.store.record_unresolved_transition(
                source_id, "Сканирование остановлено до выбора следующей игры; цепочка прогноза прервана."
            )
        current_id = (self.current_observation or {}).get("game_id")
        if current_id:
            self.store.break_incoming_transition(
                str(current_id), "Сканирование остановлено; незавершённая цепочка прогноза прервана."
            )
        self._active_chain_source_id = None
        getattr(self, "_queued_games", []).clear()
        self.observation_status = "STOPPED"
        self.current_observation = None
        self.observation_diagnostic = "Наблюдение остановлено."
        self.event("INFO", "Наблюдение остановлено.")

    async def clear_history(self) -> dict[str, Any]:
        """Stop writes, clear only observation data, and retain browser/auth state."""
        async with self.lock:
            await self.stop_observation()
            self.store.clear_all()
            self.current_observation = None
            self.game_diagnostics = []
            self._active_chain_source_id = None
            self.observation_status = "STOPPED"
            self.observation_diagnostic = "История наблюдений очищена."
            return self.snapshot()

    def clear_logs(self) -> dict[str, Any]:
        """Clear the in-memory event feed only; do not affect scanning or history."""
        self.events.clear()
        return self.snapshot()

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
        """Run one game at a time until its exact in-page finish flag is observed."""
        try:
            processed_ids = await asyncio.to_thread(self.store.finished_game_ids)
        except Exception as exc:
            processed_ids = set()
            self.event("ERROR", f"Не удалось загрузить обработанные игры из истории ({type(exc).__name__}).")
        retry_after: dict[str, float] = {}
        self.event("INFO", f"Загружено завершённых игр из истории: {len(processed_ids)}.")
        while self.context is not None and self.page is not None:
            if not authorization_allows_observation(self.authorization):
                if self.authorization == "AUTH_LOST":
                    source_id, self._active_chain_source_id = self._active_chain_source_id, None
                    if source_id:
                        self.store.record_unresolved_transition(
                            source_id, "Сессия входа потеряна до выбора следующей игры; цепочка прогноза прервана."
                        )
                    current_id = (self.current_observation or {}).get("game_id")
                    if current_id:
                        self.store.break_incoming_transition(
                            str(current_id), "Сессия входа потеряна во время сканирования; цепочка прогноза прервана."
                        )
                    self._active_chain_source_id = None
                    self.observation_status = "AUTH_LOST"
                    self.observation_diagnostic = "Сессия входа завершена. Войдите вручную в окне браузера."
                elif self.authorization == "CHECK_ERROR":
                    self.observation_status = "AUTH_CHECK_ERROR"
                    self.observation_diagnostic = "Не удалось проверить вход. Повторяем проверку."
                else:
                    self.observation_status = "AUTH_TEMPORARY"
                    self.observation_diagnostic = "Признак входа временно не виден. Повторяем проверку."
                await asyncio.sleep(max(settings.auth_poll_seconds, 0.5))
                continue
            try:
                await self._observe_iteration(processed_ids, retry_after)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                source_id, self._active_chain_source_id = self._active_chain_source_id, None
                if source_id:
                    self.store.record_unresolved_transition(
                        source_id, "Цепочка прервана после ошибки чтения или перехода списка игр."
                    )
                failed_id = (self.current_observation or {}).get("game_id")
                if failed_id and failed_id not in processed_ids:
                    retry_after[failed_id] = asyncio.get_running_loop().time() + max(settings.incomplete_retry_seconds, 5)
                self.observation_status = "ERROR"
                self.observation_diagnostic = f"Ошибка этапа «{self.observation_phase}»: {type(exc).__name__}. Повторим обход списка."
                self.event("ERROR", self.observation_diagnostic)
                log.exception("Ошибка наблюдения на этапе «%s» (%s).", self.observation_phase, type(exc).__name__)
                await self._recover_to_game_list()
                await asyncio.sleep(max(settings.observation_poll_seconds, 0.25))

    async def _remember_game_scan(
        self,
        game: Game,
        cards: list[dict[str, Any]],
        completeness: str,
        reason: str,
        scan_result: str,
        pending_card_slots: int = 0,
        *,
        finished_confirmed: bool = False,
        player_hand_final: bool = False,
        diagnostics: dict[str, Any] | None = None,
        final_snapshot_reverified: bool | None = None,
    ) -> bool:
        try:
            def persist() -> dict[str, Any] | None:
                # Keep selection metadata in current_observation, but never
                # create an empty history row before any Player card is read.
                stored = self.store.get(game.game_id)
                if cards:
                    self.store.upsert(
                        game, cards, completeness, reason, scan_result,
                        finished_confirmed=finished_confirmed,
                        final_snapshot_reverified=final_snapshot_reverified,
                        pending_card_slots=pending_card_slots,
                        player_hand_final=player_hand_final,
                    )
                    stored = self.store.get(game.game_id)
                if stored:
                    self.store.refresh_prediction_target(game.game_id)
                    stored = self.store.get(game.game_id)
                return stored

            stored_record = await asyncio.to_thread(persist)
            if stored_record and not game.round_number:
                game.round_number = str(stored_record.get("round_number") or "")
            display_cards = stored_record["cards"] if stored_record else cards
            if finished_confirmed and not display_cards:
                await asyncio.to_thread(
                    self.store.break_incoming_transition,
                    game.game_id,
                    "Игра завершилась, но достоверные карты не прочитаны; результат UNKNOWN, не LOSE.",
                )
        except Exception:
            display_cards = cards
            self.observation_status = "ERROR"
            self.observation_diagnostic = "Не удалось сохранить результат в историю; игра не отмечена обработанной."
            self.event("ERROR", self.observation_diagnostic)
            log.exception("Не удалось сохранить историю игры %s.", game.game_id)
            stored_record = None
        current = self.current_observation or {}
        current.update({
            "game_id": game.game_id,
            "url": game.url,
            "position": game.position + 1,
            "round_number": game.round_number,
            "round_number_diagnostic": game.round_number_diagnostic,
            "time": game.time,
            "site_status": game.status,
            "status_text": game.status_text,
            "player_score": game.player_score,
            "banker_score": game.banker_score,
            "cards": display_cards,
            "cards_count": len(display_cards),
            "pending_card_slots": pending_card_slots,
            "scan_result": scan_result,
            "reason": reason,
            "finished_confirmed": finished_confirmed,
            "player_hand_final": player_hand_final or bool(stored_record and stored_record.get("player_hand_final")),
            "observed_at": stored_record.get("observed_at") if stored_record else None,
            "calculated_suit": stored_record.get("calculated_suit") if stored_record else None,
            "calculation_status": stored_record.get("calculation_status") if stored_record else "not_calculated",
            "prediction_for_next_round": stored_record.get("prediction_for_next_round") if stored_record else None,
            "predicted_suit": stored_record.get("predicted_suit") if stored_record else None,
            "incoming_prediction": stored_record.get("incoming_prediction", stored_record.get("predicted_suit")) if stored_record else None,
            "outgoing_prediction": stored_record.get("outgoing_prediction", stored_record.get("prediction_for_next_round")) if stored_record else None,
            "prediction_result": stored_record.get("prediction_result") if stored_record else None,
            "prediction_reason": stored_record.get("prediction_reason") if stored_record else None,
        })
        if diagnostics is not None:
            current["diagnostics"] = diagnostics
        if final_snapshot_reverified is not None:
            current["final_snapshot_reverified"] = final_snapshot_reverified
        self.current_observation = current
        self.state_version = getattr(self, "state_version", 0) + 1
        if not cards:
            return True
        return bool(stored_record and
                    (not finished_confirmed or stored_record.get("finished_confirmed")) and
                    (not player_hand_final or stored_record.get("player_hand_final")))

    async def _read_game_list_snapshot(self) -> list[Game]:
        """Read each game's URL, number, timer, and status in one synchronous DOM pass."""
        assert self.page is not None
        rows = await self.page.evaluate(GAME_LIST_BLOCK_SNAPSHOT_SCRIPT)
        entries: list[Game] = []
        for item in rows:
            url = str(item.get("absolute_url") or item.get("href") or "")
            game_id = str(item.get("game_id") or "")
            round_value = str(item.get("round_number") or "").strip()
            round_number = round_value if re.fullmatch(r"\d+", round_value) else ""
            status_text = str(item.get("status_text") or "").strip()
            status = classify_game_status(status_text)
            player_score = "" if item.get("player_score") is None else str(item.get("player_score"))
            banker_score = "" if item.get("banker_score") is None else str(item.get("banker_score"))
            timer_evidence = str(item.get("timer_evidence") or "")[:500]
            state_evidence = str(item.get("status_evidence") or "")[:1500]
            counts = item.get("selector_counts") or {}
            fragment = str(item.get("row_html") or "")[:3500]
            diagnostic = (
                f".dashboard-game-info__additional-info={counts.get('additional_info', 0)}; "
                f".dashboard-game-info__time={counts.get('game_time', 0)}; "
                f".dashboard-game-info__period={counts.get('game_period', 0)}; "
                f"root={counts.get('row_tag', '')}.{counts.get('row_class', '')}; "
                "снимок строки получен одним page.evaluate; ID не используется вместо номера."
            )
            if not game_id:
                log.warning(
                    "Строка Baccarat пропущена: не удалось прочитать числовой game_id. %s",
                    json.dumps({"href": item.get("href"), "url": url, "dom_position": item.get("dom_position")},
                               ensure_ascii=False),
                )
                continue
            game = Game(
                game_id=game_id,
                url=url,
                time=str(item.get("timer") or ""),
                round_number=round_number,
                status=status,
                position=max(int(item.get("dom_position", len(entries) + 1)) - 1, 0),
                active=bool(item.get("started")),
                player_score=player_score,
                banker_score=banker_score,
                status_text=status_text,
                round_number_diagnostic=diagnostic,
                round_row_fragment=fragment,
                finished=bool(item.get("finished")),
                started=bool(item.get("started")),
                is_countdown_to_start=bool(item.get("is_countdown_to_start")),
                timer_evidence=timer_evidence,
                state_evidence=state_evidence,
            )
            entries.append(game)

        # Some site builds render duplicate clickable descendants for the same
        # round. Keep one entry per ID, preferring the row carrying the number.
        unique: dict[str, Game] = {}
        for game in entries:
            previous = unique.get(game.game_id)
            if previous is None or (not previous.round_number and game.round_number):
                if previous is not None:
                    game.position = previous.position
                unique[game.game_id] = game
        return sorted(unique.values(), key=lambda game: game.position)

    @staticmethod
    def _log_game_list_snapshot(games: list[Game]) -> None:
        for game in games:
            state = classify_game(game)
            record = {
                "href": game.url,
                "game_id": game.game_id,
                "round_number": game.round_number or None,
                "timer": game.time or None,
                "status": game.status,
                "status_text": game.status_text or None,
                "status_evidence": game.state_evidence or None,
                "game_state": state,
                "player_score": game.player_score or None,
                "banker_score": game.banker_score or None,
                "is_countdown_to_start": game.is_countdown_to_start,
                "timer_evidence": game.timer_evidence or None,
                "dom_position": game.position + 1,
            }
            log.info("СТРОКА: %s", json.dumps(record, ensure_ascii=False))
            log.info("GAME_LIST game_id=%s -> round_number=%s",
                     game.game_id, game.round_number or "unknown")
            if state == "UNKNOWN" and game.round_row_fragment:
                log.info("UNKNOWN: DOM-строка раздачи № %s: %s",
                         game.round_number or "не указана", game.round_row_fragment)
            if not game.round_number:
                log.warning("Номер не прочитан в этой строке; ограниченный HTML-фрагмент: %s", game.round_row_fragment)

    async def _inspect_game_dom(self, player_name: str, banker_name: str) -> dict[str, Any]:
        """Inspect the Baccarat widget in the main document and every iframe."""
        assert self.page is not None
        frames: list[dict[str, Any]] = []
        for frame in list(self.page.frames):
            try:
                result = await frame.evaluate(r"""({playerName, bankerName, finishSelector, finishText}) => {
                  const visible = node => {
                    if (!node || !(node.offsetWidth || node.offsetHeight || node.getClientRects().length)) return false;
                    for (let parent = node; parent && parent.nodeType === 1; parent = parent.parentElement) {
                      const style = getComputedStyle(parent);
                      if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    }
                    return true;
                  };
                  const exactFinish = node => visible(node) &&
                    (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim() === finishText;
                  const text = node => (node?.innerText || node?.textContent || '').replace(/\s+/g, ' ').trim();
                  const wrappers = [...document.querySelectorAll('.baccarat-player')].filter(visible);
                  const names = wrappers.flatMap(side => [...side.querySelectorAll('.baccarat-player__name')]
                    .filter(visible).map(node => ({text: text(node)})));
                  const playerSides = wrappers.filter(side => [...side.querySelectorAll('.baccarat-player__name')]
                    .some(node => visible(node) && text(node).toLocaleLowerCase() === playerName));
                  const playerSide = playerSides[0] || null;
                  const cardsContainer = [...(playerSide?.querySelectorAll('.baccarat-player__cards') || [])].find(visible) || null;
                  const boxes = cardsContainer ? [...cardsContainer.querySelectorAll('.baccarat-player__card-box')].filter(visible) : [];
                  const cards = [];
                  let pending = 0;
                  let rankCount = 0;
                  let suitCount = 0;
                  for (const [boxIndex, box] of boxes.entries()) {
                    const rankNode = box.querySelector('.baccarat-card__rank');
                    const rank = text(rankNode).toUpperCase();
                    if (rankNode && visible(rankNode)) rankCount += 1;
                    const classes = [box.className, ...[...box.querySelectorAll('[class]')].map(node => node.getAttribute('class') || '')].join(' ').toLowerCase();
                    const icons = [...box.querySelectorAll('svg[data-v-ico^="cardSuits|"]')].map(node => node.getAttribute('data-v-ico') || '').join(' ').toLowerCase();
                    const suit = ['hearts','diamonds','clubs','spades'].find(value =>
                      classes.includes('suit-' + value) || icons.includes('cardsuits|' + value)) || '';
                    if (suit) suitCount += 1;
                    if (/^(?:A|[2-9]|10|J|Q|K)$/.test(rank) && suit) cards.push({position: boxIndex + 1, rank, suit});
                    else pending += 1;
                  }
                  const statusTexts = [...document.querySelectorAll('.scoreboard-header__match-info, .ui-game-timer__label, .scoreboard-status')]
                    .filter(visible).map(text).filter(Boolean);
                  let finishFlag = null;
                  try {
                    finishFlag = [...new Set([...document.querySelectorAll('.ui-caption--size-xl.ui-caption--weight-700'),
                      ...document.querySelectorAll(finishSelector)])].find(exactFinish) || null;
                  } catch (_) {}
                  const finished = Boolean(finishFlag);
                  const relevant = playerSide || finishFlag ||
                    [...document.querySelectorAll('.scoreboard-header__match-info, .ui-game-timer__label, .scoreboard-status')].find(visible);
                  return {
                    url: location.href,
                    captured_at_ms: performance.now(),
                    counts: {
                      baccarat_player_wrappers: wrappers.length,
                      baccarat_player_confirmed_wrappers: wrappers.filter(side => side.matches('.baccarat-player.baccarat__player')).length,
                      baccarat_player_name_nodes: names.length,
                      player_names: names.filter(item => item.text.toLocaleLowerCase() === playerName).length,
                      banker_names: names.filter(item => item.text.toLocaleLowerCase() === bankerName).length,
                      player_fields: playerSides.length,
                      player_cards_containers: playerSides.filter(side => [...side.querySelectorAll('.baccarat-player__cards')].some(visible)).length,
                      player_card_boxes: boxes.length,
                      player_card_ranks: rankCount,
                      player_card_suits: suitCount
                    },
                    cards, pending_card_slots: pending, status_texts: statusTexts,
                    finished, finish_flag_text: finishFlag ? text(finishFlag) : '',
                    fragment: relevant ? (relevant.outerHTML || '').slice(0, 3500) : ''
                  };
                }""", {"playerName": player_name.strip().casefold(),
                       "bankerName": banker_name.strip().casefold(),
                       "finishSelector": settings.round_finished_selector,
                       "finishText": "Игра завершена"})
                frames.append(result)
            except Exception as exc:
                frames.append({"url": frame.url, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "counts": {}})

        count_names = (
            "baccarat_player_wrappers", "baccarat_player_confirmed_wrappers", "baccarat_player_name_nodes", "player_names", "banker_names",
            "player_fields", "player_cards_containers", "player_card_boxes", "player_card_ranks", "player_card_suits",
        )
        totals = {name: sum(frame.get("counts", {}).get(name, 0) for frame in frames) for name in count_names}
        player_frames = [frame for frame in frames if frame.get("counts", {}).get("player_fields")]
        # Each frame result is an atomic synchronous JS snapshot. Keep the most
        # complete one intact; never combine card positions captured in different frames.
        data_frame = max(player_frames, key=lambda frame: (
            frame.get("counts", {}).get("player_card_boxes", 0),
            frame.get("counts", {}).get("player_card_ranks", 0)
            + frame.get("counts", {}).get("player_card_suits", 0),
            frame.get("captured_at_ms", 0),
        ), default=None)
        diagnostic_frame = data_frame or next((frame for frame in frames if frame.get("fragment")), None)
        selected_counts = (data_frame or {}).get("counts", {})
        if not selected_counts.get("player_fields"):
            cause = "Player wrapper .baccarat-player с именем Игрок отсутствует во всех доступных документах/frame."
        elif not selected_counts.get("player_cards_containers"):
            cause = "Player wrapper найден, но контейнер .baccarat-player__cards отсутствует."
        elif not selected_counts.get("player_card_boxes"):
            cause = "Player-контейнер найден, но .baccarat-player__card-box отсутствует; данных карты в DOM нет."
        elif (data_frame or {}).get("pending_card_slots", 0):
            cause = "Найдены card-box, но в некоторых отсутствует ранг или масть; снимок неполный."
        else:
            cause = "Player wrapper, контейнер и DOM-данные карт найдены."
        return {
            "url": self.page.url,
            "frame_count": len(frames),
            "iframe_count": max(0, len(frames) - 1),
            "frames": frames,
            "counts": {**selected_counts, "frame_totals": totals,
                       "snapshot_frame_url": (data_frame or {}).get("url", "")},
            "cards": (data_frame or {}).get("cards", []),
            "pending_card_slots": (data_frame or {}).get("pending_card_slots", 0),
            "snapshot_captured_at_ms": (data_frame or {}).get("captured_at_ms"),
            "finished": any(frame.get("finished", False) for frame in frames),
            "finish_flag_text": next((frame.get("finish_flag_text", "") for frame in frames if frame.get("finished")), ""),
            "status_text": " ".join(dict.fromkeys(text for frame in frames for text in frame.get("status_texts", []))),
            "dom_fragment": (diagnostic_frame or {}).get("fragment", ""),
            "cause": cause,
        }

    async def _probe_observation_auth_locked(self) -> bool:
        """Recheck immediately before navigation so explicit logout is never bypassed."""
        previous = self.authorization
        try:
            result, diagnostic = await self._is_authorized()
        except Exception as exc:
            result, diagnostic = "CHECK_ERROR", f"Не удалось перепроверить авторизацию перед переходом ({type(exc).__name__})."
        self.authorization = result
        self.diagnostic = diagnostic
        if result == "AUTHORIZED":
            self.status = "AUTHORIZED"
        elif result == "TEMPORARILY_UNCONFIRMED":
            self.status = "AUTH_TEMPORARY"
            if previous != result:
                self.event("WARNING", "Маркер входа временно отсутствует; продолжается только чтение доступной страницы.")
        elif result == "AUTH_LOST":
            self.status = "AUTH_LOST"
            self.observation_status = "AUTH_LOST"
            self.observation_diagnostic = "Обнаружена явная форма входа; навигация наблюдателя остановлена."
            if previous != result:
                self.event("WARNING", self.observation_diagnostic)
        else:
            self.status = "AUTH_CHECK_ERROR"
            self.observation_status = "AUTH_CHECK_ERROR"
            self.observation_diagnostic = "Не удалось проверить вход перед переходом; навигация приостановлена."
            if previous != result:
                self.event("WARNING", self.observation_diagnostic)
        return authorization_allows_observation(result)

    async def _observe_iteration_legacy(self, processed_ids: set[str], retry_after: dict[str, float]) -> None:
        """Legacy implementation retained temporarily while the queue scheduler is introduced."""
        assert self.page is not None
        self.observation_phase = "загрузка списка игр"
        async with self.page_lock:
            if not await self._probe_observation_auth_locked():
                return
            await self.page.goto(settings.game_list_url, wait_until="domcontentloaded", timeout=30_000)
            if not await self._probe_observation_auth_locked():
                return
            await self.page.locator("li.dashboard-champ-body").first.wait_for(timeout=15_000)
            try:
                await self.page.wait_for_function(
                    "() => document.querySelectorAll('li.dashboard-champ-body .dashboard-champ-body__games li.dashboard-game, li.dashboard-champ-body .dashboard-champ-body__games .dashboard-game-block.dashboard-game__block').length > 0",
                    timeout=7_000,
                )
            except PlaywrightTimeoutError:
                pass
            sections = await self.page.locator("li.dashboard-champ-body").evaluate_all(
                "nodes => nodes.map(node => node.outerHTML)"
            )
            base_url = self.page.url

        games = parse_game_list_html(
            "\n".join(sections), base_url, settings.player_name_text, settings.banker_name_text
        )
        # The live list can render row shells before its round labels. Re-read the
        # same rows and merge only by their URL-derived game IDs; never guess a value.
        for _ in range(2):
            if not any(not item.round_number for item in games):
                break
            await asyncio.sleep(0.35)
            async with self.page_lock:
                if self.page is None or self.page.is_closed():
                    break
                try:
                    fresh_sections = await self.page.locator("li.dashboard-champ-body").evaluate_all(
                        "nodes => nodes.map(node => node.outerHTML)"
                    )
                    fresh_base_url = self.page.url
                except Exception:
                    fresh_sections, fresh_base_url = [], base_url
            if not fresh_sections:
                continue
            fresh_games = parse_game_list_html(
                "\n".join(fresh_sections), fresh_base_url,
                settings.player_name_text, settings.banker_name_text
            )
            fresh_by_id = {item.game_id: item for item in fresh_games}
            for item in games:
                newer = fresh_by_id.get(item.game_id)
                if newer:
                    item.position = newer.position
                    item.round_number = newer.round_number or item.round_number
                    item.round_number_diagnostic = newer.round_number_diagnostic
                    item.time = newer.time or item.time
                    if newer.status_text:
                        item.status = newer.status
                        item.status_text = newer.status_text
                    item.player_score = newer.player_score or item.player_score
                    item.banker_score = newer.banker_score or item.banker_score
        now = asyncio.get_running_loop().time()
        deferred = {game_id for game_id, due in retry_after.items() if due > now}
        unavailable = processed_ids | deferred
        pending_targets = self.store.pending_prediction_targets()
        self.game_diagnostics = []
        for game in games:
            if game.game_id in pending_targets:
                reason = "Целевая раздача связана с ожидающим прогнозом — открываем для точной проверки карт."
            elif game.status == "Завершена":
                reason = "Игра помечена завершённой в списке — пропускаем, не открывая."
            elif game.game_id in processed_ids:
                reason = "ID уже сохранён как завершённый в истории — повторно не открываем."
            elif game.game_id in deferred:
                reason = "Повторная проверка отложена после временной ошибки или тайм-аута."
            else:
                reason = "Подходящая незавершённая игра."
            if not game.round_number:
                reason += f" {game.round_number_diagnostic}"
            self.game_diagnostics.append({
                "game_id": game.game_id,
                "url": game.url,
                "position": str(game.position + 1),
                "time": game.time or "не указан",
                "site_status": game.status,
                "score": f"{game.player_score or '—'} : {game.banker_score or '—'}",
                "reason": reason,
            })

        signature = tuple((game.game_id, game.status, game.player_score, game.banker_score) for game in games)
        if signature != self._last_no_game_signature:
            finished_count = sum(game.status == "Завершена" for game in games)
            self.event("INFO", f"В списке баккары найдено игр: {len(games)}; завершённых строк пропускаем: {finished_count}.")
            self._last_no_game_signature = signature

        game = choose_next_game(games, unavailable, inspect_ids=pending_targets)
        if game is None:
            source_id = getattr(self, "_active_chain_source_id", None)
            missed = next((item for item in games if item.game_id not in processed_ids
                           and item.game_id not in pending_targets
                           and (is_finished_status(item.status_text) or item.game_id in deferred)), None)
            if source_id and missed:
                self.store.record_unresolved_transition(
                    source_id, "Следующая игра была пропущена или отложена; активная цепочка прогноза прервана."
                )
                self._active_chain_source_id = None
                self.event("WARNING", "Цепочка прогноза прервана: между наблюдениями обнаружена пропущенная игра.")
            self.observation_status = "WAITING_FOR_GAME"
            self.observation_diagnostic = (
                "Подходящих новых игр пока нет. Обновляем список и ждём."
                if games else "Игровые строки баккары пока не найдены. Обновляем список и ждём."
            )
            await asyncio.sleep(max(settings.observation_poll_seconds, 0.5))
            return

        source_id = getattr(self, "_active_chain_source_id", None)
        if source_id:
            missed_before_target = next((item for item in games
                if item.position < game.position and item.game_id not in processed_ids
                and item.game_id not in pending_targets
                and (is_finished_status(item.status_text) or item.game_id in deferred)), None)
            if missed_before_target:
                self.store.record_unresolved_transition(
                    source_id, "Перед выбранной игрой обнаружена пропущенная раздача; прогноз не переносится."
                )
                self.event("WARNING", "Цепочка прогноза прервана: обнаружена завершённая игра, которую сканер пропустил.")
            else:
                transition = self.store.link_active_transition(source_id, game)
                if transition:
                    self.event("INFO" if transition["prediction_result"] == "PENDING" else "WARNING",
                               f"Прогноз связан с выбранной игрой {game.game_id}: {transition['prediction_result']}.")
                else:
                    self.store.record_unresolved_transition(
                        source_id, "Не удалось однозначно связать прогноз со следующей выбранной игрой."
                    )
                    self.event("WARNING", "Цепочка прогноза прервана: следующая игра не определена однозначно.")
            self._active_chain_source_id = None

        try:
            existing = self.store.get(game.game_id)
        except Exception:
            existing = None
            log.exception("Не удалось прочитать историю для игры %s.", game.game_id)
        previous_cards = [
            {**card, "position": card.get("position", index + 1)}
            for index, card in enumerate((existing or {}).get("cards", []))
        ]
        self.current_observation = {
            "game_id": game.game_id,
            "url": game.url,
            "position": game.position + 1,
            "round_number": game.round_number,
            "round_number_diagnostic": game.round_number_diagnostic,
            "time": game.time,
            "site_status": game.status,
            "status_text": game.status_text,
            "player_score": game.player_score,
            "banker_score": game.banker_score,
            "cards": previous_cards,
            "cards_count": len(previous_cards),
            "pending_card_slots": 0,
            "scan_result": "SCANNING",
            "finished_confirmed": False,
            "final_snapshot_reverified": None,
            "reason": "Открываем первую сверху незавершённую игру.",
            "diagnostics": None,
        }
        self.state_version = getattr(self, "state_version", 0) + 1
        self.observation_status = "WAITING_FOR_GAME"
        self.observation_diagnostic = (
            f"Выбрана игра ID {game.game_id}, раздача {game.round_number or 'не указана'}, "
            f"позиция {game.position + 1}; переходим по фактическому URL."
        )
        self.event(
            "INFO",
            f"Выбрана первая сверху незавершённая игра: ID {game.game_id}, "
            f"раздача {game.round_number or 'не указана'}, таймер {game.time or 'не указан'}.",
        )
        if not game.round_number:
            self.event("WARNING", f"Round number was not found for game {game.game_id}; saving it as empty.")
        await self._scan_selected_game(game, previous_cards, processed_ids, retry_after)

    async def _open_planned_game(
        self,
        game: Game,
        processed_ids: set[str],
        retry_after: dict[str, float],
    ) -> None:
        """Open exactly the planned next game; never jump over a numbered round."""
        assert self.page is not None
        source_id = getattr(self, "_active_chain_source_id", None)
        origin_source_id = source_id
        if source_id:
            source_record = await asyncio.to_thread(self.store.get, source_id)
            source_text = str((source_record or {}).get("round_number") or "")
            target_text = game.round_number.strip()
            source_round = int(source_text) if source_text.isdigit() else None
            target_round = int(target_text) if target_text.isdigit() else None
            if source_round is not None and target_round is not None and target_round > source_round + 1:
                expected = str(source_round + 1)
                self._queued_games.clear()
                self.observation_status = "WAITING_FOR_GAME"
                self.observation_diagnostic = (
                    f"Ожидаем раздачу № {expected}; раздачу № {target_text} не открываем, "
                    "чтобы не пропустить игру в цепочке."
                )
                self.event(
                    "WARNING",
                    f"Защита последовательности: после № {source_round} ожидается № {expected}; "
                    f"№ {target_text} не открываем и возвращаемся к списку.",
                )
                await self._recover_to_game_list()
                await asyncio.sleep(max(settings.observation_poll_seconds, 0.1))
                return
            transition = await asyncio.to_thread(self.store.link_active_transition, source_id, game)
            if transition:
                self.event(
                    "INFO" if transition["prediction_result"] == "PENDING" else "WARNING",
                    f"Прогноз связан с раздачей № {game.round_number or game.game_id}: "
                    f"{transition['prediction_result']}.",
                )
            else:
                await asyncio.to_thread(
                    self.store.record_unresolved_transition,
                    source_id,
                    "Не удалось связать прогноз с подтверждённой следующей игрой.",
                )
                self.event("WARNING", "Цепочка прогноза прервана: следующая игра не связана однозначно.")
            self._active_chain_source_id = None

        if origin_source_id and origin_source_id != game.game_id:
            self._set_lifecycle_state(origin_source_id, "NEXT_GAME", f"target_game_id={game.game_id}")
        self._set_lifecycle_state(game.game_id, "SELECTED", f"dom_position={game.position + 1}")
        existing = await asyncio.to_thread(self.store.get, game.game_id)
        previous_cards = [
            {**card, "position": card.get("position", index + 1)}
            for index, card in enumerate((existing or {}).get("cards", []))
        ]
        self.current_observation = {
            "game_id": game.game_id, "url": game.url, "position": game.position + 1,
            "round_number": game.round_number, "round_number_diagnostic": game.round_number_diagnostic,
            "time": game.time, "site_status": game.status, "status_text": game.status_text,
            "player_score": game.player_score, "banker_score": game.banker_score,
            "cards": previous_cards, "cards_count": len(previous_cards), "pending_card_slots": 0,
            "lifecycle_state": "SELECTED",
            "scan_result": "SCANNING", "finished_confirmed": False, "player_hand_final": False,
            "final_snapshot_reverified": None, "reason": "Игра выбрана планировщиком; переходим по URL.",
            "diagnostics": None,
        }
        incoming = await asyncio.to_thread(self.store.incoming_prediction_for, game.game_id)
        if incoming:
            self.current_observation.update({
                "predicted_suit": incoming.get("predicted_suit"),
                "incoming_prediction": incoming.get("incoming_prediction"),
                "prediction_result": incoming.get("prediction_result"),
                "prediction_reason": incoming.get("prediction_reason"),
            })
        self.state_version = getattr(self, "state_version", 0) + 1
        self.observation_status = "WAITING_FOR_GAME"
        self.observation_diagnostic = (
            f"Выбрана раздача № {game.round_number or 'не указана'} (ID {game.game_id})."
        )
        self.event(
            "INFO",
            f"Раздача № {game.round_number or '—'} выбрана для последовательного наблюдения.",
        )
        if not game.round_number:
            self.event(
                "WARNING",
                f"Номер раздачи не найден для game_id={game.game_id}; ID не используется вместо номера.",
            )
        await self._scan_selected_game(game, previous_cards, processed_ids, retry_after)

    async def _observe_iteration(self, processed_ids: set[str], retry_after: dict[str, float]) -> None:
        """Select only a fresh, explicitly upcoming row and verify it just before navigation."""
        assert self.page is not None
        loop = asyncio.get_running_loop()
        now = loop.time()
        deferred = {game_id for game_id, due in retry_after.items() if due > now}

        # Games captured as UPCOMING are reserved in DOM order before we leave
        # the list. After the current Player hand is final we can open the exact
        # next reserved game directly, without reloading the list and risking a
        # 1 -> 3 jump while round 2 changes to STARTED.
        while self._queued_games:
            reserved = self._queued_games.pop(0)
            if reserved.game_id in processed_ids or reserved.game_id in deferred:
                continue
            self.observation_phase = "переход к зарезервированной следующей игре"
            self.event(
                "INFO",
                f"Используем зарезервированную следующую раздачу № "
                f"{reserved.round_number or reserved.game_id}; повторная загрузка списка не требуется.",
            )
            await self._open_planned_game(reserved, processed_ids, retry_after)
            return

        all_games: list[Game] = []
        pending_targets = await asyncio.to_thread(self.store.pending_prediction_targets)
        for target_id in pending_targets & processed_ids:
            await asyncio.to_thread(self.store.refresh_prediction_target, target_id)
        if pending_targets & processed_ids:
            pending_targets = await asyncio.to_thread(self.store.pending_prediction_targets)
        self.observation_phase = "загрузка списка игр"
        list_started = loop.time()
        async with self.page_lock:
            if not await self._probe_observation_auth_locked():
                return
            list_path = urlparse(settings.game_list_url).path.rstrip("/")
            current_path = urlparse(self.page.url).path.rstrip("/")
            if current_path != list_path:
                await self.page.goto(settings.game_list_url, wait_until="domcontentloaded", timeout=30_000)
            await self.page.locator("li.dashboard-champ-body").first.wait_for(timeout=6_000)
            try:
                await self.page.wait_for_function(
                    "() => document.querySelectorAll('.dashboard-game-block__link[href]').length > 0",
                    timeout=2_000,
                )
            except PlaywrightTimeoutError:
                pass
            try:
                await self.page.wait_for_function(
                    """() => {
                      const blocks = [...document.querySelectorAll(
                        '.dashboard-game-block.dashboard-game__block'
                      )];
                      if (!blocks.length) {
                        return document.querySelectorAll('.dashboard-game-block__link[href]').length > 0;
                      }
                      return blocks.some(block => {
                        const node = block.querySelector('.dashboard-game-info__additional-info');
                        return /^\\d+$/.test(node?.textContent?.trim() || '');
                      });
                    }""",
                    timeout=3_000,
                )
            except PlaywrightTimeoutError:
                pass
            all_games = await self._read_game_list_snapshot()
        for _attempt in range(3):
            if all(item.round_number for item in all_games):
                break
            await asyncio.sleep(min(max(settings.card_poll_seconds, 0.1), 0.25))
            async with self.page_lock:
                try:
                    fresh_games = await self._read_game_list_snapshot()
                except Exception:
                    fresh_games = []
            if fresh_games:
                all_games = fresh_games
        await asyncio.to_thread(self.store.update_game_list_metadata, all_games)
        still_missing = [item for item in all_games if not item.round_number]
        if still_missing:
            self.event("WARNING", f"Номер не найден в {len(still_missing)} строках списка после атомарных повторов; ID не подставляется.")
        self.event("INFO", f"Список игр прочитан за {(loop.time() - list_started) * 1000:.0f} мс; найдено строк: {len(all_games)}.")

        for item in all_games:
            self._set_lifecycle_state(item.game_id, "DISCOVERED",
                                      f"dom_position={item.position + 1} round_number={item.round_number or 'unknown'}")

        now = loop.time()
        deferred = {game_id for game_id, due in retry_after.items() if due > now}
        self.game_diagnostics = []
        for item in all_games:
            state = classify_game(item)
            if item.game_id in processed_ids:
                reason = "Окончательный набор уже сохранён; повторно не открываем."
            elif item.game_id in deferred:
                reason = "Повторная проверка отложена после временной ошибки или тайм-аута."
            elif state == "FINISHED":
                reason = "Игра завершена — пропуск."
            elif state == "STARTED":
                reason = "Игра уже началась — пропуск при первоначальном выборе."
            elif state == "UPCOMING":
                reason = f"До начала {item.time}; счёт 0:0 — подходит для выбора."
            else:
                reason = "Состояние UNKNOWN: явный предматчевый таймер и счёт 0:0 не подтверждены — пропуск."
            if not item.round_number:
                reason += f" {item.round_number_diagnostic}"
            self.game_diagnostics.append({
                "game_id": item.game_id, "url": item.url, "position": str(item.position + 1),
                "round_number": item.round_number or None,
                "time": item.time or "не указан", "site_status": item.status,
                "status_text": item.status_text,
                "state": state, "timer_evidence": item.timer_evidence,
                "status_evidence": item.state_evidence,
                "is_countdown_to_start": item.is_countdown_to_start,
                "score": f"{item.player_score or '—'} : {item.banker_score or '—'}", "reason": reason,
            })
            if item.game_id in pending_targets and state not in {"UPCOMING", "STARTED"}:
                await asyncio.to_thread(
                    self.store.break_incoming_transition, item.game_id,
                    f"Следующая игра пропущена: состояние {state}; прогноз через неё не переносится.",
                )

        signature = tuple((item.game_id, classify_game(item), item.round_number,
                           item.player_score, item.banker_score, item.url) for item in all_games)
        if signature != getattr(self, "_last_no_game_signature", None):
            self.event("INFO", f"Сканирование списка: найдено {len(all_games)} игр.")
            self._log_game_list_snapshot(all_games)
            for item in all_games:
                state = classify_game(item)
                if state == "FINISHED":
                    summary = "завершена — пропуск"
                elif state == "STARTED":
                    summary = "уже началась — пропуск"
                elif state == "UPCOMING":
                    summary = f"до начала {item.time}, счёт {item.player_score}:{item.banker_score}"
                else:
                    summary = "UNKNOWN — таймер до начала не подтверждён, пропуск"
                self.event("INFO", f"Раздача № {item.round_number or 'не указана'}: {summary}.")
            self._last_no_game_signature = signature

        choose_started = loop.time()
        excluded_ids = set(processed_ids) | deferred
        invalidated_ids: set[str] = set()
        game: Game | None = None
        while True:
            pending_candidate = next((
                item for item in sorted(all_games, key=lambda value: value.position)
                if item.game_id in pending_targets
                and item.game_id not in excluded_ids
                and item.game_id not in invalidated_ids
                and classify_game(item) in {"UPCOMING", "STARTED"}
            ), None)
            candidate = pending_candidate or choose_next_game(
                all_games, excluded_ids | invalidated_ids
            )
            if candidate is None:
                break
            async with self.page_lock:
                fresh_games = await self._read_game_list_snapshot()
            await asyncio.to_thread(self.store.update_game_list_metadata, fresh_games)
            observed = next((item for item in fresh_games if item.game_id == candidate.game_id), None)
            if observed is not None and is_same_upcoming_game(candidate, observed):
                game = observed
                all_games = fresh_games
                break
            self.event(
                "WARNING",
                f"Раздача № {candidate.round_number or 'не указана'} изменилась перед открытием "
                f"(состояние {classify_game(observed) if observed else 'строка отсутствует'}); пропускаем и перечитываем список.",
            )
            invalidated_ids.add(candidate.game_id)
            all_games = fresh_games
            for target_id in pending_targets & invalidated_ids:
                await asyncio.to_thread(
                    self.store.break_incoming_transition, target_id,
                    "Целевая игра изменила состояние до открытия; прогнозная цепочка прервана.",
                )
        self.event("INFO", f"Проверка кандидата завершена за {(loop.time()-choose_started)*1000:.1f} мс.")
        if game is None:
            self._queued_games = []
            self.observation_status = "WAITING_FOR_GAME"
            self.observation_diagnostic = ("Игр до начала со счётом 0:0 пока нет. Повторяем сканирование."
                                           if all_games else "Игровые строки баккары пока не найдены.")
            if all_games and signature != getattr(self, "_last_no_game_wait_signature", None):
                self.event("INFO", "Игр до начала со счётом 0:0 пока нет. Повторяем сканирование.")
                self._last_no_game_wait_signature = signature
            await asyncio.sleep(max(settings.observation_poll_seconds, 0.25))
            return
        # Reserve only later rows that are still explicitly UPCOMING. Stop
        # at the first unhandled non-upcoming row so the queue can never jump
        # over a game that belongs between two reserved entries.
        planned: list[Game] = []
        for item in sorted(all_games, key=lambda value: value.position):
            if item.position <= game.position or item.game_id in excluded_ids:
                continue
            state = classify_game(item)
            if state == "UPCOMING":
                planned.append(item)
                continue
            if item.game_id not in processed_ids:
                break
        self._queued_games = planned
        if planned:
            self.event(
                "INFO",
                "Зарезервированы следующие раздачи без перезагрузки списка: "
                + ", ".join(item.round_number or item.game_id for item in planned),
            )
        await self._open_planned_game(game, processed_ids, retry_after)

    async def _scan_selected_game_legacy(
        self,
        game: Game,
        previous_cards: list[dict[str, Any]],
        processed_ids: set[str],
        retry_after: dict[str, float],
    ) -> None:
        """Poll one game page until the exact finish text, timeout, access loss, or stop."""
        assert self.page is not None
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        timeout = max(settings.game_finish_timeout_seconds, 1)
        deadline = started_at + timeout
        poll_seconds = max(settings.observation_poll_seconds, 0.25)
        cards = previous_cards
        last_diagnostics: dict[str, Any] | None = None
        last_saved_signature: tuple[Any, ...] | None = None
        self.observation_phase = "переход к выбранной игре"

        try:
            # Persist metadata from the selected list row before page.goto replaces it.
            selection_saved = self._remember_game_scan(
                game, cards, "PARTIAL", "Selected from game list; round and URL saved before navigation.",
                "SCANNING", finished_confirmed=False,
            )
            if not selection_saved:
                raise RuntimeError("Selected game metadata was not persisted before navigation")
            async with self.page_lock:
                if not await self._probe_observation_auth_locked():
                    return
                await self.page.goto(game.url, wait_until="domcontentloaded", timeout=20_000)
                expected_path = urlparse(game.url).path.rstrip("/")
                actual_path = urlparse(self.page.url).path.rstrip("/")
                if actual_path != expected_path:
                    raise RuntimeError(f"После перехода открыт неожиданный URL: {self.page.url}")
                if not await self._probe_observation_auth_locked():
                    return

            self.event("INFO", f"Открыта страница игры {game.game_id}; ожидаем игровое поле и точный флаг завершения.")
            initial_wait = min(max(settings.game_page_wait_seconds, 0), max(deadline - loop.time(), 0))
            if initial_wait:
                await asyncio.sleep(initial_wait)
            while loop.time() < deadline:
                async with self.page_lock:
                    if not await self._probe_observation_auth_locked():
                        return
                    diagnostics = await self._inspect_game_dom(settings.player_name_text, settings.banker_name_text)
                    finish_seen = bool(diagnostics.get("finished"))
                    if finish_seen:
                        first_finish_text = diagnostics.get("finish_flag_text", "Игра завершена")
                        final_diagnostics = await self._inspect_game_dom(settings.player_name_text, settings.banker_name_text)
                        final_diagnostics["finish_flag_seen_on_final_read"] = bool(final_diagnostics.get("finished"))
                        final_diagnostics["finished"] = True
                        final_diagnostics["finish_flag_text"] = (
                            final_diagnostics.get("finish_flag_text") or first_finish_text
                        )
                        diagnostics = final_diagnostics

                last_diagnostics = diagnostics
                status_text = diagnostics.get("status_text", "")
                page_status = classify_game_status(status_text)
                if page_status != "Не удалось определить":
                    game.status = page_status
                    game.status_text = status_text
                if finish_seen:
                    game.status = "Завершена"

                observed_cards = diagnostics.get("cards", [])
                new_cards = merge_card_snapshots(cards, observed_cards)
                changed_cards = new_cards != cards
                if changed_cards:
                    old_by_position = {card.get("position", i + 1): card for i, card in enumerate(cards)}
                    for index, card in enumerate(new_cards):
                        position = card.get("position", index + 1)
                        if old_by_position.get(position) != card:
                            symbol = self._suit_symbol(card.get("suit", ""))
                            self.event("INFO", f"Карта на позиции {position}: {card.get('rank', '?')} {symbol}.")
                    cards = new_cards

                counts = diagnostics.get("counts", {})
                player_present = counts.get("player_fields", 0) > 0
                container_present = counts.get("player_cards_containers", 0) > 0
                box_count = counts.get("player_card_boxes", 0)
                pending = int(diagnostics.get("pending_card_slots", 0))
                scan_result, completeness = classify_player_scan(
                    player_field_present=player_present,
                    cards_container_present=container_present,
                    card_boxes_count=box_count,
                    cards_count=len(observed_cards),
                    pending_card_slots=pending,
                    finished=finish_seen,
                )
                final_reverified = bool(finish_seen and observed_cards and observed_cards == cards and pending == 0)
                cause = diagnostics.get("cause", "DOM-диагностика недоступна")
                if finish_seen:
                    if scan_result == "CARDS_CONFIRMED" and final_reverified:
                        reason = f"Точный флаг «Игра завершена» найден; финальные карты перечитаны и подтверждены. {cause}"
                    elif cards and not final_reverified:
                        reason = (
                            f"Флаг «Игра завершена» найден, но финальный DOM не подтвердил ранее считанный набор; "
                            f"сохранён последний подтверждённый снимок. {cause}"
                        )
                    else:
                        reason = f"Флаг «Игра завершена» найден, но карты не подтверждены в финальном DOM. {cause}"
                elif not player_present:
                    reason = f"Ожидаем игровое поле Игрока; {cause}"
                elif not container_present or box_count == 0:
                    reason = f"Ожидаем позиции карт в контейнере Игрока; {cause}"
                elif pending:
                    reason = f"Карты раскрываются: подтверждено {len(observed_cards)}, незаполненных/неподтверждённых позиций {pending}. {cause}"
                else:
                    reason = f"Карты Игрока обновлены ({len(observed_cards)}); ожидаем точный флаг завершения. {cause}"

                previous_current = self.current_observation or {}
                current = dict(previous_current)
                current.update({
                    "game_id": game.game_id,
                    "url": game.url,
                    "position": game.position + 1,
                    "round_number": game.round_number,
                    "round_number_diagnostic": game.round_number_diagnostic,
                    "time": game.time,
                    "site_status": game.status,
                    "status_text": game.status_text,
                    "player_score": game.player_score,
                    "banker_score": game.banker_score,
                    "cards": cards,
                    "cards_count": len(cards),
                    "pending_card_slots": pending,
                    "scan_result": scan_result,
                    "finished_confirmed": finish_seen,
                    "final_snapshot_reverified": final_reverified if finish_seen else None,
                    "reason": reason,
                    "diagnostics": diagnostics,
                })
                if current != previous_current:
                    self.state_version = getattr(self, "state_version", 0) + 1
                self.current_observation = current

                if finish_seen:
                    self.observation_status = "GAME_FINISHED"
                elif not player_present:
                    self.observation_status = "WAITING_FOR_GAME"
                elif not container_present or box_count == 0:
                    self.observation_status = "WAITING_FOR_CARDS"
                elif pending:
                    self.observation_status = "PARTIAL_CARDS"
                elif changed_cards:
                    self.observation_status = "CARDS_DETECTED"
                elif cards:
                    self.observation_status = "WAITING_FOR_FINISH"
                else:
                    self.observation_status = "WAITING_FOR_CARDS"
                self.observation_diagnostic = reason

                card_signature = tuple(
                    (item.get("position"), item.get("rank"), item.get("suit")) for item in cards
                )
                save_signature = (card_signature, pending, scan_result, game.status, finish_seen, reason)
                if save_signature != last_saved_signature:
                    saved = self._remember_game_scan(
                        game,
                        cards,
                        completeness,
                        reason,
                        scan_result,
                        pending,
                        finished_confirmed=finish_seen,
                        diagnostics=diagnostics,
                        final_snapshot_reverified=final_reverified if finish_seen else None,
                    )
                    last_saved_signature = save_signature
                else:
                    saved = True

                if finish_seen:
                    if saved:
                        processed_ids.add(game.game_id)
                        retry_after.pop(game.game_id, None)
                        completed_record = self.store.get(game.game_id) or {}
                        self._active_chain_source_id = (
                            game.game_id if completed_record.get("calculation_status") == "complete"
                            and completed_record.get("finished_confirmed") else None
                        )
                        self.observation_status = "SAVED"
                        self.observation_diagnostic = (
                            f"Завершение игры подтверждено точным текстом; запись {game.game_id} сохранена в истории."
                            if scan_result == "CARDS_CONFIRMED" and final_reverified
                            else f"Игра завершена и записана как неполная: {game.game_id}. Причина сохранена в истории."
                        )
                        self.event("INFO", f"Обнаружен точный флаг «Игра завершена» для {game.game_id}.")
                        self.event("INFO", f"Результат раздачи {game.game_id} сохранён в истории.")
                        self.event("INFO", "Возвращаемся к списку, чтобы выбрать следующую игру.")
                        await asyncio.sleep(max(poll_seconds, 0.5))
                    else:
                        self._active_chain_source_id = None
                        retry_after[game.game_id] = loop.time() + max(settings.incomplete_retry_seconds, 5)
                        self.observation_status = "ERROR"
                        self.observation_diagnostic = "Флаг завершения найден, но подтверждения записи в истории нет; игра не отмечена обработанной."
                        self.event("ERROR", self.observation_diagnostic)
                    return

                await asyncio.sleep(poll_seconds)

            timeout_reason = (
                f"Точный флаг «Игра завершена» не появился за {timeout:g} секунд; "
                "сохранено только промежуточное наблюдение, переход к следующей игре выполнен по тайм-ауту."
            )
            diagnostics = last_diagnostics or {
                "url": game.url,
                "frame_count": 0,
                "iframe_count": 0,
                "frames": [],
                "counts": {},
                "cards": [],
                "pending_card_slots": 0,
                "finished": False,
                "status_text": "",
                "cause": "Страница не предоставила игровой DOM.",
                "dom_fragment": "",
            }
            counts = diagnostics.get("counts", {})
            observed_cards = diagnostics.get("cards", [])
            pending = int(diagnostics.get("pending_card_slots", 0))
            scan_result, completeness = classify_player_scan(
                player_field_present=counts.get("player_fields", 0) > 0,
                cards_container_present=counts.get("player_cards_containers", 0) > 0,
                card_boxes_count=counts.get("player_card_boxes", 0),
                cards_count=len(observed_cards),
                pending_card_slots=pending,
                finished=False,
            )
            reason = f"{timeout_reason} {diagnostics.get('cause', '')}"
            saved = self._remember_game_scan(
                game, cards, completeness, reason, scan_result, pending,
                finished_confirmed=False, diagnostics=diagnostics,
            )
            retry_after[game.game_id] = loop.time() + max(settings.incomplete_retry_seconds, 5)
            self.observation_status = "INCOMPLETE" if saved else "ERROR"
            self._active_chain_source_id = None
            self.observation_diagnostic = reason if saved else "Не удалось сохранить промежуточную запись после тайм-аута."
            self.event("WARNING", self.observation_diagnostic)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = f"Не удалось завершить наблюдение игры {game.game_id}: {type(exc).__name__}. {str(exc)[:240]}"
            try:
                self._remember_game_scan(
                    game, cards, "PARTIAL", reason, "NAVIGATION_ERROR",
                    int((last_diagnostics or {}).get("pending_card_slots", 0)),
                    finished_confirmed=False, diagnostics=last_diagnostics,
                )
                self.store.break_incoming_transition(
                    game.game_id, "Navigation error; incoming forecast chain has been stopped."
                )
            finally:
                self._active_chain_source_id = None
                retry_after[game.game_id] = loop.time() + max(settings.incomplete_retry_seconds, 5)
                self.observation_status = "ERROR"
                self.observation_diagnostic = reason
                self.event("ERROR", reason)
                log.exception("Ошибка наблюдения игры %s (%s).", game.game_id, type(exc).__name__)
    async def _scan_selected_game(
        self,
        game: Game,
        previous_cards: list[dict[str, Any]],
        processed_ids: set[str],
        retry_after: dict[str, float],
    ) -> None:
        """Read cards frequently; finish on a stable three-card hand or a trusted two-card end signal."""
        assert self.page is not None
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        diagnostic_interval = min(max(settings.game_finish_timeout_seconds, 1),
                                  max(getattr(settings, "player_hand_timeout_seconds", 90), 1))
        next_diagnostic_at = started_at + diagnostic_interval
        poll_seconds = min(max(getattr(settings, "card_poll_seconds", 0.15), 0.1), 0.25)
        cards = previous_cards
        last_diagnostics: dict[str, Any] | None = None
        last_saved_signature: tuple[Any, ...] | None = None
        first_card_at: dict[int, float] = {}
        component_logged = False
        list_finished = game.status == "Завершена" or is_finished_status(game.status_text)
        self.observation_phase = "переход к выбранной игре"

        try:
            selection_saved = await self._remember_game_scan(
                game, cards, "PARTIAL", "Метаданные раунда сохранены до навигации.",
                "SCANNING", finished_confirmed=False,
            )
            if not selection_saved:
                raise RuntimeError("Не удалось сохранить метаданные выбранной игры")

            self._set_lifecycle_state(game.game_id, "OPENING")
            navigation_started = loop.time()
            async with self.page_lock:
                if not await self._probe_observation_auth_locked():
                    return
                await self.page.goto(game.url, wait_until="domcontentloaded", timeout=20_000)
                expected_path = urlparse(game.url).path.rstrip("/")
                actual_path = urlparse(self.page.url).path.rstrip("/")
                if actual_path != expected_path:
                    raise RuntimeError(f"После перехода открыт неожиданный URL: {self.page.url}")
            page_ready_at = loop.time()
            self._set_lifecycle_state(game.game_id, "OBSERVING")
            self.event("INFO", f"Страница раздачи № {game.round_number or game.game_id} открыта за {(page_ready_at-navigation_started)*1000:.0f} мс; начинаем опрос DOM без стартовой паузы.")

            while True:
                if getattr(self, "authorization", "AUTHORIZED") == "AUTH_LOST":
                    self._active_chain_source_id = None
                    return
                async with self.page_lock:
                    diagnostics = await self._inspect_game_dom(settings.player_name_text, settings.banker_name_text)
                    in_page_finished = bool(diagnostics.get("finished"))
                    if in_page_finished:
                        finish_text = diagnostics.get("finish_flag_text") or "Игра завершена"
                        reread = await self._inspect_game_dom(settings.player_name_text, settings.banker_name_text)
                        reread["finish_flag_seen_on_final_read"] = bool(reread.get("finished"))
                        reread["finished"] = True
                        reread["finish_flag_text"] = reread.get("finish_flag_text") or finish_text
                        diagnostics = reread
                finish_seen = in_page_finished or list_finished
                last_diagnostics = diagnostics
                status_text = diagnostics.get("status_text", "")
                page_status = classify_game_status(status_text)
                if page_status != "Не удалось определить":
                    game.status, game.status_text = page_status, status_text
                if finish_seen:
                    game.status = "Завершена"

                observed_cards = diagnostics.get("cards", [])
                merged = merge_card_snapshots(cards, observed_cards)
                changed_cards = merged != cards
                if changed_cards:
                    old_by_position = {card.get("position", index + 1): card for index, card in enumerate(cards)}
                    for index, card in enumerate(merged):
                        position = card.get("position", index + 1)
                        if old_by_position.get(position) != card:
                            self.event("INFO", f"Карта позиции {position}: {card.get('rank', '?')} {self._suit_symbol(card.get('suit', ''))}.")
                    cards = merged

                counts = diagnostics.get("counts", {})
                player_present = counts.get("player_fields", 0) > 0
                container_present = counts.get("player_cards_containers", 0) > 0
                box_count = int(counts.get("player_card_boxes", 0))
                pending = int(diagnostics.get("pending_card_slots", 0))
                if player_present and not component_logged:
                    component_logged = True
                    self.event("INFO", f"Игровой компонент доступен через {(loop.time()-page_ready_at)*1000:.0f} мс после DOMContentLoaded.")

                for card in observed_cards:
                    position = int(card.get("position", 0))
                    if position in (1, 2, 3) and position not in first_card_at:
                        first_card_at[position] = loop.time()
                        labels = {1: "Первая", 2: "Вторая", 3: "Третья"}
                        self.event("INFO", f"{labels[position]} карта обнаружена через {(first_card_at[position]-page_ready_at)*1000:.0f} мс после загрузки страницы.")

                exact_two = (len(observed_cards) == 2 and box_count == 2 and pending == 0
                             and [item.get("position") for item in observed_cards] == [1, 2])
                exact_three = (len(observed_cards) == 3 and box_count == 3 and pending == 0
                               and [item.get("position") for item in observed_cards] == [1, 2, 3])
                confirmed_three = exact_three
                confirmed_two = False
                if finish_seen and exact_two:
                    # The visible end flag or explicit finished list status is the signal;
                    # a repeated read verifies the same two positions without an arbitrary delay.
                    async with self.page_lock:
                        final_read = await self._inspect_game_dom(settings.player_name_text, settings.banker_name_text)
                    confirmed_two = (final_read.get("cards", []) == observed_cards
                                     and final_read.get("pending_card_slots", 0) == 0
                                     and final_read.get("counts", {}).get("player_card_boxes", 0) == 2)
                hand_final = bool(confirmed_three or confirmed_two)
                if confirmed_three:
                    # This one atomic frame snapshot contains all three positions;
                    # don't carry any older per-position values into the final record.
                    cards = [dict(item) for item in observed_cards]
                final_reverified = hand_final
                scan_result, completeness = classify_player_scan(
                    player_field_present=player_present,
                    cards_container_present=container_present,
                    card_boxes_count=box_count,
                    cards_count=len(observed_cards),
                    pending_card_slots=pending,
                    finished=hand_final,
                )
                cause = diagnostics.get("cause", "DOM-диагностика недоступна")
                if confirmed_three:
                    reason = f"В одном атомарном DOM-снимке прочитаны три полные позиции Игрока; ждать завершения игры не требуется. {cause}"
                elif confirmed_two:
                    reason = f"Подтверждены две окончательные карты: получен явный сигнал завершения раздачи и набор перечитан. {cause}"
                elif finish_seen and cards:
                    reason = f"Игра завершена, но окончательный набор карт не подтверждён; сохранён последний достоверный снимок. {cause}"
                elif finish_seen:
                    reason = f"Игра завершена, но достоверные карты Игрока не прочитаны. {cause}"
                elif not player_present:
                    reason = f"Ожидаем DOM игрового поля Игрока. {cause}"
                elif pending:
                    reason = f"Карты раскрываются: прочитано {len(observed_cards)}, неподтверждённых позиций {pending}. {cause}"
                else:
                    reason = f"Наблюдаем карты Игрока ({len(observed_cards)}); для двух карт ждём достоверный сигнал окончания набора. {cause}"

                previous_current = self.current_observation or {}
                current = dict(previous_current)
                current.update({
                    "game_id": game.game_id, "url": game.url, "position": game.position + 1,
                    "round_number": game.round_number, "round_number_diagnostic": game.round_number_diagnostic,
                    "time": game.time, "site_status": game.status, "status_text": game.status_text,
                    "player_score": game.player_score, "banker_score": game.banker_score,
                    "cards": cards, "cards_count": len(cards), "pending_card_slots": pending,
                    "scan_result": scan_result, "finished_confirmed": finish_seen,
                    "player_hand_final": hand_final,
                    "final_snapshot_reverified": final_reverified if hand_final else None,
                    "lifecycle_state": "CARDS_COMPLETE" if hand_final else "OBSERVING",
                    "reason": reason, "diagnostics": diagnostics,
                })
                if current != previous_current:
                    self.state_version = getattr(self, "state_version", 0) + 1
                self.current_observation = current
                if hand_final:
                    self._set_lifecycle_state(game.game_id, "CARDS_COMPLETE")
                self.observation_status = ("SAVING_RESULT" if hand_final else
                    "GAME_FINISHED" if finish_seen else
                    "WAITING_FOR_GAME" if not player_present else
                    "WAITING_FOR_CARDS" if not container_present or box_count == 0 else
                    "PARTIAL_CARDS" if pending else
                    "CARDS_DETECTED" if changed_cards else "WAITING_FOR_FINISH")
                self.observation_diagnostic = reason

                card_signature = tuple((item.get("position"), item.get("rank"), item.get("suit")) for item in cards)
                save_signature = (card_signature, pending, scan_result, finish_seen, hand_final, reason)
                saved = True
                if save_signature != last_saved_signature:
                    persist_started = loop.time()
                    saved = await self._remember_game_scan(
                        game, cards, completeness, reason, scan_result, pending,
                        finished_confirmed=finish_seen,
                        player_hand_final=hand_final,
                        diagnostics=diagnostics,
                        final_snapshot_reverified=final_reverified if hand_final else None,
                    )
                    last_saved_signature = save_signature
                    self.event("INFO", f"Снимок раздачи сохранён за {(loop.time()-persist_started)*1000:.0f} мс.")

                if hand_final:
                    if saved:
                        processed_ids.add(game.game_id)
                        retry_after.pop(game.game_id, None)
                        completed_record = await asyncio.to_thread(self.store.get, game.game_id) or {}
                        self._set_lifecycle_state(game.game_id, "FINALIZED")
                        incoming_result = completed_record.get("prediction_result", "NONE")
                        self._set_lifecycle_state(game.game_id, "PREDICTION_EVALUATED",
                                                  f"incoming_result={incoming_result}")
                        incoming_source = completed_record.get("source_game_id")
                        if incoming_source:
                            self.event("INFO", f"PREDICTION_RESULT source_game_id={incoming_source} "
                                       f"target_game_id={game.game_id} result={incoming_result}")
                        self._active_chain_source_id = (
                            game.game_id if completed_record.get("calculation_status") == "complete"
                            and completed_record.get("player_hand_final") else None
                        )
                        self.observation_status = "SAVED"
                        self.observation_diagnostic = (
                            f"Окончательные карты раздачи № {game.round_number or game.game_id} сохранены; переходим к следующей игре."
                            if hand_final else f"Игра завершена, но итог карт не подтверждён; запись сохранена без прогноза."
                        )
                        if hand_final:
                            last_card_time = max(first_card_at.values(), default=loop.time())
                            self.event("INFO", f"Набор Игрока в раздаче № {game.round_number or game.game_id} подтверждён за {(loop.time()-last_card_time)*1000:.0f} мс после последней карты; общий этап занял {(loop.time()-started_at)*1000:.0f} мс.")
                        else:
                            self.event("WARNING", f"Раздача № {game.round_number or game.game_id} завершилась без достоверного финального набора; WIN/LOSE не выводится.")
                        self.event("INFO", f"Следующий переход подготовлен за {(loop.time()-started_at)*1000:.0f} мс после начала чтения.")
                    else:
                        self._active_chain_source_id = None
                        retry_after[game.game_id] = loop.time() + max(settings.incomplete_retry_seconds, 5)
                        self.observation_status = "ERROR"
                        self.observation_diagnostic = "Снимок не сохранён; игра не отмечена обработанной."
                        self.event("ERROR", self.observation_diagnostic)
                    return

                await asyncio.sleep(poll_seconds)
                if loop.time() >= next_diagnostic_at:
                    reason = (f"Ожидание финального набора длится более {diagnostic_interval:g} секунд; "
                              "раздача остаётся текущей, тайм-аут не завершает её и не запускает следующую.")
                    self.observation_status = "WAITING_FOR_FINISH" if cards else "WAITING_FOR_CARDS"
                    self.observation_diagnostic = reason
                    self.event("WARNING", reason)
                    next_diagnostic_at = loop.time() + diagnostic_interval
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = f"Не удалось прочитать раздачу {game.game_id}: {type(exc).__name__}. {str(exc)[:200]}"
            try:
                await self._remember_game_scan(game, cards, "PARTIAL", reason, "NAVIGATION_ERROR",
                    int((last_diagnostics or {}).get("pending_card_slots", 0)),
                    finished_confirmed=False, diagnostics=last_diagnostics)
                await asyncio.to_thread(self.store.break_incoming_transition, game.game_id,
                                        "Ошибка чтения; входящий прогноз не объявляется LOSE.")
            finally:
                self._active_chain_source_id = None
                retry_after[game.game_id] = loop.time() + max(settings.incomplete_retry_seconds, 5)
                self.observation_status = "ERROR"
                self.observation_diagnostic = reason
                self.event("ERROR", reason)
                log.exception("Ошибка наблюдения игры %s (%s).", game.game_id, type(exc).__name__)

    @staticmethod
    def _suit_symbol(suit: str) -> str:
        return {"hearts": "♥", "diamonds": "♦", "clubs": "♣", "spades": "♠"}.get(suit, "?")

    async def _recover_to_game_list(self) -> None:
        if self.page is None or self.page.is_closed() or not authorization_allows_observation(self.authorization):
            return
        try:
            self.observation_phase = "переход к списку для восстановления"
            async with self.page_lock:
                await self.page.goto(settings.game_list_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            log.exception("Не удалось вернуться к списку игр для восстановления (%s).", type(exc).__name__)

    async def _monitor_auth(self) -> None:
        while self.context is not None and self.page is not None:
            try:
                async with self.page_lock:
                    result, diagnostic = await self._is_authorized()
                previous = self.authorization
                self.authorization = result
                self.diagnostic = diagnostic
                if result == "AUTHORIZED":
                    self.status = "AUTHORIZED"
                    if previous != "AUTHORIZED":
                        self.event("INFO", "Авторизация подтверждена.")
                elif result == "TEMPORARILY_UNCONFIRMED":
                    self.status = "AUTH_TEMPORARY"
                    if previous != "TEMPORARILY_UNCONFIRMED":
                        self.event("WARNING", "Признак входа временно недоступен. Повторяем проверку на этой странице.")
                elif result == "AUTH_LOST":
                    self.status = "AUTH_LOST"
                    if previous != "AUTH_LOST":
                        self.event("WARNING", "Сайт показал страницу входа. Проверьте авторизацию в окне браузера.")
                else:
                    self.status = "AUTH_CHECK_ERROR"
                    if previous != "AUTH_CHECK_ERROR":
                        self.event("WARNING", "Проверка авторизации временно завершилась ошибкой. Повторяем проверку.")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                previous = self.authorization
                self.authorization = "AUTH_CHECK_ERROR"
                self.status = "AUTH_CHECK_ERROR"
                self.diagnostic = "Не удалось проверить состояние авторизации. Проверка будет повторена."
                if previous != "AUTH_CHECK_ERROR":
                    self.event("WARNING", self.diagnostic)
                log.exception("Ошибка проверки авторизации (%s).", type(exc).__name__)
            await asyncio.sleep(max(settings.auth_poll_seconds, 0.5))

    async def _is_authorized(self) -> tuple[str, str]:
        if self.page is None or self.page.is_closed():
            return "CHECK_ERROR", "Страница браузера временно недоступна. Повторяем проверку."
        # Independent copy of new_bot's manual-login check. Its authenticated
        # balance header renders an exact RUB currency marker; the selector
        # fallbacks support the current and legacy site layouts.
        selectors = tuple(dict.fromkeys(
            selector for selector in (settings.currency_selector, *settings.currency_selector_fallbacks)
            if selector
        ))
        if not selectors or not settings.currency_text:
            return "CHECK_ERROR", "В настройках не указан признак авторизации."
        login_surface = await self.page.evaluate("""() => {
          const path = location.pathname.toLowerCase();
          if (/(?:^|\\/)(?:login|signin|sign-in|auth)(?:\\/|$)/i.test(path)) return true;
          const visible = element => !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
          const passwordVisible = [...document.querySelectorAll('input[type="password"]')].some(visible);
          if (!passwordVisible) return false;
          const actions = [...document.querySelectorAll('button, a, [role="button"], input[type="submit"]')]
            .filter(visible).map(element => (element.innerText || element.value || '').toLowerCase());
          return actions.some(text => /войти|вход|log\\s*in|sign\\s*in/.test(text));
        }""")
        if classify_auth_probe(balance_marker=False, login_surface=login_surface) == "AUTH_LOST":
            return "AUTH_LOST", "На странице отображается форма входа. Требуется ручная проверка авторизации."
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
                    result = classify_auth_probe(balance_marker=True, login_surface=False)
                    return result, "Авторизация подтверждена по индикатору валюты баланса."
                try:
                    if await candidate.get_by_text(settings.currency_text, exact=True).count():
                        result = classify_auth_probe(balance_marker=True, login_surface=False)
                        return result, "Авторизация подтверждена по индикатору валюты баланса."
                except Exception:
                    pass
        # Missing balance/profile markup can be transient during rendering or
        # navigation. Only an explicit login surface proves the session ended.
        result = classify_auth_probe(balance_marker=False, login_surface=False)
        return result, "Признак авторизации временно не найден. Повторяем проверку."

    def snapshot(self) -> dict[str, Any]:
        version = getattr(self, "state_version", 0)
        current = None
        events: list[dict[str, str]] = []
        games: list[dict[str, str]] = []
        history: list[dict[str, Any]] = []
        statistics: dict[str, Any] = {}
        for _ in range(3):
            version = getattr(self, "state_version", 0)
            current = dict(self.current_observation) if self.current_observation else None
            if current and current.get("game_id"):
                persisted = self.store.get(str(current["game_id"]))
                if persisted:
                    for key in ("calculated_suit", "calculation_status", "prediction_for_next_round",
                                "predicted_suit", "prediction_result", "prediction_reason",
                                "incoming_prediction", "outgoing_prediction",
                                "outgoing_target_game_id", "outgoing_target_round_number",
                                "outgoing_prediction_result", "outgoing_prediction_reason"):
                        if key in persisted:
                            current[key] = persisted[key]
                else:
                    current.update(self.store.incoming_prediction_for(str(current["game_id"])))
            events = list(self.events)
            games = [dict(item) for item in self.game_diagnostics]
            history = self.store.history()
            statistics = self.store.statistics()
            if version == getattr(self, "state_version", 0):
                break
        return {"state_epoch": getattr(self, "state_epoch", ""), "state_version": version,
                "backend": "OK", "browser": self.status,
                "authorization": self.authorization, "diagnostic": self.diagnostic, "events": events,
                "observation": {"status": self.observation_status, "current": current,
                "diagnostic": self.observation_diagnostic, "games": games,
                "history": history, "statistics": statistics}}


browser_service = BrowserService()
