"""Baccarat Canvas2D probe. Only market search is clicked; outcomes never clicked here."""
from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

MARKET_QUERY = "Игрок получит карту"
ALT_MARKET_QUERY = "Игрок получит следующую масть"
SEARCH_BUTTON = "button.ui-search-default__button"
SEARCH_INPUT = 'input.ui-search-default__input[placeholder="Поиск по рынкам"]'
CANVAS_SELECTOR = "canvas.market-grid-canvas__canvas"
SUIT_NAMES = ("пики", "трефы", "бубны", "черви")

# Used both with page.evaluate(HOOK) and (HOOK)() as a document init script.
HOOK = r"""() => {
  if (window.__bakkaraMarketHook) return;
  const original = {};
  const log = [];
  window.__bakkaraMarketHook = {log};
  const proto = CanvasRenderingContext2D.prototype;
  for (const method of ['fillText', 'strokeText']) {
    original[method] = proto[method];
    proto[method] = function(text, x, y) {
      try {
        if (this.canvas?.matches('canvas.market-grid-canvas__canvas')) {
          const m = this.getTransform();
          const metric = this.measureText(String(text));
          const ascent = metric.actualBoundingBoxAscent || 12;
          const descent = metric.actualBoundingBoxDescent || 2;
          log.push({text:String(text),
            x:m.a*x + m.c*y + m.e, y:m.b*x + m.d*y + m.f,
            width:Math.hypot(m.a,m.b)*metric.width,
            height:Math.hypot(m.c,m.d)*(ascent+descent),
            at:Date.now()});
          if (log.length > 2000) log.splice(0, 700);
        }
      } catch (_) {}
      return original[method].apply(this, arguments);
    };
  }
}"""

# Capture hook must be present before the Canvas draws its labels.
INIT_SCRIPT = "(" + HOOK + ")();"

SNAPSHOT = r"""() => {
  const canvas = document.querySelector('canvas.market-grid-canvas__canvas');
  if (!canvas) return {ok:false, status:'CANVAS_MISSING', clickReady:false};
  const rect = canvas.getBoundingClientRect();
  const entries = (window.__bakkaraMarketHook?.log || []).filter(x=>Date.now()-x.at<9000);
  const suits = ['пики','трефы','бубны','черви'];
  const labels=[], titles=[], seen=new Set();
  for (const e of entries) {
    const name=e.text.trim().toLowerCase();
    const isSuit=suits.includes(name), isTitle=name.includes('игрок получит карту') ||
      name.includes('игрок получит следующую масть');
    if (!isSuit && !isTitle) continue;
    const key=name+':'+Math.round(e.x/3)+':'+Math.round(e.y/3);
    if (seen.has(key)) continue;
    seen.add(key);
    (isSuit ? labels : titles).push(e);
  }
  return {ok:true, status:labels.length===4&&titles.length?'LABELS_OBSERVED':'TEXT_NOT_CAPTURED',
    canvas:{width:canvas.width,height:canvas.height,cssWidth:rect.width,cssHeight:rect.height},
    labels,marketTitles:titles,allText:entries.filter(x=>x.text.trim()).slice(-60),
    captureCount:entries.length,clickReady:false};
}"""


class MarketProbeError(RuntimeError):
    """Identifies the exact missing UI stage without claiming a rejected wager."""


async def probe_player_suit_market(page: Any) -> dict[str, Any]:
    """Find visible market text, respecting async game-component loading."""
    await page.evaluate(HOOK)
    canvas = page.locator(CANVAS_SELECTOR).first
    try:
        # The game component can appear several seconds after DOMContentLoaded.
        await canvas.wait_for(state="visible", timeout=12_000)
    except PlaywrightTimeoutError as exc:
        raise MarketProbeError("Canvas игры не появился за 12 секунд") from exc

    search = page.locator(SEARCH_INPUT).first
    if not await search.is_visible():
        button = page.locator(SEARCH_BUTTON).first
        try:
            await button.wait_for(state="visible", timeout=8_000)
            await button.click(timeout=5_000)
        except PlaywrightTimeoutError as exc:
            raise MarketProbeError("Кнопка поиска рынков не найдена или недоступна") from exc
    try:
        await search.wait_for(state="visible", timeout=5_000)
    except PlaywrightTimeoutError as exc:
        raise MarketProbeError("После кнопки поиска поле «Поиск по рынкам» не появилось") from exc

    result: dict[str, Any] = {}
    for query in (MARKET_QUERY, ALT_MARKET_QUERY):
        await page.evaluate("() => { if (window.__bakkaraMarketHook) window.__bakkaraMarketHook.log.length = 0; }")
        await search.fill("")
        if query == MARKET_QUERY:
            await search.fill(MARKET_QUERY)
        else:
            await search.fill(query)
        for _ in range(14):
            result = await page.evaluate(SNAPSHOT)
            if not result.get("ok"):
                break
            names = {str(x.get("text", "")).strip().casefold() for x in result.get("labels", [])}
            if all(suit in names for suit in SUIT_NAMES) and result.get("marketTitles"):
                result["status"] = "LABELS_OBSERVED"
                result["market"] = query
                return result
            await asyncio.sleep(0.25)
    result["market"] = query
    return result
