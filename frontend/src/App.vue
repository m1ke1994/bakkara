<script setup>
import StakePlanner from './StakePlanner.vue'
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
  SCAN_TIMEOUT: 'Время ожидания карт истекло — сканирование продолжается',
  CARDS_UNCONFIRMED: '\u0427\u0430\u0441\u0442\u044c \u0441\u043b\u043e\u0442\u043e\u0432 \u043d\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430',
  NAVIGATION_ERROR: '\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043a\u0440\u044b\u0442\u044c \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443',
  HISTORY_ERROR: '\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442',
}
const scanResultName = value => scanResultNames[value] || value || '\u2014'
const state = ref({ state_epoch: '', state_version: 0, backend: 'CHECKING', browser: 'STOPPED', authorization: 'UNKNOWN', diagnostic: '', events: [], observation: { status: 'STOPPED', current: null, history: [], games: [] } })
const busy = ref(false)
const probingMarket = ref(false)
const marketProbe = ref(null)
const live = computed(() => state.value.live || { mode: 'DEMO', step: 1 })
async function changeMode(mode) {
  if (busy.value || observing.value) return
  busy.value = true
  try {
    const response = await fetch(`${api}/api/mode`, { method: 'PUT',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || 'Не удалось сменить режим')
    applySnapshot(payload)
    error.value = ''
  } catch (exception) { error.value = exception.message || 'Не удалось сменить режим' }
  finally { busy.value = false }
}
async function changeCouponTestFilter(enabled) {
  if (busy.value || observing.value) return
  busy.value = true
  try {
    const response = await fetch(`${api}/api/live/coupon-test-filter`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
    const next = await response.json()
    if (!response.ok) throw new Error(next.detail || 'Не удалось изменить тестовый фильтр')
    applySnapshot(next)
    error.value = ''
  } catch (exception) { error.value = exception.message || 'Не удалось изменить тестовый фильтр' }
  finally { busy.value = false }
}
async function setFirstRoundTest(enabled, suit) {
  if (busy.value || observing.value) return
  busy.value = true
  try {
    const response = await fetch(`${api}/api/live/first-round-test`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled, suit}),
    })
    const next = await response.json()
    if (!response.ok) throw new Error(next.detail || 'Не удалось изменить тестовый режим')
    applySnapshot(next)
    error.value = ''
  } catch (exception) { error.value = exception.message || 'Ошибка тестового режима' }
  finally { busy.value = false }
}
async function probeCanvasMarket() {
  if (busy.value || observing.value || probingMarket.value) return
  probingMarket.value = true
  marketProbe.value = null
  try {
    const response = await fetch(api + '/api/market/probe', {method:'POST'})
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || 'Проверка Canvas не удалась')
    marketProbe.value = data
    error.value = ''
  } catch (err) {
    error.value = err.message || 'Ошибка Canvas'
  } finally { probingMarket.value = false }
}
const error = ref('')
const cycle18 = computed(() => state.value.cycle18 || {})
const strategyChoice = ref('n18')
const strategyTouched = ref(false)
const strategyNotice = ref('')
const strategyInfo = computed(() => state.value.strategy || {})
const activeStrategy = computed(() => cycle18.value.session_id
  ? cycle18.value.strategy : (strategyInfo.value.selected || 'n18'))
const strategyLabel = computed(() => activeStrategy.value === 'n18'
  ? 'Цикл N−18' : 'Голосование N−3/10/24/31/40')
const strategyShortLabel = computed(() => activeStrategy.value === 'n18' ? 'цикл 18' : '5 циклов')
const strategyDirty = computed(() => strategyChoice.value !== (strategyInfo.value.selected || 'n18'))
const strategyOptions = [
  { id: 'n18', label: 'Цикл N−18', detail: 'Один источник · 20 подтверждённых раздач для подготовки' },
  { id: 'vote_3_10_24_31_40', label: 'Голосование N−3/10/24/31/40', detail: 'Пять голосов · 40 подтверждённых раздач для подготовки' },
]
const sourceDescription = forecast => (forecast?.source_round_numbers || [forecast?.source_round_number])
  .filter(Boolean).map(number => '№' + number).join(', ') || '—'
const votesDescription = forecast => (forecast?.votes || [])
  .map(vote => 'N−' + vote.offset + ': ' + (suits[vote.suit]?.[0] || '—')).join(' · ')

const cycleStats = computed(() => cycle18.value.statistics || {})
const currentForecast = computed(() => cycle18.value.ready
  ? cycle18.value.live_preview?.find(item => item.phase === 'CURRENT') : null)
const nextForecast = computed(() => cycle18.value.ready
  ? cycle18.value.live_preview?.find(item => item.phase === 'NEXT') : null)
// Read-only outcomes: the selected strategy and the backend provide all WIN/LOSE data.
const verifiedPredictionRows = computed(() => (cycle18.value.recent || [])
  .filter(item => ['WIN', 'LOSE'].includes(item.result)))
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
  // Browser/observer commands return a smaller snapshot than /api/state.
  // Preserve the last confirmed backend strategy across such snapshots.
  if (!next.strategy && state.value.strategy) next.strategy = state.value.strategy
  if (!next.live && state.value.live) next.live = state.value.live
  state.value = next
  if (!strategyTouched.value && next.strategy?.selected) strategyChoice.value = next.strategy.selected
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
async function saveStrategy() {
  if (busy.value || observing.value) return
  busy.value = true
  strategyNotice.value = ''
  try {
    const response = await fetch(`${api}/api/strategy`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ strategy: strategyChoice.value }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || 'Сервер отклонил выбор фильтра')
    state.value = { ...state.value, strategy: payload }
    strategyChoice.value = payload.selected
    strategyTouched.value = false
    strategyNotice.value = 'Фильтр сохранён на сервере. Следующий запуск сканирования применит его.'
    error.value = ''
    await load()
  } catch (exception) {
    error.value = exception.message || 'Не удалось сохранить фильтр на сервере'
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
      <div><p class="eyebrow">BAKKARA / LIVE ANALYTICS</p><h1>Панель наблюдения <span class="header-spark">✦</span></h1><p>Карты Игрока, прогнозы выбранной стратегии и история раздач.</p></div>
      <nav class="quick-nav" aria-label="Разделы панели"><a href="#calculator">Калькулятор</a>
        <a href="#observation">Наблюдение</a>
        <a href="#forecasts">Прогнозы</a>
        <a href="#history">История</a>
        <a href="#events">События</a>
      </nav>
    </header>
    <section class="events" aria-label="Текущий режим работы">
      <div class="section-title"><h2>Режим: ДЕМО</h2><span class="standalone-label">Только наблюдение</span></div>
      <p class="diagnostic">Калькулятор и наблюдение работают без денежных действий. Отправка реальных ставок в этой версии отсутствует.</p>
    </section>
    <section class="cards">
      <article><span>Сервер</span><strong :class="badge(state.backend)">{{ label('backend', state.backend) }}</strong></article>
      <article><span>Браузер</span><strong :class="badge(state.browser)">{{ label('browser', state.browser) }}</strong></article>
      <article><span>Авторизация</span><strong :class="badge(state.authorization)">{{ label('authorization', state.authorization) }}</strong></article>
    </section>
    <section class="events strategy-panel" aria-label="Выбор фильтра прогнозирования">
      <div class="section-title"><div><p class="eyebrow">СТРАТЕГИЯ / ФИЛЬТР</p><h2>Выберите алгоритм прогнозирования</h2></div><span class="standalone-label">На сервере: {{ strategyInfo.selected === 'vote_3_10_24_31_40' ? '5 циклов' : 'N−18' }}</span></div>
      <div class="strategy-options" role="radiogroup" aria-label="Фильтр прогноза">
        <label v-for="option in strategyOptions" :key="option.id" class="strategy-option" :class="{ 'strategy-option-selected': strategyChoice === option.id }">
          <input v-model="strategyChoice" type="radio" name="forecast-strategy" :value="option.id" :disabled="busy || observing" @change="strategyTouched = true; strategyNotice = ''" />
          <span><strong>{{ option.label }}</strong><small>{{ option.detail }}</small></span>
        </label>
      </div>
      <div class="controls strategy-save-controls">
        <button :disabled="busy || observing || !strategyDirty" @click="saveStrategy">Сохранить стратегию</button>
        <span class="diagnostic">{{ observing ? 'Сканирование активно: для смены фильтра сначала остановите его.' : (strategyDirty ? 'Выбор изменён — нажмите «Сохранить стратегию».' : 'Настройка хранится в базе данных бэкенда.') }}</span>
      </div>
      <p v-if="strategyNotice" class="strategy-notice">{{ strategyNotice }}</p>
      <p class="diagnostic">После сохранения запустите браузер и нажмите «Начать сканирование». Во время сканирования фильтр фиксирован. ДЕМО только наблюдает. В LIVE бот работает с купоном БК; тест с первой раздачи не является прогнозом стратегии.</p>
    </section>
    <section class="controls"><button :disabled="busy || running" @click="command('/api/browser/start')">Запустить браузер</button><button class="secondary" :disabled="busy || !running" @click="command('/api/browser/stop')">Остановить браузер</button></section>
    <p class="diagnostic">{{ state.diagnostic }}</p><p v-if="error" class="error">{{ error }}</p>
    <StakePlanner />
    <section class="events" id="live-mode-notice" aria-label="Режим работы">
      <div class="section-title"><h2>Режим работы</h2><span class="standalone-label">{{ live.mode }}</span></div>
      <div class="controls"><button type="button" :class="live.mode === 'DEMO' ? 'active-mode' : 'secondary'" :disabled="busy || observing || live.mode === 'DEMO'" @click="changeMode('DEMO')">ДЕМО</button><button type="button" :class="live.mode === 'LIVE' ? 'active-mode' : 'secondary'" :disabled="busy || observing || live.mode === 'LIVE'" @click="changeMode('LIVE')">LIVE</button></div>
      <div class="controls">
        <label class="diagnostic">
          <input type="checkbox" :checked="Boolean(live.coupon_test_filter)"
            :disabled="busy || observing" @change="changeCouponTestFilter($event.target.checked)" />
          Тест LIVE-купона: сначала коэффициент масти, затем сумма и «Сделать ставку»
        </label>
      </div>
      <div class="controls">
        <label class="diagnostic">
          <input type="checkbox" :checked="Boolean(live.warmup_test)"
            :disabled="busy || observing"
            @change="setFirstRoundTest($event.target.checked, live.test_suit || 'hearts')" />
          Тест с первой раздачи — без прогрева стратегий
        </label>
        <label class="diagnostic">Тестовая масть:
          <select :value="live.test_suit || 'hearts'" :disabled="busy || observing"
            @change="setFirstRoundTest(Boolean(live.warmup_test), $event.target.value)">
            <option value="hearts">♥ Черви</option>
            <option value="diamonds">♦ Бубны</option>
            <option value="clubs">♣ Трефы</option>
            <option value="spades">♠ Пики</option>
          </select>
        </label>
      </div>
      <p v-if="live.warmup_test" class="diagnostic">ТЕСТ: ближайшая предстоящая раздача; масть выбрана вручную, а не рассчитана стратегией. В LIVE бот использует обычный купон: коэффициент → сумма шага → «Сделать ставку». Прогнозы N−18 и N−3/10/24/31/40 продолжат подготовку отдельно.</p>
      <p v-if="live.coupon_test_filter" class="diagnostic">Тестовый фильтр включён. Для отправки используется текущий виртуальный RUB-режим БК. Если включена ставка в 1 клик, бот сначала отключает её, затем открывает обычный купон и проверяет введённую сумму.</p>
      <p v-if="live.mode === 'LIVE'" class="diagnostic">Раздача №{{ live.attempt?.round_number || '—' }} · {{ live.attempt?.signal_source === 'MANUAL_TEST' ? 'ТЕСТ (масть вручную)' : strategyLabel }} · Масть: {{ suits[live.attempt?.suit]?.[1] || '—' }} · Шаг: {{ live.step }} · Сумма: {{ live.attempt?.amount || '—' }} RUB · Купон: {{ live.attempt?.status || 'ожидание' }} · Принятие: {{ live.attempt?.status || '—' }} · Итог: {{ live.attempt?.result || '—' }}</p>
      <div class="controls">
        <button class="secondary" :disabled="busy || !running || observing || probingMarket" @click="probeCanvasMarket">
          {{ probingMarket ? 'Проверяем Canvas…' : 'Проверить рынок «Игрок получит карту»' }}
        </button>
        <span class="diagnostic">Для проверки вручную откройте ближайшую раздачу баккары и остановите наблюдение.</span>
      </div>
      <div v-if="marketProbe" class="diagnostic">
        <p>Результат: {{ marketProbe.status }} · Текстовых записей: {{ marketProbe.captureCount ?? 0 }} · Найдено мастей: {{ marketProbe.labels?.length ?? 0 }}</p>
        <p v-for="(entry,index) in marketProbe.labels || []" :key="index">{{ entry.text }}: x={{ Math.round(entry.x) }}, y={{ Math.round(entry.y) }} (Canvas-пиксели; пока не подтверждённая область клика)</p>
        <p v-if="!marketProbe.labels?.length">Текст мог быть нарисован изображением или до установки обработчика. Координаты автоматически не угадываются.</p>
      </div>
      <p class="diagnostic">LIVE использует RUB текущей сессии. При неизвестном статусе отправки повторная ставка блокируется.</p>
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
        <p v-if="cycle18.ready && outcomeFor(state.observation.current.game_id)">{{ strategyLabel }}: {{ suits[outcomeFor(state.observation.current.game_id).predicted_suit]?.[0] || '—' }} · {{ outcomeLabel(outcomeFor(state.observation.current.game_id).result) }}</p>
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
      <section class="cycle18-panel" aria-label="Прогноз масти Игрока по выбранной стратегии">
        <div class="section-title">
          <h3>{{ strategyLabel }} · прогноз масти Игрока</h3>
          <strong>{{ cycle18.ready ? 'Готов к прогнозированию' : 'Сбор истории' }}</strong>
        </div>
        <p class="diagnostic">Подготовка: {{ cycle18.warmup_required ?? 20 }} уникальных подтверждённых рук. {{ activeStrategy === 'n18' ? 'Для игры N используем масть N−18.' : 'Для игры N голосуют масти N−3, N−10, N−24, N−31 и N−40 по исходной таблице. При равенстве: ♥ → ♦ → ♣ → ♠.' }} Пропущенный источник не заменяем соседним. Реальные ставки бот не делает.</p>
        <div class="warmup">
          <div class="section-title"><strong>Подтверждено за запуск: {{ cycle18.warmup_count ?? 0 }} / {{ cycle18.warmup_required ?? 20 }}</strong><span>{{ cycle18.ready ? strategyLabel + ' активен' : 'Только сбор карт' }}</span></div>
          <progress :value="cycle18.warmup_count ?? 0" :max="cycle18.warmup_required ?? 20"></progress>
          <p v-if="!cycle18.ready" class="diagnostic">Подготовка: первые {{ cycle18.warmup_required ?? 20 }} финально подтверждённых раздач. До её завершения WIN/LOSE не рассчитываем. Если источник отсутствует, сигнал пропускается.</p>
          <p v-else class="diagnostic">Подготовка завершена. Прогнозы строятся только по реальным номерам завершённых источников: {{ strategyLabel }}.</p>
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
          <article><span>Проверено ({{ strategyShortLabel }})</span><strong>{{ cycleStats.checked_count ?? 0 }}</strong></article>
          <article><span>WIN</span><strong class="win-text">{{ cycleStats.wins ?? 0 }}</strong></article>
          <article><span>LOSE</span><strong class="lose-text">{{ cycleStats.losses ?? 0 }}</strong></article>
          <article><span>Точность</span><strong>{{ cycleStats.win_rate == null ? '—' : cycleStats.win_rate + '%' }}</strong></article>
          <article><span>Онлайн-проверки</span><strong>{{ cycleStats.live_checked ?? 0 }}</strong></article>
          <article><span>Постфактум</span><strong>{{ cycleStats.backfill_checked ?? 0 }}</strong></article>
          <article><span>Максимум LOSE подряд</span><strong>{{ cycleStats.max_lose_streak ?? 0 }}</strong></article>
          <article><span>Виртуальный следующий шаг</span><strong>{{ cycleStats.next_step ?? 1 }}</strong></article>
        </div>
        <details v-if="cycle18.ready && Object.keys(cycleStats.wins_by_step || {}).length"><summary>Победы по шагам (виртуально)</summary><p v-for="(count, step) in cycleStats.wins_by_step" :key="step">Шаг {{ step }}: {{ count }} WIN</p></details>
        <details v-if="cycle18.historical_audit?.length">
          <summary>Пересчёт по исправленной истории</summary>
          <p class="diagnostic">Расчёт по текущим подтверждённым картам. Исходные прогнозы и исполненные ставки сохраняются. Пересчёт не означает, что сигнал был известен до начала игры.</p>
          <div class="history-table-scroll">
            <table class="history-table" aria-label="Исторический пересчёт">
              <thead><tr><th>Раздача</th><th>Голоса</th><th>Пересчёт</th><th>Исходный прогноз</th><th>Причина</th></tr></thead>
              <tbody><tr v-for="audit in cycle18.historical_audit" :key="audit.target_game_id">
                <td>№{{ audit.target_round_number }}</td>
                <td>{{ votesDescription(audit) || '—' }}</td>
                <td>{{ suits[audit.predicted_suit]?.[0] || '—' }} · {{ predictionLabels[audit.result] || audit.result }}</td>
                <td>{{ suits[audit.original_suit]?.[0] || '—' }} · {{ predictionLabels[audit.original_result] || audit.original_result || 'Не записан' }}</td>
                <td>{{ audit.reason }}{{ audit.changed ? ' · Есть расхождение' : '' }}</td>
              </tr></tbody>
            </table>
          </div>
        </details>
        <details v-if="cycle18.ready && cycle18.recent?.length">
          <summary>История прогнозов — {{ strategyLabel }} ({{ cycle18.recent.length }})</summary>
          <div class="history-table-scroll">
            <table class="history-table" aria-label="Прогнозы выбранной стратегии">
              <thead><tr><th>Источник</th><th>Целевая игра</th><th>Прогноз</th><th>Результат</th><th>Шаг</th><th>Проверка</th></tr></thead>
              <tbody><tr v-for="forecast in cycle18.recent" :key="forecast.source_game_id">
                <td>{{ sourceDescription(forecast) }}</td><td>№{{ forecast.target_round_number }}</td>
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

    <section class="events forecast-strip" id="forecasts" aria-label="Прогнозы следующих раздач">
      <div class="section-title"><div><p class="eyebrow">LIVE / ПРОГНОЗЫ</p><h2>Текущая и следующая раздачи</h2></div><span class="standalone-label">{{ cycle18.ready ? strategyLabel + ' активен' : 'Подготовка прогноза' }}</span></div>
        <div v-if="cycle18.ready" class="forecast-pair" aria-label="Текущая и следующая раздачи">
          <article class="forecast-tile" data-testid="forecast-current">
            <div class="forecast-tile-head"><span>Текущая раздача</span><span class="forecast-round">№{{ currentForecast?.target_round_number || state.observation?.current?.round_number || '—' }}</span></div>
            <div v-if="currentForecast?.available" class="forecast-suit" :class="isRedSuit(currentForecast.predicted_suit) ? 'red-suit' : 'black-suit'">
              {{ suits[currentForecast.predicted_suit]?.[0] }} <small>{{ suits[currentForecast.predicted_suit]?.[1] }}</small>
            </div>
            <div v-else class="forecast-unavailable">Нет подтверждённого источника</div>
            <p v-if="activeStrategy === 'n18'">Масть из раздачи №{{ currentForecast?.source_round_number || '—' }} (N−18)</p>
            <p v-else>Источники: {{ sourceDescription(currentForecast) }}</p>
            <p v-if="currentForecast?.votes?.length > 1" class="diagnostic">{{ votesDescription(currentForecast) }}</p>
          </article>
          <article class="forecast-tile" data-testid="forecast-next">
            <div class="forecast-tile-head"><span>Следующая раздача</span><span class="forecast-round">№{{ nextForecast?.target_round_number || '—' }}</span></div>
            <div v-if="nextForecast?.available" class="forecast-suit" :class="isRedSuit(nextForecast.predicted_suit) ? 'red-suit' : 'black-suit'">
              {{ suits[nextForecast.predicted_suit]?.[0] }} <small>{{ suits[nextForecast.predicted_suit]?.[1] }}</small>
            </div>
            <div v-else class="forecast-unavailable">Нет подтверждённого источника</div>
            <p v-if="activeStrategy === 'n18'">Масть из раздачи №{{ nextForecast?.source_round_number || '—' }} (N−18)</p>
            <p v-else>Источники: {{ sourceDescription(nextForecast) }}</p>
            <p v-if="nextForecast?.votes?.length > 1" class="diagnostic">{{ votesDescription(nextForecast) }}</p>
          </article>
        </div>
        <p v-if="cycle18.ready && !currentForecast" class="diagnostic">Ожидаем выбора текущей игры с действительным номером; прогнозы нельзя определить без N.</p>
        <p v-if="!cycle18.ready" class="diagnostic">Прогнозы появятся после {{ cycle18.warmup_required ?? 20 }} подтверждённых игр в этом запуске.</p>
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
          <thead><tr><th scope="col">Время</th><th scope="col">№ раздачи</th><th scope="col">Карты Игрока</th><th scope="col">Расчётная масть</th><th v-if="cycle18.ready" scope="col">WIN / LOSE ({{ strategyShortLabel }})</th></tr></thead>
          <tbody>
            <tr v-for="item in filteredHistory" :key="item.game_id">
              <td class="history-time">{{ formatObservedAt(item.observed_at || item.last_scanned_at || item.discovered_at) }}</td>
              <td class="history-round"><strong><a v-if="item.game_url || item.url" :href="item.game_url || item.url" target="_blank" rel="noopener noreferrer" :title="`Открыть игру ${item.game_id}`">{{ item.round_number || '—' }}</a><span v-else>{{ item.round_number || '—' }}</span></strong></td>
              <td><div v-if="item.player_cards?.length" class="history-card-line"><span v-for="(card, index) in item.player_cards" :key="card.position || index" class="history-card" :class="isRedSuit(card.suit) ? 'red-suit' : 'black-suit'">{{ card.rank }}{{ suits[card.suit]?.[0] || '' }}</span></div><span v-else class="prediction-none">—</span></td>
              <td><span v-if="item.calculated_suit" class="calculated-suit" :class="isRedSuit(item.calculated_suit) ? 'red-suit' : 'black-suit'" title="Масть, рассчитанная из карт этой раздачи">{{ suits[item.calculated_suit]?.[0] || '—' }}</span><span v-else class="prediction-none">—</span></td>
              <td v-if="cycle18.ready"><span v-if="outcomeFor(item.game_id) && ['WIN', 'LOSE'].includes(outcomeFor(item.game_id).result)" class="prediction-result" :class="`prediction-${outcomeFor(item.game_id).result.toLowerCase()}`" :title="`${strategyLabel}: прогноз ${suits[outcomeFor(item.game_id).predicted_suit]?.[1] || '—'}`">{{ outcomeFor(item.game_id).result }}</span><span v-else-if="outcomeFor(item.game_id)?.result === 'PENDING'" class="prediction-pending">Ожидаем карт</span></td>
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
@media (max-width: 760px) {
  .forecast-pair { grid-template-columns: 1fr; }
  .forecast-tile { padding-right: 0; border-right: 0; border-bottom: 1px solid #263753; }
  .forecast-tile + .forecast-tile { padding-left: 0; border-bottom: 0; }
}
</style>

