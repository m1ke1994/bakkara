"""One regular bookmaker coupon path for both LIVE and first-round checks."""
from __future__ import annotations

import asyncio
import re
from decimal import Decimal

from .live_executor import PlacementError, SUITS, _amount, close_result_dialog, outcome_box
from .live_market_probe import CANVAS_SELECTOR, MarketProbeError, probe_player_suit_market

ONE_CLICK = ".coupon-one-click input.selection__input"
BET_CARD = ".coupon-app .coupon-bets__bet"
QUICK_CARD = ".quick-coupon-main .quick-coupon-events-card"
AMOUNT = (".coupon-app .coupon-amount input.ui-number-input__field, "
          ".quick-coupon-main input.ui-number-input__field")
CONFIRM = (".coupon-app .coupon-buttons button, "
           ".quick-coupon-main button.quick-coupon-put-bet-button")
BALANCE = '[data-gtm="account-balance-value-desktop"]'
SUCCESS_MODAL = '.modal__content[data-test="modal-content"]'
LOCK_TEXT = ".coupon-bet-lock__text, .quick-coupon-events-card-lock__text"


def _check_message(text: str) -> str | None:
    value = text.casefold()
    if any(part in value for part in ("ваша ставка принята", "ставка принята",
                                      "ставка успешно", "ставка размещена")):
        return "ACCEPTED"
    if any(part in value for part in ("ставка отклонена", "недостаточно средств",
                                      "заблокированное событие")):
        return "REJECTED"
    return None


async def _visible(locator):
    for index in range(await locator.count()):
        item = locator.nth(index)
        if await item.is_visible():
            yield item


async def _read_balance(page) -> Decimal | None:
    for item in [item async for item in _visible(page.locator(BALANCE))]:
        text = await item.inner_text()
        number = re.sub(r"[^\d,.]", "", text)
        if number:
            try:
                return _amount(number)
            except PlacementError:
                pass
    return None


async def _selected_card(page, suit: str, odds: float):
    """Wait for exactly one selected outcome and verify it before amount entry."""
    expected = SUITS[suit]
    deadline = asyncio.get_running_loop().time() + 12
    while asyncio.get_running_loop().time() < deadline:
        if [item async for item in _visible(page.locator(LOCK_TEXT))]:
            raise PlacementError("Букмекер заблокировал выбранный рынок")
        cards = [item async for item in _visible(page.locator(BET_CARD + ", " + QUICK_CARD))]
        if len(cards) > 1:
            raise PlacementError(f"В купоне выбрано несколько исходов: {len(cards)}")
        if cards:
            text = " ".join((await cards[0].inner_text()).casefold().split())
            if ("игрок получит карту" not in text or expected not in text.replace("червы", "черви")
                    or str(odds) not in text):
                raise PlacementError(f"Купон не совпал с мастью {expected} и коэффициентом {odds}")
            return cards[0]
        await asyncio.sleep(0.1)
    raise PlacementError("После клика по коэффициенту выбранный исход не появился в купоне")


async def _clear_locked_coupon(page, stage):
    """Remove one stale locked outcome; leave an active human selection alone."""
    cards = [item async for item in _visible(page.locator(BET_CARD))]
    if len(cards) != 1:
        return
    card = cards[0]
    if not [item async for item in _visible(card.locator(".coupon-bet-lock__text"))]:
        return
    remove = card.locator('button.coupon-bet-lock-remove[aria-label="Удалить"]')
    if await remove.count() != 1 or not await remove.is_visible():
        raise PlacementError("Заблокированный исход остался в купоне: кнопка удаления не найдена")
    await remove.click(timeout=3_000)
    await card.wait_for(state="detached", timeout=3_000)
    stage("Купон: удалён заблокированный исход прошлой раздачи")


async def _ready_confirm(page):
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        for item in [item async for item in _visible(page.locator(CONFIRM))]:
            if " ".join((await item.inner_text()).casefold().split()) != "сделать ставку":
                continue
            if not await item.is_disabled():
                return item
        await asyncio.sleep(0.1)
    raise PlacementError("Кнопка «Сделать ставку» в купоне не готова")


async def _submission_result(page, before: str, balance_before: Decimal | None,
                             amount: Decimal, stage) -> str:
    body = page.locator("body").first
    for _ in range(35):
        for modal in [item async for item in _visible(page.locator(SUCCESS_MODAL))]:
            status = _check_message(await modal.inner_text(timeout=2_000))
            if status:
                closed = await close_result_dialog(page)
                stage(f"БК: {status}; модальное окно {'закрыто' if closed else 'не удалось закрыть'}")
                return status
        current = (await body.inner_text(timeout=2_000)).casefold()
        delta = " ".join(part for part in (
            "ваша ставка принята", "ставка принята", "ставка успешно", "ставка размещена",
            "ставка отклонена", "недостаточно средств", "заблокированное событие")
            if current.count(part) > before.count(part))
        status = _check_message(delta)
        if status:
            closed = await close_result_dialog(page)
            stage(f"БК: {status}; модальное окно {'закрыто' if closed else 'не обнаружено'}")
            return status
        await asyncio.sleep(0.2)
    balance_after = await _read_balance(page)
    if (balance_before is not None and balance_after is not None
            and abs(balance_before - balance_after - amount) <= Decimal("0.01")):
        closed = await close_result_dialog(page)
        stage("БК: ACCEPTED; списание точной суммы подтверждено балансом; "
              f"модальное окно {'закрыто' if closed else 'не обнаружено'}")
        return "ACCEPTED"
    stage("БК: подтверждение не обнаружено; автоматический повтор запрещён")
    return "UNKNOWN"


async def place_regular_coupon(page, suit: str, amount: Decimal, mark_submitting,
                               report=None) -> str:
    """Search Canvas, select odds, verify coupon, fill step, submit exactly once."""
    def stage(message: str) -> None:
        if report:
            report(message)

    stage("Поиск: ожидаем рынок «Игрок получит карту»")
    try:
        probe = await probe_player_suit_market(page)
    except MarketProbeError as exc:
        raise PlacementError(f"Поиск рынка: {exc}") from exc
    stage(f"Canvas: {probe.get('diagnostic', probe.get('status'))}; "
          f"слой={probe.get('captureSource', 'неизвестен')}; "
          f"размер={probe.get('canvas', {}).get('width')}x{probe.get('canvas', {}).get('height')}; "
          f"подписей={len(probe.get('labels') or [])}; "
          f"коэффициентов={len(probe.get('oddsBySuit') or {})}")
    for label in probe.get("labels") or []:
        odds = (probe.get("oddsBySuit") or {}).get(label["text"])
        stage(f"Canvas: {label.get('rawText', label['text'])} — "
              f"{odds.get('amount') if odds else 'не найден'} — "
              f"{(round(odds['x'], 1), round(odds['y'], 1)) if odds else 'без координат'}")
    try:
        box = outcome_box(probe, suit)
    except PlacementError as exc:
        raise PlacementError(f"{exc}; диагностика: {probe.get('diagnostic', 'неизвестно')}") from exc
    stage(f"Canvas: выбран исход {SUITS[suit]}, коэффициент {box['odds']}, "
          f"координаты {box['x']:.1f}/{box['y']:.1f}")

    toggle = page.locator(ONE_CLICK).first
    if await toggle.count() != 1:
        raise PlacementError("Не удалось определить переключатель ставки в 1 клик")
    if await toggle.is_checked():
        stage("Купон: отключаем ставку в 1 клик перед выбором коэффициента")
        await toggle.uncheck(force=True, timeout=3_000)
        if await toggle.is_checked():
            raise PlacementError("Ставка в 1 клик всё ещё включена: выбор исхода остановлен")

    await _clear_locked_coupon(page, stage)
    existing = [item async for item in _visible(page.locator(BET_CARD + ", " + QUICK_CARD))]
    if existing:
        raise PlacementError("В купоне уже есть исход; автоматическое добавление остановлено")

    canvas = page.locator(CANVAS_SELECTOR).first
    bounds = await canvas.bounding_box()
    if not bounds or not bounds["width"] or not bounds["height"]:
        raise PlacementError("Canvas без видимой области — клик остановлен")
    position = {"x": box["x"] * bounds["width"] / box["width"],
                "y": box["y"] * bounds["height"] / box["height"]}
    await canvas.hover(position=position, timeout=3_000)
    try:
        await page.wait_for_function("""() =>
            getComputedStyle(document.querySelector('canvas.market-grid-canvas__canvas')).cursor === 'pointer'""",
            timeout=2_000)
    except Exception as exc:
        raise PlacementError("Коэффициент Canvas сейчас неактивен: указатель не стал кликабельным") from exc
    await canvas.click(position=position, timeout=3_000)
    stage("Canvas: выполнен клик по коэффициенту нужной масти")
    await _selected_card(page, suit, box["odds"])
    stage("Купон: выбранный рынок, масть и коэффициент проверены")

    amount_fields = [item async for item in _visible(page.locator(AMOUNT))]
    if len(amount_fields) != 1:
        raise PlacementError(f"В купоне найдено полей суммы: {len(amount_fields)}")
    amount_field = amount_fields[0]
    value = format(amount, "f")
    await amount_field.fill("")
    await amount_field.fill(value)
    if _amount(await amount_field.input_value()) != amount:
        raise PlacementError("В купоне сумма не совпала с выбранным шагом")
    stage(f"Купон: сумма {value} RUB введена и проверена")

    confirm = await _ready_confirm(page)
    await _selected_card(page, suit, box["odds"])
    if _amount(await amount_field.input_value()) != amount:
        raise PlacementError("Сумма ставки изменилась перед подтверждением")
    balance_before = await _read_balance(page)
    before = (await page.locator("body").first.inner_text(timeout=2_000)).casefold()
    stage("Купон: нажимаем «Сделать ставку» один раз")
    mark_submitting()
    await confirm.click(timeout=5_000)
    stage("БК: ожидаем подтверждение ставки")
    return await _submission_result(page, before, balance_before, amount, stage)


# Existing imports keep working while all LIVE modes use one implementation.
place_test_coupon = place_regular_coupon
