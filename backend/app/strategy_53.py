"""Замороженная таблица назначения мастей «53%» — экспериментальная модель.

Используются только уже завершённые карты Игрока. Название не обещает точность.
"""
from __future__ import annotations
from typing import Any
from .suit_calculator import normalize_suit, SUIT_SYMBOL_TO_NAME

MAP_53 = {
    ("A", "♥"): "♣", ("A", "♠"): "♦", ("A", "♣"): "♥", ("A", "♦"): "♣",
    ("K", "♥"): "♣", ("K", "♣"): "♥", ("K", "♠"): "♣", ("K", "♦"): "♣",
    ("Q", "♥"): "♦", ("Q", "♦"): "♥", ("Q", "♠"): "♦", ("Q", "♣"): "♦",
    ("J", "♠"): "♥", ("J", "♣"): "♣", ("J", "♦"): "♠", ("J", "♥"): "♠",
}
MIRROR_53 = {"♥": "♠", "♠": "♥", "♦": "♣", "♣": "♦"}
WARMUP_53 = 40
PAUSE_53 = 4
MAX_GAP_53 = 185


def map_facecard_53(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Одна A/K/Q/J в руке N−1, зеркало назначенной масти для карты №2 из 3."""
    if len(cards) not in (2, 3):
        return None
    found = []
    for index, card in enumerate(cards):
        rank = str(card.get("rank") or "").strip().upper()
        suit = normalize_suit(card.get("suit"))
        target = MAP_53.get((rank, suit))
        if target:
            found.append((index, rank, suit, target))
    if len(found) != 1:
        return None
    index, rank, suit, target = found[0]
    mirrored = len(cards) == 3 and index == 1
    if mirrored:
        target = MIRROR_53[target]
    return {
        "predicted_suit": SUIT_SYMBOL_TO_NAME[target],
        "trigger_card": rank + suit,
        "mirrored": mirrored,
    }
