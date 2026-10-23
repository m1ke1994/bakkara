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
            textAlign:this.textAlign,
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
  const canvas=document.querySelector('canvas.market-grid-canvas__canvas');
  if(!canvas) return {ok:false,status:'CANVAS_MISSING_AFTER_SEARCH',clickReady:false,
      captureCount:(window.__bakkaraMarketHook?.log||[]).length};
  const rect=canvas.getBoundingClientRect();
  const captureSince=window.__bakkaraMarketHook?.captureSince||0;
  const entries=(window.__bakkaraMarketHook?.log||[])
    .filter(e=>e.at>=captureSince&&Date.now()-e.at<12000);
  const suits=['пики','трефы','бубны','черви'];
  const latest=new Map();
  for(const e of entries){
    const key=e.text.trim().toLowerCase()+':'+Math.round(e.x/3)+':'+Math.round(e.y/3);
    latest.set(key,e);
  }
  const items=[...latest.values()],labels=[],titles=[],oddsBySuit={};
  for(const e of items){
    const name=e.text.trim().toLowerCase();
    if(suits.includes(name))labels.push(e);
    if(name.includes('игрок получит карту')||name.includes('игрок получит следующую масть'))
      titles.push(e);
  }
  for(const suit of suits){
    const same=labels.filter(e=>e.text.trim().toLowerCase()===suit);
    if(same.length!==1)continue;
    const label=same[0];
    const possible=items.filter(e=>{
      const n=Number(e.text.trim().replace(',','.'));
      return Number.isFinite(n)&&n>=1.01&&n<=100&&
        Math.abs(e.y-label.y)<=Math.max(9,label.height*0.65)&&
        e.x>label.x+label.width+12;
    }).sort((a,b)=>b.x-a.x||b.at-a.at);
    if(possible.length!==1)continue;
    const e=possible[0];
    const centerX=e.textAlign==='right'||e.textAlign==='end' ? e.x-e.width/2 :
      e.textAlign==='center' ? e.x : e.x+e.width/2;
    oddsBySuit[suit]={text:e.text,x:centerX,y:e.y,width:e.width,
      height:e.height,amount:Number(e.text.trim().replace(',','.'))};
  }
  return {ok:true,
    status:labels.length===4&&titles.length&&Object.keys(oddsBySuit).length===4?
      'ODDS_MAPPED':'TEXT_NOT_CAPTURED',
    canvas:{width:canvas.width,height:canvas.height,cssWidth:rect.width,cssHeight:rect.height},
    labels,marketTitles:titles,oddsBySuit,
    allText:entries.filter(x=>x.text.trim()).slice(-75),captureCount:entries.length,
    clickReady:false};
}"""


def market_probe_diagnostic(result: dict[str, Any]) -> str:
    """Why a mapped Canvas is absent (without inventing a bookmaker rejection)."""
    status = result.get("status") or "NO_STATUS"
    if not result.get("ok"):
        return (f"{status}: после поиска рынок не отрисован. "
                f"Зафиксировано операций Canvas2D: {result.get('captureCount', 0)}")
    titles = len(result.get("marketTitles") or [])
    labels = len(result.get("labels") or [])
    prices = len(result.get("oddsBySuit") or {})
    count = result.get("captureCount", 0)
    if not titles:
        failure = "не найдена надпись заголовка рынка в Canvas"
    elif labels != 4:
        failure = "не распознаны все четыре подписи мастей"
    elif prices != 4:
        failure = "не найдены четыре однозначных коэффициента напротив мастей"
    else:
        return "Рынок и четыре коэффициента Canvas подтверждены"
    texts = [str(row.get("text", "")).strip()[:28]
             for row in (result.get("allText") or [])[-12:]]
    return (f"{failure}; заголовков={titles}, мастей={labels}, "
            f"коэффициентов={prices}, операций Canvas2D={count}; "
            f"последний текст={texts}")


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

    # Do not fall back to a *different* market name. In production that second
    # search removed the correct "Игрок получит карту (масть)" Canvas, leading
    # to CANVAS_MISSING and the unhelpful "Рынок Canvas не подтверждён".
    await page.evaluate(
        "() => { if (window.__bakkaraMarketHook) "
        "window.__bakkaraMarketHook.captureSince = Date.now() - 100; }"
    )
    await search.fill("")
    await search.fill(MARKET_QUERY)
    try:
        # Filtering often remounts the market canvas; the old locator can
        # become detached. Wait for a fresh visible element after the search.
        await canvas.wait_for(state="visible", timeout=6_000)
    except PlaywrightTimeoutError as exc:
        raise MarketProbeError(
            f"После запроса «{MARKET_QUERY}» Canvas рынка исчез или не появился"
        ) from exc

    result: dict[str, Any] = {}
    for _ in range(24):
        result = await page.evaluate(SNAPSHOT)
        if not result.get("ok"):
            # Vue may detach/recreate Canvas in the same search transaction.
            await asyncio.sleep(0.2)
            continue
        names = {str(x.get("text", "")).strip().casefold()
                 for x in result.get("labels", [])}
        if (all(suit in names for suit in SUIT_NAMES)
                and result.get("marketTitles")
                and len(result.get("oddsBySuit", {})) == 4):
            result["status"] = "ODDS_MAPPED"
            break
        await asyncio.sleep(0.2)
    result["market"] = MARKET_QUERY
    result["diagnostic"] = market_probe_diagnostic(result)
    return result
