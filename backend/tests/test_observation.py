from app.observation import Game, choose_next_game, parse_game_list_html, parse_player_cards_html, suit_from_values

def test_parses_games_and_skips_completed():
    html = '''<li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/baccarat/2050671-baccara/759886939-player-banker"></a><span class="dashboard-game-info__period">407</span><span class="dashboard-game-info__additional-info">Игра завершена</span></li><li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/baccarat/2050671-baccara/759886940-player-banker"></a><span class="dashboard-game-info__period">408</span><span class="dashboard-game-info__time">00:25</span></li>'''
    games = parse_game_list_html(html, 'https://example.test')
    assert [item.game_id for item in games] == ['759886939', '759886940']
    games[1].active = True
    assert choose_next_game(games, set()).game_id == '759886940'

def test_reads_three_cards_in_dom_order():
    html = '''<div class="baccarat-player baccarat__player"><div class="baccarat-player__card-box scoreboard-card--suit-hearts"><span class="baccarat-card__rank">9</span></div><div class="baccarat-player__card-box"><span class="baccarat-card__rank">2</span><svg data-v-ico="cardSuits|clubs"></svg></div><div class="baccarat-player__card-box scoreboard-card--suit-diamonds"><span class="baccarat-card__rank">K</span></div></div>'''
    assert parse_player_cards_html(html) == [{'rank': '9', 'suit': 'hearts'}, {'rank': '2', 'suit': 'clubs'}, {'rank': 'K', 'suit': 'diamonds'}]

def test_all_suits_and_handled_game():
    assert [suit_from_values(f'scoreboard-card--suit-{suit}') for suit in ('hearts', 'diamonds', 'clubs', 'spades')] == ['hearts', 'diamonds', 'clubs', 'spades']
    assert choose_next_game([Game('1', 'https://x/1-a', '', '', '', 0, True)], {'1'}) is None
    assert choose_next_game([Game('2', 'https://x/2-a', '', '', '', 0)], set()) is None
