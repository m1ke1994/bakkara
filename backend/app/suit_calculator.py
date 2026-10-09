"""Pure, ordered suit transformations for Baccarat observations."""
from __future__ import annotations

import unicodedata
from typing import Any, Sequence


suit_map: dict[tuple[str, str], str] = {
    ("♥", "♥"): "♦",
    ("♥", "♠"): "♥",
    ("♥", "♣"): "♠",
    ("♥", "♦"): "♦",
    ("♦", "♦"): "♥",
    ("♦", "♠"): "♠",
    ("♦", "♣"): "♣",
    ("♦", "♥"): "♦",
    ("♣", "♣"): "♦",
    ("♣", "♥"): "♠",
    ("♣", "♦"): "♠",
    ("♣", "♠"): "♣",
    ("♠", "♠"): "♥",
    ("♠", "♥"): "♣",
    ("♠", "♦"): "♥",
    ("♠", "♣"): "♦",
}

SUIT_NAME_TO_SYMBOL = {
    "hearts": "♥", "heart": "♥", "червы": "♥",
    "diamonds": "♦", "diamond": "♦", "бубны": "♦",
    "clubs": "♣", "club": "♣", "трефы": "♣",
    "spades": "♠", "spade": "♠", "пики": "♠",
}
SUIT_SYMBOL_TO_NAME = {symbol: name for name, symbol in (
    ("hearts", "♥"), ("diamonds", "♦"), ("clubs", "♣"), ("spades", "♠")
)}


def normalize_suit(value: Any) -> str | None:
    """Normalize a suit name/icon, including Unicode text/emoji presentation variants."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\ufe0e", "").replace("\ufe0f", "")
    text = "".join(character for character in text if not character.isspace()).casefold()
    if text in {"♥", "♦", "♣", "♠"}:
        return text
    return SUIT_NAME_TO_SYMBOL.get(text)


def calculate_suit(suits: Sequence[Any]) -> str | None:
    """Return the ordered two/three-card result as a suit icon; invalid counts return None."""
    if len(suits) not in (2, 3):
        return None
    normalized = [normalize_suit(suit) for suit in suits]
    if any(suit is None for suit in normalized):
        return None
    result = suit_map[(normalized[0], normalized[1])]
    if len(normalized) == 3:
        result = suit_map[(result, normalized[2])]
    return result


def calculate_cards_suit(cards: Sequence[dict[str, Any]]) -> str | None:
    """Calculate from card positions in DOM order; card ranks never affect the result."""
    ordered = list(cards)
    positions = [card.get("position") for card in ordered]
    if all(isinstance(position, int) for position in positions) and len(set(positions)) == len(positions):
        ordered.sort(key=lambda card: card["position"])
    return calculate_suit([card.get("suit") for card in ordered])


def calculate_final_result(
    cards: Sequence[dict[str, Any]], *, finished: bool, final_snapshot_reverified: bool,
    pending_card_slots: int = 0, player_hand_final: bool = False,
) -> tuple[str | None, str]:
    """Calculate only after the Player hand itself (not necessarily the game) is final."""
    if not (finished or player_hand_final):
        return None, "pending"
    if not final_snapshot_reverified or pending_card_slots or len(cards) not in (2, 3):
        return None, "incomplete"
    result = calculate_cards_suit(cards)
    return (SUIT_SYMBOL_TO_NAME[result], "complete") if result else (None, "incomplete")
