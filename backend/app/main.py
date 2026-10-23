from contextlib import asynccontextmanager
import asyncio
import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from .browser import browser_service
from .config import settings
from .cycle18 import Cycle18Engine
from .live_market_probe import probe_player_suit_market

cycle18_engine = Cycle18Engine(settings.observation_db)


class StrategySelection(BaseModel):
    strategy: str

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        await browser_service.stop()
        await asyncio.to_thread(cycle18_engine.stop_session)


app = FastAPI(title="BAKKARA BOT API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/state")
async def state():
    snapshot = await asyncio.to_thread(browser_service.snapshot)
    snapshot['cycle18'] = await asyncio.to_thread(cycle18_engine.refresh, snapshot.get('observation', {}).get('current'))
    snapshot['strategy'] = await asyncio.to_thread(cycle18_engine.get_strategy)
    return snapshot


@app.get("/api/strategy")
async def get_strategy():
    return await asyncio.to_thread(cycle18_engine.get_strategy)


@app.put("/api/strategy")
async def save_strategy(selection: StrategySelection):
    # Running observation sessions cannot silently change their scoring rule.
    active_observer = bool(browser_service.observation_task and not browser_service.observation_task.done())
    if active_observer:
        raise HTTPException(status_code=409, detail="Остановите сканирование перед сменой фильтра")
    await asyncio.to_thread(cycle18_engine.stop_session)
    try:
        return await asyncio.to_thread(cycle18_engine.save_strategy, selection.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/browser/start")
async def start_browser():
    await browser_service.start()
    return await asyncio.to_thread(browser_service.snapshot)


@app.post("/api/browser/stop")
async def stop_browser():
    await browser_service.stop()
    await asyncio.to_thread(cycle18_engine.stop_session)
    return await asyncio.to_thread(browser_service.snapshot)


@app.post("/api/observation/start")
async def start_observation():
    was_running = bool(browser_service.observation_task and not browser_service.observation_task.done())
    # Register the new session BEFORE the observer can discover/save its
    # first round. Otherwise the first records have discovered_at < started_at
    # and are silently excluded from the 20-round preparation.
    if not was_running:
        await asyncio.to_thread(cycle18_engine.start_session)
    try:
        await browser_service.start_observation()
    except Exception:
        if not was_running:
            await asyncio.to_thread(cycle18_engine.stop_session)
        raise
    is_running = bool(browser_service.observation_task and not browser_service.observation_task.done())
    if not is_running and not was_running:
        await asyncio.to_thread(cycle18_engine.stop_session)
    return await asyncio.to_thread(browser_service.snapshot)


@app.post("/api/observation/stop")
async def stop_observation():
    await browser_service.stop_observation()
    await asyncio.to_thread(cycle18_engine.stop_session)
    return await asyncio.to_thread(browser_service.snapshot)


@app.delete("/api/observations")
async def clear_observations():
    result = await browser_service.clear_history()
    await asyncio.to_thread(cycle18_engine.clear)
    result['cycle18'] = await asyncio.to_thread(cycle18_engine.refresh)
    return result



@app.post("/api/market/probe")
async def probe_market():
    """Explicit read-only Canvas diagnostic. Does not place real bets."""
    if browser_service.observation_task and not browser_service.observation_task.done():
        raise HTTPException(status_code=409, detail="Остановите сканирование перед проверкой Canvas")
    page = browser_service.page
    if page is None or page.is_closed():
        raise HTTPException(status_code=409, detail="Сначала запустите браузер")
    if "/baccarat/" not in page.url and "/baccara/" not in page.url:
        raise HTTPException(status_code=409, detail="Откройте конкретную раздачу баккары в Chromium")
    async with browser_service.page_lock:
        try:
            return await probe_player_suit_market(page)
        except Exception as exc:
            logging.getLogger(__name__).warning("Canvas probe failed: %s", type(exc).__name__)
            raise HTTPException(status_code=422, detail="Не удалось прочитать Canvas: " + type(exc).__name__) from exc


@app.get("/api/history")
async def history(limit: int = 200):
    """Read the last 200 completed Player hands without bloating live polling.

    This endpoint is read-only; calculation and observation state are never
    changed by opening history.
    """
    safe_limit = min(200, max(1, limit))
    return await asyncio.to_thread(browser_service.store.history, safe_limit)


@app.get("/api/cycle18")
async def cycle18_state():
    snapshot = await asyncio.to_thread(browser_service.snapshot)
    return await asyncio.to_thread(cycle18_engine.refresh, snapshot.get("observation", {}).get("current"))


@app.delete("/api/logs")
async def clear_logs():
    return browser_service.clear_logs()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
