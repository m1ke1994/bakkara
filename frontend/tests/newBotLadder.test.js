import test from 'node:test'
import assert from 'node:assert/strict'
import {generateStakes,describeStakes} from '../src/betPlanCalculator.js'
test('NewBot 171 x 1.84 x 8',{skip:false},()=>assert.deepEqual(generateStakes(171,1.84,8),[171,314,577,1061,1952,3591,6607,12156]))
test('Custom row and payout',()=>{const x=generateStakes(15,2.5,4);assert.deepEqual(x,[15,37,92,230]);x[1]=42;assert.equal(describeStakes(x,1.9).rows[2].lostBefore,57)})
test('Invalid input',()=>{assert.equal(generateStakes(0,2,8),null);assert.equal(describeStakes([15,-1],1.9),null)})
