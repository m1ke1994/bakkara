"""Accepted N-1 wager recovery is safe, idempotent and independent of scanner restarts."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

from app.browser import BrowserService
from app.cycle18 import Cycle18Engine, STRATEGY_SECOND_CARD
from app.live_bets import LiveBets
from app.observation import Game, ObservationStore


def cards(*suits):
    return [
        {"position": pos, "rank": "7" if pos == 1 else "K", "suit": suit}
        for pos, suit in enumerate(suits, 1)
    ]


def finished(store, n, hand):
    game = Game(f"g{n}", f"https://example.test/baccarat/{n}", "00:00",
                str(n), "Завершена", n)
    store.upsert(game, hand, "CONFIRMED", "reverified Player",
                 "CARDS_CONFIRMED", player_hand_final=True,
                 final_snapshot_reverified=True, finished_confirmed=True)
    return game


def state(tmp_path):
    path = tmp_path / "live.sqlite3"
    store = ObservationStore(path)
    engine = Cycle18Engine(path)
    ledger = LiveBets(path)
    engine.save_strategy(STRATEGY_SECOND_CARD, entry_losses=1)
    sid = engine.start_session()
    ledger.mode("LIVE")

    finished(store, 521, cards("clubs", "diamonds"))
    forecast = engine.refresh({"game_id": "g522", "round_number": "522"})
    assert forecast["current"]["predicted_suit"] == "diamonds"
    assert ledger.reserve("g522", "522", "diamonds", Decimal("21"),
                          strategy=STRATEGY_SECOND_CARD,
                          strategy_session_id=sid, expected_step=2)
    ledger.update("g522", "ACCEPTED")
    return store, engine, ledger


def make_observer(store, engine, ledger):
    service = object.__new__(BrowserService)
    service.store = store
    service.live_bets = ledger
    service.cycle_engine = engine
    service.live_plan_store = SimpleNamespace(load=lambda: SimpleNamespace(
        row=[Decimal("10"), Decimal("21"), Decimal("45"), Decimal("90")]))
    service.events = []
    service.event = lambda level, text: service.events.append((level, text))
    return service


def test_old_accepted_after_restart_reconciles_and_unblocks_new_round(tmp_path):
    store, engine, ledger = state(tmp_path)
    # The original session can have ended; its saved forecast is still the
    # independent proof. No fabricated re-forecast in a new session.
    engine.stop_session()
    engine.start_session()
    finished(store, 522, cards("spades", "clubs"))  # diamonds forecast LOSE
    assert ledger.snapshot()["blocked"]
    assert ledger.pending_blocker()["round_number"] == "522"
    service = make_observer(store, engine, ledger)
    asyncio.run(service._reconcile_second_card_accepted())
    result = ledger.get_attempt("g522")
    assert result["result"] == "LOSE"
    assert result["status"] == "ACCEPTED"
    assert ledger.snapshot()["step"] == 3
    assert not ledger.snapshot()["blocked"]
    assert ledger.pending_accepted() == []
    assert any("LIVE_BACKLOG_SETTLED" in s for _, s in service.events)

    # Idempotent after a second check; original accepted bet is never replayed.
    asyncio.run(service._reconcile_second_card_accepted())
    assert ledger.snapshot()["step"] == 3
    assert sum("LIVE_BACKLOG_SETTLED" in s for _, s in service.events) == 1


def test_unverified_accepted_stays_blocked_without_false_lose(tmp_path):
    store, engine, ledger = state(tmp_path)
    service = make_observer(store, engine, ledger)
    asyncio.run(service._reconcile_second_card_accepted())
    assert ledger.get_attempt("g522")["result"] is None
    assert ledger.snapshot()["blocked"]
    assert len(ledger.pending_accepted()) == 1
    assert any("LIVE_BACKLOG_UNVERIFIED" in s for _, s in service.events)
    assert not ledger.reserve("g523", "523", "clubs", Decimal("45"))
    asyncio.run(service._reconcile_second_card_accepted())
    assert sum("LIVE_BACKLOG_UNVERIFIED" in s for _, s in service.events) == 1

    finished(store, 522, cards("spades", "clubs"))
    observation = store.get("g522")
    assert observation["player_hand_final"]
    # An altered / incomplete card snapshot does not bypass the gate.
    with __import__("sqlite3").connect(store.path) as db:
        db.execute("UPDATE observations SET final_snapshot_reverified=0 WHERE game_id='g522'")
    asyncio.run(service._reconcile_second_card_accepted())
    assert ledger.get_attempt("g522")["result"] is None
    assert ledger.snapshot()["blocked"]


def test_started_current_round_triggers_backlog_reconciliation_before_skip(tmp_path):
    store, engine, ledger = state(tmp_path)
    finished(store, 522, cards("clubs", "diamonds"))  # diamonds forecast WIN
    service = make_observer(store, engine, ledger)
    game = Game("g524", "https://example.test/baccarat/524", "00:01",
                "524", "Идёт", 0)
    asyncio.run(service._live_before_cards(game))
    assert ledger.get_attempt("g522")["result"] == "WIN"
    assert ledger.snapshot()["step"] == 1
    assert not ledger.snapshot()["blocked"]
    assert any("LIVE_BACKLOG_SETTLED" in s for _, s in service.events)
