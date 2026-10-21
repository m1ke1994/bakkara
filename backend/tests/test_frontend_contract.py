from pathlib import Path


def test_observation_panel_displays_live_cards_and_pending_slots_in_russian():
    app = (Path(__file__).resolve().parents[2] / 'frontend' / 'src' / 'App.vue').read_text(encoding='utf-8')
    assert 'state.observation.current.cards' in app
    assert 'state.observation.current.cards_count' in app
    assert 'state.observation.current.pending_card_slots' in app
    assert 'state.observation.current.site_status' in app
    assert 'state.observation.current.player_score' in app
    assert 'state.observation.current.scan_result' in app
    assert 'state.observation.current.diagnostics' in app
    assert 'state.observation.current.round_number_diagnostic' in app
    assert 'PLAYER_FIELD_NOT_FOUND' in app
    assert 'CARDS_UNCONFIRMED' in app
    assert 'item.url' in app
    assert 'formatObservedAt(item.observed_at' in app
    assert 'Начать сканирование' in app
    assert 'Раскрытых карт' in app
    assert 'Нераскрытых позиций' in app


def test_history_shows_only_warmup_or_cycle18_results_and_inline_cards():
    app = (Path(__file__).resolve().parents[2] / 'frontend' / 'src' / 'App.vue').read_text(encoding='utf-8')
    assert '<table class="history-table"' in app
    assert 'Время</th>' in app
    assert '№ раздачи</th>' in app
    assert 'Карты Игрока</th>' in app
    assert 'Расчётная масть</th>' in app
    assert 'WIN / LOSE</th>' in app
    assert 'item.player_cards' in app
    assert 'history-card-line' in app
    assert '.history-card-line { display: flex; flex-wrap: nowrap;' in app
    assert 'red-suit' in app and 'black-suit' in app
    assert "hourCycle: 'h23'" in app
    assert ':href="item.game_url || item.url"' in app
    assert 'item.calculated_suit' in app
    assert 'outcomeFor(item.game_id)' in app
    assert 'WARMUP_N_MINUS_1' in app
    assert "cycle18.value.warmup_results" in app
    assert "cycle18.value.recent" in app
    assert "Проверки подготовки N−1" in app
    assert ':key="item.game_id"' in app
    assert 'prediction-win' in app and 'prediction-lose' in app
    assert 'Эффективность прогнозов на следующую игру (старый режим)' not in app
    assert 'item.prediction_result' not in app
    assert 'prediction_hit' not in app.split('<template>', 1)[-1]


def test_history_cells_are_compact_and_clear_buttons_use_separate_delete_routes():
    app = (Path(__file__).resolve().parents[2] / 'frontend' / 'src' / 'App.vue').read_text(encoding='utf-8')
    body = app.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
    assert 'item.reason' not in body
    assert 'item.prediction_reason' not in body
    assert 'Очистить историю' in app and "command('/api/observations', 'DELETE')" in app
    assert 'window.confirm(' in app
    assert 'Очистить логи' in app and "command('/api/logs', 'DELETE')" in app
