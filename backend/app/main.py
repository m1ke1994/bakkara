from contextlib import asynccontextmanager
import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .browser import browser_service
from .config import settings
from .cycle18 import Cycle18Engine

cycle18_engine = Cycle18Engine(settings.observation_db)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        await browser_service.stop()


app = FastAPI(title="BAKKARA BOT API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/state")
async def state():
    snapshot = await asyncio.to_thread(browser_service.snapshot)
    snapshot['cycle18'] = await asyncio.to_thread(cycle18_engine.refresh, snapshot.get('observation', {}).get('current'))
    return snapshot


@app.post("/api/browser/start")
async def start_browser():
    await browser_service.start()
    return await asyncio.to_thread(browser_service.snapshot)


@app.post("/api/browser/stop")
async def stop_browser():
    await browser_service.stop()
    return await asyncio.to_thread(browser_service.snapshot)


@app.post("/api/observation/start")
async def start_observation():
    await browser_service.start_observation()
    return await asyncio.to_thread(browser_service.snapshot)


@app.post("/api/observation/stop")
async def stop_observation():
    await browser_service.stop_observation()
    return await asyncio.to_thread(browser_service.snapshot)


@app.delete("/api/observations")
async def clear_observations():
    result = await browser_service.clear_history()
    await asyncio.to_thread(cycle18_engine.clear)
    return result


@app.get("/api/cycle18")
async def cycle18_state():
    snapshot = await asyncio.to_thread(browser_service.snapshot)
    return await asyncio.to_thread(cycle18_engine.refresh, snapshot.get("observation", {}).get("current"))


@app.delete("/api/logs")
async def clear_logs():
    return browser_service.clear_logs()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
