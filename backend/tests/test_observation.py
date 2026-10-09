from app.observation import (
    Game,
    ObservationStore,
    choose_next_game,
    classify_game,
    classify_game_status,
    classify_player_scan,
    is_finished_flag_text,
    merge_card_snapshots,
    parse_game_list_html,
    parse_player_cards_html,
    parse_player_cards_snapshot_html,
    suit_from_values,
)


def test_parses_game_numbers_and_skips_finished_and_timer_only_rows():
    html = '''<li class="dashboard-champ-body"><ul class="dashboard-champ-body__games"><li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/baccarat/2050671-baccara/759886939-player-banker"></a><span class="dashboard-game-info__period">Игра завершена</span><span class="dashboard-game-info__additional-info">520</span><div class="dashboard-game-block__teams"><span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span><div class="ui-game-scores__item--total"><span class="ui-game-scores__num">7</span><span class="ui-game-scores__num">4</span></div></div></li><li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/baccarat/2050671-baccara/759886940-player-banker"></a><span class="dashboard-game-info__time countdown" data-state="countdown">00:01</span><span class="dashboard-game-info__additional-info">521</span><div class="dashboard-game-block__teams"><span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span><div class="ui-game-scores__item--total"><span class="ui-game-scores__num">0</span><span class="ui-game-scores__num">0</span></div></div></li><li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/baccarat/2050671-baccara/759886941-player-banker"></a><span class="dashboard-game-info__time countdown" data-state="countdown">02:25</span><span class="dashboard-game-info__additional-info">522</span><div class="dashboard-game-block__teams"><span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span><div class="ui-game-scores__item--total"><span class="ui-game-scores__num">0</span><span class="ui-game-scores__num">0</span></div></div></li></ul></li><li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/tennis/999-other-sport"></a><span class="dashboard-game-info__time">00:01</span></li>'''
    games = parse_game_list_html(html, 'https://example.test')
    assert len(games) == 3
    assert [game.game_id for game in games] == ['759886939', '759886940', '759886941']
    assert [game.position for game in games] == [0, 1, 2]
    assert games[0].round_number == '520'
    assert games[0].status == 'Завершена'
    assert games[1].time == '00:01'
    assert games[0].player_score == '7'
    assert games[0].banker_score == '4'
    assert games[1].status == 'Не удалось определить'  # timer is not a status
    assert classify_game(games[0]) == "FINISHED"
    assert classify_game(games[1]) == "UPCOMING"
    assert classify_game(games[2]) == "UPCOMING"
    assert choose_next_game(games, set()).game_id == '759886940'


def test_unhandled_game_selection_follows_dom_order_not_round_sorting():
    games = [
        Game('unknown', 'https://example.test/unknown-game', '00:01', '0', '', 0,
             player_score='0', banker_score='0'),
        Game('finished', 'https://example.test/finished-game', '00:02', '1', 'Завершена', 1,
             player_score='6', banker_score='4', finished=True),
        Game('started', 'https://example.test/started-game', '00:03', '2', 'Идёт', 2,
             player_score='0', banker_score='0'),
        Game('3', 'https://example.test/3-game', '00:15', '3', '', 3,
             player_score='0', banker_score='0', is_countdown_to_start=True),
        Game('4', 'https://example.test/4-game', '00:45', '4', '', 4,
             player_score='0', banker_score='0', is_countdown_to_start=True),
    ]
    assert choose_next_game(games, set()).game_id == '3'
    assert choose_next_game(games, {'3'}).game_id == '4'
    assert choose_next_game(games, {'3', '4'}) is None


def test_pending_forecast_does_not_authorize_opening_a_finished_game():
    game = Game('target', 'https://example.test/target-game', '', '617', 'Завершена', 0)
    assert choose_next_game([game], set()) is None
    assert choose_next_game([game], set(), inspect_ids={'target'}) is None


def test_game_classification_requires_explicit_countdown_and_zero_scores():
    assert classify_game(Game("finished", "/finished", "00:01", "1", "", 0,
                              player_score="8", banker_score="5", finished=True)) == "FINISHED"
    assert classify_game(Game("started", "/started", "00:01", "2", "Идёт", 0,
                              player_score="0", banker_score="0")) == "STARTED"
    assert classify_game(Game("timer", "/timer", "00:01", "3", "", 0,
                              player_score="0", banker_score="0")) == "UNKNOWN"
    assert classify_game(Game("zero", "/zero", "", "4", "", 0,
                              player_score="0", banker_score="0",
                              is_countdown_to_start=True)) == "UNKNOWN"
    assert classify_game(Game("unknown-score", "/unknown-score", "00:01", "5", "", 0,
                              player_score="0", banker_score="")) == "UNKNOWN"
    assert classify_game(Game("upcoming", "/upcoming", "00:15", "6", "", 0,
                              player_score="0", banker_score="0",
                              is_countdown_to_start=True)) == "UPCOMING"


def test_not_started_negation_is_not_misclassified_as_started():
    for evidence in ("countdown not started", "countdown not_started", "countdown not-started"):
        game = Game("upcoming", "/upcoming", "00:15", "6", "", 0,
                    player_score="0", banker_score="0",
                    timer_evidence=evidence,
                    is_countdown_to_start=True)
        assert classify_game(game) == "UPCOMING"


def test_game_classification_uses_explicit_row_metadata_but_not_timer_text():
    countdown = Game("countdown", "/countdown", "00:15", "7", "", 0,
                     player_score="0", banker_score="0",
                     state_evidence="data-state=countdown")
    started = Game("started", "/started", "00:15", "8", "", 0,
                   player_score="0", banker_score="0",
                   state_evidence="aria-label=live")
    timer_only = Game("timer", "/timer", "00:15", "9", "", 0,
                      player_score="0", banker_score="0",
                      state_evidence="class=dashboard-game-info__time")
    assert classify_game(countdown) == "UPCOMING"
    assert classify_game(started) == "STARTED"
    assert classify_game(timer_only) == "UNKNOWN"


def test_legacy_empty_rows_are_hidden_from_history_without_deleting_data(tmp_path):
    store = ObservationStore(tmp_path / 'history.sqlite3')
    game = Game('empty-legacy', 'https://example.test/empty-legacy', '', '', 'Идёт', 0)
    store.upsert(game, [], 'PARTIAL', 'legacy placeholder', 'CARDS_NOT_FOUND')

    assert store.get(game.game_id) is not None
    assert store.history() == []


def test_list_round_number_backfills_history_without_changing_observed_result(tmp_path):
    store = ObservationStore(tmp_path / "round-backfill.sqlite3")
    scanned = Game("technical-id", "https://example.test/technical-id", "00:03", "",
                   "РРґС‘С‚", 0)
    cards = [{"position": 1, "rank": "K", "suit": "spades"},
             {"position": 2, "rank": "4", "suit": "hearts"}]
    store.upsert(scanned, cards, "CONFIRMED", "saved cards", "CARDS_CONFIRMED",
                 finished_confirmed=True, final_snapshot_reverified=True, player_hand_final=True)

    listed = Game("technical-id", scanned.url, "00:03", "784", "РРґС‘С‚", 0)
    store.update_game_list_metadata([listed])

    saved = store.get("technical-id")
    assert saved["game_id"] == "technical-id"
    assert saved["round_number"] == "784"
    assert saved["player_cards"] == cards
    assert saved["reason"] == "saved cards"
    assert store.history()[0]["round_number"] == "784"

    stale_listed = Game("technical-id", scanned.url, "00:03", "", "РРґС‘С‚", 0)
    store.update_game_list_metadata([stale_listed])
    assert store.get("technical-id")["round_number"] == "784"


def test_status_is_unknown_when_dom_only_exposes_a_timer():
    assert classify_game_status('00:12') == 'Не удалось определить'
    assert classify_game_status('Игра завершена') == 'Завершена'
    assert classify_game_status('Ожидает начала') == 'Ожидается'
    assert classify_game_status('Игра не завершена') == 'Не удалось определить'


def test_actual_list_layout_does_not_treat_market_count_or_game_id_as_round_number():
    html = '''<li class="dashboard-champ-body"><ul class="dashboard-champ-body__games"><li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/baccarat/table/759921023-player-banker"></a><div class="dashboard-game-block__info"><span></span></div><div class="dashboard-game-block__teams"><span class="ui-team-score-name">Игрок</span><span class="ui-team-score-name">Банкир</span><div class="ui-game-scores__item--total"><span class="ui-game-scores__num">2</span><span class="ui-game-scores__num">8</span></div></div><button class="dashboard-game-more"><span class="dashboard-game-more__count">122</span></button></li></ul></li>'''
    game = parse_game_list_html(html, 'https://example.test')[0]
    assert game.game_id == '759921023'
    assert game.round_number == ''
    assert game.time == ''
    assert game.player_score == '2'
    assert game.banker_score == '8'
    assert game.status == 'Не удалось определить'
    assert '122' in game.round_number_diagnostic
    assert 'игнорируется как число рынков' in game.round_number_diagnostic
    assert 'ID игры из URL не используется' in game.round_number_diagnostic


def test_round_number_comes_only_from_additional_info_inside_matching_game_container():
    html = '''<li class="dashboard-champ-body"><ul class="dashboard-champ-body__games">
      <li class="dashboard-game-block dashboard-game__block"><a class="dashboard-game-block__link" href="/ru/live/baccarat/table/759925627-player-banker"></a>
        <span class="dashboard-game-info__period">700</span><span class="dashboard-game-info__additional-info">616</span></li>
      <li class="dashboard-game-block dashboard-game__block"><a class="dashboard-game-block__link" href="/ru/live/baccarat/table/759925628-player-banker"></a>
        <span class="dashboard-game-info__period">701</span><span class="dashboard-game-more__count">122</span></li>
    </ul></li>'''
    games = parse_game_list_html(html, 'https://example.test')
    assert [(game.game_id, game.round_number) for game in games] == [
        ('759925627', '616'), ('759925628', '')
    ]
    assert games[0].position == 0
    assert 'additional-info' in games[0].round_number_diagnostic
    assert games[1].round_number == ''


def test_live_nested_game_info_extracts_round_652_and_timer_from_same_row():
    html = '''<li class="dashboard-champ-body"><ul class="dashboard-champ-body__games">
      <li class="dashboard-game"><div class="dashboard-game-block dashboard-game__block">
        <a class="dashboard-game-block__link" href="/ru/live/baccarat/table/759925627-player-banker"></a>
        <div class="dashboard-game-block__info"><span data-test="betting-dashboard-game-info" class="dashboard-game-info dashboard-game__info">
          <span class="dashboard-game-info__time">00:20</span>
          <span class="dashboard-game-info__additional-info has-tooltip">652</span>
        </span></div>
      </div></li>
    </ul></li>'''
    game = parse_game_list_html(html, 'https://example.test')[0]
    assert game.game_id == '759925627'
    assert game.round_number == '652'
    assert game.time == '00:20'


def test_live_nested_game_info_extracts_round_616():
    html = '''<li class="dashboard-champ-body"><ul class="dashboard-champ-body__games">
      <li class="dashboard-game"><div class="dashboard-game-block dashboard-game__block">
        <a class="dashboard-game-block__link" href="/ru/live/baccarat/table/759925628-player-banker"></a>
        <div class="dashboard-game-block__info"><span class="dashboard-game-info dashboard-game__info">
          <span class="dashboard-game-info__time">01:10</span>
          <span class="dashboard-game-info__additional-info">616</span>
        </span></div>
      </div></li>
    </ul></li>'''
    game = parse_game_list_html(html, 'https://example.test')[0]
    assert game.round_number == '616'
    assert game.time == '01:10'


def test_real_game_header_statuses_are_explicitly_normalized():
    assert classify_game_status('Ставки до начала игры') == 'Ожидается'
    assert classify_game_status('Игра завершена') == 'Завершена'


def test_finish_flag_requires_exact_text_not_a_matching_class_alone():
    assert is_finished_flag_text('Игра завершена')
    assert is_finished_flag_text('  Игра   завершена  ')
    assert not is_finished_flag_text('Игра ещё не завершена')
    assert not is_finished_flag_text('Завершено')


def test_card_scan_distinguishes_missing_field_container_empty_and_partial_slots():
    assert classify_player_scan(player_field_present=False, cards_container_present=False,
                                card_boxes_count=0, cards_count=0, pending_card_slots=0,
                                finished=True) == ('PLAYER_FIELD_NOT_FOUND', 'PARTIAL')
    assert classify_player_scan(player_field_present=True, cards_container_present=False,
                                card_boxes_count=0, cards_count=0, pending_card_slots=0,
                                finished=True) == ('PLAYER_CARDS_CONTAINER_NOT_FOUND', 'PARTIAL')
    assert classify_player_scan(player_field_present=True, cards_container_present=True,
                                card_boxes_count=0, cards_count=0, pending_card_slots=0,
                                finished=True) == ('CARDS_NOT_FOUND', 'PARTIAL')
    assert classify_player_scan(player_field_present=True, cards_container_present=True,
                                card_boxes_count=3, cards_count=2, pending_card_slots=1,
                                finished=True) == ('CARDS_UNCONFIRMED', 'PARTIAL')
    assert classify_player_scan(player_field_present=True, cards_container_present=True,
                                card_boxes_count=2, cards_count=2, pending_card_slots=0,
                                finished=False) == ('CARDS_FOUND', 'PARTIAL')
    assert classify_player_scan(player_field_present=True, cards_container_present=True,
                                card_boxes_count=2, cards_count=2, pending_card_slots=0,
                                finished=True) == ('CARDS_CONFIRMED', 'CONFIRMED')


def test_url_without_numeric_id_gets_stable_fallback_identity():
    html = '''<li class="dashboard-champ-body"><ul class="dashboard-champ-body__games"><li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/baccarat/table-a-player-banker"></a></li></ul></li>'''
    first = parse_game_list_html(html, 'https://example.test')[0]
    second = parse_game_list_html(html, 'https://example.test')[0]
    assert first.game_id.startswith('url:')
    assert first.game_id == second.game_id


def test_position_counts_rows_without_links_and_numeric_id_allows_trailing_slash():
    html = '''<li class="dashboard-champ-body"><ul class="dashboard-champ-body__games"><li class="dashboard-game"></li><li class="dashboard-game"><a class="dashboard-game-block__link" href="/ru/live/baccarat/table/123-player-banker/"></a></li></ul></li>'''
    game = parse_game_list_html(html, 'https://example.test')[0]
    assert game.game_id == '123'
    assert game.position == 1


def test_reads_revealed_cards_only_and_keeps_dom_order():
    html = '''<div class="baccarat-player--is-reversed baccarat-player baccarat__player"><span class="baccarat-player__name">Банкир</span><ul class="baccarat-player__cards"><li class="baccarat-player__card-box"><div class="scoreboard-card--suit-spades"><span class="baccarat-card__rank">K</span></div></li></ul></div><div class="baccarat-player baccarat__player"><span class="baccarat-player__name">Игрок</span><ul class="baccarat-player__cards"><li class="baccarat-player__card-box"><div class="scoreboard-card--suit-clubs"><span class="baccarat-card__rank">3</span><svg data-v-ico="cardSuits|clubs"></svg></div></li><li class="baccarat-player__card-box"><div class="scoreboard-card--suit-diamonds"><span class="baccarat-card__rank">7</span><svg data-v-ico="cardSuits|diamonds"></svg></div></li><li class="baccarat-player__card-box"></li></ul></div>'''
    snapshot = parse_player_cards_snapshot_html(html)
    assert snapshot == {'cards': [{'position': 1, 'rank': '3', 'suit': 'clubs'}, {'position': 2, 'rank': '7', 'suit': 'diamonds'}], 'cards_count': 2, 'pending_card_slots': 1}
    assert parse_player_cards_html(html) == snapshot['cards']


def test_reads_three_cards_and_all_suits():
    html = '''<div class="baccarat-player baccarat__player"><span class="baccarat-player__name">Игрок</span><div class="baccarat-player__cards"><div class="baccarat-player__card-box scoreboard-card--suit-hearts"><span class="baccarat-card__rank">9</span></div><div class="baccarat-player__card-box"><span class="baccarat-card__rank">2</span><svg data-v-ico="cardSuits|clubs"></svg></div><div class="baccarat-player__card-box scoreboard-card--suit-diamonds"><span class="baccarat-card__rank">K</span></div></div></div>'''
    assert parse_player_cards_snapshot_html(html)['cards_count'] == 3
    assert [suit_from_values(f'scoreboard-card--suit-{suit}') for suit in ('hearts', 'diamonds', 'clubs', 'spades')] == ['hearts', 'diamonds', 'clubs', 'spades']


def test_regression_example_preserves_positions_and_never_reads_banker_cards():
    html = '''<div class="baccarat-player baccarat__player"><span class="baccarat-player__name">Банкир</span><ul class="baccarat-player__cards"><li class="baccarat-player__card-box"><div class="scoreboard-card--suit-hearts"><span class="baccarat-card__rank">A</span></div></li></ul></div><div class="baccarat-player baccarat__player"><span class="baccarat-player__name">Игрок</span><ul class="baccarat-player__cards"><li class="baccarat-player__card-box"><div class="scoreboard-card--suit-spades"><span class="baccarat-card__rank">3</span></div></li><li class="baccarat-player__card-box"><div class="scoreboard-card--suit-spades"><span class="baccarat-card__rank">K</span></div></li><li class="baccarat-player__card-box"><div class="scoreboard-card--suit-clubs"><span class="baccarat-card__rank">J</span></div></li></ul></div>'''
    snapshot = parse_player_cards_snapshot_html(html)
    assert snapshot['cards'] == [
        {'position': 1, 'rank': '3', 'suit': 'spades'},
        {'position': 2, 'rank': 'K', 'suit': 'spades'},
        {'position': 3, 'rank': 'J', 'suit': 'clubs'},
    ]


def test_store_updates_same_game_when_third_card_appears(tmp_path):
    store = ObservationStore(tmp_path / 'history.sqlite3')
    game = Game('759887344', 'https://example.test/759887344-player-banker', '', '442', '', 0)
    assert store.upsert(game, [{'rank': '3', 'suit': 'clubs'}, {'rank': '7', 'suit': 'diamonds'}], 'PARTIAL', scan_result='CARDS_FOUND')
    assert not store.upsert(game, [{'rank': '3', 'suit': 'clubs'}, {'rank': '7', 'suit': 'diamonds'}], 'PARTIAL', scan_result='CARDS_FOUND')
    assert store.upsert(game, [{'rank': '3', 'suit': 'clubs'}, {'rank': '7', 'suit': 'diamonds'}, {'rank': 'K', 'suit': 'hearts'}], 'CONFIRMED', scan_result='CARDS_CONFIRMED', player_hand_final=True, final_snapshot_reverified=True)
    history = store.history()
    assert len(history) == 1
    assert history[0]['cards_count'] == 3


def test_snapshot_merge_preserves_known_card_positions_across_temporary_dom_gaps():
    previous = [
        {'position': 1, 'rank': '3', 'suit': 'spades'},
        {'position': 2, 'rank': 'K', 'suit': 'spades'},
    ]
    later_snapshot = [
        {'position': 1, 'rank': '3', 'suit': 'spades'},
        {'position': 3, 'rank': 'J', 'suit': 'clubs'},
    ]
    assert merge_card_snapshots(previous, later_snapshot) == [
        {'position': 1, 'rank': '3', 'suit': 'spades'},
        {'position': 2, 'rank': 'K', 'suit': 'spades'},
        {'position': 3, 'rank': 'J', 'suit': 'clubs'},
    ]


def test_empty_rescan_does_not_erase_known_cards_and_saves_game_metadata(tmp_path):
    store = ObservationStore(tmp_path / 'history.sqlite3')
    game = Game('759887344', 'https://example.test/759887344-player-banker', '00:05', '442', 'Завершена', 0,
                player_score='7', banker_score='4')
    cards = [{'rank': '3', 'suit': 'clubs'}, {'rank': '7', 'suit': 'diamonds'}]
    store.upsert(game, cards, 'CONFIRMED', 'Карты считаны.', 'CARDS_CONFIRMED',
                 finished_confirmed=True, player_hand_final=True, final_snapshot_reverified=True)
    store.upsert(game, [], 'PARTIAL', 'Временный пустой DOM.', 'CARDS_NOT_FOUND')
    record = store.history()[0]
    assert record['cards'] == cards
    assert record['site_status'] == 'Завершена'
    assert (record['player_score'], record['banker_score']) == ('7', '4')
    assert record['time'] == '00:05'
    assert record['scan_result'] == 'CARDS_NOT_FOUND'
    assert store.get(game.game_id) == record


def test_finished_but_incomplete_hand_remains_recoverable_until_final_cards(tmp_path):
    path = tmp_path / 'history.sqlite3'
    store = ObservationStore(path)
    game = Game('finish-1', 'https://example.test/finish-1', '', '580', 'Завершена', 0)
    cards = [{'position': 1, 'rank': '3', 'suit': 'spades'}]
    store.upsert(game, cards, 'PARTIAL', 'Список помечен завершённым.', 'CARDS_FOUND')
    assert 'finish-1' not in store.finished_game_ids()
    store.upsert(game, cards, 'PARTIAL', 'Обнаружен точный флаг.', 'CARDS_FOUND',
                 finished_confirmed=True, final_snapshot_reverified=False)
    assert 'finish-1' not in store.finished_game_ids()
    final_cards = [
        {'position': 1, 'rank': '3', 'suit': 'spades'},
        {'position': 2, 'rank': 'K', 'suit': 'hearts'},
    ]
    store.upsert(game, final_cards, 'CONFIRMED', 'Финальный набор подтверждён.', 'CARDS_CONFIRMED',
                 finished_confirmed=True, player_hand_final=True, final_snapshot_reverified=True)
    assert store.finished_game_ids() == {'finish-1'}
    reloaded = ObservationStore(path)
    assert reloaded.history()[0]['finished_confirmed'] is True
    assert reloaded.history()[0]['final_snapshot_reverified'] is True
    assert reloaded.history()[0]['cards'] == final_cards


def test_final_calculation_persists_once_with_prediction_fields_left_unchecked(tmp_path):
    path = tmp_path / 'calculated-history.sqlite3'
    store = ObservationStore(path)
    game = Game('calc-1', 'https://example.test/calc-1', '', '616', '', 0)
    cards = [
        {'position': 1, 'rank': '3', 'suit': 'spades'},
        {'position': 2, 'rank': 'K', 'suit': 'spades'},
        {'position': 3, 'rank': 'J', 'suit': 'clubs'},
    ]
    assert store.upsert(game, cards, 'CONFIRMED', 'final', 'CARDS_CONFIRMED',
                        finished_confirmed=True, final_snapshot_reverified=True)
    assert not store.upsert(game, cards, 'CONFIRMED', 'final', 'CARDS_CONFIRMED',
                            finished_confirmed=True, final_snapshot_reverified=True)
    record = ObservationStore(path).history()[0]
    assert record['game_id'] == 'calc-1'
    assert record['round_number'] == '616'
    assert record['game_url'] == game.url
    assert record['player_cards'] == cards
    assert record['calculated_suit'] == 'spades'
    assert record['calculation_status'] == 'complete'
    assert record['prediction_for_next_round'] == 'spades'
    assert record['prediction_checked'] is None
    assert record['prediction_hit'] is None
    assert record['observed_at']
    assert len(record['player_cards']) == 3
    assert len(ObservationStore(path).history()) == 1


def test_finished_unverified_final_snapshot_is_saved_as_incomplete_without_prediction(tmp_path):
    store = ObservationStore(tmp_path / 'incomplete-calculation.sqlite3')
    game = Game('calc-incomplete', 'https://example.test/incomplete', '', '', '', 0)
    cards = [{'position': 1, 'rank': '9', 'suit': 'hearts'}, {'position': 2, 'rank': '2', 'suit': 'clubs'}]
    store.upsert(game, cards, 'PARTIAL', 'final DOM did not confirm cards', 'CARDS_UNCONFIRMED',
                 finished_confirmed=True, final_snapshot_reverified=False)
    record = store.get(game.game_id)
    assert record['cards'] == cards
    assert record['calculated_suit'] is None
    assert record['calculation_status'] == 'incomplete'
    assert record['prediction_for_next_round'] is None


def test_migration_calculates_only_reliable_legacy_completed_records(tmp_path):
    import json
    import sqlite3
    path = tmp_path / 'legacy-completed.sqlite3'
    cards = [
        {'position': 1, 'rank': '9', 'suit': 'hearts'},
        {'position': 2, 'rank': '2', 'suit': 'clubs'},
    ]
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE observations (
            game_id TEXT PRIMARY KEY, url TEXT NOT NULL, round_number TEXT,
            discovered_at TEXT NOT NULL, finished_at TEXT, cards_json TEXT NOT NULL,
            completeness TEXT NOT NULL, reason TEXT)''')
        db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)',
                   ('legacy-1', 'https://example.test/legacy', '617',
                    '2026-10-09T12:42:08+00:00', '2026-10-09T12:43:10+00:00',
                    json.dumps(cards), 'CONFIRMED', 'legacy verified result'))
    record = ObservationStore(path).get('legacy-1')
    assert record['calculated_suit'] == 'spades'
    assert record['calculation_status'] == 'complete'
    assert record['prediction_for_next_round'] == 'spades'
    assert record['prediction_hit'] is None
    assert record['round_number'] == '617'


def test_migrates_existing_history_table_without_resetting_it(tmp_path):
    import sqlite3
    path = tmp_path / 'old-history.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE observations (
            game_id TEXT PRIMARY KEY, url TEXT NOT NULL, round_number TEXT,
            discovered_at TEXT NOT NULL, finished_at TEXT, cards_json TEXT NOT NULL,
            completeness TEXT NOT NULL, reason TEXT)''')
        db.execute('''INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)''',
                   ('old-1', 'https://example.test/old', '', '2026-01-01T00:00:00+00:00', None, '[]', 'PARTIAL', 'old record'))
    store = ObservationStore(path)
    assert store.get('old-1') is not None
    assert store.history() == []
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(observations)')}
    assert {'list_position', 'site_status', 'player_score', 'banker_score', 'scan_result', 'game_time',
            'final_snapshot_reverified', 'observed_at', 'calculated_suit', 'calculation_status',
            'prediction_for_next_round', 'prediction_checked', 'prediction_hit'} <= columns
