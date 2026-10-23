"""Regression: don't erase a visible Baccarat market with an unrelated fallback search."""
import asyncio
from urllib.parse import quote

from playwright.async_api import async_playwright

from app.live_market_probe import (
    INIT_SCRIPT, MARKET_QUERY, market_probe_diagnostic, probe_player_suit_market,
)
from app.live_executor import outcome_box, PlacementError


HTML = """<!doctype html><html><meta charset="utf-8"><body>
  <button class="ui-search-default__button" onclick="document.querySelector('input').style.display='block'">🔎</button>
  <input class="ui-search-default__input" placeholder="Поиск по рынкам"
    style="display:none">
  <canvas class="market-grid-canvas__canvas" width="951" height="850"></canvas>
  <script>
    const input=document.querySelector('input');
    let canvas=document.querySelector('canvas');
    function draw() {
      canvas=document.querySelector('canvas');
      if (!canvas) return;
      const ctx=canvas.getContext('2d');
      ctx.font='14px Arial';
      ctx.clearRect(0,0,951,850);
      ctx.fillText('Игрок получит карту (масть)',20,45);
      for (const [i,suit] of ['Пики','Трефы','Бубны','Черви'].entries()) {
        const y=100+i*42;
        ctx.fillText(suit,25,y);
        ctx.textAlign='right';
        ctx.fillText('1.9',925,y);
        ctx.textAlign='left';
      }
    }
    draw();
    input.addEventListener('input',()=>{
      const text=input.value;
      if (!text || !'Игрок получит карту (масть)'.includes(text)) {
        canvas?.remove(); return;
      }
      if (!document.querySelector('canvas')) {
        const el=document.createElement('canvas');
        el.className='market-grid-canvas__canvas';
        el.width=951; el.height=850;
        document.body.appendChild(el);
      }
      setTimeout(draw,70);
    });
  </script></body></html>"""


def test_primary_query_survives_canvas_remount_and_targets_numeric_odds():
    async def run():
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True)
            try:
                context=await browser.new_context()
                await context.add_init_script(script=INIT_SCRIPT)
                page=await context.new_page()
                await page.goto('data:text/html,' + quote(HTML),wait_until='domcontentloaded')
                probe=await probe_player_suit_market(page)
                assert probe["ok"], probe
                assert probe["status"]=="ODDS_MAPPED",probe
                assert probe["market"]==MARKET_QUERY,probe
                assert len(probe["labels"])==4,probe
                assert len(probe["oddsBySuit"])==4,probe
                assert probe["captureCount"]>0
                assert await page.locator("input.ui-search-default__input").input_value()==MARKET_QUERY
                chosen=outcome_box(probe,"diamonds")
                assert chosen["odds"]==1.9
                assert chosen["x"]>800
                assert chosen["y"]==184
            finally:
                await browser.close()
    asyncio.run(run())


def test_missing_canvas_rejected_with_specific_reason():
    from app.live_executor import outcome_box
    result={"ok":False,"status":"CANVAS_MISSING_AFTER_SEARCH","captureCount":0}
    result["diagnostic"]=market_probe_diagnostic(result)
    assert "CANVAS_MISSING_AFTER_SEARCH" in result["diagnostic"]
    try:
        outcome_box(result,"hearts")
    except PlacementError as exc:
        assert "CANVAS_MISSING_AFTER_SEARCH" in str(exc)
    else:
        raise AssertionError("Missing Canvas must not produce a click")


def test_partial_render_reports_four_odds_mapping_failure():
    snapshot={"ok":True,"status":"TEXT_NOT_CAPTURED","captureCount":10,
        "marketTitles":[{"text":"Игрок получит карту"}],
        "labels":[{"text":x} for x in ("Пики","Трефы","Бубны","Черви")],
        "oddsBySuit":{},"allText":[{"text":"Игрок получит карту"}]}
    reason=market_probe_diagnostic(snapshot)
    assert "четыре однозначных коэффициента" in reason
    assert "мастей=4" in reason
