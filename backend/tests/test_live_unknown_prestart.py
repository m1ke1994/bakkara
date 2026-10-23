"""Baccarat list UNKNOWN must not silently suppress explicitly requested LIVE testing."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.browser import BrowserService, live_test_prestart_evidence
from app.live_bets import LiveBets
from app.observation import Game, classify_game


URL = "https://example.invalid/ru/live/baccarat/2050671-baccara/764340081-player-banker"


def upcoming_unknown():
    game = Game(
        game_id="764340081", url=URL, time="00:17", round_number="1108",
        status="Не удалось определить", position=0,
        player_score="0", banker_score="0",
    )
    assert classify_game(game) == "UNKNOWN"
    return game


def diagnostics(**update):
    obj = {
        "counts": {"player_fields": 1, "player_cards_containers": 1, "player_card_boxes": 3},
        "cards": [], "round_timer_text": "00:16",
        "finished": False, "expired_screen": False,
    }
    obj.update(update)
    return obj


def test_unknown_list_status_can_be_verified_using_live_game_component():
    allowed, reason = live_test_prestart_evidence(upcoming_unknown(), diagnostics(), URL)
    assert allowed
    assert "0:0" in reason


def test_unknown_status_does_not_allow_blind_bet_when_page_unverified():
    game = upcoming_unknown()
    for kw in [
        {"cards": [{"position": 1, "rank": "K", "suit": "clubs"}]},
        {"finished": True},
        {"expired_screen": True},
        {"counts": {"player_fields": 0, "player_cards_containers": 0, "player_card_boxes": 0}},
        {"round_timer_text": ""},
        {"round_timer_text": "00:01"},
    ]:
        allowed, _ = live_test_prestart_evidence(game, diagnostics(**kw), URL)
        assert not allowed, kw
    game.player_score = "1"
    assert not live_test_prestart_evidence(game, diagnostics(), URL)[0]
    game.player_score = "0"
    assert not live_test_prestart_evidence(game, diagnostics(), "https://example.invalid/ru/block")[0]


def test_unknown_status_does_not_skip_manual_live_test(tmp_path, monkeypatch):
    import app.browser as module

    game = upcoming_unknown()
    store = LiveBets(tmp_path / "baccarat.sqlite3")
    store.mode("LIVE")
    store.set_warmup_test(True, "clubs")
    recorded = []

    class Page:
        url = URL

        async def evaluate(self, js):
            return False  # No access-denied banner.

    class NoForecast:
        def refresh(self, _):
            raise AssertionError("Manual test must bypass cycle prediction and warmup")

    async def inspect(_self, _player, _banker):
        return diagnostics()

    async def fake_coupon(_page, suit, stake, mark, progress):
        recorded.append((suit, stake))
        progress("Коэффициент выбран и купон проверен")
        mark()
        return "ACCEPTED"

    monkeypatch.setattr(module, "place_test_coupon", fake_coupon)
    fake = SimpleNamespace(
        live_bets=store,
        cycle_engine=NoForecast(),
        page=Page(),
        page_lock=asyncio.Lock(),
        live_plan_store=SimpleNamespace(load=lambda: SimpleNamespace(row=[Decimal("10")])),
        _inspect_game_dom=inspect,
        event=lambda severity, message: recorded.append(message),
        current_observation=None,
    )
    asyncio.run(BrowserService._live_before_cards(fake, game))
    assert ("clubs", Decimal("10")) in recorded
    assert any("статус списка UNKNOWN" in value for value in recorded if isinstance(value,str))
    assert store.get_attempt(game.game_id)["status"] == "ACCEPTED"


def test_access_denied_aborts_before_any_coupon(tmp_path, monkeypatch):
    import app.browser as module

    game = upcoming_unknown()
    store = LiveBets(tmp_path / "baccarat.sqlite3")
    store.mode("LIVE")
    store.set_warmup_test(True, "clubs")
    recorded = []

    class Page:
        url = URL
        async def evaluate(self, js):
            return True  # Site-level access restriction.

    async def forbidden_coupon(*args, **kwargs):
        raise AssertionError("Never click the bookmaker if access denied")

    monkeypatch.setattr(module, "place_test_coupon", forbidden_coupon)
    fake = SimpleNamespace(
        live_bets=store, cycle_engine=object(), page=Page(), page_lock=asyncio.Lock(),
        event=lambda level, msg: recorded.append(msg),
    )
    asyncio.run(BrowserService._live_before_cards(fake, game))
    assert any("Доступ запрещён" in msg for msg in recorded)
    assert store.get_attempt(game.game_id) is None
