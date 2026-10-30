"""Card-derived Baccarat outcomes and the independent Banker observer."""
import asyncio
from collections import deque

import pytest

from app.browser import BrowserService
from app.cycle18 import Cycle18Engine, STRATEGY_53
from app.draw_rules import resolved_baccarat_outcome
from app.observation import Game, ObservationStore


def hand(*ranks):
    suits = ("hearts", "clubs", "diamonds")
    return [{"position": i + 1, "rank": rank, "suit": suits[i]}
            for i, rank in enumerate(ranks)]


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
