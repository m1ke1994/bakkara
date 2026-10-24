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
  const draws = [];
  const generations = new WeakMap();
  const generation = canvas => generations.get(canvas) || 0;
  const tracked = canvas => canvas?.matches?.(
    'canvas.market-grid-canvas__canvas, canvas.market-grid-canvas__offscreen-canvas');
  window.__bakkaraMarketHook = {log, draws, generation, captureSince:0};
  const proto = CanvasRenderingContext2D.prototype;
  for (const method of ['fillText', 'strokeText']) {
    original[method] = proto[method];
    proto[method] = function(text, x, y) {
      try {
        if (tracked(this.canvas)) {
          const m = this.getTransform();
          const metric = this.measureText(String(text));
          const font=Number(/([\d.]+)px/.exec(this.font)?.[1]) || 14;
          const left=Number.isFinite(metric.actualBoundingBoxLeft) ?
            metric.actualBoundingBoxLeft : 0;
          const right=Number.isFinite(metric.actualBoundingBoxRight) ?
            metric.actualBoundingBoxRight : metric.width;
          const ascent=metric.actualBoundingBoxAscent || font*0.8;
          const descent=metric.actualBoundingBoxDescent || font*0.2;
          const corners=[[-left,-ascent],[right,-ascent],[right,descent],[-left,descent]]
            .map(([dx,dy])=>({x:m.a*(x+dx)+m.c*(y+dy)+m.e,
                              y:m.b*(x+dx)+m.d*(y+dy)+m.f}));
          const xs=corners.map(p=>p.x), ys=corners.map(p=>p.y);
          const minX=Math.min(...xs), minY=Math.min(...ys);
          log.push({text:String(text),canvas:this.canvas,textAlign:this.textAlign,
            generation:generation(this.canvas), x:minX, y:minY,
            width:Math.max(...xs)-minX,height:Math.max(...ys)-minY,
            at:Date.now()});
          if (log.length > 4000) log.splice(0, 1400);
        }
      } catch (_) {}
      return original[method].apply(this, arguments);
    };
  }
  original.clearRect=proto.clearRect;
  proto.clearRect=function(x,y,width,height) {
    try {
      if (tracked(this.canvas) && x<=0 && y<=0 &&
          width>=this.canvas.width && height>=this.canvas.height)
        generations.set(this.canvas,generation(this.canvas)+1);
    } catch (_) {}
    return original.clearRect.apply(this,arguments);
  };
  original.drawImage=proto.drawImage;
  proto.drawImage=function(source,...args) {
    try {
      if (this.canvas?.matches?.('canvas.market-grid-canvas__canvas') && tracked(source)) {
        let sx=0,sy=0,sw=source.width,sh=source.height,dx,dy,dw,dh;
        if(args.length===8) [sx,sy,sw,sh,dx,dy,dw,dh]=args;
        else if(args.length===4) [dx,dy,dw,dh]=args;
        else if(args.length===2) { [dx,dy]=args; dw=sw; dh=sh; }
        if(sw>0&&sh>0&&dw>0&&dh>0) {
          const m=this.getTransform();
          draws.push({source,target:this.canvas,sourceGeneration:generation(source),
            targetGeneration:generation(this.canvas),sx,sy,sw,sh,
            dx:m.a*dx+m.c*dy+m.e,dy:m.b*dx+m.d*dy+m.f,
            dw:Math.hypot(m.a,m.b)*dw,dh:Math.hypot(m.c,m.d)*dh,
            at:Date.now()});
          if(draws.length>300) draws.splice(0,100);
        }
      }
    } catch (_) {}
    return original.drawImage.apply(this,arguments);
  };
}"""

# Capture hook must be present before the Canvas draws its labels.
INIT_SCRIPT = "(" + HOOK + ")();"

SNAPSHOT = r"""() => {
  const canvas=document.querySelector('canvas.market-grid-canvas__canvas');
  if(!canvas) return {ok:false,status:'CANVAS_MISSING_AFTER_SEARCH',clickReady:false,
      captureCount:(window.__bakkaraMarketHook?.log||[]).length};
  const rect=canvas.getBoundingClientRect();
  const hook=window.__bakkaraMarketHook||{};
  const captureSince=hook.captureSince||0;
  const currentGeneration=c=>hook.generation?.(c) || 0;
  const direct=(hook.log||[]).filter(e=>e.canvas===canvas&&
    e.generation===currentGeneration(canvas)&&e.at>=captureSince&&Date.now()-e.at<30000);
  const mapped=[];
  for(const d of (hook.draws||[]).filter(d=>d.target===canvas&&
      d.targetGeneration===currentGeneration(canvas)&&
      d.at>=captureSince&&Date.now()-d.at<30000).slice(-8)) {
    for(const e of (hook.log||[])) {
      if(e.canvas!==d.source||e.generation!==d.sourceGeneration||
          Date.now()-e.at>30000) continue;
      const cx=e.x+e.width/2, cy=e.y+e.height/2;
      if(cx<d.sx||cx>d.sx+d.sw||cy<d.sy||cy>d.sy+d.sh) continue;
      mapped.push({...e,source:'offscreen',
        x:d.dx+(e.x-d.sx)*d.dw/d.sw,y:d.dy+(e.y-d.sy)*d.dh/d.sh,
        width:e.width*d.dw/d.sw,height:e.height*d.dh/d.sh});
    }
  }
  const entries=direct.length?direct:mapped;
  const suits=['пики','трефы','бубны','черви'];
  const normalize=text=>text.trim().toLowerCase().replace('червы','черви');
  const items=[];
  for(const e of entries){
    const index=items.findIndex(old=>old.text===e.text&&
      Math.abs(old.x-e.x)<4&&Math.abs(old.y-e.y)<4);
    if(index>=0) items[index]=e;
    else items.push(e);
  }
  const labels=[],titles=[],oddsBySuit={},candidateCounts={};
  for(const e of items){
    const name=normalize(e.text);
    if(suits.includes(name))labels.push({text:name,rawText:e.text,x:e.x,y:e.y,
      width:e.width,height:e.height,source:e.source||'visible'});
    if(name.includes('игрок получит карту')||name.includes('игрок получит следующую масть'))
      titles.push({text:e.text,x:e.x,y:e.y,width:e.width,height:e.height,
        source:e.source||'visible'});
  }
  if(!titles.length && /игрок получит карту\s*\(масть\)/i.test(document.body?.innerText||''))
    titles.push({text:'Игрок получит карту (масть)',source:'DOM',y:-1});
  for(const suit of suits){
    const same=labels.filter(e=>e.text===suit);
    if(same.length!==1)continue;
    const label=same[0];
    const possible=items.filter(e=>{
      const raw=e.text.trim();
      const n=Number(raw.replace(',','.'));
      return /^\d{1,2}(?:[.,]\d{1,3})?$/.test(raw)&&
        Number.isFinite(n)&&n>=1.01&&n<=100&&
        Math.abs((e.y+e.height/2)-(label.y+label.height/2))<=
          Math.max(10,label.height*0.75)&&
        e.x>label.x+label.width+8;
    });
    candidateCounts[suit]=possible.length;
    if(possible.length!==1)continue;
    const e=possible[0];
    oddsBySuit[suit]={text:e.text,x:e.x+e.width/2,y:e.y+e.height/2,
      width:e.width,height:e.height,amount:Number(e.text.trim().replace(',','.')),
      source:e.source||'visible'};
  }
  return {ok:true,
    status:labels.length===4&&titles.length&&Object.keys(oddsBySuit).length===4?
      'ODDS_MAPPED':'TEXT_NOT_CAPTURED',
    canvas:{width:canvas.width,height:canvas.height,cssWidth:rect.width,cssHeight:rect.height},
    labels,marketTitles:titles,oddsBySuit,
    allText:entries.filter(x=>x.text.trim()).slice(-75).map(e=>
      ({text:e.text,x:e.x,y:e.y,source:e.source||'visible'})),
    captureCount:entries.length,
    captureSource:direct.length?'visible':mapped.length?'offscreen':'none',
    drawCount:(hook.draws||[]).length,
    candidateCounts,
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
        # The market toolbar and Canvas are mounted together. Waiting here
        # avoids clicking an early search control just as Vue opens its input.
        await canvas.wait_for(state="visible", timeout=8_000)
    except PlaywrightTimeoutError:
        pass  # Some games mount the Canvas only after the filter is applied.
    search = page.locator(SEARCH_INPUT).first
    if not await search.is_visible():
        button = page.locator(SEARCH_BUTTON).first
        try:
            await button.wait_for(state="visible", timeout=8_000)
            if not await search.is_visible():
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
        # Filtering can mount or replace the market component only after search.
        await canvas.wait_for(state="visible", timeout=12_000)
    except PlaywrightTimeoutError as exc:
        raise MarketProbeError(
            f"После запроса «{MARKET_QUERY}» Canvas рынка исчез или не появился"
        ) from exc

    result: dict[str, Any] = {}
    for _ in range(60):
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
