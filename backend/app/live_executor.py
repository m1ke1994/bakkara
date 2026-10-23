"""Conservative one-click submission on the existing RUB bookmaker page."""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation

from .live_market_probe import CANVAS_SELECTOR, probe_player_suit_market

AMOUNT = ".coupon-one-click input.ui-number-input__field"
ONE_CLICK = ".coupon-one-click input.selection__input"
SUITS = {"clubs": "трефы", "hearts": "черви", "diamonds": "бубны", "spades": "пики"}


class PlacementError(Exception):
    pass


def outcome_box(probe: dict, suit: str) -> dict:
    """Map the target to its numeric *odds* cell, not to the suit's label."""
    if not probe.get("ok") or suit not in SUITS:
        raise PlacementError("Рынок Canvas не подтверждён")
    titles = probe.get("marketTitles") or []
    if not any("игрок получит карту" in str(x.get("text", "")).casefold() or
               "игрок получит следующую масть" in str(x.get("text", "")).casefold()
               for x in titles):
        raise PlacementError("Название рынка не найдено в Canvas")
    labels = probe.get("labels") or []
    groups = {str(x.get("text", "")).strip().casefold(): x for x in labels}
    if set(groups) != set(SUITS.values()) or len(labels) != 4:
        raise PlacementError("Не удалось однозначно определить четыре масти")
    odds = probe.get("oddsBySuit") or {}
    if set(odds) != set(SUITS.values()):
        raise PlacementError("Не установлены координаты четырёх коэффициентов Canvas")
    entry = odds[SUITS[suit]]
    label = groups[SUITS[suit]]
    canvas = probe.get("canvas") or {}
    width, height = float(canvas.get("width") or 0), float(canvas.get("height") or 0)
    x, y = float(entry["x"]), float(entry["y"])
    price = float(entry["amount"])
    if not (width > 0 and height > 0 and 1.01 <= price <= 100 and
            0 < x < width and 0 < y < height and
            x > float(label["x"]) + float(label["width"]) and
            abs(y-float(label["y"])) < 18):
        raise PlacementError("Координаты коэффициента не подтверждены")
    if not all(float(t.get("y") or 0) < y for t in titles):
        raise PlacementError("Коэффициент не привязан к нужному рынку")
    return {"x": x, "y": y, "width": width, "height": height, "odds": price}


def _amount(value: str) -> Decimal:
    try:
        return Decimal(value.replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise PlacementError("Сумма в купоне не распознана") from exc


async def close_result_dialog(page) -> bool:
    """Close a visible result dialog after a verified site status, never beforehand.

    NewBot has no Baccarat-specific modal selectors; use accessibility names
    only when there is exactly one visible dialog and one matching button.
    """
    dialogs = page.get_by_role("dialog")
    visible = [dialogs.nth(i) for i in range(await dialogs.count())
               if await dialogs.nth(i).is_visible()]
    if len(visible) != 1:
        return False
    for title in ("Закрыть", "Продолжить"):
        buttons = visible[0].get_by_role("button", name=title, exact=True)
        matches = [buttons.nth(i) for i in range(await buttons.count())
                   if await buttons.nth(i).is_visible()]
        if len(matches) == 1 and not await matches[0].is_disabled():
            await matches[0].click(timeout=3000)
            return True
    return False


async def place_one_click(page, suit: str, amount: Decimal, mark_submitting):
    """Prepare verified one-click wager and confirm site response exactly once.

    Before the potentially submitting click, all errors identify their stage.
    An ambiguous click result is UNKNOWN, never retried.
    """
    from .live_market_probe import MarketProbeError
    try:
        probe = await probe_player_suit_market(page)
    except MarketProbeError as exc:
        raise PlacementError(f"Поиск рынка: {exc}") from exc
    box = outcome_box(probe, suit)
    field = page.locator(AMOUNT).first
    try:
        await field.wait_for(state="visible", timeout=8_000)
    except Exception as exc:
        raise PlacementError("Поле суммы быстрой ставки не появилось") from exc
    value = format(amount, "f")
    await field.fill(value)
    if _amount(await field.input_value()) != amount:
        raise PlacementError("Сумма в купоне не совпала с шагом калькулятора")
    one_click = page.locator(ONE_CLICK).first
    if await one_click.count() != 1:
        raise PlacementError("Состояние «Ставка в 1 клик» не определено")
    immediate = await one_click.is_checked()
    canvas = page.locator(CANVAS_SELECTOR).first
    css = await canvas.bounding_box()
    if not css or not css["width"] or not css["height"]:
        raise PlacementError("Canvas не имеет видимой области")
    before = (await page.locator("body").inner_text(timeout=2000)).casefold()
    accepted_phrases = ("ставка принята", "ставка успешно")
    rejected_phrases = ("ставка отклонена", "недостаточно средств", "заблокированное событие")
    mark_submitting()
    await canvas.click(position={"x": box["x"] * css["width"] / box["width"],
                                 "y": box["y"] * css["height"] / box["height"]}, timeout=5_000)
    if not immediate:
        # Standard coupon: the familiar NewBot confirmation button.
        confirm = page.get_by_role("button", name="Сделать ставку", exact=True)
        visible = [confirm.nth(i) for i in range(await confirm.count())
                   if await confirm.nth(i).is_visible()]
        if len(visible) != 1 or await visible[0].is_disabled():
            return "UNKNOWN"
        await visible[0].click(timeout=5000)
    for _ in range(16):
        body = (await page.locator("body").inner_text(timeout=2000)).casefold()
        if any(body.count(p) > before.count(p) for p in accepted_phrases):
            await close_result_dialog(page)
            return "ACCEPTED"
        if any(body.count(p) > before.count(p) for p in rejected_phrases):
            await close_result_dialog(page)
            return "REJECTED"
        await asyncio.sleep(0.25)
    return "UNKNOWN"
