<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
const api = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8010'
const state = ref({ backend: 'CHECKING', browser: 'STOPPED', authorization: 'UNKNOWN', diagnostic: '', events: [], observation: { status: 'STOPPED', current: null, history: [] } })
const busy = ref(false); const error = ref('')
const badge = value => String(value || 'UNKNOWN').toLowerCase().replaceAll('_', '-')
const running = computed(() => !['STOPPED', 'ERROR'].includes(state.value.browser))
const observing = computed(() => !['STOPPED', 'ERROR'].includes(state.value.observation?.status))
async function load() { try { const r = await fetch(`${api}/api/state`); if (!r.ok) throw Error(); state.value = await r.json(); error.value = '' } catch { state.value.backend = 'OFFLINE'; error.value = 'Backend is unreachable.' } }
async function command(path) { busy.value = true; try { const r = await fetch(`${api}${path}`, { method: 'POST' }); if (!r.ok) throw Error(); state.value = await r.json() } catch { error.value = 'Could not send command.' } finally { busy.value = false } }
let timer; onMounted(() => { load(); timer = setInterval(load, 1500) }); onBeforeUnmount(() => clearInterval(timer))
</script>
<template>
  <main>
    <header><p class="eyebrow">READ-ONLY SESSION CONTROL</p><h1>BAKKARA BOT</h1><p>Manual login, DOM-only card observation, no betting actions.</p></header>
    <section class="cards"><article><span>Backend</span><strong :class="badge(state.backend)">{{ state.backend }}</strong></article><article><span>Browser</span><strong :class="badge(state.browser)">{{ state.browser }}</strong></article><article><span>Authorization</span><strong :class="badge(state.authorization)">{{ state.authorization }}</strong></article></section>
    <section class="controls"><button :disabled="busy || running" @click="command('/api/browser/start')">Запустить браузер</button><button class="secondary" :disabled="busy || !running" @click="command('/api/browser/stop')">Остановить браузер</button></section>
    <p class="diagnostic">{{ state.diagnostic }}</p><p v-if="error" class="error">{{ error }}</p>
    <section class="events observation"><div class="section-title"><h2>Наблюдение</h2><strong :class="badge(state.observation?.status)">{{ state.observation?.status }}</strong></div><div class="controls"><button :disabled="busy || state.authorization !== 'AUTHORIZED' || observing" @click="command('/api/observation/start')">Начать сканирование</button><button class="secondary" :disabled="busy || !observing" @click="command('/api/observation/stop')">Остановить сканирование</button></div><p class="diagnostic">{{ state.observation?.diagnostic }}</p><template v-if="state.observation?.current"><p>Игра: {{ state.observation.current.game_id }} · Раздача: {{ state.observation.current.round_number || '—' }} · Карт: {{ state.observation.current.cards.length }}</p><ol><li v-for="(card, index) in state.observation.current.cards" :key="index">Карта {{ index + 1 }}: <b>{{ card.rank || 'неизвестно' }} {{ { hearts: '♥', diamonds: '♦', clubs: '♣', spades: '♠' }[card.suit] || 'неизвестно' }}</b></li></ol></template><p v-else>Текущая раздача ещё не выбрана.</p><h3>История</h3><p v-if="!state.observation?.history?.length">Завершённых или частичных наблюдений пока нет.</p><ol v-else><li v-for="item in state.observation.history" :key="item.game_id">{{ item.game_id }} — {{ item.cards.map(c => `${c.rank || 'неизвестно'} ${c.suit || 'неизвестно'}`).join(', ') || item.reason }} <small>({{ item.completeness }})</small></li></ol></section>
    <section class="events"><h2>События и логи</h2><p v-if="!state.events.length">Событий пока нет.</p><ol v-else><li v-for="event in state.events" :key="event.at"><time>{{ new Date(event.at).toLocaleTimeString() }}</time><b>{{ event.level }}</b> {{ event.message }}</li></ol></section>
  </main>
</template>
