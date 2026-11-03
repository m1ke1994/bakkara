"""Fast N+1 handoff after the final third Player card (no blind LIVE bet)."""
import asyncio
from collections import deque
from decimal import Decimal
from types import SimpleNamespace

import app.browser as browser_module
from app.browser import (
    BrowserService, cached_successor_prestart_evidence,
)
from app.cycle18 import STRATEGY_SECOND_CARD
from app.live_bets import LiveBets
from app.observation import Game, ObservationStore, classify_game


def upcoming(number=101, *, inferred=False):
    game = Game(
        f"g{number}",
        f"https://example.test/ru/live/baccarat/2050671-baccara/{number}-player-banker",
        "00:15", str(number), "Не удалось определить", 0,
        player_score="0", banker_score="0",
        is_countdown_to_start=not inferred,
        upcoming_by_sequence=inferred,
    )
    assert classify_game(game) == "UPCOMING"
    return game


def page_state(**overrides):
    snapshot = {
        "counts": {"player_fields": 1, "player_cards_containers": 1, "player_card_boxes": 3},
        "cards": [], "banker_cards": [], "round_timer_text": "00:12",
        "finished": False, "expired_screen": False,
    }
    snapshot.update(overrides)
    return snapshot


def test_prefetched_successor_not_discarded_when_old_timer_expires():
    async def run():
        previous = 100
        candidate = upcoming()
        service = object.__new__(BrowserService)
        service.page = object()
        service._prefetched_rounds = {101: candidate}
        service._prefetched_at = asyncio.get_running_loop().time() - 30
        service._last_selected_round_number = previous
        service.live_bets = SimpleNamespace(snapshot=lambda: {"mode": "LIVE"})
        service.events = deque(maxlen=20)
        service.state_version = 0
        calls = []

        async def selected(game, *args, **kwargs):
            calls.append((game.game_id, kwargs.get("selected_from_cache"),
                          service._stale_cached_game_id))
        service._begin_selected_game = selected
        await service._observe_iteration({"g100"}, {})
        assert calls == [("g101", True, "g101")]
        assert service._stale_cached_game_id is None
        assert any("таймер списка устарел" in x["message"] for x in service.events)
    asyncio.run(run())


def test_expired_list_timer_fresh_page_is_required_before_live_money():
    game = upcoming()
    allowed, reason = cached_successor_prestart_evidence(
        game, page_state(), game.url)
    assert allowed, reason
    inferred = upcoming(inferred=True)
    assert cached_successor_prestart_evidence(inferred, page_state(), inferred.url)[0]

    for bad in (
        page_state(cards=[{"position": 1, "rank": "K", "suit": "clubs"}]),
        page_state(banker_cards=[{"position": 1, "rank": "A", "suit": "hearts"}]),
        page_state(finished=True),
        page_state(expired_screen=True),
        page_state(round_timer_text=""),
        page_state(round_timer_text="00:01"),
        page_state(counts={"player_fields": 0, "player_cards_containers": 0, "player_card_boxes": 0}),
    ):
        assert not cached_successor_prestart_evidence(game, bad, game.url)[0]
    assert not cached_successor_prestart_evidence(
        game, page_state(), "https://example.test/another-round")[0]
    game.player_score = "2"
    assert not cached_successor_prestart_evidence(game, page_state(), game.url)[0]


def test_stale_cache_never_sends_live_money_without_fresh_evidence(tmp_path, monkeypatch):
    async def run():
        game = upcoming()
        ledger = LiveBets(tmp_path / "baccarat.sqlite3")
        ledger.mode("LIVE")
        events = []
        async def forbidden(*_args, **_kwargs):
            raise AssertionError("A stale timer may never authorize a LIVE coupon")
        monkeypatch.setattr(browser_module, "place_regular_coupon", forbidden)

        class Cycle:
            def get_strategy(self):
                return {"selected": STRATEGY_SECOND_CARD, "entry_losses": 3}

            def refresh(self, *_args):
                return {
                    "ready": True,
                    "strategy": STRATEGY_SECOND_CARD,
                    "entry_losses": 3,
                    "session_id": 17,
                    "entry_gate": {"bet_allowed": True, "phase": "BETTING",
                                   "step": 1, "max_steps": 8},
                    "current": {"target_game_id": game.game_id,
                                "target_round_number": 101,
                                "predicted_suit": "clubs",
                                "result": "PENDING", "mode": "LIVE"},
                    "live_preview": [{"phase": "CURRENT", "available": True,
                                     "target_round_number": 101,
                                     "predicted_suit": "clubs",
                                     "source_round_numbers": [100]}],
                }

        async def inspect(*_args):
            return page_state(cards=[{"position": 1, "rank": "A", "suit": "clubs"}])

        fake = SimpleNamespace(
            live_bets=ledger,
            cycle_engine=Cycle(),
            live_plan_store=SimpleNamespace(load=lambda: SimpleNamespace(
                row=[Decimal("10")] * 8, steps=8)),
            page=SimpleNamespace(url=game.url),
            page_lock=asyncio.Lock(),
            current_observation={"game_id": game.game_id},
            _stale_cached_game_id=game.game_id,
            _inspect_game_dom=inspect,
            event=lambda *args: events.append(args),
        )
        await BrowserService._live_before_cards(fake, game)
        assert ledger.get_attempt(game.game_id) is None
        assert any("ставка запрещена" in message for _, message in events)

    asyncio.run(run())


def test_three_card_final_goes_to_next_page_without_waiting_for_banker(tmp_path):
    async def run():
        class Page:
            url = "about:blank"

            async def goto(self, url, **_kwargs):
                self.url = url

        service = object.__new__(BrowserService)
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "three.sqlite3")
        service.events = deque(maxlen=30)
        service.state_version = 0
        service.current_observation = {"game_id": "three"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service.observation_phase = ""
        service._active_chain_source_id = None

        async def resolved():
            return True

        service._probe_observation_auth_locked = resolved
        after = []
        async def no_live(*_args, **_kwargs):
            after.append("settlement")
        service._live_after_cards = no_live

        async def after_handoff(_game, _cards):
            after.append("next tab ready")
        service._handoff_outcome_page = after_handoff

        async def forbidden_outcome(*_args, **_kwargs):
            raise AssertionError("Must not wait for Banker before switching")
        service._confirm_outcome_from_snapshot = forbidden_outcome

        cards = [
            {"position": 1, "rank": "3", "suit": "spades"},
            {"position": 2, "rank": "K", "suit": "spades"},
            {"position": 3, "rank": "J", "suit": "clubs"},
        ]

        async def inspect(*_args):
            return {
                "cards": cards, "pending_card_slots": 0,
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 3},
                "banker_cards": [], "finished": False,
                "status_text": "", "cause": "fixture",
            }
        service._inspect_game_dom = inspect
        service._live_before_cards = lambda _game: no_live()
        game = Game("three", "https://example.test/baccarat/three",
                    "00:12", "650", "Не удалось определить", 0)
        processed = set()
        await service._scan_selected_game(game, [], processed, {})
        await service._pending_live_settlement
        assert processed == {"three"}
        assert service.store.get("three")["player_hand_final"]
        assert after == ["next tab ready", "settlement"] or after == ["settlement", "next tab ready"]

    asyncio.run(run())
