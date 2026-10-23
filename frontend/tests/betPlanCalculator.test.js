import test from 'node:test'
import assert from 'node:assert/strict'
import {calculateLadder} from '../src/betPlanCalculator.js'

test('multiplier 2.5 and payout are independent',()=>{
 const r=calculateLadder({stake:10,multiplier:2.5,odds:1.9,steps:3})
 assert.deepEqual(r.rows.map(x=>x.stake),[10,25,62.5])
 assert.equal(r.totalBankroll,97.5)
 assert.equal(r.rows[2].netIfWin,21.25)
})
test('each stake rounds up to kopecks',()=>{
 const r=calculateLadder({stake:15,multiplier:1.55,odds:2.8,steps:5})
 assert.deepEqual(r.rows.map(x=>x.stake),[15,23.25,36.04,55.87,86.60])
 assert.equal(r.profitFirstStep,27)
})
test('2x at 1.9 does not always cover previous losses',()=>{
 const r=calculateLadder({stake:10,multiplier:2,odds:1.9,steps:6})
 assert.deepEqual(r.negativeSteps,[5,6])
 assert.equal(r.rows[5].netIfWin,-22)
})
test('invalid parameters',()=>{
 for(const v of [
  {stake:0,multiplier:2,odds:1.9,steps:4},
  {stake:15,multiplier:1,odds:1.9,steps:4},
  {stake:15,multiplier:2,odds:1,steps:4},
  {stake:15,multiplier:2,odds:1.9,steps:0},
  {stake:15,multiplier:2,odds:1.9,steps:21}])
 assert.equal(calculateLadder(v),null)
})
