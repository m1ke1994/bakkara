"""Baccarat 2-vs-3 Player card decision in ONLY the N-1 second-card strategy.

A Player total 0..5 requires a third card unless Banker opens with a
natural 8/9. A Player total 6..9 always stands. No Banker suits, third
Banker card, or Banker outcome are required for this decision.
"""
import asyncio
from collections import deque

import pytest
from playwright.async_api import async_playwright

from app.browser import BrowserService
from app.cycle18 import STRATEGY_SECOND_CARD
from app.draw_rules import player_card_count
from app.observation import Game, ObservationStore


def hand(*ranks):
    return [
        {"position": i, "rank": rank, "suit": ["hearts", "diamonds", "clubs"][i - 1]}
        for i, rank in enumerate(ranks, start=1)
    ]


@pytest.mark.parametrize("player,banker,expected", [
    (("J", "10"), ("A", "7"), 2),    # Player 0; Banker natural 8
    (("K", "Q"), ("9", "10"), 2),   # Player 0; Banker natural 9
    (("3", "2"), ("8", "K"), 2),    # Player 5; Banker natural 8
    (("3", "2"), ("6", "K"), 3),    # Player 5; Banker 6 -> draw third
    (("A", "A"), ("3", "4"), 3),    # Player 2; Banker 7 -> draw third
    (("J", "10"), ("6", "K"), 3),   # Player 0; Banker 6 -> draw third
    (("7", "K"), (), 2),             # Player 7 stands without reading Banker
    (("3", "3"), (), 2),             # Player 6 stands without reading Banker
    (("J", "10"), (), None),         # Banker not yet revealed -> undecidable
    (("3", "2"), ("A",), None),     # Banker second rank not revealed
])
def test_punto_banco_required_player_cards(player, banker, expected):
    assert player_card_count(hand(*player), banker) == expected


def test_dom_player_only_reads_two_banker_ranks_only_if_natural_can_matter():
    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service = object.__new__(BrowserService)
            service.page = page
            try:
                async def set_html(p0, p1):
                    await page.set_content(f"""
                    <style>
                     .baccarat-player,.baccarat-player__name,
                     .baccarat-player__cards,.baccarat-player__card-box,
                     .baccarat-card__rank {{ display:block; }}
                    </style>
                    <div class="baccarat-player baccarat__player">
                      <span class="baccarat-player__name">Игрок</span>
                      <ul class="baccarat-player__cards">
                        <li class="baccarat-player__card-box suit-hearts"><span class="baccarat-card__rank">{p0}</span></li>
                        <li class="baccarat-player__card-box suit-diamonds"><span class="baccarat-card__rank">{p1}</span></li>
                        <li class="baccarat-player__card-box"></li>
                      </ul>
                    </div>
                    <div class="baccarat-player baccarat__player">
                      <span class="baccarat-player__name">Банкир</span>
                      <ul class="baccarat-player__cards">
                        <li class="baccarat-player__card-box suit-clubs"><span class="baccarat-card__rank">A</span></li>
                        <li class="baccarat-player__card-box suit-spades"><span class="baccarat-card__rank">7</span></li>
                        <li class="baccarat-player__card-box suit-diamonds"><span class="baccarat-card__rank">3</span></li>
                      </ul>
                    </div>
                    """)
                await set_html("J", "10")
                snap = await service._inspect_game_dom("Игрок", "Банкир", player_only=True)
                assert [c["rank"] for c in snap["cards"]] == ["J", "10"]
                assert snap["banker_initial_ranks"] == ["A", "7"]
                assert snap["banker_cards"] == []
                assert snap["banker_ranks"] == ["A", "7"]
                assert player_card_count(snap["cards"], snap["banker_initial_ranks"]) == 2

                await set_html("7", "K")
                stood = await service._inspect_game_dom("Игрок", "Банкир", player_only=True)
                assert [c["rank"] for c in stood["cards"]] == ["7", "K"]
                assert stood["banker_initial_ranks"] == []
                assert stood["banker_cards"] == []
                assert player_card_count(stood["cards"]) == 2
            finally:
                await browser.close()

    asyncio.run(run())


def fake_service(tmp_path):
    class Page:
        url = "about:blank"

        async def goto(self, url, **_kwargs):
            self.url = url

    service = object.__new__(BrowserService)
    service.page = Page()
    service.page_lock = asyncio.Lock()
    service.store = ObservationStore(tmp_path / "two-three.sqlite3")
    service.cycle_engine = type("Engine", (), {"get_strategy": lambda _: {
        "selected": STRATEGY_SECOND_CARD, "active": STRATEGY_SECOND_CARD
    }})()
    service.events = deque(maxlen=100)
    service.state_version = 0
    service.current_observation = {"game_id": "g502"}
    service.observation_status = "WAITING_FOR_GAME"
    service.observation_diagnostic = ""
    service.observation_phase = ""

    async def allow():
        return True

    async def no_live(*_args, **_kwargs):
        return None

    async def should_not_handoff(*_args, **_kwargs):
        raise AssertionError("Second-card strategy must not open a Banker watcher")

    service._probe_observation_auth_locked = allow
    service._live_before_cards = no_live
    service._live_after_cards = no_live
    service._handoff_outcome_page = should_not_handoff
    service._confirm_outcome_from_snapshot = should_not_handoff
    return service


def snapshot(player_cards, banker, *, boxes=3, pending=1):
    return {
        "cards": player_cards, "pending_card_slots": pending,
        "counts": {"player_fields": 1, "player_cards_containers": 1,
                   "player_card_boxes": boxes},
        "banker_initial_ranks": list(banker),
        "banker_ranks": list(banker),
        "banker_cards": [],
        "finished": False, "status_text": "", "cause": "test",
    }


def test_player_zero_banker_natural_handoffs_on_second_player_card(tmp_path):
    async def run():
        service = fake_service(tmp_path)
        seen = 0

        async def inspect(*_args, **kwargs):
            nonlocal seen
            seen += 1
            assert kwargs.get("player_only") is True
            return snapshot(hand("J", "10"), ("A", "7"))

        service._inspect_game_dom = inspect
        game = Game("g502", "https://example.test/baccarat/502",
                    "00:12", "502", "Ожидается", 0)
        processed = set()
        await asyncio.wait_for(
            service._scan_selected_game(game, [], processed, {}), 2)
        stored = service.store.get("g502")
        assert seen == 2, "Two independent reads must confirm final Player hand"
        assert processed == {"g502"}
        assert stored["cards"] == hand("J", "10")
        assert stored["player_hand_final"] is True
        assert stored["final_snapshot_reverified"] is True
        assert stored["banker_cards"] == []
        assert any("SECOND_CARD_FAST_HANDOFF" in e["message"] for e in service.events)
        await service._pending_live_settlement

    asyncio.run(run())


def test_player_zero_non_natural_banker_waits_for_real_third(tmp_path):
    async def run():
        service = fake_service(tmp_path)
        seen = 0

        async def inspect(*_args, **kwargs):
            nonlocal seen
            seen += 1
            assert kwargs.get("player_only") is True
            if seen < 4:
                return snapshot(hand("J", "10"), ("6", "K"))
            return snapshot(hand("J", "10", "A"), ("6", "K"),
                            pending=0)

        service._inspect_game_dom = inspect
        game = Game("g502", "https://example.test/baccarat/502",
                    "00:12", "502", "Ожидается", 0)
        done = set()
        await asyncio.wait_for(
            service._scan_selected_game(game, [], done, {}), 3)
        assert seen >= 4
        assert done == {"g502"}
        saved = service.store.get("g502")
        assert saved["cards_count"] == 3
        assert saved["player_hand_final"] is True
        assert saved["cards"][2]["rank"] == "A"
        await service._pending_live_settlement

    asyncio.run(run())


def test_missing_banker_rank_cannot_prematurely_finalize_player_zero(tmp_path):
    async def run():
        service = fake_service(tmp_path)
        seen = 0
        async def inspect(*_args, **kwargs):
            nonlocal seen
            seen += 1
            if seen < 4:
                return snapshot(hand("J", "10"), ("A",))
            return snapshot(hand("J", "10", "4"), ("A", "7"), pending=0)

        service._inspect_game_dom = inspect
        game = Game("g502", "https://example.test/baccarat/502",
                    "00:12", "502", "Ожидается", 0)
        done = set()
        await asyncio.wait_for(
            service._scan_selected_game(game, [], done, {}), 3)
        assert done == {"g502"}
        assert service.store.get("g502")["cards_count"] == 3
        await service._pending_live_settlement

    asyncio.run(run())
