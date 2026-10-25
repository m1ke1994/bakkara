import asyncio
import json
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone

from app.browser import BrowserService
from app.observation import Game, ObservationStore
from app.cycle18 import Cycle18Engine, STRATEGY_VOTE
from app.draw_rules import player_card_count

TWO = [{"position": 1, "rank": "6", "suit": "diamonds"},
       {"position": 2, "rank": "7", "suit": "spades"}]
THREE = TWO + [{"position": 3, "rank": "K", "suit": "clubs"}]


def game(n):
    return Game(str(n), f"https://example.test/{n}", "", str(n), "Идёт", 0)


def save(store, n, cards):
    store.upsert(game(n), cards, "CONFIRMED", "Проверено", "CARDS_CONFIRMED",
                 finished_confirmed=True, player_hand_final=True, final_snapshot_reverified=True)


def test_draw_rule_includes_banker_natural_exception():
    assert player_card_count(TWO, ["4", "10"]) == 3
    assert player_card_count(TWO, ["9", "K"]) == 2
    assert player_card_count(TWO) is None
    assert player_card_count([{"rank": "4"}, {"rank": "3"}]) == 2


def test_new_third_card_revokes_old_verification_and_is_recoverable(tmp_path):
    store = ObservationStore(tmp_path / "history.sqlite3")
    save(store, 1164, TWO)
    store.upsert(game(1164), THREE, "PARTIAL", "Третья карта появилась", "CARDS_FOUND")
    record = store.get("1164")
    assert record["cards"] == THREE
    assert record["calculation_status"] == "incomplete"
    assert record["calculated_suit"] is None
    assert "1164" not in store.finished_game_ids()
    save(store, 1164, THREE)
    assert store.get("1164")["calculated_suit"] == "diamonds"


def test_1164_changes_audited_1204_without_changing_original_or_live(tmp_path):
    path = tmp_path / "history.sqlite3"
    store, engine = ObservationStore(path), Cycle18Engine(path)
    engine.save_strategy(STRATEGY_VOTE)
    sid = engine.start_session()
    # 40 confirmed hands preserve the production warm-up requirement.
    for n in range(1161, 1204):
        if n in (1169, 1170):  # missing physical numbers must not shift sources
            continue
        cards = [{"position": 1, "rank": "6", "suit": "diamonds"},
                 {"position": 2, "rank": "K", "suit": "clubs"}]  # -> clubs
        if n == 1180:
            cards = TWO
        if n == 1173:
            cards = [{"position": 1, "rank": "6", "suit": "clubs"},
                     {"position": 2, "rank": "K", "suit": "clubs"}]  # -> diamonds
        if n == 1164:
            cards = TWO
        save(store, n, cards)
    engine.refresh()
    store.register_discovered_games([game(1204)])
    original = engine.refresh()
    forecast = next(x for x in original["recent"] if x["target_round_number"] == 1204)
    assert forecast["predicted_suit"] == "clubs"
    save(store, 1204, [{"position": 1, "rank": "4", "suit": "hearts"},
                       {"position": 2, "rank": "3", "suit": "diamonds"}])
    engine.refresh()
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM cycle18_forecasts WHERE target_game_id='1204'").fetchone()
        db.execute("CREATE TABLE live_attempts(game_id TEXT, result TEXT)")
        db.execute("INSERT INTO live_attempts VALUES('1204','LOSE')")
    save(store, 1164, THREE)
    state = engine.refresh()
    audit = next(x for x in state["historical_audit"] if x["target_round_number"] == 1204)
    assert audit["predicted_suit"] == "diamonds"
    assert audit["result"] == "WIN"
    assert audit["original_result"] == "LOSE"
    assert audit["changed"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM cycle18_forecasts WHERE target_game_id='1204'").fetchone() == before
        assert db.execute("SELECT result FROM live_attempts").fetchone()[0] == "LOSE"
        assert json.loads(db.execute(
            "SELECT payload_json FROM cycle18_history_audit WHERE session_id=? AND target_game_id='1204'",
            (sid,)).fetchone()[0])["result"] == "WIN"


def test_finished_partial_game_can_be_recovered_after_restart(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = ObservationStore(path)
    store.upsert(game(1164), TWO, "PARTIAL", "Набор неполный", "CARDS_UNCONFIRMED",
                 finished_confirmed=True, final_snapshot_reverified=False)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE observations SET discovered_at=?",
                   ((datetime.now(timezone.utc)-timedelta(minutes=60)).isoformat(),))
    store = ObservationStore(path)
    assert [g.game_id for g in store.recoverable_games()] == ["1164"]
    store.mark_recovery_attempt("1164")
    assert store.recoverable_games() == []


def test_legacy_low_two_cards_with_non_natural_banker_are_invalidated(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = ObservationStore(path)
    g = game(1164)
    g.banker_score = "4"
    save(store, 1164, TWO)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE observations SET banker_score='4'")
        db.execute("DELETE FROM observation_migrations WHERE name='draw_rule_v1'")
    store = ObservationStore(path)
    assert store.get("1164")["calculation_status"] == "incomplete"
    assert "1164" not in store.finished_game_ids()


def test_late_third_card_after_old_timer_deadline_is_captured(tmp_path):
    async def run():
        service = object.__new__(BrowserService)
        class Page:
            url = "about:blank"
            async def goto(self, url, **kwargs):
                self.url = url
        service.page = Page()
        service.page_lock = asyncio.Lock()
        service.store = ObservationStore(tmp_path / "late.sqlite3")
        service.events = deque(maxlen=100)
        service.current_observation = None
        async def auth():
            return True
        service._probe_observation_auth_locked = auth
        started = asyncio.get_running_loop().time()
        async def inspect(*args):
            elapsed = asyncio.get_running_loop().time() - started
            cards = THREE if elapsed >= 2.2 else TWO
            return {"cards": cards, "finished": False, "pending_card_slots": 0,
                    "round_timer_text": "00:01" if elapsed < .1 else "00:00",
                    "banker_initial_ranks": ["4", "10"],
                    "counts": {"player_fields": 1, "player_cards_containers": 1,
                               "player_card_boxes": len(cards)}}
        service._inspect_game_dom = inspect
        processed = set()
        await asyncio.wait_for(service._scan_selected_game(game(1164), [], processed, {}), 5)
        assert service.store.get("1164")["cards"] == THREE
        assert service.store.get("1164")["calculated_suit"] == "diamonds"
        assert processed == {"1164"}
    asyncio.run(run())
