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
    """Require one visible label of each suit and the exact market title."""
    if not probe.get("ok") or suit not in SUITS:
        raise PlacementError("Рынок Canvas не подтверждён")
    entries = probe.get("marketTitles") or []
    if not any("игрок получит карту" in str(x.get("text", "")).casefold() for x in entries):
        raise PlacementError("Название рынка не найдено в Canvas")
    labels = probe.get("labels") or []
    matched = {}
    for label in labels:
        name = str(label.get("text", "")).strip().casefold()
        if name in SUITS.values():
            matched.setdefault(name, []).append(label)
    if set(matched) != set(SUITS.values()) or any(len(group) != 1 for group in matched.values()):
        raise PlacementError("Не удалось однозначно определить четыре масти")
    box = matched[SUITS[suit]][0]
    title_y = max(float(entry.get("y") or 0) for entry in entries)
    if title_y <= 0 or any(float(group[0].get("y") or 0) <= title_y
                           for group in matched.values()):
        raise PlacementError("Масти не привязаны к найденному рынку")
    canvas = probe.get("canvas") or {}
    width, height = float(canvas.get("width") or 0), float(canvas.get("height") or 0)
    x, y = float(box["x"]), float(box["y"])
    text_width = float(box.get("width") or 0)
    if not (width > 0 and height > 0 and text_width > 0 and 0 < x < width and 0 < y < height):
        raise PlacementError("Координаты исхода недостоверны")
    return {"x": x + text_width / 2, "y": y - max(float(box.get("height") or 0), 10) / 2,
            "width": width, "height": height}


def _amount(value: str) -> Decimal:
    try:
        return Decimal(value.replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise PlacementError("Сумма в купоне не распознана") from exc


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
            return "ACCEPTED"
        if any(body.count(p) > before.count(p) for p in rejected_phrases):
            return "REJECTED"
        await asyncio.sleep(0.25)
    return "UNKNOWN"
