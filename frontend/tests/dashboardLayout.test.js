import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')

test('Forecasts come first, followed by a full-width calculator and virtual history', () => {
  const positions = ['id="forecasts"', 'id="calculator"', 'id="virtual-bets"', 'id="observation"']
    .map(marker => app.indexOf(marker))
  assert.ok(positions.every(position => position >= 0))
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b))
  assert.ok(!app.includes('workspace-grid'))
  assert.ok(css.includes('max-width: none'))
  assert.ok(css.includes('width: 100%'))
})

test('Stake ladder is horizontal and recomputes in recovery mode by default', () => {
  assert.ok(app.includes("const stakeMode = ref('recover')"))
  assert.ok(app.includes('class="step-strip"'))
  assert.ok(app.includes('v-for="row in ladder.rows"'))
  assert.ok(app.includes('v-model.number="odds"'))
  assert.ok(app.includes('mode: stakeMode.value'))
  assert.ok(app.includes("['double', 'recover'].includes(raw.mode)"))
  assert.ok(app.includes('currentSettings &&'))
  assert.ok(app.includes('scroll-snap-type: x proximity'))
  assert.ok(app.includes('grid-template-columns: repeat(4, minmax(0, 1fr))'))
})

test('Virtual bet rows are explicitly modeled from confirmed results', () => {
  assert.ok(app.includes('const virtualBetRows = computed('))
  assert.ok(app.includes(".filter(item => ['WIN', 'LOSE'].includes(item.result))"))
  assert.ok(app.includes('Number(odds.value) - 1'))
  assert.ok(app.includes('item.virtualStake'))
  assert.ok(app.includes('item.virtualNet'))
  assert.ok(app.includes('Только виртуально'))
  assert.ok(app.includes('не берутся из истории реальных ставок'))
})
