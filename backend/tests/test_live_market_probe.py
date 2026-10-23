from pathlib import Path

def test_market_probe_is_explicit_and_read_only():
    root=Path(__file__).resolve().parents[1]
    script=(root/'app/live_market_probe.py').read_text(encoding='utf-8')
    api=(root/'app/main.py').read_text(encoding='utf-8')
    assert 'Игрок получит карту' in script
    assert 'market-grid-canvas__canvas' in script
    assert 'clickReady:false' in script
    assert 'await button.click' in script
    assert 'await search.fill(MARKET_QUERY)' in script
    assert 'coupon.click' not in script
    assert 'confirm.click' not in script
    assert '@app.post("/api/market/probe")' in api
    assert 'browser_service.page_lock' in api

def test_canvas_probe_waits_for_component_and_installs_before_navigation():
    from app.live_market_probe import INIT_SCRIPT, MARKET_QUERY, ALT_MARKET_QUERY, SNAPSHOT
    assert "CanvasRenderingContext2D" in INIT_SCRIPT
    assert "strokeText" in INIT_SCRIPT and "fillText" in INIT_SCRIPT
    assert "market-grid-canvas__canvas" in INIT_SCRIPT
    assert MARKET_QUERY == "Игрок получит карту"
    assert ALT_MARKET_QUERY == "Игрок получит следующую масть"
    assert "marketTitles" in SNAPSHOT
    assert "clickReady:false" in SNAPSHOT
    from pathlib import Path
    browser = (Path(__file__).resolve().parents[1]/"app/browser.py").read_text(encoding="utf-8")
    assert "await self.context.add_init_script(script=INIT_SCRIPT)" in browser
    assert "await self._live_before_cards(game)" in browser
