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
