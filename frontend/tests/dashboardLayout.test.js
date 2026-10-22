import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
const template = app.split('<template>', 2)[1] || ''

test('Current and next forecasts are immediately above the full-width overall history', () => {
  const sectionMarkers = [
    'id="virtual-results"', 'id="observation"', 'id="forecasts"', 'id="history"', 'id="events"',
  ]
  const positions = sectionMarkers.map(marker => template.indexOf(marker))
  assert.ok(positions.every(position => position >= 0))
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b))
  const afterForecast = template.slice(template.indexOf('id="forecasts"'), template.indexOf('id="history"'))
  assert.ok(afterForecast.includes('data-testid="forecast-current"'))
  assert.ok(afterForecast.includes('data-testid="forecast-next"'))
  assert.equal((afterForecast.match(/<section /g) || []).length, 1)
  assert.ok(css.includes('max-width: none'))
  assert.ok(css.includes('width: 100%'))
})

test('Martingale calculator and its old navigation link are absent', () => {
  assert.ok(!template.includes('id="calculator"'))
  assert.ok(!template.includes('href="#calculator"'))
  assert.ok(!template.includes('class="calculator-panel"'))
  assert.ok(!app.includes('calculateLadder'))
  assert.ok(!app.includes('savedCalculatorKey'))
  assert.ok(!app.includes('stakeMode'))
  assert.ok(!app.includes('virtualStake'))
  assert.ok(!app.includes('virtualNet'))
  assert.ok(!app.includes('ladder.totalBankroll'))
  assert.ok(!app.includes('class="step-strip"'))
  assert.ok(!app.includes('.calculator-overview'))
})

test('Verified outcomes remain visible with steps and no fictional money amounts', () => {
  assert.ok(app.includes('const verifiedPredictionRows = computed('))
  assert.ok(app.includes(".filter(item => ['WIN', 'LOSE'].includes(item.result))"))
  assert.ok(template.includes('История виртуальных результатов'))
  assert.ok(template.includes('v-for="item in verifiedPredictionRows"'))
  assert.ok(template.includes('item.step'))
  assert.ok(template.includes("item.result === 'WIN'"))
  assert.ok(template.includes('Без реальных ставок'))
})
