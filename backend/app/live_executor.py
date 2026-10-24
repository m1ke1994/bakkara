"""Shared outcome validation and result-dialog helpers for LIVE placement."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

SUITS = {"clubs": "трефы", "hearts": "черви", "diamonds": "бубны", "spades": "пики"}


class PlacementError(Exception):
    pass


def outcome_box(probe: dict, suit: str) -> dict:
    """Map the target to its numeric *odds* cell, not to the suit's label."""
    if suit not in SUITS:
        raise PlacementError(f"Недопустимая масть для LIVE: {suit}")
    if not probe.get("ok"):
        raise PlacementError("Рынок Canvas не подтверждён: " +
                             str(probe.get("diagnostic") or probe.get("status") or
                                 "отсутствует Canvas после поиска"))
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
    first_row_y = min(float(item.get("y") or 0) for item in labels)
    if not any(float(t.get("y") or 0) < first_row_y for t in titles):
        raise PlacementError("Коэффициент не привязан к нужному рынку")
    return {"x": x, "y": y, "width": width, "height": height, "odds": price}


def _amount(value: str) -> Decimal:
    try:
        return Decimal(value.replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise PlacementError("Сумма в купоне не распознана") from exc


async def close_result_dialog(page) -> bool:
    """Dismiss exactly one confirmed result dialog, never an unverified coupon.

    Failure to close is nonfatal: acceptance status has already been determined.
    """
    try:
        for get_panel in (lambda: page.locator('.modal__content[data-test="modal-content"]'),
                          lambda: page.get_by_role("dialog"),
                          lambda: page.locator(".modal-container")):
            try:
                panel = get_panel()
                visible = [panel.nth(i) for i in range(await panel.count())
                           if await panel.nth(i).is_visible()]
            except (AttributeError, AssertionError, KeyError):
                continue
            if len(visible) != 1:
                continue
            modal = visible[0]
            for selector in ('button[data-test="betting-coupon-modal-control-contune"]',
                             'button[data-test="modal-control"][data-original-title="Закрыть"]',
                             'button[data-test="modal-control"][aria-label="Закрыть"]'):
                try:
                    buttons = modal.locator(selector)
                    matches = [buttons.nth(i) for i in range(await buttons.count())
                               if await buttons.nth(i).is_visible()]
                except (AttributeError, KeyError):
                    continue
                if len(matches) == 1 and not await matches[0].is_disabled():
                    await matches[0].click(timeout=3000)
                    return True
            for title in ("Продолжить", "Закрыть"):
                buttons = modal.get_by_role("button", name=title, exact=True)
                matches = [buttons.nth(i) for i in range(await buttons.count())
                           if await buttons.nth(i).is_visible()]
                if len(matches) == 1 and not await matches[0].is_disabled():
                    await matches[0].click(timeout=3000)
                    return True
    except Exception:
        # Modal closure is cosmetic and must not turn an accepted wager UNKNOWN.
        return False
    return False


async def place_one_click(page, suit: str, amount: Decimal, mark_submitting):
    """Compatibility entry point; regular coupon handles all LIVE placements."""
    from .live_coupon_test import place_regular_coupon
    return await place_regular_coupon(page, suit, amount, mark_submitting)
