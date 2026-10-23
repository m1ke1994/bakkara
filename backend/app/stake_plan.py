"""Persist the standalone budgeting worksheet; never execute bets."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StakePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    initial: Decimal = Field(default=Decimal("15"), ge=1, le=1_000_000_000, decimal_places=2)
    multiplier: Decimal = Field(default=Decimal("2"), gt=1, le=10, decimal_places=4)
    steps: int = Field(default=8, ge=1, le=20, strict=True)
    row: list[Decimal] = Field(default_factory=lambda: [Decimal(v) for v in (15, 30, 60, 120, 240, 480, 960, 1920)], min_length=1, max_length=20)
    odds: Decimal = Field(default=Decimal("1.9"), gt=1, le=10_000, decimal_places=4)
    revision: int = Field(default=0, ge=0, strict=True)

    @field_validator("initial", "multiplier", "odds", "row", mode="before")
    @classmethod
    def reject_booleans(cls, value):
        values = value if isinstance(value, list) else [value]
        if any(isinstance(item, bool) for item in values):
            raise ValueError("Ожидается число, а не логическое значение")
        return value

    @model_validator(mode="after")
    def validate_row(self):
        if len(self.row) != self.steps:
            raise ValueError("Количество сумм должно совпадать с количеством шагов")
        for amount in self.row:
            if not amount.is_finite() or not Decimal("0.01") <= amount <= Decimal("1000000000"):
                raise ValueError("Сумма шага должна быть от 0,01 до 1 000 000 000 ₽")
            if amount != amount.quantize(Decimal("0.01")):
                raise ValueError("Суммы шагов должны иметь точность до копейки")
        return self


class PlanConflict(Exception):
    pass


class StakePlanStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS calculator_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL
            )""")

    def load(self) -> StakePlan:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM calculator_settings WHERE id = 1").fetchone()
        return StakePlan.model_validate_json(row[0]) if row else StakePlan()

    def save(self, plan: StakePlan) -> StakePlan:
        # Serialize decimal strings, preserving manual amounts without floats
        # or a generated-row recalculation. A single transaction prevents a
        # second tab from silently overwriting a newer saved worksheet.
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM calculator_settings WHERE id = 1").fetchone()
            revision = json.loads(row[0])["revision"] if row else 0
            if plan.revision != revision:
                raise PlanConflict("Настройки изменены в другом окне. Загрузите сохранённый ряд заново")
            saved = plan.model_copy(update={"revision": revision + 1})
            db.execute("INSERT INTO calculator_settings (id, payload) VALUES (1, ?) "
                       "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload", (saved.model_dump_json(),))
        return saved
