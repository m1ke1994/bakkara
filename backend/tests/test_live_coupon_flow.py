"""Fake bookmaker coupon tests: odds click, amount verification, confirmation and modal close."""
import asyncio
from decimal import Decimal

import pytest

import app.live_executor as live
from app.live_executor import PlacementError, place_one_click, close_result_dialog


def mapped_market():
    labels = [{"text": name, "x": 30, "y": 80 + i * 28, "width": 54, "height": 13}
              for i, name in enumerate(("пики", "трефы", "бубны", "черви"))]
    odds = {label["text"]: {"x": 900, "y": label["y"], "width": 19,
                             "height": 13, "amount": 1.9}
            for label in labels}
    return {"ok": True, "marketTitles": [{"text": "Игрок получит карту", "y": 40}],
            "labels": labels, "oddsBySuit": odds,
            "canvas": {"width": 951, "height": 850}}


class Field:
    def __init__(self):
        self.value = ""
        self.fills = []

    @property
    def first(self):
        return self

    async def wait_for(self, **kwargs):
        pass

    async def fill(self, value):
        self.value = value
        self.fills.append(value)

    async def input_value(self):
        return self.value


class Toggle:
    def __init__(self, enabled):
        self.enabled = enabled

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def is_checked(self):
        return self.enabled


class Button:
    def __init__(self, on_click=None):
        self.clicked = 0
        self.on_click = on_click

    async def count(self):
        return 1

    def nth(self, i):
        return self

    async def is_visible(self):
        return True

    async def is_disabled(self):
        return False

    async def click(self, **kwargs):
        self.clicked += 1
        if self.on_click:
            self.on_click()


class Dialog:
    def __init__(self):
        self.close = Button()
        self.continue_button = Button()

    async def count(self):
        return 1

    def nth(self, i):
        return self

    async def is_visible(self):
        return True

    def get_by_role(self, kind, name=None, exact=False):
        return self.close if name == "Закрыть" else self.continue_button


class Canvas:
    def __init__(self, page):
        self.page = page
        self.clicks = []

    @property
    def first(self):
        return self

    async def bounding_box(self):
        return {"x": 20, "y": 30, "width": 951, "height": 850}

    async def click(self, position, **kwargs):
        self.clicks.append(position)
        if self.page.immediate and self.page.reply:
            self.page.message = self.page.reply


class Body:
    def __init__(self, page):
        self.page = page

    async def inner_text(self, **kwargs):
        return self.page.message


class Page:
    def __init__(self, immediate=True, reply="Ставка принята"):
        self.immediate = immediate
        self.reply = reply
        self.message = ""
        self.amount = Field()
        self.toggle = Toggle(immediate)
        self.canvas = Canvas(self)
        self.body = Body(self)
        self.dialog = Dialog()
        self.confirm = Button(on_click=lambda: setattr(self, "message", self.reply))

    def locator(self, selector):
        return {live.AMOUNT: self.amount, live.ONE_CLICK: self.toggle,
                live.CANVAS_SELECTOR: self.canvas, "body": self.body}[selector]

    def get_by_role(self, kind, name=None, exact=False):
        return self.dialog if kind == "dialog" else self.confirm


@pytest.fixture
def mock_probe(monkeypatch):
    async def fake_probe(_page):
        return mapped_market()
    monkeypatch.setattr(live, "probe_player_suit_market", fake_probe)


def test_one_click_uses_numeric_odds_and_closes_result(mock_probe):
    page = Page()
    marked = []
    outcome = asyncio.run(place_one_click(page, "clubs", Decimal("15"), lambda: marked.append(True)))
    assert outcome == "ACCEPTED"
    assert page.amount.fills == ["15"]
    assert page.canvas.clicks == [{"x": 900.0, "y": 108.0}]
    assert page.confirm.clicked == 0  # one-click is already a submission
    assert page.dialog.close.clicked == 1
    assert marked == [True]


def test_regular_coupon_confirms_once(mock_probe):
    page = Page(immediate=False)
    outcome = asyncio.run(place_one_click(page, "hearts", Decimal("37.50"), lambda: None))
    assert outcome == "ACCEPTED"
    assert page.amount.value == "37.50"
    assert page.confirm.clicked == 1
    assert page.dialog.close.clicked == 1


def test_missing_odds_blocks_before_any_submission(monkeypatch):
    async def broken_probe(_page):
        data = mapped_market()
        data["oddsBySuit"] = {}
        return data
    monkeypatch.setattr(live, "probe_player_suit_market", broken_probe)
    page = Page()
    submitted = []
    with pytest.raises(PlacementError, match="координаты"):
        asyncio.run(place_one_click(page, "diamonds", Decimal("15"), lambda: submitted.append(True)))
    assert page.canvas.clicks == []
    assert submitted == []


def test_unknown_after_click_is_not_retried(mock_probe, monkeypatch):
    async def no_delay(_seconds):
        pass
    monkeypatch.setattr(live.asyncio, "sleep", no_delay)
    page = Page(reply="")
    statuses = []
    result = asyncio.run(place_one_click(page, "spades", Decimal("22"), lambda: statuses.append("SUBMITTING")))
    assert result == "UNKNOWN"
    assert page.canvas.clicks == [{"x": 900.0, "y": 80.0}]
    assert page.dialog.close.clicked == 0
    assert len(statuses) == 1
