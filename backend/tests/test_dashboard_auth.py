"""Server-side access control; no plaintext credentials are stored in the repository."""
import pytest
import httpx

from app import dashboard_auth
from app import main as main_module


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("BAKKARA_AUTH_ENABLED", "1")
    monkeypatch.setenv("BAKKARA_SESSION_SECRET", "for-tests-only-a-secret-that-is-long-enough-1234")
    monkeypatch.setenv("BAKKARA_ASLAN_PASSWORD_HASH", dashboard_auth.password_hash("test-user-aslan"))
    monkeypatch.setenv("BAKKARA_TEDEEV_PASSWORD_HASH", dashboard_auth.password_hash("test-user-tedeev"))
    dashboard_auth.validate()
    dashboard_auth.failures.clear()
    yield
    dashboard_auth.failures.clear()


def test_account_login_hashes_and_cookie_signatures(credentials):
    assert dashboard_auth.authenticate("ASLAN1994@gmail.com", "test-user-aslan") == "aslan1994@gmail.com"
    assert dashboard_auth.authenticate("Tedeev1994@gmail.com", "test-user-tedeev") == "tedeev1994@gmail.com"
    assert dashboard_auth.authenticate("aslan1994@gmail.com", "bad-password") is None
    token = dashboard_auth.issue("aslan1994@gmail.com", now=1000)
    assert dashboard_auth.verify(token, now=1001) == "aslan1994@gmail.com"
    assert dashboard_auth.verify(token, now=1000 + dashboard_auth.MAX_AGE) is None
    assert dashboard_auth.verify(token + "x", now=1001) is None
    assert dashboard_auth.verify("not-a-valid-cookie!") is None


@pytest.mark.asyncio
async def test_protected_api_and_both_accounts(credentials):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main_module.app),
        base_url="https://sistem-loyality.ru",
    ) as client:
        origin = {"Origin": "https://sistem-loyality.ru"}
        assert (await client.get("/api/state")).status_code == 401
        assert (await client.get("/api/history")).status_code == 401
        assert (await client.get("/api/stake-plan")).status_code == 401
        failed = await client.post("/api/auth/login", json={"email": "no", "password": "wrong"}, headers=origin)
        assert failed.status_code == 401
        ok = await client.post("/api/auth/login", json={
            "email": "Aslan1994@gmail.com", "password": "test-user-aslan",
        }, headers=origin)
        assert ok.status_code == 200
        assert client.cookies.get(dashboard_auth.COOKIE)
        assert (await client.get("/api/strategy")).status_code == 200
        assert (await client.post("/api/auth/logout", headers=origin)).status_code == 200
        assert (await client.get("/api/strategy")).status_code == 401
        ok2 = await client.post("/api/auth/login", json={
            "email": "Tedeev1994@gmail.com", "password": "test-user-tedeev",
        }, headers=origin)
        assert ok2.status_code == 200
        assert (await client.get("/api/strategy")).status_code == 200
        assert (await client.post("/api/auth/logout", headers={"Origin": "https://malicious.example"})).status_code == 403


def test_read_only_deployment_disables_live(monkeypatch, tmp_path):
    from app.live_bets import LiveBets
    from app.config import settings
    monkeypatch.setattr(settings, "public_read_only", True)
    control = LiveBets(tmp_path / "live.sqlite3")
    with pytest.raises(ValueError):
        control.mode("LIVE")
    control.mode("DEMO")
