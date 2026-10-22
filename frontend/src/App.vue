<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { shouldApplySnapshot } from './stateVersion.js'
import { calculateLadder } from './stakeCalculator.js'

const api = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8010'
const scanResultNames = {
  SCANNING: '\u0421\u043a\u0430\u043d\u0438\u0440\u0443\u0435\u0442\u0441\u044f',
  CARDS_CONFIRMED: '\u041a\u0430\u0440\u0442\u044b \u0441\u0447\u0438\u0442\u0430\u043d\u044b, \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0438\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e',
  CARDS_FOUND: '\u041a\u0430\u0440\u0442\u044b \u0441\u0447\u0438\u0442\u0430\u043d\u044b',
  CARDS_NOT_FOUND: '\u041a\u0430\u0440\u0442\u044b \u043d\u0435 \u043e\u0431\u043d\u0430\u0440\u0443\u0436\u0435\u043d\u044b',
  PLAYER_FIELD_NOT_FOUND: '\u041f\u043e\u043b\u0435 \u0418\u0433\u0440\u043e\u043a\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e',
  PLAYER_CARDS_CONTAINER_NOT_FOUND: '\u041d\u0435\u0442 \u043a\u043e\u043d\u0442\u0435\u0439\u043d\u0435\u0440\u0430 \u043a\u0430\u0440\u0442 \u0418\u0433\u0440\u043e\u043a\u0430',
  SCAN_TIMEOUT: 'Время ожидания карт истекло — сканирование продолжается',
  CARDS_UNCONFIRMED: '\u0427\u0430\u0441\u0442\u044c \u0441\u043b\u043e\u0442\u043e\u0432 \u043d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430',
  NAVIGATION_ERROR: '\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043a\u0440\u044b\u0442\u044c \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443',
  HISTORY_ERROR: '\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442',
}
const scanResultName = value => scanResultNames[value] || value || '\u2014'
const state = ref({ state_epoch: '', state_version: 0, backend: 'CHECKING', browser: 'STOPPED', authorization: 'UNKNOWN', diagnostic: '', events: [], observation: { status: 'STOPPED', current: null, history: [], games: [] } })
const busy = ref(false)
const error = ref('')
const cycle18 = computed(() => state.value.cycle18 || {})
const cycleStats = computed(() => cycle18.value.statistics || {})
const currentForecast = computed(() => cycle18.value.ready
  ? cycle18.value.live_preview?.find(item => item.phase === 'CURRENT') : null)
const nextForecast = computed(() => cycle18.value.ready
  ? cycle18.value.live_preview?.find(item => item.phase === 'NEXT') : null)
const initialStake = ref(10)
const odds = ref(1.90)
const steps = ref(6)
const stakeMode = ref('recover')
const savedCalculatorKey = 'bakkara.calculator.settings.v2'
function restoreCalculatorSettings() {
  try {
    const currentSettings = window.localStorage.getItem(savedCalculatorKey)
    const raw = JSON.parse(currentSettings || window.localStorage.getItem('bakkara.calculator.settings.v1') || 'null')
    if (!raw || typeof raw !== 'object') return
    if (Number.isFinite(Number(raw.stake)) && Number(raw.stake) > 0) initialStake.value = Number(raw.stake)
    if (Number.isFinite(Number(raw.odds)) && Number(raw.odds) > 1) odds.value = Number(raw.odds)
    if (Number.isInteger(Number(raw.steps)) && Number(raw.steps) >= 1 && Number(raw.steps) <= 20) steps.value = Number(raw.steps)
    if (currentSettings && ['double', 'recover'].includes(raw.mode)) stakeMode.value = raw.mode
  } catch { /* Private browsing and restricted storage must not break the calculator. */ }
}
watch([initialStake, odds, steps, stakeMode], () => {
  try {
    window.localStorage.setItem(savedCalculatorKey, JSON.stringify({
      stake: initialStake.value, odds: odds.value, steps: steps.value, mode: stakeMode.value,
    }))
  } catch { /* Storage is optional. */ }
})
const ladder = computed(() => calculateLadder({
  stake: initialStake.value, odds: odds.value, steps: steps.value, mode: stakeMode.value,
}))
// Simulation only: results come from the verified cycle; amounts are recalculated
// locally from the current calculator settings and were never placed as bets.
const virtualBetRows = computed(() => (cycle18.value.recent || [])
  .filter(item => ['WIN', 'LOSE'].includes(item.result))
  .map(item => {
    const step = ladder.value?.rows.find(row => row.step === Number(item.step))
    const virtualStake = step?.stake ?? null
    const virtualNet = virtualStake === null ? null : item.result === 'WIN'
      ? Math.round(virtualStake * (Number(odds.value) - 1) * 100) / 100 : -virtualStake
    return { ...item, virtualStake, virtualNet }
  }))
const money = amount => new Intl.NumberFormat('ru-RU', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
}).format(Number(amount) || 0) + ' ₽'
const historyRows = ref([])
const historyLoaded = ref(false)
const historySearch = ref('')
const fullHistory = computed(() => historyLoaded.value
  ? historyRows.value : (state.value.observation?.history || []))
const filteredHistory = computed(() => {
  const term = historySearch.value.trim().toLowerCase()
  return term ? fullHistory.value.filter(item =>
    String(item.round_number || '').includes(term)
    || String(item.game_id || '').toLowerCase().includes(term)
    || (item.player_cards || []).some(card =>
      (card.rank + (suits[card.suit]?.[0] || '')).toLowerCase().includes(term))
  ) : fullHistory.value
})
async function refreshHistory() {
  try {
    const response = await fetch(`${api}/api/history?limit=200`)
    if (!response.ok) return
    const rows = await response.json()
    if (!Array.isArray(rows)) return
    historyRows.value = rows
    historyLoaded.value = true
  } catch { /* Live state stays functional even if history API is temporarily unavailable. */ }
}
const outcomesById = computed(() => {
  const outcomes = new Map()
  for (const result of cycle18.value.recent || []) outcomes.set(result.target_game_id, result)
  return outcomes
})
const outcomeFor = gameId => cycle18.value.ready ? outcomesById.value.get(gameId) : null
const outcomeLabel = result => result === 'WIN' || result === 'LOSE' ? result : result === 'PENDING' ? 'Ожидаем итог' : '—'
const badge = value => String(value || 'UNKNOWN').toLowerCase().replaceAll('_', '-')
const running = computed(() => !['STOPPED', 'ERROR'].includes(state.value.browser))
const observing = computed(() => !['STOPPED', 'ERROR'].includes(state.value.observation?.status))
const canObserve = computed(() => ['AUTHORIZED', 'TEMPORARILY_UNCONFIRMED'].includes(state.value.authorization))
const names = {
  backend: { CHECKING: 'Проверяется', OK: 'Работает', OFFLINE: 'Недоступен' },
  browser: { ACCESS_DENIED: 'Доступ запрещён сайтом', STOPPED: 'Остановлен', STARTING: 'Запускается', WAITING_AUTH: 'Ожидает входа', AUTHORIZED: 'Авторизован', AUTH_TEMPORARY: 'Проверяем вход', AUTH_LOST: 'Требуется вход', AUTH_CHECK_ERROR: 'Ошибка проверки входа', ERROR: 'Ошибка' },
  authorization: { ACCESS_DENIED: 'Доступ запрещён сайтом', UNKNOWN: 'Не проверена', UNCONFIRMED: 'Ожидает подтверждения', AUTHORIZED: 'Подтверждена', TEMPORARILY_UNCONFIRMED: 'Временно не подтверждена', AUTH_LOST: 'Потеряна', CHECK_ERROR: 'Ошибка проверки', AUTH_CHECK_ERROR: 'Ошибка проверки' },
  observation: { ACCESS_DENIED: 'Доступ запрещён сайтом', STOPPED: 'Остановлено', WAITING_AUTH: 'Ожидает авторизации', AUTH_TEMPORARY: 'Временно приостановлено', AUTH_LOST: 'Требуется проверить вход', AUTH_CHECK_ERROR: 'Повторная проверка входа', WAITING_GAME: 'Поиск игры', PAGE_LOADING: 'Загрузка игры', GAME_OPENED: 'Игра открыта', WAITING_CARDS: 'Ожидание карт Игрока', TWO_CARDS_VISIBLE: 'Обнаружены две карты', THREE_CARDS_VISIBLE: 'Обнаружены три карты', ROUND_FINISHED: 'Игра завершена', RETURNING_TO_LIST: 'Возврат к списку', NAVIGATION_ERROR: 'Ошибка перехода', DOM_ERROR: 'Ошибка чтения страницы', HISTORY_ERROR: 'Ошибка сохранения истории', ERROR: 'Ошибка' },
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
  RECOVERING: 'Восстановление после ошибки',
})
names.observation.CARDS_NOT_FOUND = 'Карты не обнаружены'
const label = (group, value) => names[group]?.[value] || value || '—'
const suits = { hearts: ['♥', 'черви'], diamonds: ['♦', 'бубны'], clubs: ['♣', 'трефы'], spades: ['♠', 'пики'] }
const predictionLabels = { WIN: 'WIN', LOSE: 'LOSE', PENDING: 'Ожидаем итог', UNKNOWN: 'Нет финальных карт', SKIPPED: 'Нет результата' }
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
  historyRows.value = []
  historyLoaded.value = true
  await refreshHistory()
}
async function clearLogs() {
  await command('/api/logs', 'DELETE')
}
let timer
let historyTimer
onMounted(() => {
  restoreCalculatorSettings()
  load()
  refreshHistory()
  timer = window.setInterval(load, 500)
  historyTimer = window.setInterval(refreshHistory, 10000)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  clearInterval(historyTimer)
})
</script>

<template>
  <main>
    <header class="dashboard-header">
      <div><p class="eyebrow">BAKKARA / LIVE ANALYTICS</p><h1>Панель наблюдения <span class="header-spark">✦</span></h1><p>Карты Игрока, прогнозы цикла 18 и расчёт виртуального бюджета.</p></div>
      <nav class="quick-nav" aria-label="Разделы панели">
        <a href="#observation">Наблюдение</a>
        <a href="#calculator">Калькулятор</a>
        <a href="#history">История</a>
        <a href="#events">События</a>
      </nav>
    </header>
    <section class="cards">
      <article><span>Сервер</span><strong :class="badge(state.backend)">{{ label('backend', state.backend) }}</strong></article>
      <article><span>Браузер</span><strong :class="badge(state.browser)">{{ label('browser', state.browser) }}</strong></article>
      <article><span>Авторизация</span><strong :class="badge(state.authorization)">{{ label('authorization', state.authorization) }}</strong></article>
    </section>
    <section class="controls"><button :disabled="busy || running" @click="command('/api/browser/start')">Запустить браузер</button><button class="secondary" :disabled="busy || !running" @click="command('/api/browser/stop')">Остановить браузер</button></section>
    <p class="diagnostic">{{ state.diagnostic }}</p><p v-if="error" class="error">{{ error }}</p>
    <section class="events forecast-strip" id="forecasts" aria-label="Прогнозы следующих раздач">
      <div class="section-title"><div><p class="eyebrow">LIVE / ПРОГНОЗЫ</p><h2>Текущая и следующая раздачи</h2></div><span class="standalone-label">{{ cycle18.ready ? 'Цикл N−18 активен' : 'Подготовка прогноза' }}</span></div>
        <div v-if="cycle18.ready" class="forecast-pair" aria-label="Текущая и следующая раздачи">
          <article class="forecast-tile" data-testid="forecast-current">
            <div class="forecast-tile-head"><span>Текущая раздача</span><span class="forecast-round">№{{ currentForecast?.target_round_number || state.observation?.current?.round_number || '—' }}</span></div>
            <div v-if="currentForecast?.available" class="forecast-suit" :class="isRedSuit(currentForecast.predicted_suit) ? 'red-suit' : 'black-suit'">
              {{ suits[currentForecast.predicted_suit]?.[0] }} <small>{{ suits[currentForecast.predicted_suit]?.[1] }}</small>
            </div>
            <div v-else class="forecast-unavailable">Нет подтверждённого источника</div>
            <p>Масть из раздачи №{{ currentForecast?.source_round_number || '—' }} (N−18)</p>
          </article>
          <article class="forecast-tile" data-testid="forecast-next">
            <div class="forecast-tile-head"><span>Следующая раздача</span><span class="forecast-round">№{{ nextForecast?.target_round_number || '—' }}</span></div>
            <div v-if="nextForecast?.available" class="forecast-suit" :class="isRedSuit(nextForecast.predicted_suit) ? 'red-suit' : 'black-suit'">
              {{ suits[nextForecast.predicted_suit]?.[0] }} <small>{{ suits[nextForecast.predicted_suit]?.[1] }}</small>
            </div>
            <div v-else class="forecast-unavailable">Нет подтверждённого источника</div>
            <p>Масть из раздачи №{{ nextForecast?.source_round_number || '—' }} (N−18)</p>
          </article>
        </div>
        <p v-if="cycle18.ready && !currentForecast" class="diagnostic">Ожидаем выбора текущей игры с действительным номером; прогнозы нельзя определить без N.</p>
        <p v-if="!cycle18.ready" class="diagnostic">Прогнозы двух раздач появятся только после подтверждения 20 игр.</p>
    </section>
    <section class="events calculator-panel" id="calculator" aria-label="Калькулятор догона">
      <div class="section-title"><h2>Калькулятор догона</h2><span class="standalone-label">Без ставок</span></div>
      <p class="diagnostic">Горизонтальная лестница · изменение коэффициента пересчитывает следующие суммы в режиме покрытия потерь. Не влияет на работу бота.</p>
      <div class="calculator-fields">
        <label class="field">Начальная ставка, ₽
          <input v-model.number="initialStake" type="number" min="0.01" step="0.01" inputmode="decimal" />
        </label>
        <label class="field">Коэффициент
          <input v-model.number="odds" type="number" min="1.01" step="0.01" inputmode="decimal" />
        </label>
        <label class="field">Количество шагов
          <input v-model.number="steps" type="number" min="1" max="20" step="1" inputmode="numeric" />
        </label>
        <label class="field">Расчёт суммы
          <select v-model="stakeMode">
            <option value="recover">Покрытие потерь и прибыли · зависит от кф</option>
            <option value="double">Фиксированное ×2 · не зависит от кф</option>
          </select>
        </label>
      </div>
      <template v-if="ladder">
        <div class="calculator-overview">
          <div class="bankroll-summary">
            <span>Бюджет на все {{ ladder.rows.length }} шагов</span>
            <strong>{{ money(ladder.totalBankroll) }}</strong>
            <small>Потери, если проиграют все шаги</small>
          </div>
          <div class="calculator-meta"><span>Целевая прибыль первого шага</span><strong>{{ money(ladder.targetNetProfit) }}</strong></div>
          <div class="calculator-meta"><span>Безубыточная доля WIN</span><strong>{{ ladder.breakEvenChance.toFixed(2) }}%</strong></div>
        </div>
        <p v-if="stakeMode === 'double'" class="diagnostic">Режим ×2 фиксированный: изменение коэффициента влияет на прибыль, но не на суммы шагов. Для пересчёта следующих сумм выберите «Покрытие потерь и прибыли».</p>
        <p v-if="ladder.anyNegativeProfit" class="calculator-warning">При выбранном коэффициенте WIN на некоторых шагах не покрывает предыдущие проигрыши.</p>
        <div class="step-strip" role="list" aria-label="Горизонтальная лестница ставок">
          <div v-for="row in ladder.rows" :key="row.step" class="step-column" role="listitem">
            <strong class="step-title">Шаг {{ row.step }}</strong>
            <span>Ставка</span><strong>{{ money(row.stake) }}</strong>
            <span>Бюджет до шага</span><strong>{{ money(row.bankThroughStep) }}</strong>
            <span>Итог при WIN</span><strong :class="row.netIfWin < 0 ? 'lose-text' : 'win-text'">{{ money(row.netIfWin) }}</strong>
          </div>
        </div>
        <p class="diagnostic">Расчёт предполагает постоянный коэффициент и последовательные LOSE до выигрышного шага. Фактическая вероятность выигрыша не определяется калькулятором.</p>
      </template>
      <p v-else class="calculator-warning">Введите ставку больше нуля, коэффициент больше 1 и от 1 до 20 шагов.</p>
    </section>
    <section class="events virtual-history" id="virtual-bets" aria-label="История виртуальных ставок">
      <div class="section-title"><div><h2>История виртуальных ставок</h2><p class="diagnostic">Моделирование по проверенным прогнозам цикла 18 · {{ virtualBetRows.length }} результатов</p></div><span class="standalone-label">Только виртуально</span></div>
      <p class="diagnostic">Ставка и финансовый итог вычисляются по текущим настройкам калькулятора, а не берутся из истории реальных ставок. При изменении коэффициента суммы здесь также изменятся. Результаты WIN/LOSE получены из наблюдений.</p>
      <p v-if="!cycle18.ready" class="diagnostic">История появится после подготовки цикла и проверки прогнозов.</p>
      <p v-else-if="!virtualBetRows.length" class="diagnostic">Проверенных виртуальных результатов пока нет.</p>
      <div v-else class="history-table-scroll virtual-history-scroll">
        <table class="history-table" aria-label="История виртуальных ставок цикла 18">
          <thead><tr><th>Целевая игра</th><th>Источник N−18</th><th>Прогноз</th><th>Шаг</th><th>Виртуальная ставка</th><th>Коэффициент</th><th>Результат</th><th>Виртуальный итог</th><th>Тип проверки</th></tr></thead>
          <tbody><tr v-for="item in virtualBetRows" :key="item.source_game_id">
            <td>№{{ item.target_round_number || '—' }}</td>
            <td>№{{ item.source_round_number || '—' }}</td>
            <td :class="isRedSuit(item.predicted_suit) ? 'red-suit' : 'black-suit'">{{ suits[item.predicted_suit]?.[0] || '—' }}</td>
            <td>{{ item.step ?? '—' }}</td>
            <td>{{ item.virtualStake === null ? 'За пределами лестницы' : money(item.virtualStake) }}</td>
            <td>{{ item.virtualStake === null ? '—' : Number(odds).toFixed(2) }}</td>
            <td><strong :class="item.result === 'WIN' ? 'win-text' : 'lose-text'">{{ item.result }}</strong></td>
            <td :class="item.virtualNet !== null ? (item.virtualNet >= 0 ? 'win-text' : 'lose-text') : ''">{{ item.virtualNet === null ? '—' : money(item.virtualNet) }}</td>
            <td>{{ item.mode === 'BACKFILL' ? 'По истории' : 'Наблюдение' }}</td>
          </tr></tbody>
        </table>
      </div>
    </section>
    <section class="events observation" id="observation">
      <div class="section-title"><h2>Текущее наблюдение</h2><strong :class="badge(state.observation?.status)">{{ label('observation', state.observation?.status) }}</strong></div>
      <div class="controls"><button :disabled="busy || !canObserve || observing" @click="command('/api/observation/start')">Начать сканирование</button><button class="secondary" :disabled="busy || !observing" @click="command('/api/observation/stop')">Остановить сканирование</button></div>
      <p class="diagnostic">{{ state.observation?.diagnostic }}</p>
      <template v-if="state.observation?.current">
        <p>Позиция в списке: {{ state.observation.current.position }} · Время: {{ state.observation.current.time || '—' }} · Статус на сайте: {{ state.observation.current.site_status || 'Не удалось определить' }}</p>
        <p>Счёт Игрок : Банкир — {{ state.observation.current.player_score || '—' }} : {{ state.observation.current.banker_score || '—' }} · Результат: {{ scanResultName(state.observation.current.scan_result) }}</p>
        <p v-if="state.observation.current.round_timer_text !== undefined">
          Таймер раздачи: <strong>{{ state.observation.current.round_timer_text || 'не определён' }}</strong>
          · Отслеживаем завершение и возможную третью карту Игрока.
        </p>
        <details><summary>Адрес страницы игры</summary><p>{{ state.observation.current.url }}</p></details>
        <p>Игра: {{ state.observation.current.game_id }} · Раздача: {{ state.observation.current.round_number || '—' }}</p>
        <p v-if="cycle18.ready && outcomeFor(state.observation.current.game_id)">Цикл N−18: {{ suits[outcomeFor(state.observation.current.game_id).predicted_suit]?.[0] || '—' }} · {{ outcomeLabel(outcomeFor(state.observation.current.game_id).result) }}</p>
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
      <section class="cycle18-panel" aria-label="Цикл 18 мастей Игрока">
        <div class="section-title">
          <h3>Цикл 18 · прогноз масти Игрока</h3>
          <strong>{{ cycle18.ready ? 'Готов к прогнозированию' : 'Сбор истории' }}</strong>
        </div>
        <p class="diagnostic">Каждый запуск наблюдения требует 20 уникальных финально подтверждённых раздач Игрока. Пропуски не обнуляют собранные карты и не сдвигают источник прогноза N−18. Для целевой игры №N берём расчётную масть из №N−18. Реальные ставки бот не делает.</p>
        <div class="warmup">
          <div class="section-title"><strong>Подтверждено за запуск: {{ cycle18.warmup_count ?? 0 }} / {{ cycle18.warmup_required ?? 20 }}</strong><span>{{ cycle18.ready ? 'Цикл 18 активен' : 'Только сбор карт' }}</span></div>
          <progress :value="cycle18.warmup_count ?? 0" :max="cycle18.warmup_required ?? 20"></progress>
          <p v-if="!cycle18.ready" class="diagnostic">Первые 20 уникальных подтверждённых раздач: только чтение и запись карт Игрока, без WIN/LOSE. Если сайт пропустил игру, счётчик не сбрасывается. После 20-й подтверждённой игры запускается цикл N−18 по реальным номерам (прогноз возможен лишь при наличии исходной игры N−18).</p>
          <p v-else class="diagnostic">Подготовка завершена. Прогноз для №N берётся из подтверждённой раздачи №N−18.</p>
        </div>
        <p class="diagnostic">
          Всего обнаружено игр: <strong>{{ cycle18.discovered_count ?? 0 }}</strong>.
          Игр с прочитанными картами: <strong>{{ cycle18.captured_count ?? 0 }}</strong>.
          Финальные руки: <strong>{{ cycle18.verified_count ?? 0 }}</strong>.
          Ожидают подтверждения карт или начала: <strong>{{ cycle18.missing_count ?? 0 }}</strong>.
          Раздачи без подтверждения остаются в истории и не сдвигают цикл. Карты соседних игр не подменяют отсутствующие.
        </p>
        <details v-if="cycle18.missing_hands?.length">
          <summary>Раздачи без финального подтверждения ({{ cycle18.missing_count }})</summary>
          <p v-for="item in cycle18.missing_hands" :key="item.game_id">
            Раздача №{{ item.round_number || 'номер ещё не получен' }} ·
            ID {{ item.game_id }} · {{ scanResultName(item.scan_result) }}
            · карт: {{ item.cards_count ?? 0 }}
            <span v-if="item.reason"> · {{ item.reason }}</span>
          </p>
        </details>
        <div v-if="cycle18.ready" class="prediction-stats">
          <article><span>Проверено (цикл 18)</span><strong>{{ cycleStats.checked_count ?? 0 }}</strong></article>
          <article><span>WIN</span><strong class="win-text">{{ cycleStats.wins ?? 0 }}</strong></article>
          <article><span>LOSE</span><strong class="lose-text">{{ cycleStats.losses ?? 0 }}</strong></article>
          <article><span>Точность</span><strong>{{ cycleStats.win_rate == null ? '—' : cycleStats.win_rate + '%' }}</strong></article>
          <article><span>Онлайн-проверки</span><strong>{{ cycleStats.live_checked ?? 0 }}</strong></article>
          <article><span>Постфактум</span><strong>{{ cycleStats.backfill_checked ?? 0 }}</strong></article>
          <article><span>Максимум LOSE подряд</span><strong>{{ cycleStats.max_lose_streak ?? 0 }}</strong></article>
          <article><span>Виртуальный следующий шаг</span><strong>{{ cycleStats.next_step ?? 1 }}</strong></article>
        </div>
        <details v-if="cycle18.ready && Object.keys(cycleStats.wins_by_step || {}).length"><summary>Победы по шагам (виртуально)</summary><p v-for="(count, step) in cycleStats.wins_by_step" :key="step">Шаг {{ step }}: {{ count }} WIN</p></details>
        <details v-if="cycle18.ready && cycle18.recent?.length">
          <summary>История прогнозов цикла 18 ({{ cycle18.recent.length }})</summary>
          <div class="history-table-scroll">
            <table class="history-table" aria-label="Прогнозы цикла 18">
              <thead><tr><th>Источник</th><th>Целевая игра</th><th>Прогноз</th><th>Результат</th><th>Шаг</th><th>Проверка</th></tr></thead>
              <tbody><tr v-for="forecast in cycle18.recent" :key="forecast.source_game_id">
                <td>№{{ forecast.source_round_number }}</td><td>№{{ forecast.target_round_number }}</td>
                <td :class="isRedSuit(forecast.predicted_suit) ? 'red-suit' : 'black-suit'">{{ suits[forecast.predicted_suit]?.[0] || '—' }}</td>
                <td><strong :class="forecast.result === 'WIN' ? 'win-text' : forecast.result === 'LOSE' ? 'lose-text' : ''">{{ predictionLabels[forecast.result] || forecast.result }}</strong></td>
                <td>{{ forecast.step ?? '—' }}</td>
                <td>{{ forecast.mode === 'BACKFILL' ? 'По истории' : 'Наблюдение' }}</td>
              </tr></tbody>
            </table>
          </div>
        </details>
      </section>
    </section>

    <section class="events history-panel" id="history">
      <div class="section-title history-heading"><div><h2>История раздач</h2><p class="diagnostic">Последние {{ fullHistory.length }} подтверждённых раздач · доступно до 200 записей.</p></div><div class="history-actions"><button class="secondary" @click="refreshHistory">Обновить</button><button class="secondary" :disabled="busy" @click="clearHistory">Очистить историю</button></div></div>
      <label class="field history-search">Поиск по номеру, ID или картам
        <input v-model="historySearch" type="search" placeholder="Например: 600 или A♥" />
      </label>
      <p v-if="!fullHistory.length">Сохранённых наблюдений пока нет.</p>
      <p v-else-if="!filteredHistory.length" class="diagnostic">По указанному запросу раздач не найдено.</p>
      <div v-else class="history-table-scroll history-full-scroll">
        <table class="history-table" aria-label="История наблюдений">
          <thead><tr><th scope="col">Время</th><th scope="col">№ раздачи</th><th scope="col">Карты Игрока</th><th scope="col">Расчётная масть</th><th v-if="cycle18.ready" scope="col">WIN / LOSE (цикл 18)</th></tr></thead>
          <tbody>
            <tr v-for="item in filteredHistory" :key="item.game_id">
              <td class="history-time">{{ formatObservedAt(item.observed_at || item.last_scanned_at || item.discovered_at) }}</td>
              <td class="history-round"><strong><a v-if="item.game_url || item.url" :href="item.game_url || item.url" target="_blank" rel="noopener noreferrer" :title="`Открыть игру ${item.game_id}`">{{ item.round_number || '—' }}</a><span v-else>{{ item.round_number || '—' }}</span></strong></td>
              <td><div v-if="item.player_cards?.length" class="history-card-line"><span v-for="(card, index) in item.player_cards" :key="card.position || index" class="history-card" :class="isRedSuit(card.suit) ? 'red-suit' : 'black-suit'">{{ card.rank }}{{ suits[card.suit]?.[0] || '' }}</span></div><span v-else class="prediction-none">—</span></td>
              <td><span v-if="item.calculated_suit" class="calculated-suit" :class="isRedSuit(item.calculated_suit) ? 'red-suit' : 'black-suit'" title="Масть, рассчитанная из карт этой раздачи">{{ suits[item.calculated_suit]?.[0] || '—' }}</span><span v-else class="prediction-none">—</span></td>
              <td v-if="cycle18.ready"><span v-if="outcomeFor(item.game_id) && ['WIN', 'LOSE'].includes(outcomeFor(item.game_id).result)" class="prediction-result" :class="`prediction-${outcomeFor(item.game_id).result.toLowerCase()}`" :title="`Цикл N−18: прогноз ${suits[outcomeFor(item.game_id).predicted_suit]?.[1] || '—'}`">{{ outcomeFor(item.game_id).result }}</span><span v-else-if="outcomeFor(item.game_id)?.result === 'PENDING'" class="prediction-pending">Ожидаем карт</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <section class="events" id="events"><div class="section-title"><h2>События</h2><button class="secondary" :disabled="busy" @click="clearLogs">Очистить логи</button></div><p v-if="!state.events.length">Событий пока нет.</p><ol v-else><li v-for="event in state.events" :key="event.at"><time>{{ new Date(event.at).toLocaleTimeString('ru-RU') }}</time><b>{{ label('level', event.level) }}</b> {{ event.message }}</li></ol></section>
  </main>
</template>

<style scoped>
.cycle18-panel {
  margin: 22px 0 0;
  padding: 22px 0 0;
  border-top: 1px solid #263753;
}
.cycle18-panel h3 { margin: 0; }
.warmup { display: grid; gap: 10px; margin: 16px 0; }
.warmup progress { display: block; width: 100%; height: 9px; accent-color: #73e2a2; }
.warmup .section-title { margin: 0; }
.history-table-scroll {
  width: 100%;
  max-height: min(56vh, 620px);
  overflow: auto;
  border-top: 1px solid #263753;
  border-bottom: 1px solid #263753;
}
.history-table { width: 100%; min-width: 750px; border-collapse: collapse; text-align: left; }
.history-table th, .history-table td {
  padding: 11px 13px;
  border-bottom: 1px solid #263753;
  vertical-align: middle;
}
.history-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  color: #aebed4;
  background: #101b2d;
  font-size: .81rem;
  font-weight: 650;
}
.history-table tbody tr:last-child td { border-bottom: 0; }
.history-table tbody tr:hover { background: #142338; }
.history-heading { margin-top: 0; }
.history-heading h3 { margin: 0; }
.history-time, .history-round, .calculated-suit, .prediction-result { white-space: nowrap; }
.history-round a { color: #64d7ff; text-decoration: none; }
.history-round a:hover { text-decoration: underline; }
.history-card-line { display: flex; flex-wrap: nowrap; gap: 9px; white-space: nowrap; }
.history-card { font-weight: 700; }
.red-suit { color: #ff687d; }
.black-suit { color: #edf4ff; }
.calculated-suit { display: inline-block; font-size: 1.35rem; font-weight: 800; }
.history-table small { display: inline-block; max-width: 300px; }
.prediction-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  margin: 18px 0 24px;
  border-top: 1px solid #263753;
  border-bottom: 1px solid #263753;
}
.prediction-stats article {
  display: grid;
  gap: 6px;
  min-width: 132px;
  flex: 1 1 132px;
  padding: 12px 18px;
  border-right: 1px solid #263753;
}
.prediction-stats article:first-child { padding-left: 0; }
.prediction-stats article:last-child { border-right: 0; }
.prediction-stats span { color: #aebed4; font-size: .78rem; }
.prediction-stats strong { font-size: 1.12rem; }
.prediction-result {
  display: inline-flex;
  min-width: 78px;
  justify-content: center;
  padding: 5px 9px;
  border-radius: 999px;
  font-size: .82rem;
  font-weight: 800;
}
.prediction-win, .win-text { color: #73e2a2; }
.prediction-lose, .lose-text { color: #ff687d; }
.prediction-win { background: #123728; }
.prediction-lose { background: #421d2a; }
.prediction-pending { color: #d8e1ee; background: #29364b; }
.prediction-unknown, .prediction-skipped { color: #aeb8c8; background: #303846; }
.prediction-none { color: #8795a9; }
.forecast-strip { padding-top: 22px; }
.forecast-strip .section-title .eyebrow { margin: 0 0 8px; }
.forecast-pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin: 18px 0 0;
  border-top: 1px solid #263753;
}
.forecast-tile {
  min-width: 0;
  padding: 20px 24px 6px 0;
  border-right: 1px solid #263753;
}
.forecast-tile + .forecast-tile { padding-left: 26px; border-right: 0; }
.forecast-tile-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  color: #aebed4;
  font-size: .88rem;
}
.forecast-round { font-weight: 800; font-size: 1.3rem; color: #e8f2ff; }
.forecast-suit {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: clamp(2.7rem, 4.5vw, 4.5rem);
  font-weight: 800;
  margin: 8px 0 4px;
}
.forecast-suit small { font-size: 1.02rem; font-weight: 600; color: #cad9ee; }
.forecast-unavailable { margin: 20px 0 10px; color: #ffd08d; font-weight: 650; }
.forecast-tile p { font-size: .85rem; color: #94a9c4; margin: 5px 0 0; }
.calculator-panel { width: 100%; position: static; }
.calculator-fields {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin: 20px 0;
}
.field { display: grid; gap: 8px; font-size: .84rem; color: #aebed4; font-weight: 600; }
.field input, .field select {
  color: #edf4ff;
  min-width: 0;
  width: 100%;
  border: 1px solid #365171;
  border-radius: 7px;
  padding: 12px;
  background: #101b2c;
  font: inherit;
  font-size: .98rem;
  outline-offset: 3px;
}
.field input:focus, .field select:focus { border-color: #64d7ff; outline: 2px solid #335776; }
.calculator-overview {
  display: grid;
  grid-template-columns: minmax(260px, 1.4fr) repeat(2, minmax(160px, 1fr));
  gap: 0;
  margin: 12px 0 20px;
  border-top: 1px solid #263753;
  border-bottom: 1px solid #263753;
}
.bankroll-summary, .calculator-meta {
  display: grid;
  align-content: center;
  gap: 7px;
  min-width: 0;
  margin: 0;
  padding: 18px 20px;
  border-right: 1px solid #263753;
}
.bankroll-summary { padding-left: 0; }
.calculator-meta:last-child { border-right: 0; }
.bankroll-summary span, .bankroll-summary small, .calculator-meta span { color: #aec2d8; font-size: .85rem; }
.bankroll-summary strong { font-size: clamp(1.5rem, 2.5vw, 2.2rem); letter-spacing: -.04em; color: #78ebc9; }
.calculator-meta strong { color: #edf4ff; font-size: 1.25rem; }
.calculator-warning { padding: 10px 12px; border-left: 2px solid #ffcb8a; color: #ffd08d; background: #3d2b2033; font-size: .86rem; }
.step-strip {
  display: flex;
  width: 100%;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scroll-snap-type: x proximity;
  border-top: 1px solid #263753;
  border-bottom: 1px solid #263753;
  margin: 16px 0;
}
.step-column {
  display: grid;
  align-content: start;
  gap: 6px;
  flex: 1 0 175px;
  min-width: 175px;
  padding: 18px 17px;
  border-right: 1px solid #263753;
  scroll-snap-align: start;
}
.step-column:first-child { padding-left: 0; }
.step-column:last-child { border-right: 0; }
.step-title { display: block; margin-bottom: 9px; color: #64d7ff; font-size: 1rem; }
.step-column span { margin-top: 8px; color: #98acc8; font-size: .78rem; }
.step-column strong { font-size: .94rem; }
.step-column .step-title { font-size: 1.02rem; }
.virtual-history-scroll { max-height: 56vh; }
.virtual-history th, .virtual-history td { white-space: nowrap; }
.history-panel { min-width: 0; }
.history-full-scroll { max-height: 78vh; min-height: 340px; }
.history-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.history-search { max-width: 450px; margin: 14px 0; }
.standalone-label {
  font-size: .76rem;
  font-weight: 700;
  color: #86d7f2;
  padding: 4px 0;
}
@media (max-width: 980px) {
  .calculator-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .forecast-pair { grid-template-columns: 1fr; }
  .forecast-tile { padding-right: 0; border-right: 0; border-bottom: 1px solid #263753; }
  .forecast-tile + .forecast-tile { padding-left: 0; border-bottom: 0; }
  .calculator-overview { grid-template-columns: 1fr; }
  .bankroll-summary, .calculator-meta { padding: 12px 0; border-right: 0; border-bottom: 1px solid #263753; }
  .calculator-meta:last-child { border-bottom: 0; }
}
@media (max-width: 480px) {
  .calculator-fields { grid-template-columns: 1fr; }
  .step-column { min-width: 168px; flex-basis: 168px; }
}
</style>
