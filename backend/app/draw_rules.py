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
