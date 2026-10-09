<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { shouldApplySnapshot } from './stateVersion.js'

const api = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8010'
const scanResultNames = {
  SCANNING: '\u0421\u043a\u0430\u043d\u0438\u0440\u0443\u0435\u0442\u0441\u044f',
  CARDS_CONFIRMED: '\u041a\u0430\u0440\u0442\u044b \u0441\u0447\u0438\u0442\u0430\u043d\u044b, \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0438\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e',
  CARDS_FOUND: '\u041a\u0430\u0440\u0442\u044b \u0441\u0447\u0438\u0442\u0430\u043d\u044b',
  CARDS_NOT_FOUND: '\u041a\u0430\u0440\u0442\u044b \u043d\u0435 \u043e\u0431\u043d\u0430\u0440\u0443\u0436\u0435\u043d\u044b',
  PLAYER_FIELD_NOT_FOUND: '\u041f\u043e\u043b\u0435 \u0418\u0433\u0440\u043e\u043a\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e',
  PLAYER_CARDS_CONTAINER_NOT_FOUND: '\u041d\u0435\u0442 \u043a\u043e\u043d\u0442\u0435\u0439\u043d\u0435\u0440\u0430 \u043a\u0430\u0440\u0442 \u0418\u0433\u0440\u043e\u043a\u0430',
  CARDS_UNCONFIRMED: '\u0427\u0430\u0441\u0442\u044c \u0441\u043b\u043e\u0442\u043e\u0432 \u043d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430',
  NAVIGATION_ERROR: '\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043a\u0440\u044b\u0442\u044c \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443',
  HISTORY_ERROR: '\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442',
}
const scanResultName = value => scanResultNames[value] || value || '\u2014'
const state = ref({ state_epoch: '', state_version: 0, backend: 'CHECKING', browser: 'STOPPED', authorization: 'UNKNOWN', diagnostic: '', events: [], observation: { status: 'STOPPED', current: null, history: [], games: [] } })
const busy = ref(false)
const error = ref('')
const statistics = computed(() => state.value.observation?.statistics || {})
const badge = value => String(value || 'UNKNOWN').toLowerCase().replaceAll('_', '-')
const running = computed(() => !['STOPPED', 'ERROR'].includes(state.value.browser))
const observing = computed(() => !['STOPPED', 'ERROR'].includes(state.value.observation?.status))
const canObserve = computed(() => ['AUTHORIZED', 'TEMPORARILY_UNCONFIRMED'].includes(state.value.authorization))
const names = {
  backend: { CHECKING: 'Проверяется', OK: 'Работает', OFFLINE: 'Недоступен' },
  browser: { STOPPED: 'Остановлен', STARTING: 'Запускается', WAITING_AUTH: 'Ожидает входа', AUTHORIZED: 'Авторизован', AUTH_TEMPORARY: 'Проверяем вход', AUTH_LOST: 'Требуется вход', AUTH_CHECK_ERROR: 'Ошибка проверки входа', ERROR: 'Ошибка' },
  authorization: { UNKNOWN: 'Не проверена', UNCONFIRMED: 'Ожидает подтверждения', AUTHORIZED: 'Подтверждена', TEMPORARILY_UNCONFIRMED: 'Временно не подтверждена', AUTH_LOST: 'Потеряна', CHECK_ERROR: 'Ошибка проверки', AUTH_CHECK_ERROR: 'Ошибка проверки' },
  observation: { STOPPED: 'Остановлено', WAITING_AUTH: 'Ожидает авторизации', AUTH_TEMPORARY: 'Временно приостановлено', AUTH_LOST: 'Требуется проверить вход', AUTH_CHECK_ERROR: 'Повторная проверка входа', WAITING_GAME: 'Поиск игры', PAGE_LOADING: 'Загрузка игры', GAME_OPENED: 'Игра открыта', WAITING_CARDS: 'Ожидание карт Игрока', TWO_CARDS_VISIBLE: 'Обнаружены две карты', THREE_CARDS_VISIBLE: 'Обнаружены три карты', ROUND_FINISHED: 'Игра завершена', RETURNING_TO_LIST: 'Возврат к списку', NAVIGATION_ERROR: 'Ошибка перехода', DOM_ERROR: 'Ошибка чтения страницы', HISTORY_ERROR: 'Ошибка сохранения истории', ERROR: 'Ошибка' },
  level: { INFO: 'Информация', WARNING: 'Предупреждение', ERROR: 'Ошибка' },
}
Object.assign(names.observation, {
  WAITING_FOR_GAME: 'Ожидание игрового поля',
  WAITING_FOR_CARDS: 'Ожидание раскрытия карт',
  PARTIAL_CARDS: 'Раскрыта часть карт',
  CARDS_DETECTED: 'Карты обнаружены',
  WAITING_FOR_FINISH: 'Ожидание завершения раздачи',
  GAME_FINISHED: 'Раздача завершена',
  SAVED: 'Результат сохранён',
  INCOMPLETE: 'Неполный результат',
})
names.observation.CARDS_NOT_FOUND = 'Карты не обнаружены'
const label = (group, value) => names[group]?.[value] || value || '—'
const suits = { hearts: ['♥', 'черви'], diamonds: ['♦', 'бубны'], clubs: ['♣', 'трефы'], spades: ['♠', 'пики'] }
const predictionLabels = { WIN: 'WIN', LOSE: 'LOSE', PENDING: 'Ожидается', UNKNOWN: 'Неизвестно', SKIPPED: 'Пропуск' }
const isRedSuit = suit => ['hearts', 'diamonds'].includes(suit)
function formatObservedAt(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).format(date)
}

async function load() {
  try {
    const response = await fetch(`${api}/api/state`)
    if (!response.ok) throw new Error()
    applySnapshot(await response.json())
    error.value = ''
  } catch {
    state.value.backend = 'OFFLINE'
    error.value = 'Сервер недоступен. Проверьте, запущен ли backend.'
  }
}
let highestStateVersion = 0
function applySnapshot(next) {
  const version = Number(next?.state_version ?? 0)
  const nextEpoch = String(next?.state_epoch || '')
  const currentEpoch = String(state.value.state_epoch || '')
  if (!shouldApplySnapshot(version, highestStateVersion, nextEpoch, currentEpoch)) return false
  if (nextEpoch && currentEpoch && nextEpoch !== currentEpoch) highestStateVersion = 0
  highestStateVersion = version
  state.value = next
  return true
}
async function command(path, method = 'POST') {
  busy.value = true
  try {
    const response = await fetch(`${api}${path}`, { method })
    if (!response.ok) throw new Error()
    applySnapshot(await response.json())
    error.value = ''
  } catch {
    error.value = 'Не удалось выполнить команду. Проверьте соединение с сервером.'
  } finally {
    busy.value = false
  }
}
async function clearHistory() {
  if (!window.confirm('\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0432\u0441\u044e \u0438\u0441\u0442\u043e\u0440\u0438\u044e \u043d\u0430\u0431\u043b\u044e\u0434\u0435\u043d\u0438\u0439, \u043a\u0430\u0440\u0442\u044b, \u043f\u0440\u043e\u0433\u043d\u043e\u0437\u044b \u0438 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b WIN/LOSE?')) return
  await command('/api/observations', 'DELETE')
}
async function clearLogs() {
  await command('/api/logs', 'DELETE')
}
let timer
onMounted(() => { load(); timer = window.setInterval(load, 500) })
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <main>
    <header><p class="eyebrow">УПРАВЛЕНИЕ СЕАНСОМ</p><h1>BAKKARA BOT</h1><p>Ручной вход и чтение карт Игрока со страницы.</p></header>
    <section class="cards">
      <article><span>Сервер</span><strong :class="badge(state.backend)">{{ label('backend', state.backend) }}</strong></article>
      <article><span>Браузер</span><strong :class="badge(state.browser)">{{ label('browser', state.browser) }}</strong></article>
      <article><span>Авторизация</span><strong :class="badge(state.authorization)">{{ label('authorization', state.authorization) }}</strong></article>
    </section>
    <section class="controls"><button :disabled="busy || running" @click="command('/api/browser/start')">Запустить браузер</button><button class="secondary" :disabled="busy || !running" @click="command('/api/browser/stop')">Остановить браузер</button></section>
    <p class="diagnostic">{{ state.diagnostic }}</p><p v-if="error" class="error">{{ error }}</p>
    <section class="events observation">
      <div class="section-title"><h2>Текущее наблюдение</h2><strong :class="badge(state.observation?.status)">{{ label('observation', state.observation?.status) }}</strong></div>
      <div class="controls"><button :disabled="busy || !canObserve || observing" @click="command('/api/observation/start')">Начать сканирование</button><button class="secondary" :disabled="busy || !observing" @click="command('/api/observation/stop')">Остановить сканирование</button></div>
      <p class="diagnostic">{{ state.observation?.diagnostic }}</p>
      <template v-if="state.observation?.current">
        <p>Позиция в списке: {{ state.observation.current.position }} · Время: {{ state.observation.current.time || '—' }} · Статус на сайте: {{ state.observation.current.site_status || 'Не удалось определить' }}</p>
        <p>Счёт Игрок : Банкир — {{ state.observation.current.player_score || '—' }} : {{ state.observation.current.banker_score || '—' }} · Результат: {{ scanResultName(state.observation.current.scan_result) }}</p>
        <details><summary>Адрес страницы игры</summary><p>{{ state.observation.current.url }}</p></details>
        <p>Игра: {{ state.observation.current.game_id }} · Раздача: {{ state.observation.current.round_number || '—' }}</p>
        <p v-if="state.observation.current.outgoing_prediction || state.observation.current.incoming_prediction">Входящий прогноз: {{ suits[state.observation.current.incoming_prediction]?.[0] || '—' }} · Исходящий прогноз: {{ suits[state.observation.current.outgoing_prediction]?.[0] || '—' }}</p>
        <details v-if="state.observation.current.round_number_diagnostic"><summary>Проверка номера раздачи</summary><p>{{ state.observation.current.round_number_diagnostic }}</p></details>
        <ol><li v-for="(card, index) in state.observation.current.cards" :key="card.position || index">{{ card.position || index + 1 }}. {{ card.rank }} {{ suits[card.suit]?.[0] }} — {{ suits[card.suit]?.[1] }}</li></ol>
        <p>Раскрытых карт: {{ state.observation.current.cards_count }} · Нераскрытых позиций: {{ state.observation.current.pending_card_slots }}</p>
        <p v-if="state.observation.current.final_snapshot_reverified !== null && state.observation.current.final_snapshot_reverified !== undefined">Финальный набор карт перепроверен: {{ state.observation.current.final_snapshot_reverified ? 'да' : 'нет; сохранён предыдущий подтверждённый снимок' }}</p>
        <details v-if="state.observation.current.diagnostics"><summary>Диагностика DOM: {{ state.observation.current.diagnostics.cause }}</summary>
          <p>URL: {{ state.observation.current.diagnostics.url }} · документов/frame: {{ state.observation.current.diagnostics.frame_count }}</p>
          <pre>{{ JSON.stringify(state.observation.current.diagnostics.counts, null, 2) }}</pre>
          <div v-for="(frame, index) in state.observation.current.diagnostics.frames" :key="index"><p>Frame {{ index + 1 }}: {{ frame.url }}<span v-if="frame.error"> · ошибка: {{ frame.error }}</span></p><pre>{{ JSON.stringify(frame.counts || {}, null, 2) }}</pre><pre v-if="frame.fragment">{{ frame.fragment }}</pre></div>
        </details>
      </template>
      <p v-else>Текущая игра пока не выбрана.</p>
      <details v-if="state.observation?.games?.length"><summary>Проверенные игры: {{ state.observation.games.length }}</summary><ol><li v-for="game in state.observation.games" :key="game.game_id">№ {{ game.game_id }} · {{ game.time }} · {{ game.reason }}</li></ol></details>
      <h3>Эффективность прогнозов</h3>
      <div class="prediction-stats" aria-label="Статистика проверенных прогнозов">
        <article><span>Проверено</span><strong>{{ statistics.checked_count ?? 0 }}</strong></article>
        <article><span>WIN</span><strong class="win-text">{{ statistics.wins ?? 0 }}</strong></article>
        <article><span>LOSE</span><strong class="lose-text">{{ statistics.losses ?? 0 }}</strong></article>
        <article><span>Процент WIN</span><strong>{{ statistics.win_rate == null ? 'Нет данных' : `${statistics.win_rate}%` }}</strong></article>
        <article><span>Серия WIN</span><strong>{{ statistics.current_win_streak ?? 0 }}</strong></article>
        <article><span>Серия LOSE</span><strong>{{ statistics.current_lose_streak ?? 0 }}</strong></article>
        <article><span>Макс. серия LOSE</span><strong>{{ statistics.max_lose_streak ?? 0 }}</strong></article>
      </div>
      <div class="section-title history-heading"><h3>История наблюдений</h3><button class="secondary" :disabled="busy" @click="clearHistory">Очистить историю</button></div><p v-if="!state.observation?.history?.length">Сохранённых наблюдений пока нет.</p>
      <div v-else class="history-table-scroll">
        <table class="history-table" aria-label="История наблюдений">
          <thead><tr><th scope="col">Время</th><th scope="col">№ раздачи</th><th scope="col">Карты Игрока</th><th scope="col">Прогноз на следующую</th><th scope="col">WIN / LOSE</th></tr></thead>
          <tbody>
            <tr v-for="item in state.observation.history" :key="item.game_id">
              <td class="history-time">{{ formatObservedAt(item.observed_at || item.last_scanned_at || item.discovered_at) }}</td>
              <td class="history-round"><strong><a v-if="item.game_url || item.url" :href="item.game_url || item.url" target="_blank" rel="noopener noreferrer" :title="`Открыть игру ${item.game_id}`">{{ item.round_number || '—' }}</a><span v-else>{{ item.round_number || '—' }}</span></strong></td>
              <td><div v-if="item.player_cards?.length" class="history-card-line"><span v-for="(card, index) in item.player_cards" :key="card.position || index" class="history-card" :class="isRedSuit(card.suit) ? 'red-suit' : 'black-suit'">{{ card.rank }}{{ suits[card.suit]?.[0] || '' }}</span></div><span v-else class="prediction-none">—</span></td>
              <td><span v-if="item.outgoing_prediction" class="calculated-suit" :class="isRedSuit(item.outgoing_prediction) ? 'red-suit' : 'black-suit'" :title="`Для следующей игры. Входящий прогноз этой строки: ${suits[item.incoming_prediction]?.[1] || 'нет'}; результат WIN/LOSE относится к нему.`">{{ suits[item.outgoing_prediction]?.[0] || '—' }}</span><span v-else class="prediction-none" :title="`Входящий прогноз: ${suits[item.incoming_prediction]?.[1] || 'нет'}`">—</span></td>
              <td><span v-if="item.prediction_result" class="prediction-result" :class="`prediction-${item.prediction_result.toLowerCase()}`">{{ predictionLabels[item.prediction_result] || item.prediction_result }}</span><span v-else class="prediction-none">—</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section class="events"><div class="section-title"><h2>События</h2><button class="secondary" :disabled="busy" @click="clearLogs">Очистить логи</button></div><p v-if="!state.events.length">Событий пока нет.</p><ol v-else><li v-for="event in state.events" :key="event.at"><time>{{ new Date(event.at).toLocaleTimeString('ru-RU') }}</time><b>{{ label('level', event.level) }}</b> {{ event.message }}</li></ol></section>
  </main>
</template>

<style scoped>
.history-table-scroll {
  max-height: min(56vh, 620px);
  overflow: auto;
  border: 1px solid #263753;
  border-radius: 8px;
}

.history-table {
  width: 100%;
  min-width: 700px;
  border-collapse: collapse;
  text-align: left;
}

.history-table th,
.history-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #263753;
  vertical-align: middle;
}

.history-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  color: #aebed4;
  background: #131f32;
  font-size: .82rem;
}

.history-table tbody tr:last-child td { border-bottom: 0; }
.history-heading { margin-top: 24px; }
.history-heading h3 { margin: 0; }
.history-time,.history-round,.calculated-suit,.prediction-result { white-space: nowrap; }
.history-round a { color: #64d7ff; text-decoration: none; }
.history-round a:hover { text-decoration: underline; }
.history-card-line { display: flex; flex-wrap: nowrap; gap: 9px; white-space: nowrap; }
.history-card { font-weight: 700; }
.red-suit { color: #ff687d; }
.black-suit { color: #edf4ff; }
.calculated-suit { display: inline-block; font-size: 1.35rem; font-weight: 800; }
.history-table small { display: inline-block; max-width: 300px; }
.prediction-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 9px; margin: 12px 0 20px; }
.prediction-stats article { display: grid; gap: 6px; padding: 12px; border: 1px solid #263753; border-radius: 8px; background: #131f32; }
.prediction-stats span { color: #aebed4; font-size: .78rem; }
.prediction-stats strong { font-size: 1.12rem; }
.prediction-result { display: inline-flex; min-width: 78px; justify-content: center; padding: 5px 9px; border-radius: 999px; font-size: .82rem; font-weight: 800; }
.prediction-win,.win-text { color: #73e2a2; }
.prediction-lose,.lose-text { color: #ff687d; }
.prediction-win { background: #123728; }
.prediction-lose { background: #421d2a; }
.prediction-pending { color: #d8e1ee; background: #29364b; }
.prediction-unknown,.prediction-skipped { color: #aeb8c8; background: #303846; }
.prediction-none { color: #8795a9; }
</style>
