"""Regression for round #523: UNKNOWN list status, valid N-1 LIVE signal.

A strategy coupon may proceed only after fresh, pre-deal DOM evidence,
then independently verified forecast, entry gate and persistent ledger.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.browser import BrowserService, live_test_prestart_evidence
from app.cycle18 import STRATEGY_SECOND_CARD
from app.live_bets import LiveBets
from app.observation import Game


URL = "https://example.test/ru/live/baccarat/2050671-baccara/767619094-player-banker"


def round_523(**kwargs):
    options = dict(
        game_id="767619094", url=URL, time="00:37", round_number="523",
        status="Не удалось определить", position=2,
        player_score="0", banker_score="0",
    )
    options.update(kwargs)
    return Game(**options)


def dom(**kwargs):
    state = {
        "counts": {"player_fields": 1, "player_cards_containers": 1,
                   "player_card_boxes": 3},
        "cards": [], "banker_cards": [], "round_timer_text": "00:27",
        "finished": False, "expired_screen": False,
        "player_score_text": "0", "banker_score_text": "0",
    }
    state.update(kwargs)
    return state


def test_unknown_list_requires_fresh_page_with_zero_score_and_no_cards():
    game = round_523()
    assert live_test_prestart_evidence(game, dom(), URL)[0]
    no_list_score = round_523(player_score="", banker_score="")
    assert live_test_prestart_evidence(no_list_score, dom(), URL)[0]
    for invalid in (
        dom(player_score_text="", banker_score_text=""),
        dom(player_score_text="1"),
        dom(round_timer_text="00:02"),
        dom(round_timer_text=""),
        dom(cards=[{"rank": "J", "suit": "hearts", "position": 1}]),
        dom(banker_cards=[{"rank": "7", "suit": "clubs", "position": 1}]),
        dom(finished=True),
        dom(expired_screen=True),
        dom(counts={"player_fields": 0, "player_cards_containers": 0, "player_card_boxes": 0}),
    ):
        if invalid.get("player_score_text") == "0" and invalid.get("banker_score_text") == "0":
            # With 0:0 already independently confirmed in the LIST, an
            # incomplete page score alone is not a reason to refuse.
            pass
        assert not live_test_prestart_evidence(no_list_score, invalid, URL)[0]
    assert not live_test_prestart_evidence(game, dom(), URL.replace("example.test", "bad.test"))[0]
    game.started = True
    assert not live_test_prestart_evidence(game, dom(), URL)[0]


def test_selected_second_card_unknown_accepts_exactly_one_verified_strategy_wager(tmp_path, monkeypatch):
    import app.browser as browser_module
    ledger = LiveBets(tmp_path / "ledger.sqlite3")
    ledger.mode("LIVE")
    attempted = []
    messages = []

    class Page:
        url = URL
        async def evaluate(self, _script):
            return False

    class Engine:
        def get_strategy(self):
            return {"selected": STRATEGY_SECOND_CARD,
                    "active": STRATEGY_SECOND_CARD, "entry_losses": 1}

        def refresh(self, _current):
            return {
                "ready": True, "strategy": STRATEGY_SECOND_CARD,
                "entry_losses": 1, "session_id": 123,
                "entry_gate": {"phase": "BETTING", "bet_allowed": True,
                               "step": 3, "max_steps": 4},
                "live_preview": [{
                    "phase": "CURRENT", "available": True,
                    "target_round_number": 523,
                    "predicted_suit": "spades",
                    "source_round_numbers": [522],
                }],
                "current": {
                    "target_game_id": "767619094",
                    "target_round_number": 523, "predicted_suit": "spades",
                    "result": "PENDING", "mode": "LIVE",
                },
            }

    async def inspect(_player, _banker):
        return dom()

    async def coupon(_page, suit, amount, mark, progress, **kwargs):
        attempted.append((suit, amount, kwargs["expected_url"]))
        mark()
        return "ACCEPTED"

    monkeypatch.setattr(browser_module, "place_regular_coupon", coupon)
    fake = SimpleNamespace(
        cycle_engine=Engine(), live_bets=ledger, page=Page(),
        page_lock=asyncio.Lock(),
        live_plan_store=SimpleNamespace(load=lambda: SimpleNamespace(
            row=[Decimal("10"), Decimal("21"), Decimal("45"), Decimal("90")],
            steps=4,
        )),
        current_observation={"game_id": "767619094", "round_number": "523"},
        _inspect_game_dom=inspect,
        event=lambda level, msg: messages.append((level, msg)),
    )

    async def run():
        await BrowserService._live_before_cards(fake, round_523())
        await BrowserService._live_before_cards(fake, round_523())
    asyncio.run(run())
    assert attempted == [("spades", Decimal("45"), URL)]
    attempt = ledger.get_attempt("767619094")
    assert attempt["status"] == "ACCEPTED"
    assert attempt["step"] == 3
    assert attempt["strategy"] == STRATEGY_SECOND_CARD
    assert attempt["strategy_session_id"] == 123
    assert sum("LIVE_PRESTART_VERIFIED" in s for _, s in messages) == 2
    assert any("новая попытка не создана" in s for _, s in messages)


@pytest.mark.parametrize("scenario", [
    "no_clock", "already_started", "revealed_card", "wrong_url",
    "other_strategy", "inactive_session", "wrong_forecast", "waiting",
    "missing_scores",
])
def test_unknown_status_fails_closed_without_strong_proof(tmp_path, monkeypatch, scenario):
    import app.browser as browser_module
    ledger = LiveBets(tmp_path / "blocked.sqlite3")
    ledger.mode("LIVE")
    game = round_523()
    page_url = URL if scenario != "wrong_url" else URL + "/different"
    page_data = dom()
    if scenario == "no_clock":
        page_data["round_timer_text"] = ""
    elif scenario == "revealed_card":
        page_data["cards"] = [{"position": 1, "rank": "J", "suit": "clubs"}]
    elif scenario == "already_started":
        game.started = True
    elif scenario == "missing_scores":
        game.player_score = game.banker_score = ""
        page_data["player_score_text"] = ""
        page_data["banker_score_text"] = ""

    class Page:
        url = page_url
        async def evaluate(self, _script):
            return False

    class Engine:
        def get_strategy(self):
            return {"selected": "n18" if scenario == "other_strategy"
                    else STRATEGY_SECOND_CARD,
                    "active": "n18" if scenario == "inactive_session"
                    else STRATEGY_SECOND_CARD, "entry_losses": 1}

        def refresh(self, _current):
            return {
                "ready": True, "strategy": STRATEGY_SECOND_CARD,
                "entry_losses": 1, "session_id": 123,
                "entry_gate": {"phase": "WAITING" if scenario == "waiting"
                               else "BETTING", "bet_allowed": scenario != "waiting",
                               "step": 3, "max_steps": 4},
                "live_preview": [{
                    "phase": "CURRENT", "available": scenario != "waiting",
                    "target_round_number": 523,
                    "predicted_suit": "spades",
                    "source_round_numbers": [522],
                }],
                "current": {
                    "target_game_id": "767619094" if scenario != "wrong_forecast" else "other",
                    "target_round_number": 523,
                    "predicted_suit": "spades",
                    "result": "PENDING", "mode": "LIVE",
                },
            }

    async def inspect(_player, _banker):
        return page_data

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Unverified N-1 attempt must not send a coupon")

    monkeypatch.setattr(browser_module, "place_regular_coupon", forbidden)
    fake = SimpleNamespace(
        cycle_engine=Engine(), live_bets=ledger,
        page=Page(), page_lock=asyncio.Lock(),
        live_plan_store=SimpleNamespace(load=lambda: SimpleNamespace(
            row=[Decimal("10"), Decimal("21"), Decimal("45"), Decimal("90")], steps=4)),
        current_observation={"game_id": "767619094", "round_number": "523"},
        _inspect_game_dom=inspect,
        event=lambda *_args: None,
    )
    asyncio.run(BrowserService._live_before_cards(fake, game))
    assert ledger.get_attempt("767619094") is None
