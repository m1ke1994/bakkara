import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

import app.main as main_module
from app.stake_plan import PlanConflict, StakePlan, StakePlanStore


def test_manual_amounts_survive_store_restart_and_do_not_touch_history(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE observations (game_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO observations VALUES ('keep-me')")
    store = StakePlanStore(path)
    assert store.load().revision == 0
    saved = store.save(StakePlan(initial="15", multiplier="2.5", steps=3, row=["15", "42.29", "92.01"]))
    restored = StakePlanStore(path).load()
    assert restored == saved
    assert restored.row == [Decimal("15"), Decimal("42.29"), Decimal("92.01")]
    assert restored.revision == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT game_id FROM observations").fetchall() == [("keep-me",)]


@pytest.mark.parametrize("patch", [
    {"initial": "0"}, {"initial": "15.001"}, {"initial": True},
    {"initial": "1000000001"}, {"multiplier": "1"}, {"multiplier": "10.0001"},
    {"multiplier": "2.12345"}, {"odds": "NaN"}, {"odds": "Infinity"},
    {"odds": "1"}, {"odds": "1.23456"}, {"steps": 0}, {"steps": 21},
    {"steps": True}, {"steps": 8.5}, {"steps": "8"},
    {"row": []}, {"row": ["15"]}, {"row": [True] * 8},
    {"row": ["0.001"] * 8}, {"row": ["NaN"] * 8},
    {"row": ["1000000001"] * 8}, {"row": ["-1"] * 8},
    {"revision": -1}, {"execution_mode": "LIVE"},
])
def test_invalid_settings_rejected(patch):
    with pytest.raises(ValidationError):
        StakePlan.model_validate({**StakePlan().model_dump(mode="json"), **patch})


def test_simultaneous_saves_cannot_overwrite_newer_revision(tmp_path):
    store = StakePlanStore(tmp_path / "concurrent.sqlite3")
    def write():
        try:
            return store.save(StakePlan()).revision
        except PlanConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: write(), range(2)))
    assert sorted(outcomes, key=str) == [1, "conflict"]
    assert store.load().revision == 1


def test_api_roundtrip_validation_and_conflict(tmp_path, monkeypatch):
    path = tmp_path / "api.sqlite3"
    monkeypatch.setattr(main_module, "stake_plan_store", StakePlanStore(path))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app), base_url="http://test") as client:
            response = await client.get("/api/stake-plan")
            assert response.status_code == 200
            plan = response.json()
            plan["row"][1] = "42.29"
            response = await client.put("/api/stake-plan", json=plan)
            assert response.status_code == 200
            assert response.json()["row"][1] == "42.29"
            assert response.json()["revision"] == 1
            assert (await client.put("/api/stake-plan", json=plan)).status_code == 409
            monkeypatch.setattr(main_module, "stake_plan_store", StakePlanStore(path))
            loaded = (await client.get("/api/stake-plan")).json()
            assert loaded == response.json()
            loaded["row"][1] = "0.001"
            assert (await client.put("/api/stake-plan", json=loaded)).status_code == 422
            assert (await client.get("/api/stake-plan")).json() == response.json()
    asyncio.run(run())
