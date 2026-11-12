from contextlib import asynccontextmanager
import asyncio
import logging
import os
import time

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from . import dashboard_auth

from .browser import browser_service
from .config import settings
from .cycle18 import Cycle18Engine
from .live_market_probe import probe_player_suit_market
from .stake_plan import PlanConflict, StakePlan, StakePlanStore

cycle18_engine = Cycle18Engine(settings.observation_db)
stake_plan_store = StakePlanStore(settings.observation_db)
browser_service.cycle_engine = cycle18_engine
browser_service.live_plan_store = stake_plan_store
live_bet_store = browser_service.live_bets


class LoginInput(BaseModel):
    email: str
    password: str


class StrategySelection(BaseModel):
    strategy: str


class ModeSelection(BaseModel):
    mode: str


class CouponTestFilterSelection(BaseModel):
    enabled: bool


class FirstRoundTestSelection(BaseModel):
    enabled: bool
    suit: str

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def browser_resource_usage() -> tuple[int, int | None]:
    """Count Chromium processes and read this container's cgroup memory usage.

    /proc and cgroup paths are inside the bakkara-api container, not the host.
    A missing cgroup counter is not treated as zero usage.
    """
    chromium = 0
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    with open(f"/proc/{entry.name}/comm", encoding="utf-8") as process:
                        name = process.read().strip().lower()
                    if "chrome" in name or "chromium" in name:
                        chromium += 1
                except (OSError, UnicodeError):
                    continue
    except OSError:
        pass

    for path in ("/sys/fs/cgroup/memory.current",
                 "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            with open(path, encoding="ascii") as counter:
                return chromium, int(counter.read().strip())
        except (OSError, ValueError):
            continue
    return chromium, None


async def browser_recycle_watchdog() -> None:
    """Recycle a growing Chromium before it can exhaust the host's RAM.

    Also retain the scheduled three-hour recycle. A fresh context resets
    the timer; resource thresholds are checked during continuous observation.
    """
    log = logging.getLogger(__name__)
    context_id = None
    launched_at = None
    last_warning_at = 0.0
    while True:
        await asyncio.sleep(10)
        context = browser_service.context
        if context is None:
            context_id, launched_at = None, None
            continue
        if id(context) != context_id:
            context_id, launched_at = id(context), time.monotonic()
            continue
        if launched_at is None:
            continue

        age = time.monotonic() - launched_at
        scheduled = age >= 3 * 60 * 60
        # On a machine with 15 GiB RAM, the previously observed leak reached
        # 5.5 GiB / 50 renderers after ~90 minutes. Recycle substantially earlier.
        periodic = age >= max(300, int(os.getenv("BAKKARA_BROWSER_RECYCLE_SECONDS", "1800")))
        chromium_count, memory_bytes = await asyncio.to_thread(browser_resource_usage)
        count_limit = max(1, int(os.getenv("BAKKARA_CHROMIUM_PROCESS_LIMIT", "25")))
        memory_limit = max(1, int(os.getenv("BAKKARA_BROWSER_MEMORY_LIMIT_MIB", "3072"))) * 1024 * 1024
        exhausted = (chromium_count >= count_limit or
                     (memory_bytes is not None and memory_bytes >= memory_limit))
        if not (scheduled or periodic or exhausted):
            continue

        # Never interrupt reconciliation of a bet which may already be accepted.
        settlement = getattr(browser_service, "_pending_live_settlement", None)
        if settlement is not None and not settlement.done():
            if time.monotonic() - last_warning_at >= 60:
                log.warning("Chromium recycle postponed: LIVE bet settlement pending")
                last_warning_at = time.monotonic()
            continue

        observing = bool(browser_service.observation_task
                         and not browser_service.observation_task.done())
        # Do not reset the context marker before the recycle succeeds:
        # failures must be retried rather than ignored for another three hours.
        reason = ("scheduled" if scheduled else "age" if periodic else "resource limit")
        log.warning("Chromium recycle (%s): age=%.0fs processes=%d memory_mib=%s",
                    reason, age, chromium_count,
                    round(memory_bytes / 1048576) if memory_bytes is not None else "unknown")
        try:
            await asyncio.wait_for(browser_service.stop(), timeout=60)
            await asyncio.to_thread(cycle18_engine.stop_session)
            await asyncio.wait_for(browser_service.start(), timeout=90)
            if browser_service.context is None:
                raise RuntimeError("Chromium did not start")
            # New context may reuse an object id; always register the fresh
            # context immediately, preventing a second recycle on next tick.
            context_id, launched_at = id(browser_service.context), time.monotonic()
            if observing:
                for _ in range(60):
                    if browser_service.authorization in ("AUTHORIZED", "TEMPORARILY_UNCONFIRMED", "PUBLIC_VIEW"):
                        break
                    await asyncio.sleep(2)
                else:
                    log.warning("Browser restarted but authorization missing")
                    continue
                await asyncio.to_thread(cycle18_engine.start_session, resume=True)
                await browser_service.start_observation()
            log.info("Chromium recycle completed")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Retry on next tick; an external supervisor may still be needed
            # for an uninterruptible Playwright shutdown.
            log.exception("Chromium recycle failed; an external supervisor may be required")


@asynccontextmanager
async def lifespan(_: FastAPI):
    dashboard_auth.validate()
    maintenance = asyncio.create_task(browser_recycle_watchdog())
    try:
        yield
    finally:
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)
        await browser_service.stop()
        await asyncio.to_thread(cycle18_engine.stop_session)


app = FastAPI(title="BAKKARA BOT API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def protect_dashboard_api(request: Request, call_next):
    if not dashboard_auth.enabled() or request.method == "OPTIONS":
        return await call_next(request)
    public = {"/api/health", "/api/auth/me", "/api/auth/login", "/api/auth/logout"}
    if request.url.path not in public and not dashboard_auth.verify(request.cookies.get(dashboard_auth.COOKIE)):
        return JSONResponse({"detail": "Требуется вход"}, status_code=401)
    if request.method not in ("GET", "HEAD"):
        if request.headers.get("origin") != settings.public_origin.rstrip("/"):
            return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
    return await call_next(request)


@app.get("/api/auth/me")
async def dashboard_me(request: Request):
    if not dashboard_auth.enabled():
        return {"required": False, "authenticated": True, "email": None}
    email = dashboard_auth.verify(request.cookies.get(dashboard_auth.COOKIE))
    return {"required": True, "authenticated": bool(email), "email": email}


@app.post("/api/auth/login")
async def dashboard_login(credentials: LoginInput, response: Response, request: Request):
    if not dashboard_auth.enabled():
        raise HTTPException(409, "Авторизация в панели отключена")
    ip = request.client.host if request.client else "unknown"
    if not dashboard_auth.login_allowed(ip):
        raise HTTPException(429, "Слишком много попыток входа")
    email = dashboard_auth.authenticate(credentials.email, credentials.password)
    if email is None:
        dashboard_auth.login_failed(ip)
        raise HTTPException(401, "Неверный логин или пароль")
    dashboard_auth.login_succeeded(ip)
    response.set_cookie(dashboard_auth.COOKIE, dashboard_auth.issue(email),
                        max_age=dashboard_auth.MAX_AGE, path="/", secure=True,
                        httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"required": True, "authenticated": True, "email": email}


@app.post("/api/auth/logout")
async def dashboard_logout(response: Response):
    response.delete_cookie(dashboard_auth.COOKIE, path="/", secure=True,
                           httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"required": True, "authenticated": False}



@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/stake-plan", response_model=StakePlan)
async def get_stake_plan():
    return await asyncio.to_thread(stake_plan_store.load)


@app.put("/api/stake-plan", response_model=StakePlan)
async def save_stake_plan(plan: StakePlan):
    """Save a calculator worksheet, independent of observation and execution."""
    try:
        return await asyncio.to_thread(stake_plan_store.save, plan)
    except PlanConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/state")
async def state():
    snapshot = await asyncio.to_thread(browser_service.snapshot)
    snapshot['cycle18'] = await asyncio.to_thread(cycle18_engine.refresh, snapshot.get('observation', {}).get('current'))
    snapshot['strategy'] = await asyncio.to_thread(cycle18_engine.get_strategy)
    snapshot['live'] = await asyncio.to_thread(live_bet_store.snapshot)
    snapshot['public_read_only'] = settings.public_read_only
    return snapshot


@app.put("/api/mode")
async def save_mode(selection: ModeSelection):
    if browser_service.observation_task and not browser_service.observation_task.done():
        raise HTTPException(status_code=409, detail="Остановите сканирование перед сменой режима")
    try:
        await asyncio.to_thread(live_bet_store.mode, selection.mode.upper())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    browser_service.state_version += 1
    return await state()


@app.put("/api/live/coupon-test-filter")
async def set_coupon_test_filter(selection: CouponTestFilterSelection):
    if browser_service.observation_task and not browser_service.observation_task.done():
        raise HTTPException(status_code=409, detail="Остановите сканирование для смены тестового фильтра")
    await asyncio.to_thread(live_bet_store.set_coupon_test_filter, selection.enabled)
    browser_service.state_version += 1
    return await state()


@app.put("/api/live/first-round-test")
async def set_first_round_test(selection: FirstRoundTestSelection):
    if browser_service.observation_task and not browser_service.observation_task.done():
        raise HTTPException(status_code=409, detail="Остановите сканирование для смены тестового фильтра")
    try:
        await asyncio.to_thread(live_bet_store.set_warmup_test, selection.enabled, selection.suit)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    browser_service.state_version += 1
    return await state()


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
        await asyncio.to_thread(cycle18_engine.start_session, resume=True)
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
async def history(limit: int = 100):
    """Read up to 100 completed Player hands and their saved pre-hand forecasts.

    Kept separate from the frequent /api/state polling so live scanning does
    not serialize thousands of history entries every half-second.
    """
    safe_limit = min(100, max(1, limit))
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
