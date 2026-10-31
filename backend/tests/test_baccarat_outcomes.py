"""Card-derived Baccarat outcomes and the independent Banker observer."""
import asyncio
import sqlite3
from collections import deque

import pytest

from app.browser import BrowserService
from app.cycle18 import Cycle18Engine, STRATEGY_53
from app.draw_rules import resolved_baccarat_outcome
from app.observation import Game, ObservationStore
from playwright.async_api import async_playwright


def hand(*ranks):
    suits = ("hearts", "clubs", "diamonds")
    return [{"position": i + 1, "rank": rank, "suit": suits[i]}
            for i, rank in enumerate(ranks)]



def test_chromium_banker_dom_three_cards_ignores_hidden_suit_faces():
    """Actual Playwright DOM pass: three Banker cards and an invisible old suit."""
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content("""
                    <style>
                    .baccarat-player,.baccarat-player__card-box,
                    .baccarat-player__cards {display:block}
                    .hidden {display:none}
                    </style>
                    <div class="baccarat-player">
                      <span class="baccarat-player__name">Игрок</span>
                      <ul class="baccarat-player__cards">
                        <li class="baccarat-player__card-box suit-hearts"><span class="baccarat-card__rank">A</span></li>
                        <li class="baccarat-player__card-box suit-clubs"><span class="baccarat-card__rank">A</span></li>
                        <li class="baccarat-player__card-box suit-diamonds"><span class="baccarat-card__rank">5</span></li>
                      </ul>
                    </div>
                    <div class="baccarat-player">
                      <span class="baccarat-player__name">Банкир</span>
                      <ul class="baccarat-player__cards">
                        <li class="baccarat-player__card-box">
                          <span class="hidden">♠</span>
                          <span class="baccarat-card__rank">A</span>
                          <span class="baccarat-card__suit">♥</span>
                        </li>
                        <li class="baccarat-player__card-box suit-clubs"><span class="baccarat-card__rank">A</span></li>
                        <li class="baccarat-player__card-box suit-diamonds"><span class="baccarat-card__rank">5</span></li>
                      </ul>
                    </div>
                    <span class="baccarat-panel__caption">Ничья</span>
                """)
                snapshot = await service._inspect_game_dom("Игрок", "Банкир")
                expected_player = [
                    {"position": 1, "rank": "A", "suit": "hearts"},
                    {"position": 2, "rank": "A", "suit": "clubs"},
                    {"position": 3, "rank": "5", "suit": "diamonds"},
                ]
                assert snapshot["cards"] == expected_player
                assert snapshot["banker_cards"] == expected_player
                assert snapshot["banker_card_boxes"] == 3
                assert snapshot["banker_pending_card_slots"] == 0
                assert resolved_baccarat_outcome(snapshot["cards"], snapshot["banker_cards"])[0] == "DRAW"
            finally:
                await browser.close()
    asyncio.run(run())



def test_chromium_reads_banker_only_terminal_dom_after_player_hidden():
    """The 53% outcome observer must still parse Banker when Player is gone."""
    async def run():
        service = object.__new__(BrowserService)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            service.page = page
            try:
                await page.set_content("""
                    <style>.baccarat-player,.baccarat-player__cards,
                    .baccarat-player__card-box {display:block}</style>
                    <div class="baccarat-player">
                      <span class="baccarat-player__name">Банкир</span>
                      <ul class="baccarat-player__cards">
                        <li class="baccarat-player__card-box suit-hearts"><span class="baccarat-card__rank">A</span></li>
                        <li class="baccarat-player__card-box suit-clubs"><span class="baccarat-card__rank">A</span></li>
                        <li class="baccarat-player__card-box suit-diamonds"><span class="baccarat-card__rank">5</span></li>
                      </ul>
                    </div>
                    <span class="baccarat-panel__caption">Ничья</span>
                """)
                result = await service._inspect_game_dom("Игрок", "Банкир")
                assert result["cards"] == []
                assert result["banker_cards"] == hand("A", "A", "5")
                assert result["banker_card_boxes"] == 3
                assert result["finished"] is True
            finally:
                await browser.close()

    asyncio.run(run())


@pytest.mark.parametrize("player,banker,outcome,scores", [
    (hand("9", "9"), hand("K", "6"), "PLAYER", (8, 6)),
    (hand("3", "4"), hand("2", "3", "A"), "PLAYER", (7, 6)),
    (hand("2", "3", "4"), hand("2", "5"), "PLAYER", (9, 7)),
    (hand("A", "A", "5"), hand("A", "A", "5"), "DRAW", (7, 7)),
    (hand("4", "K"), hand("8", "K"), "BANKER", (4, 8)),
])

def test_complete_card_sets_resolve_all_outcomes(player, banker, outcome, scores):
    assert resolved_baccarat_outcome(player, banker) == (outcome, *scores)


def test_missing_required_card_or_suit_is_unknown():
    player = hand("2", "3", "4")
    banker = hand("A", "A", "3")
    assert resolved_baccarat_outcome(player[:2], banker[:2]) is None
    assert resolved_baccarat_outcome(player, banker[:2]) is None
    assert resolved_baccarat_outcome(player, [{**banker[0], "suit": ""}, *banker[1:]]) is None
    assert resolved_baccarat_outcome(player, [banker[1], banker[0], banker[2]]) is None
    assert resolved_baccarat_outcome([], []) is None  # pre-deal 0:0


def test_outcome_is_stored_on_exact_verified_game_and_survives_restart(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = ObservationStore(path)
    game = Game("real-id", "https://example.test/real-id", "", "1000", "Идёт", 0)
    player, banker = hand("A", "A", "5"), hand("A", "A", "5")
    store.upsert(game, player, "CONFIRMED", "final Player", "CARDS_CONFIRMED",
                 player_hand_final=True, final_snapshot_reverified=True)
    assert store.confirm_baccarat_outcome("real-id", "1001", player, banker) is None
    assert store.confirm_baccarat_outcome("real-id", "1000", hand("2", "3", "4"), banker) is None
    assert store.get("real-id")["baccarat_outcome"] == "UNKNOWN"
    assert store.confirm_baccarat_outcome("real-id", "1000", player, banker)["baccarat_outcome"] == "DRAW"
    assert store.confirm_baccarat_outcome("real-id", "1000", player, banker)["baccarat_outcome"] == "DRAW"
    saved = ObservationStore(path).get("real-id")
    assert saved["banker_cards"] == banker
    assert saved["banker_hand_final"] is True
    assert saved["baccarat_outcome"] == "DRAW"
    assert saved["player_score"] == "7" and saved["banker_score"] == "7"
    assert saved["outcome_confirmed_at"]
    assert len(ObservationStore(path).history()) == 1
    corrected_player = hand("A", "A", "6")
    store.upsert(game, corrected_player, "CONFIRMED", "corrected Player", "CARDS_CONFIRMED",
                 player_hand_final=True, final_snapshot_reverified=True)
    corrected = store.get("real-id")
    assert corrected["baccarat_outcome"] == "UNKNOWN"
    assert corrected["banker_hand_final"] is False
    assert corrected["banker_cards"] == []


def test_reconfirmation_repairs_incomplete_outcome_metadata(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = ObservationStore(path)
    game = Game("hand-42", "https://example.test/hand-42", "", "42", "Завершена", 0)
    player, banker = hand("9", "9"), hand("K", "6")
    store.upsert(game, player, "CONFIRMED", "final Player", "CARDS_CONFIRMED",
                 player_hand_final=True, final_snapshot_reverified=True)
    assert store.confirm_baccarat_outcome(game.game_id, game.round_number, player, banker)
    with sqlite3.connect(path) as db:
        db.execute("""UPDATE observations SET banker_hand_final=0, banker_score='',
            outcome_confirmed_at=NULL WHERE game_id=?""", (game.game_id,))
    assert store.confirm_baccarat_outcome(game.game_id, game.round_number, player, banker)
    saved = ObservationStore(path).get(game.game_id)
    assert saved["banker_hand_final"] is True
    assert saved["banker_score"] == "6"
    assert saved["outcome_confirmed_at"]


def test_strategy_53_waits_for_persisted_banker_outcome_then_survives_restart(tmp_path):
    path = tmp_path / "cycle.sqlite3"
    store = ObservationStore(path)
    engine = Cycle18Engine(path)
    engine.save_strategy(STRATEGY_53)
    engine.start_session()
    banker = hand("9", "K")  # natural 9: two Player cards are final
    for number in range(1, 41):
        game = Game(f"game-{number}", f"https://example.test/game-{number}",
                    "", str(number), "Завершена", number)
        player = hand("A", "4") if number == 40 else hand("2", "4")
        store.upsert(game, player, "CONFIRMED", "final Player", "CARDS_CONFIRMED",
                     player_hand_final=True, final_snapshot_reverified=True)
        if 37 <= number <= 39:
            assert store.confirm_baccarat_outcome(game.game_id, str(number), player, banker)
    current = {"game_id": "game-41", "round_number": "41"}
    waiting = engine.refresh(current)["live_preview"][0]
    assert waiting["available"] is False
    assert waiting["provisional_suit"] == "clubs"
    assert waiting["unverified_outcome_round_numbers"] == [40]
    assert engine.refresh(current)["recent"] == []
    assert store.confirm_baccarat_outcome("game-40", "40", hand("A", "4"), banker)
    restarted = Cycle18Engine(path)
    ready = restarted.refresh(current)["live_preview"][0]
    assert ready["available"] is True
    assert ready["predicted_suit"] == "clubs"
    assert ObservationStore(path).get("game-40")["baccarat_outcome"] == "BANKER"


def test_banker_outcome_requires_exact_original_player_cards(tmp_path):
    async def run():
        service = object.__new__(BrowserService)
        service.store = ObservationStore(tmp_path / "player-identity.sqlite3")
        service.events = deque(maxlen=10)
        service.state_version = 0
        service.current_observation = {}
        service._outcome_conflicts = set()
        game = Game("hand-50", "https://example.test/hand-50", "", "50", "Идёт", 0)
        player = hand("2", "3", "4")
        banker = hand("A", "A", "3")
        service.store.upsert(game, player, "CONFIRMED", "final Player", "CARDS_CONFIRMED",
                             player_hand_final=True, final_snapshot_reverified=True)
        for wrong in ([], hand("2", "3"), hand("2", "3", "5")):
            result = await service._confirm_outcome_from_snapshot(
                game, player, {"url": game.url, "cards": wrong, "banker_cards": banker})
            assert result is None
            assert service.store.get(game.game_id)["baccarat_outcome"] == "UNKNOWN"
        valid = await service._confirm_outcome_from_snapshot(
            game, player, {"url": game.url, "cards": player, "banker_cards": banker})
        assert valid["baccarat_outcome"] == "PLAYER"

    asyncio.run(run())



def test_banker_only_terminal_snapshot_accepted_after_original_player_seen(tmp_path):
    """DOM hides Player after it was confirmed; late Banker result is still usable."""
    async def run():
        class Page:
            url = "https://example.test/hand-600"
            closed = False
            def is_closed(self):
                return self.closed
            async def close(self):
                self.closed = True
        game = Game("hand-600", Page.url, "", "600", "Идёт", 0)
        player = hand("2", "3", "4")
        banker = hand("A", "A", "3")
        service = object.__new__(BrowserService)
        service.store = ObservationStore(tmp_path / "hidden-player.sqlite3")
        service.store.upsert(game, player, "CONFIRMED", "Player final", "CARDS_CONFIRMED",
                             player_hand_final=True, final_snapshot_reverified=True)
        service.events = deque(maxlen=20)
        service.state_version = 0
        service.current_observation = {"game_id": game.game_id}
        service._outcome_conflicts = set()
        service._outcome_watchers = {}
        page = Page()
        seen = 0

        async def inspect(_player, _banker, *, page):
            nonlocal seen
            seen += 1
            if seen == 1:
                return {"url": game.url, "cards": player, "banker_cards": [], "finished": False}
            return {"url": game.url, "cards": [], "banker_cards": banker,
                    "finished": True, "finish_flag_text": "Победа"}

        service._inspect_game_dom = inspect
        await asyncio.wait_for(service._watch_outcome(game, player, page), 3)
        saved = service.store.get(game.game_id)
        assert saved["baccarat_outcome"] == "PLAYER"
        assert saved["banker_cards"] == banker
        assert saved["banker_hand_final"] is True
        assert seen >= 2 and page.closed

    asyncio.run(run())


def test_banker_only_snapshot_without_prior_player_identity_is_not_trusted(tmp_path):
    async def run():
        service = object.__new__(BrowserService)
        service.store = ObservationStore(tmp_path / "unverified-page.sqlite3")
        service.events = deque(maxlen=10)
        service.state_version = 0
        service.current_observation = None
        game = Game("hand-601", "https://example.test/hand-601", "", "601", "Идёт", 0)
        player = hand("2", "3", "4")
        banker = hand("A", "A", "3")
        service.store.upsert(game, player, "CONFIRMED", "Player final", "CARDS_CONFIRMED",
                             player_hand_final=True, final_snapshot_reverified=True)
        result = await service._confirm_outcome_from_snapshot(
            game, player, {"url": game.url, "cards": [], "banker_cards": banker,
                           "finished": True}, allow_missing_player=False)
        assert result is None
        assert service.store.get(game.game_id)["baccarat_outcome"] == "UNKNOWN"

    asyncio.run(run())



def test_banker_dom_available_immediately_after_player_disappears_at_handoff(tmp_path):
    """There may be no Player node left on the first outcome-watcher poll."""
    async def run():
        class Page:
            def __init__(self, url):
                self.url = url
                self.closed = False
            def is_closed(self):
                return self.closed
            async def close(self):
                self.closed = True
            async def bring_to_front(self):
                pass

        class Context:
            async def new_page(self):
                return Page("about:blank")

        game = Game("hand-650", "https://example.test/hand-650", "", "650", "Идёт", 0)
        player = hand("2", "3", "4")
        banker = hand("A", "A", "3")
        service = object.__new__(BrowserService)
        service.store = ObservationStore(tmp_path / "early-disappear.sqlite3")
        service.store.upsert(game, player, "CONFIRMED", "Final Player", "CARDS_CONFIRMED",
                             player_hand_final=True, final_snapshot_reverified=True)
        old = Page(game.url)
        service.page = old
        service.context = Context()
        service.page_lock = asyncio.Lock()
        service.events = deque(maxlen=20)
        service.state_version = 0
        service.current_observation = {"game_id": game.game_id}
        service._outcome_watchers = {}
        service._opening_primary_page = False

        async def inspect(_player, _banker, *, page):
            assert page is old
            return {"url": game.url, "cards": [], "banker_cards": banker,
                    "finished": True, "finish_flag_text": "Победа"}

        service._inspect_game_dom = inspect
        await service._handoff_outcome_page(game, player)
        task = service._outcome_watchers[game.game_id][1]
        await asyncio.wait_for(task, timeout=2)
        saved = service.store.get(game.game_id)
        assert saved["baccarat_outcome"] == "PLAYER"
        assert saved["banker_cards"] == banker

    asyncio.run(run())


def test_old_page_captures_late_banker_card_without_holding_next_page(tmp_path):
    async def run():
        class Page:
            def __init__(self, url):
                self.url = url
                self.closed = False
            def is_closed(self):
                return self.closed
            async def close(self):
                self.closed = True
            async def bring_to_front(self):
                pass

        class Context:
            async def new_page(self):
                return Page("about:blank")

        game = Game("hand-1000", "https://example.test/hand-1000", "", "1000", "Идёт", 0)
        player = hand("2", "3", "4")
        service = object.__new__(BrowserService)
        service.store = ObservationStore(tmp_path / "watch.sqlite3")
        service.store.upsert(game, player, "CONFIRMED", "final Player", "CARDS_CONFIRMED",
                             player_hand_final=True, final_snapshot_reverified=True)
        old = Page(game.url)
        service.page = old
        service.context = Context()
        service.page_lock = asyncio.Lock()
        service.events = deque(maxlen=20)
        service.state_version = 0
        service.current_observation = {"game_id": game.game_id}
        service._outcome_watchers = {}
        service._opening_primary_page = False
        reads = 0

        async def inspect(_player, _banker, *, page):
            nonlocal reads
            assert page is old
            reads += 1
            return {"url": game.url, "cards": player,
                    "banker_cards": hand("A", "A") if reads == 1 else hand("A", "A", "3")}

        service._inspect_game_dom = inspect
        await service._handoff_outcome_page(game, player)
        assert service.page is not old
        assert service.page.url == "about:blank"
        watcher = service._outcome_watchers[game.game_id][1]
        await asyncio.wait_for(watcher, 2)
        assert reads >= 2 and old.closed
        assert service.store.get(game.game_id)["baccarat_outcome"] == "PLAYER"
        assert service.store.get(game.game_id)["banker_cards"] == hand("A", "A", "3")

    asyncio.run(run())
