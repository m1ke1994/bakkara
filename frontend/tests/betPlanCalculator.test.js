import test from 'node:test'
import assert from 'node:assert/strict'
import {generateStakes,describeStakes} from '../src/betPlanCalculator.js'

test('171 × 1.84 reproduces NewBot numbers',()=>{
 assert.deepEqual(generateStakes(171,1.84,8),[171,314,577,1061,1952,3591,6607,12156])
})
test('editable second step changes next summary',()=>{
 const steps=generateStakes(15,2.5,4)
 assert.deepEqual(steps,[15,37,92,230])
 steps[1]=42
 const report=describeStakes(steps,1.9)
 assert.equal(report.rows[2].lostBefore,57)
 assert.equal(report.totalBankroll,379)
})
test('correct signed profit and risk',()=>{
 const result=describeStakes([10,20,40,80,160,320],1.9)
 assert.equal(result.totalBankroll,630)
 assert.equal(result.rows[5].netIfWin,-22)
 assert.deepEqual(result.negativeSteps,[5,6])
})
test('reject out-of-range values',()=>{
 assert.equal(generateStakes(0,2,6),null)
 assert.equal(generateStakes(15,1,6),null)
 assert.equal(describeStakes([15,-2],1.9),null)
})

test('manual amounts have exact cent totals and monetary half-up rounding',()=>{
 const result=describeStakes(['0.10','0.20','0.29'],'1.5')
 assert.equal(result.totalBankroll,0.59)
 assert.equal(result.rows[2].lostBefore,0.30)
 assert.equal(result.rows[2].netIfWin,-0.16)
 assert.equal(describeStakes(['1.01'],'1.5').rows[0].netIfWin,0.51)
 for(const value of [true,null,'','0.001',1e12,Infinity]) assert.equal(describeStakes([value],1.9),null)
 assert.equal(describeStakes([1],'1.90001'),null)
})
