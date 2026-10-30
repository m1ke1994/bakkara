"""Standard Punto Banco Player draw rule; no predictions from past hands."""
def card_points(rank):
    rank = str(rank).upper()
    if rank == "A":
        return 1
    if rank in ("10", "J", "Q", "K"):
        return 0
    if rank in tuple(str(n) for n in range(2, 10)):
        return int(rank)
    return None


def player_card_count(player_cards, banker_ranks=()):
    """Return required total card count, or None if initial cards are insufficient."""
    if len(player_cards) < 2:
        return None
    player = [card_points(c.get("rank")) for c in player_cards[:2]]
    if None in player:
        return None
    total = sum(player) % 10
    if total >= 6:
        return 2
    banker = [card_points(rank) for rank in banker_ranks[:2]]
    if len(banker) != 2 or None in banker:
        return None
    return 2 if sum(banker) % 10 >= 8 else 3



def banker_card_count(player_cards, banker_ranks):
    """Number of Banker cards dictated by the standard Punto Banco rules."""
    if len(player_cards) < 2 or len(banker_ranks) < 2:
        return None
    p0 = [card_points(card.get("rank")) for card in player_cards[:2]]
    b0 = [card_points(rank) for rank in banker_ranks[:2]]
    if None in p0 + b0:
        return None
    player, banker = sum(p0) % 10, sum(b0) % 10
    if player >= 8 or banker >= 8:
        return 2
    if player >= 6:
        return 3 if banker <= 5 else 2
    if len(player_cards) < 3:
        return None
    third = card_points(player_cards[2].get("rank"))
    if third is None:
        return None
    if banker <= 2:
        return 3
    if banker == 3:
        return 3 if third != 8 else 2
    if banker == 4:
        return 3 if 2 <= third <= 7 else 2
    if banker == 5:
        return 3 if 4 <= third <= 7 else 2
    if banker == 6:
        return 3 if third in (6, 7) else 2
    return 2


def resolved_baccarat_scores(player_cards, banker_ranks):
    """Resolve actual final scores; a prelisted game's 0:0 is not a draw."""
    if len(player_cards) not in (2, 3) or len(banker_ranks) not in (2, 3):
        return None
    if (player_card_count(player_cards, banker_ranks[:2]) != len(player_cards)
            or banker_card_count(player_cards, banker_ranks) != len(banker_ranks)):
        return None
    values = [card_points(card.get("rank")) for card in player_cards]
    bank = [card_points(rank) for rank in banker_ranks]
    if None in values + bank:
        return None
    return sum(values) % 10, sum(bank) % 10


def resolved_baccarat_outcome(player_cards, banker_cards):
    """Resolve only two complete, positioned, suited hands from one round."""
    valid_suits = {"hearts", "diamonds", "clubs", "spades"}
    for hand in (player_cards, banker_cards):
        if len(hand) not in (2, 3):
            return None
        if [card.get("position") for card in hand] != list(range(1, len(hand) + 1)):
            return None
        if any(card_points(card.get("rank")) is None or card.get("suit") not in valid_suits
               for card in hand):
            return None
    scores = resolved_baccarat_scores(player_cards, [card["rank"] for card in banker_cards])
    if scores is None:
        return None
    player, banker = scores
    return ("DRAW" if player == banker else "PLAYER" if player > banker else "BANKER"), player, banker
