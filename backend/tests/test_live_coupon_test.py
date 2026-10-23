"""LIVE test filter: coupon-first placement ordering and virtual browser fixtures."""
import asyncio
from decimal import Decimal

import pytest

import app.live_coupon_test as live
from app.live_executor import PlacementError
from app.live_bets import LiveBets


class Holder:
    def __init__(self, value):
        self.value = value

    @property
    def first(self):
        return self.value


class Toggle:
    def __init__(self, trace, checked=True, can_disable=True):
        self.trace = trace
        self.checked = checked
        self.can_disable = can_disable

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def is_checked(self):
        return self.checked

    async def uncheck(self, **_):
        self.trace.append("disable_one_click")
        if self.can_disable:
            self.checked = False


class Canvas:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def bounding_box(self):
        return {"width": 951, "height": 850}

    async def click(self, **kwargs):
        assert not self.page.toggle.checked
        assert kwargs["position"] == {"x": 900, "y": 200}
        self.page.trace.append("select_odds")
        self.page.opened = True


class Amount:
    def __init__(self, page):
        self.page = page
        self.value = ""

    @property
    def first(self):
        return self

    async def wait_for(self, **kwargs):
        assert self.page.opened

    async def fill(self, value):
        self.page.trace.append("fill_" + value)
        self.value = value

    async def input_value(self):
        return self.value


class Confirm:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def wait_for(self, **_):
        assert self.page.opened

    async def is_disabled(self):
        return False

    async def inner_text(self):
        return "Сделать ставку"

    async def click(self, **kwargs):
        self.page.trace.append("confirm")
        self.page.message = self.page.reply


class Coupon:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def wait_for(self, **kwargs):
        if not self.page.opened:
            raise RuntimeError("Купон не открылся")
        self.page.trace.append("coupon_open")

    def locator(self, selector):
        return Holder(self.page.amount if "input." in selector else self.page.confirm)


class Body:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def inner_text(self, **_):
        return self.page.message


class EmptyDialogs:
    async def count(self):
        return 0


class Page:
    def __init__(self, reply="Ставка принята", checked=True, can_disable=True):
        self.trace = []
        self.message = ""
        self.reply = reply
        self.opened = False
        self.toggle = Toggle(self.trace, checked, can_disable)
        self.canvas = Canvas(self)
        self.amount = Amount(self)
        self.confirm = Confirm(self)
        self.coupon = Coupon(self)
        self.body = Body(self)

    def locator(self, selector):
        mapping = {
            live.ONE_CLICK: self.toggle, live.CANVAS_SELECTOR: self.canvas,
            live.COUPON: self.coupon, "body": self.body,
        }
        return Holder(mapping[selector])

    def get_by_role(self, name):
        return EmptyDialogs()


@pytest.fixture
def fake_market(monkeypatch):
    async def probe(_page):
        return {"ok": True}

    async def close(_page):
        return True

    monkeypatch.setattr(live, "probe_player_suit_market", probe)
    monkeypatch.setattr(live, "outcome_box", lambda p, s: {
        "x": 900, "y": 200, "width": 951, "height": 850, "odds": 1.9})
    monkeypatch.setattr(live, "close_result_dialog", close)


def test_test_filter_clicks_outcome_then_fills_coupon_and_confirms(fake_market):
    page = Page()
    marked, messages = [], []
    status = asyncio.run(live.place_test_coupon(
        page, "clubs", Decimal("22.50"), lambda: marked.append("SUBMITTING"), messages.append))
    assert status == "ACCEPTED"
    assert page.trace == ["disable_one_click", "select_odds", "coupon_open",
                          "fill_", "fill_22.50", "confirm"]
    assert marked == ["SUBMITTING"]
    assert any("ставку" in entry.casefold() for entry in messages)


def test_test_filter_aborts_before_selection_if_one_click_stays_enabled(fake_market):
    page = Page(can_disable=False)
    marked = []
    with pytest.raises(PlacementError, match="всё ещё включена"):
        asyncio.run(live.place_test_coupon(
            page, "clubs", Decimal("15"), lambda: marked.append(True)))
    assert page.trace == ["disable_one_click"]
    assert not marked


def test_test_filter_stops_before_confirmation_if_quick_coupon_missing(fake_market):
    page = Page()
    async def missing_coupon(**kwargs):
        raise RuntimeError("No coupon")
    page.coupon.wait_for = missing_coupon
    marked = []
    with pytest.raises(PlacementError, match="quick-coupon"):
        asyncio.run(live.place_test_coupon(
            page, "hearts", Decimal("15"), lambda: marked.append(True)))
    assert "select_odds" in page.trace
    assert "confirm" not in page.trace
    assert not marked


def test_test_filter_unknown_is_not_retried(fake_market, monkeypatch):
    async def no_delay(_):
        pass
    monkeypatch.setattr(live.asyncio, "sleep", no_delay)
    page = Page(reply="")
    marked = []
    status = asyncio.run(live.place_test_coupon(
        page, "diamonds", Decimal("10"), lambda: marked.append(True)))
    assert status == "UNKNOWN"
    assert marked == [True]
    assert page.trace.count("confirm") == 1


def test_coupon_test_filter_migrates_existing_database(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE live_control (id INTEGER PRIMARY KEY, mode TEXT, step INTEGER)")
        db.execute("INSERT INTO live_control VALUES (1,'LIVE',3)")
    store = LiveBets(path)
    assert store.snapshot()["coupon_test_filter"] == 0
    store.set_coupon_test_filter(True)
    assert LiveBets(path).snapshot()["coupon_test_filter"] == 1
    assert store.snapshot()["step"] == 3
