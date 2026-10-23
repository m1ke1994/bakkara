"""Read-only coordinates of Baccarat's 'Игрок получит карту' Canvas market.

Never clicks Canvas outcomes, fills a coupon or confirms a bet.
Results are diagnostic candidates, not click-ready geometry.
"""
from typing import Any

MARKET_QUERY = "Игрок получит карту"
SEARCH_BUTTON = "button.ui-search-default__button"
SEARCH_INPUT = 'input.ui-search-default__input[placeholder="Поиск по рынкам"]'
CANVAS_SELECTOR = "canvas.market-grid-canvas__canvas"

HOOK = r"""() => {
  if (window.__bakkaraMarketHook) return;
  const original = CanvasRenderingContext2D.prototype.fillText;
  const log = [];
  window.__bakkaraMarketHook = {log};
  CanvasRenderingContext2D.prototype.fillText = function(text, x, y) {
    try {
      if (this.canvas?.matches('canvas.market-grid-canvas__canvas')) {
        const m = this.getTransform();
        const metrics = this.measureText(String(text));
        log.push({text:String(text), x:m.a*x+m.c*y+m.e,
          y:m.b*x+m.d*y+m.f, width:Math.abs(m.a)*metrics.width,
          height:Math.abs(m.d)*(metrics.actualBoundingBoxAscent+metrics.actualBoundingBoxDescent),
          font:this.font, at:Date.now()});
        if (log.length > 2000) log.splice(0, 500);
      }
    } catch (_) {}
    return original.apply(this, arguments);
  };
}"""


async def probe_player_suit_market(page: Any) -> dict[str, Any]:
    """Search a market and return the visible Canvas text positions only."""
    await page.evaluate(HOOK)
    search = page.locator(SEARCH_INPUT).first
    if not await search.is_visible():
        button = page.locator(SEARCH_BUTTON).first
        if not await button.is_visible():
            return {"ok": False, "status": "SEARCH_BUTTON_MISSING", "market": MARKET_QUERY}
        await button.click(timeout=3000)  # Search UI only, never a bet outcome.
    await search.wait_for(state="visible", timeout=3000)
    await page.evaluate("() => { window.__bakkaraMarketHook.log.length = 0; }")
    await search.fill("")
    await search.fill(MARKET_QUERY)
    await page.wait_for_timeout(350)
    payload = await page.evaluate(r"""() => {
      const canvas = document.querySelector('canvas.market-grid-canvas__canvas');
      if (!canvas) return {ok:false, status:'CANVAS_MISSING'};
      const rect = canvas.getBoundingClientRect();
      const entries = (window.__bakkaraMarketHook?.log || [])
        .filter(x => Date.now()-x.at<3000);
      const suits = ['пики', 'трефы', 'бубны', 'черви'];
      const labels = entries.filter(x => suits.includes(x.text.trim().toLowerCase()));
      return {ok:true, status:labels.length ? 'LABELS_OBSERVED' : 'TEXT_NOT_CAPTURED',
        canvas:{width:canvas.width,height:canvas.height,cssWidth:rect.width,
          cssHeight:rect.height},labels,
        marketTitles:entries.filter(x=>x.text.toLowerCase().includes('игрок получит карту')),
        allText:entries.filter(x=>x.text.trim()).slice(-70),
        captureCount:entries.length,clickReady:false};
    }""")
    payload["market"] = MARKET_QUERY
    return payload
