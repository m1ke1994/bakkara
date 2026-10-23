import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
const template = app.split('<template>', 2)[1] || ''

test('Current and next forecasts are immediately above the full-width overall history', () => {
  const sectionMarkers = [
    'id="observation"', 'id="forecasts"', 'id="history"', 'id="events"',
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

test('Planner replaces the old virtual results and old calculator', () => {
  assert.ok(template.includes('<StakePlanner />'))
  assert.ok(template.includes('id="live-mode-notice"'))
  assert.ok(!template.includes('id="virtual-results"'))
  assert.ok(template.includes('href="#calculator"'))
})

test('Forecast outcomes remain visible without old virtual stake table', () => {
  assert.ok(template.includes('v-for="forecast in cycle18.recent"'))
  assert.ok(!template.includes('История виртуальных результатов'))
  assert.ok(template.includes('WIN / LOSE'))
})

test('Compact NewBot-style editable calculator replaces the full-page table', () => {
  const component=readFileSync(new URL('../src/StakePlanner.vue',import.meta.url),'utf8')
  assert.ok(component.includes('Первоначальная ставка'))
  assert.ok(component.includes('Множитель следующей ставки'))
  assert.ok(component.includes('Количество шагов'))
  assert.ok(component.includes('Рассчитать ряд'))
  assert.ok(component.includes('Применить настройки'))
  assert.ok(component.includes('v-for="(v,index) in row"'))
  assert.ok(component.includes('generateStakes'))
  assert.ok(component.includes('saveStakePlan'))
  assert.ok(component.includes('loadStakePlan'))
  assert.ok(!component.includes('localStorage.setItem'))
  assert.ok(!component.includes('<table'))
  assert.ok(!template.includes('История виртуальных результатов'))
})
