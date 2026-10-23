"""Persistent LIVE attempt ledger, separate from virtual forecast statistics."""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path


class LiveBets:
    def __init__(self, path: Path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS live_control (
                id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL, step INTEGER NOT NULL)""")
            db.execute("INSERT OR IGNORE INTO live_control VALUES(1,'DEMO',1)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(live_control)")}
            if "coupon_test_filter" not in columns:
                db.execute("ALTER TABLE live_control ADD COLUMN coupon_test_filter INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE TABLE IF NOT EXISTS live_attempts (
                game_id TEXT PRIMARY KEY, round_number TEXT NOT NULL,
                suit TEXT NOT NULL, amount TEXT NOT NULL, step INTEGER NOT NULL,
                status TEXT NOT NULL, result TEXT, detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            control = dict(db.execute("SELECT mode,step,coupon_test_filter FROM live_control WHERE id=1").fetchone())
            attempt = db.execute("SELECT * FROM live_attempts ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
            control["attempt"] = dict(attempt) if attempt else None
            control["blocked"] = bool(db.execute(
                "SELECT 1 FROM live_attempts WHERE status IN ('PREPARING','SUBMITTING','UNKNOWN','ACCEPTED') "
                "AND result IS NULL LIMIT 1").fetchone())
            return control

    def mode(self, value: str):
        if value not in ("DEMO", "LIVE"):
            raise ValueError("Неизвестный режим")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE live_control SET mode=? WHERE id=1", (value,))

    def set_coupon_test_filter(self, enabled: bool):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE live_control SET coupon_test_filter=? WHERE id=1",
                       (int(enabled),))

    def reserve(self, game_id: str, round_number: str, suit: str, amount: Decimal) -> bool:
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM live_attempts WHERE game_id=?", (game_id,)).fetchone():
                return False
            if db.execute("SELECT 1 FROM live_attempts WHERE status IN ('PREPARING','SUBMITTING','UNKNOWN','ACCEPTED') "
                          "AND result IS NULL LIMIT 1").fetchone():
                return False
            step = db.execute("SELECT step FROM live_control WHERE id=1").fetchone()[0]
            db.execute("INSERT INTO live_attempts(game_id,round_number,suit,amount,step,status) "
                       "VALUES(?,?,?,?,?,'PREPARING')", (game_id,round_number,suit,str(amount),step))
            return True

    def update(self, game_id: str, status: str, detail: str = ""):
        if status not in ("PREPARING", "SUBMITTING", "ACCEPTED", "REJECTED", "UNKNOWN"):
            raise ValueError(status)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE live_attempts SET status=?,detail=? WHERE game_id=?", (status,detail,game_id))

    def settle(self, game_id: str, result: str, row_length: int) -> bool:
        if result not in ("WIN", "LOSE"):
            return False
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            attempt = db.execute("SELECT status,result,step FROM live_attempts WHERE game_id=?", (game_id,)).fetchone()
            if not attempt or attempt[0] != "ACCEPTED" or attempt[1] is not None:
                return False
            db.execute("UPDATE live_attempts SET result=? WHERE game_id=?", (result,game_id))
            next_step = 1 if result == "WIN" else min(attempt[2]+1,row_length+1)
            db.execute("UPDATE live_control SET step=? WHERE id=1", (next_step,))
            return True
