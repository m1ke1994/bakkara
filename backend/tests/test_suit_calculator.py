import pytest

from app.suit_calculator import (
    calculate_cards_suit,
    calculate_final_result,
    calculate_suit,
    normalize_suit,
    suit_map,
)


def test_all_sixteen_ordered_mappings_match_the_specification():
    expected = {
        ("♥", "♥"): "♦", ("♥", "♠"): "♥", ("♥", "♣"): "♠", ("♥", "♦"): "♦",
        ("♦", "♦"): "♥", ("♦", "♠"): "♠", ("♦", "♣"): "♣", ("♦", "♥"): "♦",
        ("♣", "♣"): "♦", ("♣", "♥"): "♠", ("♣", "♦"): "♠", ("♣", "♠"): "♣",
        ("♠", "♠"): "♥", ("♠", "♥"): "♣", ("♠", "♦"): "♥", ("♠", "♣"): "♦",
    }
    assert len(suit_map) == 16
    assert suit_map == expected


@pytest.mark.parametrize(("value", "expected"), [
    ("  ♥  ", "♥"), ("♥️", "♥"), ("♥︎", "♥"), (" spades ", "♠"),
    ("diamonds", "♦"), ("clubs", "♣"), ("hearts", "♥"),
])
def test_normalizes_suit_names_whitespace_and_unicode_variants(value, expected):
    assert normalize_suit(value) == expected


def test_two_card_example_and_three_card_example():
    assert calculate_suit(("hearts", "clubs")) == "♠"
    assert calculate_suit(("♠", "♠", "♣")) == "♠"
    assert calculate_suit(("spades", "hearts", "spades")) == "♣"


def test_order_is_significant_and_card_ranks_do_not_participate():
    assert calculate_suit(("♥", "♠")) == "♥"
    assert calculate_suit(("♠", "♥")) == "♣"
    cards = [
        {"position": 3, "rank": "J", "suit": "clubs"},
        {"position": 1, "rank": "3", "suit": "spades"},
        {"position": 2, "rank": "K", "suit": "spades"},
    ]
    assert calculate_cards_suit(cards) == "♠"


@pytest.mark.parametrize("suits", [(), ("♥",), ("♥", "?"), ("♥", "♣", "♠", "♦")])
def test_missing_or_incomplete_suits_do_not_calculate(suits):
    assert calculate_suit(suits) is None


def test_final_calculation_requires_two_or_three_reverified_cards():
    assert calculate_final_result([], finished=True, final_snapshot_reverified=True) == (None, "incomplete")
    assert calculate_final_result([{"suit": "hearts"}], finished=True, final_snapshot_reverified=True) == (None, "incomplete")
    assert calculate_final_result([{"suit": "hearts"}, {"suit": "clubs"}],
                                  finished=True, final_snapshot_reverified=False) == (None, "incomplete")
    assert calculate_final_result([{"suit": "hearts"}, {"suit": "clubs"}],
                                  finished=False, final_snapshot_reverified=True) == (None, "pending")
    assert calculate_final_result([{"suit": "hearts"}, {"suit": "clubs"}],
                                  finished=True, final_snapshot_reverified=True) == ("spades", "complete")
    assert calculate_final_result(
        [{"position": 1, "suit": "spades"}, {"position": 2, "suit": "hearts"},
         {"position": 3, "suit": "spades"}],
        finished=False, player_hand_final=True, final_snapshot_reverified=True,
    ) == ("clubs", "complete")
