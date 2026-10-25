"""The early LIVE test exercises the real executor path, not a fake strategy forecast."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.browser import BrowserService
from app.live_bets import LiveBets, settle_test_hand


class NoForecastEngine:
    def refresh(self, *_args):
        raise AssertionError("Manual suit test must not ask Cycle18Engine for a prediction")


def final_hand(game_id="game-1", number="601", suits=("hearts", "clubs")):
    return {
        "game_id": game_id,
        "round_number": number,
        "cards": [{"position": i + 1, "rank": rank, "suit": suit}
                  for i, (rank, suit) in enumerate(zip(("A", "K", "7"), suits))],
        "finished_confirmed": True,
        "player_hand_final": True,
        "final_snapshot_reverified": True,
    }


def test_early_filter_settings_migrate_existing_db_and_survive_restart(tmp_path):
    store = LiveBets(tmp_path / "live.sqlite3")
    assert store.snapshot()["warmup_test"] == 0
    assert store.snapshot()["test_suit"] == "hearts"
    store.set_warmup_test(True, "clubs")
    restarted = LiveBets(tmp_path / "live.sqlite3")
    assert restarted.snapshot()["warmup_test"] == 1
    assert restarted.snapshot()["test_suit"] == "clubs"
    restarted.set_warmup_test(False, "diamonds")
    assert restarted.snapshot()["warmup_test"] == 0
    assert restarted.snapshot()["test_suit"] == "diamonds"
    with pytest.raises(ValueError):
        store.set_warmup_test(True, "joker")


def test_manual_test_does_not_change_strategy_prediction_stats(tmp_path):
    store = LiveBets(tmp_path / "live.sqlite3")
    store.set_warmup_test(True, "hearts")
    assert store.reserve("game-1", "601", "hearts", Decimal("15"), "MANUAL_TEST")
    attempt = store.get_attempt("game-1")
    assert attempt["signal_source"] == "MANUAL_TEST"
    assert settle_test_hand(attempt, final_hand()) == "WIN"
    assert settle_test_hand(attempt, final_hand(suits=("diamonds", "spades"))) == "LOSE"
    assert settle_test_hand(attempt, final_hand(suits=("hearts", "clubs"), number="602")) == "UNKNOWN"
    assert settle_test_hand(attempt, {**final_hand(), "final_snapshot_reverified": False}) == "UNKNOWN"
    assert settle_test_hand({**attempt, "signal_source": "STRATEGY"}, final_hand()) == "UNKNOWN"


def test_filter_settings_cannot_change_during_an_unresolved_bet(tmp_path):
    store = LiveBets(tmp_path / "live.sqlite3")
    assert store.reserve("game-1", "601", "clubs", Decimal("15"), "MANUAL_TEST")
    with pytest.raises(ValueError, match="незавершённая"):
        store.set_warmup_test(True, "hearts")
    store.update("game-1", "REJECTED")
    store.set_warmup_test(True, "hearts")
    assert store.snapshot()["warmup_test"] == 1


def test_first_game_bypasses_warmup_and_uses_regular_coupon(tmp_path, monkeypatch):
    import app.browser as browser_mod
    store = LiveBets(tmp_path / "live.sqlite3")
    store.mode("LIVE")
    store.set_warmup_test(True, "clubs")
    calls, events = [], []
    monkeypatch.setattr(browser_mod, "classify_game", lambda _game: "UPCOMING")

    async def fake_coupon(_page, suit, amount, mark_submitting, report, **_kwargs):
        calls.append((suit, amount))
        report("Canvas и обычный купон проверены")
        mark_submitting()
        return "ACCEPTED"

    monkeypatch.setattr(browser_mod, "place_regular_coupon", fake_coupon)

    fake = SimpleNamespace(
        live_bets=store,
        cycle_engine=NoForecastEngine(),
        live_plan_store=SimpleNamespace(load=lambda: SimpleNamespace(row=[Decimal("15"), Decimal("30")])),
        page_lock=asyncio.Lock(), page=object(),
        current_observation=None, event=lambda level, message: events.append((level, message)),
    )
    game = SimpleNamespace(game_id="game-1", round_number="601")
    asyncio.run(BrowserService._live_before_cards(fake, game))
    attempt = store.get_attempt("game-1")
    assert calls == [("clubs", Decimal("15"))]
    assert attempt["signal_source"] == "MANUAL_TEST"
    assert attempt["status"] == "ACCEPTED"
    assert any("ожидание подготовки 20/40 рук отключено" in msg for _, msg in events)
    assert not store.reserve("game-1", "601", "clubs", Decimal("15"), "MANUAL_TEST")

    fake.store = SimpleNamespace(get=lambda _id: final_hand(suits=("hearts", "diamonds")))
    asyncio.run(BrowserService._live_after_cards(fake, game))
    assert store.get_attempt("game-1")["result"] == "LOSE"
    assert store.snapshot()["step"] == 2

    game2 = SimpleNamespace(game_id="game-2", round_number="602")
    asyncio.run(BrowserService._live_before_cards(fake, game2))
    assert calls[-1] == ("clubs", Decimal("30"))
    fake.store = SimpleNamespace(get=lambda _id: final_hand(game_id="game-2", number="602",
                                                               suits=("clubs", "diamonds")))
    asyncio.run(BrowserService._live_after_cards(fake, game2))
    assert store.get_attempt("game-2")["result"] == "WIN"
    assert store.snapshot()["step"] == 1


def test_disabled_filter_keeps_cycle_warmup_gate(tmp_path, monkeypatch):
    import app.browser as browser_mod
    store = LiveBets(tmp_path / "live.sqlite3")
    store.mode("LIVE")
    monkeypatch.setattr(browser_mod, "classify_game", lambda _: "UPCOMING")

    class NotReady:
        def refresh(self, *_):
            return {"ready": False, "live_preview": []}

    fake = SimpleNamespace(live_bets=store, cycle_engine=NotReady(),
        current_observation=None, event=lambda *_: None)
    asyncio.run(BrowserService._live_before_cards(fake,
                  SimpleNamespace(game_id="game-1", round_number="601")))
    assert store.get_attempt("game-1") is None


@pytest.mark.parametrize("warmup", [20, 40])
def test_strategy_live_uses_regular_coupon_after_warmup(tmp_path, monkeypatch, warmup):
    import app.browser as browser_mod

    store = LiveBets(tmp_path / "live.sqlite3")
    store.mode("LIVE")
    monkeypatch.setattr(browser_mod, "classify_game", lambda _game: "UPCOMING")
    calls = []

    async def fake_coupon(_page, suit, amount, mark_submitting, report, **_kwargs):
        calls.append((suit, amount))
        mark_submitting()
        return "ACCEPTED"

    monkeypatch.setattr(browser_mod, "place_regular_coupon", fake_coupon)

    class Cycle:
        count = warmup - 1

        def refresh(self, _observation):
            return {
                "ready": self.count >= warmup,
                "current": {"target_game_id": "game-601", "target_round_number": 601,
                            "predicted_suit": "diamonds", "result": "PENDING", "mode": "LIVE"}
                if self.count >= warmup else None,
                "live_preview": [{"phase": "CURRENT", "available": True,
                                  "target_round_number": 601, "predicted_suit": "diamonds"}]
                if self.count >= warmup else [],
            }

    cycle = Cycle()
    fake = SimpleNamespace(
        live_bets=store, cycle_engine=cycle,
        live_plan_store=SimpleNamespace(load=lambda: SimpleNamespace(row=[Decimal("10")])),
        page_lock=asyncio.Lock(), page=object(), current_observation=None,
        event=lambda *_args: None,
    )
    game = SimpleNamespace(game_id="game-601", round_number="601")
    asyncio.run(BrowserService._live_before_cards(fake, game))
    assert store.get_attempt(game.game_id) is None
    assert calls == []

    cycle.count = warmup
    asyncio.run(BrowserService._live_before_cards(fake, game))
    attempt = store.get_attempt(game.game_id)
    assert calls == [("diamonds", Decimal("10"))]
    assert attempt["signal_source"] == "STRATEGY"
    assert attempt["status"] == "ACCEPTED"


def test_live_requires_recorded_forecast_for_the_exact_game(tmp_path, monkeypatch):
    import app.browser as browser_mod
    store = LiveBets(tmp_path / "live.sqlite3")
    store.mode("LIVE")
    monkeypatch.setattr(browser_mod, "classify_game", lambda _: "UPCOMING")

    class PreviewOnly:
        def refresh(self, _):
            return {"ready": True, "current": None, "live_preview": [
                {"phase": "CURRENT", "available": True,
                 "target_round_number": 601, "predicted_suit": "diamonds"}]}

    fake = SimpleNamespace(
        live_bets=store, cycle_engine=PreviewOnly(), current_observation=None,
        event=lambda *_: None)
    asyncio.run(BrowserService._live_before_cards(
        fake, SimpleNamespace(game_id="game-601", round_number="601")))
    assert store.get_attempt("game-601") is None


def test_pre_submit_failure_is_not_a_bookmaker_rejection(tmp_path, monkeypatch):
    import app.browser as browser_mod
    from app.live_executor import PlacementError

    store = LiveBets(tmp_path / "live.sqlite3")
    store.mode("LIVE")
    store.set_warmup_test(True, "diamonds")
    monkeypatch.setattr(browser_mod, "classify_game", lambda _game: "UPCOMING")

    async def missing_market(*_args, **_kwargs):
        raise PlacementError("Canvas: коэффициент не найден")

    monkeypatch.setattr(browser_mod, "place_regular_coupon", missing_market)
    fake = SimpleNamespace(
        live_bets=store, cycle_engine=NoForecastEngine(),
        live_plan_store=SimpleNamespace(load=lambda: SimpleNamespace(row=[Decimal("10")])),
        page_lock=asyncio.Lock(), page=object(), current_observation=None,
        event=lambda *_args: None,
    )
    game = SimpleNamespace(game_id="game-601", round_number="601")
    asyncio.run(BrowserService._live_before_cards(fake, game))
    attempt = store.get_attempt(game.game_id)
    assert attempt["status"] == "NOT_SUBMITTED"
    assert "коэффициент не найден" in attempt["detail"]
    assert store.snapshot()["step"] == 1
