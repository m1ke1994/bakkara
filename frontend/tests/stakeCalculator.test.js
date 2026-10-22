import test from 'node:test'
import assert from 'node:assert/strict'
import { calculateLadder } from '../src/stakeCalculator.js'

test('NewBot fixed x2 progression: bankroll and late-step net at 1.90', () => {
  const result = calculateLadder({ stake: 10, odds: 1.9, steps: 6 })
  assert.deepEqual(result.rows.map(row => row.stake), [10, 20, 40, 80, 160, 320])
  assert.equal(result.totalBankroll, 630)
  assert.equal(result.rows[0].netIfWin, 9)
  assert.equal(result.rows[5].netIfWin, -22)
  assert.equal(result.anyNegativeProfit, true)
})

test('Odds change ×2 profits but not bankroll', () => {
  const a = calculateLadder({ stake: 10, odds: 1.9, steps: 6 })
  const b = calculateLadder({ stake: 10, odds: 2, steps: 6 })
  assert.equal(a.totalBankroll, b.totalBankroll)
  assert.equal(b.rows[5].netIfWin, 10)
})

test('Recovery mode covers past losses and keeps first-step profit', () => {
  const result = calculateLadder({ stake: 10, odds: 1.9, steps: 6, mode: 'recover' })
  assert.equal(result.rows[0].stake, 10)
  assert.ok(result.rows.every(row => row.netIfWin >= 9 - 0.01))
  assert.equal(result.rows.at(-1).bankThroughStep, result.totalBankroll)
})

test('Invalid parameters cannot produce infinite or unsafe bankrolls', () => {
  for (const input of [
    { stake: 0, odds: 1.9, steps: 6 },
    { stake: 10, odds: 1, steps: 6 },
    { stake: 10, odds: 1.9, steps: 0 },
    { stake: 10, odds: 1.9, steps: 21 },
    { stake: 10, odds: 1.9, steps: 2.5 },
    { stake: 10, odds: 1.9, steps: 6, mode: 'other' },
    { stake: 10, odds: 1.0000000001, steps: 20, mode: 'recover' },
  ]) assert.equal(calculateLadder(input), null)
})
