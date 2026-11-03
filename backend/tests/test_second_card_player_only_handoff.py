"""Player-only Baccarat N-1 fast handoff and accepted LIVE outcome regression tests."""
import asyncio
from collections import deque
from decimal import Decimal
from types import SimpleNamespace

from app.browser import BrowserService, verified_second_card_settlement
from app.cycle18 import Cycle18Engine, STRATEGY_SECOND_CARD
from app.live_bets import LiveBets
from app.observation import Game, ObservationStore


def cards(*pairs):
    return [
        {"position": idx, "rank": rank, "suit": suit}
        for idx, (rank, suit) in enumerate(pairs, 1)
    ]


def finished(store, number, hand):
    game = Game(f"g{number}", f"https://example.test/baccarat/round-{number}",
                "00:00", str(number), "Завершена", number)
    store.upsert(game, hand, "CONFIRMED", "verified Player hand", "CARDS_CONFIRMED",
                 player_hand_final=True, final_snapshot_reverified=True,
                 finished_confirmed=True)
    return game


def test_direct_n_minus_one_settlement_before_full_history_refresh(tmp_path):
    path = tmp_path / "only-player.sqlite3"
    store = ObservationStore(path)
    engine = Cycle18Engine(path)
    ledger = LiveBets(path)
    engine.save_strategy(STRATEGY_SECOND_CARD, entry_losses=1)
    sid = engine.start_session()
    ledger.mode("LIVE")
    finished(store, 1, cards(("7", "spades"), ("K", "clubs")))
    ready = engine.refresh({"game_id": "g2", "round_number": "2"})
    assert ready["current"]["predicted_suit"] == "clubs"
    assert ready["current"]["result"] == "PENDING"
    assert ledger.reserve("g2", "2", "clubs", Decimal("10"),
                          expected_step=1, strategy=STRATEGY_SECOND_CARD,
                          strategy_session_id=sid)
    ledger.update("g2", "ACCEPTED")
    target = finished(store, 2, cards(("2", "hearts"), ("3", "diamonds"),
                                       ("4", "spades")))
    attempt = ledger.get_attempt(target.game_id)
    record = store.get(target.game_id)
    assert record["banker_cards"] == []
    assert record["banker_hand_final"] is False

    # No cycle.refresh() called for target N: Player's final hand is enough.
    assert verified_second_card_settlement(path, attempt, record) == "LOSE"
    assert ledger.settle(target.game_id, "LOSE", 8)
    assert ledger.snapshot()["step"] == 2
    assert verified_second_card_settlement(path, {**attempt, "status": "UNKNOWN"}, record) is None
    assert verified_second_card_settlement(path, {**attempt, "suit": "hearts"}, record) is None
    assert verified_second_card_settlement(path, {**attempt, "strategy_session_id": 99}, record) is None
    assert verified_second_card_settlement(path, attempt, {
        **record, "final_snapshot_reverified": False}) is None
    assert verified_second_card_settlement(path, attempt, {
        **record, "cards": cards(("2", "hearts"), ("3", "diamonds")),
        "player_hand_final": False, "finished_confirmed": False}) is None


def test_scan_three_player_cards_skips_banker_watcher_and_handoffs_same_page(tmp_path):
    async def run():
        class Page:
            url = "about:blank"

            async def goto(self, url, **_kwargs):
                self.url = url

        service = object.__new__(BrowserService)
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "third-card.sqlite3")
        service.cycle_engine = SimpleNamespace(get_strategy=lambda: {
            "selected": STRATEGY_SECOND_CARD, "active": STRATEGY_SECOND_CARD})
        service.events = deque(maxlen=50)
        service.state_version = 0
        service.current_observation = {"game_id": "g3"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service.observation_phase = ""
        seen_player_only = []

        async def allow():
            return True

        async def no_banker(*_args, **_kwargs):
            raise AssertionError("N-1 scan must not wait for Banker outcome or create another tab")

        async def no_live(*_args, **_kwargs):
            return None

        async def inspect(*_args, **kwargs):
            seen_player_only.append(kwargs.get("player_only"))
            return {
                "cards": cards(("2", "spades"), ("3", "diamonds"),
                               ("4", "hearts")),
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 3},
                "pending_card_slots": 0, "banker_cards": [],
                "finished": False, "status_text": "", "cause": "fixture",
            }

        service._probe_observation_auth_locked = allow
        service._inspect_game_dom = inspect
        service._live_before_cards = no_live
        service._live_after_cards = no_live
        service._handoff_outcome_page = no_banker
        service._confirm_outcome_from_snapshot = no_banker

        game = Game("g3", "https://example.test/baccarat/round-3",
                    "00:15", "3", "Ожидается", 0)
        processed = set()
        await asyncio.wait_for(service._scan_selected_game(game, [], processed, {}), 2)
        assert processed == {"g3"}
        assert service.store.get("g3")["player_hand_final"]
        assert service.store.get("g3")["banker_hand_final"] is False
        assert service.page.url == game.url
        assert seen_player_only and all(seen_player_only)
        assert any("SECOND_CARD_FAST_HANDOFF" in row["message"] for row in service.events)
        await service._pending_live_settlement

    asyncio.run(run())


def test_two_player_cards_with_known_stand_are_final_without_banker(tmp_path):
    async def run():
        class Page:
            url = "about:blank"
            async def goto(self, url, **_kwargs):
                self.url = url

        service = object.__new__(BrowserService)
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "two-card.sqlite3")
        service.cycle_engine = SimpleNamespace(get_strategy=lambda: {
            "selected": STRATEGY_SECOND_CARD, "active": STRATEGY_SECOND_CARD})
        service.events = deque(maxlen=50)
        service.current_observation = {"game_id": "g4"}
        service.observation_status = "WAITING_FOR_GAME"
        service.observation_diagnostic = ""
        service.observation_phase = ""

        async def allow():
            return True
        async def no_live(*_args, **_kwargs):
            return None
        async def no_banker(*_args, **_kwargs):
            raise AssertionError("Banker processing must not block N-1")

        async def inspect(*_args, **kwargs):
            assert kwargs["player_only"] is True
            return {
                "cards": cards(("7", "spades"), ("K", "diamonds")),
                "counts": {"player_fields": 1, "player_cards_containers": 1,
                           "player_card_boxes": 3},
                "pending_card_slots": 1,
                "banker_initial_ranks": [],
                "finished": False, "status_text": "", "cause": "fixture",
            }

        service._probe_observation_auth_locked = allow
        service._inspect_game_dom = inspect
        service._live_before_cards = no_live
        service._live_after_cards = no_live
        service._handoff_outcome_page = no_banker
        service._confirm_outcome_from_snapshot = no_banker
        game = Game("g4", "https://example.test/baccarat/round-4",
                    "00:15", "4", "Ожидается", 0)
        done = set()
        await asyncio.wait_for(service._scan_selected_game(game, [], done, {}), 2)
        assert done == {"g4"}
        assert service.store.get("g4")["cards_count"] == 2
        await service._pending_live_settlement
    asyncio.run(run())
