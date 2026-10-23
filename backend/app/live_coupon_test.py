"""Experimental regular quick-coupon flow, separate from one-click placement.

Search Canvas -> odds selection -> quick coupon -> amount -> confirm -> result dialog.
Never click the odds while one-click mode remains enabled.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from .live_executor import PlacementError, _amount, close_result_dialog, outcome_box
from .live_market_probe import CANVAS_SELECTOR, probe_player_suit_market

ONE_CLICK = ".coupon-one-click input.selection__input"
COUPON = ".quick-coupon-main"
AMOUNT = '.quick-coupon-main input.ui-number-input__field'
CONFIRM = ".quick-coupon-main button.quick-coupon-put-bet-button"


def _check_message(text: str) -> str | None:
    value = text.casefold()
    if any(part in value for part in ("ставка принята", "ставка успешно", "ставка размещена")):
        return "ACCEPTED"
    if any(part in value for part in ("ставка отклонена", "недостаточно средств", "заблокированное событие")):
        return "REJECTED"
    return None


async def place_test_coupon(page, suit: str, amount: Decimal, mark_submitting, report=None) -> str:
    """The user-selected LIVE test filter. No blind retries or early one-click bet."""
    def stage(message: str) -> None:
        if report:
            report(message)

    stage("Поиск: ожидаем рынок «Игрок получит карту»")
    probe = await probe_player_suit_market(page)
    stage(f"Canvas: {probe.get('diagnostic', probe.get('status'))}")
    try:
        box = outcome_box(probe, suit)
    except PlacementError as exc:
        raise PlacementError(f"{exc}; диагностика: {probe.get('diagnostic', 'неизвестно')}") from exc
    stage(f"Canvas: {suit}; коэффициент {box['odds']}; координаты найдены")

    toggle = page.locator(ONE_CLICK).first
    if await toggle.count() != 1:
        raise PlacementError("Не удалось определить переключатель ставки в 1 клик")
    if await toggle.is_checked():
        stage("Купон: отключаем ставку в 1 клик перед выбором коэффициента")
        await toggle.uncheck(force=True, timeout=3_000)
        if await toggle.is_checked():
            raise PlacementError("Ставка в 1 клик всё ещё включена: выбор исхода остановлен")
    if await toggle.is_checked():
        raise PlacementError("Однокликовый режим не отключён")

    canvas = page.locator(CANVAS_SELECTOR).first
    bounds = await canvas.bounding_box()
    if not bounds or not bounds["width"] or not bounds["height"]:
        raise PlacementError("Canvas без видимой области — клик остановлен")
    await canvas.click(position={
        "x": box["x"] * bounds["width"] / box["width"],
        "y": box["y"] * bounds["height"] / box["height"],
    }, timeout=3_000)
    stage("Canvas: нажали коэффициент нужной масти")

    coupon = page.locator(COUPON).first
    try:
        await coupon.wait_for(state="visible", timeout=5_000)
    except Exception as exc:
        raise PlacementError("После клика по коэффициенту не появился quick-coupon") from exc
    stage("Купон: открыт quick-coupon")

    amount_field = coupon.locator("input.ui-number-input__field").first
    try:
        await amount_field.wait_for(state="visible", timeout=3_000)
    except Exception as exc:
        raise PlacementError("В открытом quick-coupon не найдено поле суммы") from exc
    value = format(amount, "f")
    await amount_field.fill("")
    await amount_field.fill(value)
    if _amount(await amount_field.input_value()) != amount:
        raise PlacementError("В quick-coupon сумма не совпала с выбранным шагом")
    stage(f"Купон: сумма {value} RUB введена и проверена")

    confirm = coupon.locator("button.quick-coupon-put-bet-button").first
    try:
        await confirm.wait_for(state="visible", timeout=3_000)
    except Exception as exc:
        raise PlacementError("В quick-coupon не появилась кнопка «Сделать ставку»") from exc
    if await confirm.is_disabled() or "сделать ставку" not in (await confirm.inner_text()).casefold():
        raise PlacementError("Кнопка «Сделать ставку» недоступна или не соответствует купону")

    body = page.locator("body").first
    before = (await body.inner_text(timeout=2_000)).casefold()
    stage("Купон: нажимаем «Сделать ставку» один раз")
    mark_submitting()
    await confirm.click(timeout=5_000)
    stage("БК: ожидаем результат отправки и модальное окно")

    for _ in range(20):
        current = (await body.inner_text(timeout=2_000)).casefold()
        delta = " ".join(part for part in (
            "ставка принята", "ставка успешно", "ставка размещена",
            "ставка отклонена", "недостаточно средств", "заблокированное событие"
        ) if current.count(part) > before.count(part))
        status = _check_message(delta)
        if status is not None:
            await close_result_dialog(page)
            stage(f"БК: {status}; модальное окно обработано")
            return status
        dialogs = page.get_by_role("dialog")
        for i in range(min(await dialogs.count(), 3)):
            dialog = dialogs.nth(i)
            if not await dialog.is_visible():
                continue
            message = await dialog.inner_text(timeout=2_000)
            status = _check_message(message)
            if status:
                await close_result_dialog(page)
                stage(f"БК: {status}; модальное окно обработано")
                return status
        await asyncio.sleep(0.2)

    stage("БК: подтверждение не обнаружено; автоматический повтор запрещён")
    return "UNKNOWN"
