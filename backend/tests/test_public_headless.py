"""Headless read-only bookmaker mode: no login and no false authorization bypass."""
import asyncio
from types import SimpleNamespace

import app.browser as browser_module
from app.browser import BrowserService


class FakeLocator:
    async def count(self):
        return 0


class FakePage:
    def __init__(self, *, login=False, denied=False):
        self.login, self.denied = login, denied

    def is_closed(self):
        return False

    async def evaluate(self, expression):
        return self.denied if "доступ" in expression else self.login

    def locator(self, _selector):
        return FakeLocator()


def configure(monkeypatch):
    monkeypatch.setattr(browser_module, "settings", SimpleNamespace(
        public_read_only=True, currency_selector=".currency",
        currency_selector_fallbacks=(), currency_text="RUB",
    ))


def service(page):
    subject = object.__new__(BrowserService)
    subject.page = page
    subject.authorization = "PUBLIC_VIEW"
    subject.diagnostic = ""
    subject.status = "PUBLIC_VIEW"
    subject.observation_status = "WAITING_FOR_GAME"
    return subject


def test_public_observer_needs_no_bookmaker_login(monkeypatch):
    configure(monkeypatch)
    observer = service(FakePage())
    result, detail = asyncio.run(observer._is_authorized())
    assert result == "PUBLIC_VIEW"
    assert "DEMO" in detail
    assert asyncio.run(observer._probe_observation_auth_locked())
    assert observer.status == "PUBLIC_VIEW"
    assert observer.authorization == "PUBLIC_VIEW"


def test_explicit_login_and_access_denied_still_block(monkeypatch):
    configure(monkeypatch)
    assert asyncio.run(service(FakePage(login=True))._is_authorized())[0] == "AUTH_LOST"
    assert asyncio.run(service(FakePage(denied=True))._is_authorized())[0] == "ACCESS_DENIED"


def test_bets_are_never_sent_from_public_read_only_branch():
    from app.auth_state import authorization_allows_observation
    assert authorization_allows_observation("PUBLIC_VIEW")
    assert not authorization_allows_observation("AUTH_LOST")
    assert not authorization_allows_observation("ACCESS_DENIED")
