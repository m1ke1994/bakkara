import test from 'node:test'
import assert from 'node:assert/strict'
import {loadStakePlan,saveStakePlan} from '../src/stakePlanApi.js'

test('server worksheet uses GET and PUT without recalculating the manual row',async t=>{
  const plan={initial:'15',multiplier:'2.5',steps:3,row:['15','42.29','92'],odds:'1.9',revision:1}
  const calls=[]
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    calls.push({url,options})
    return {ok:true,json:async()=>plan}
  })
  assert.deepEqual(await loadStakePlan(),plan)
  assert.deepEqual(await saveStakePlan(plan),plan)
  assert.equal(calls[0].options.method,'GET')
  assert.match(calls[0].url,/\/api\/stake-plan$/)
  assert.equal(calls[1].options.method,'PUT')
  assert.deepEqual(JSON.parse(calls[1].options.body),plan)
})

test('save rejection exposes conflict and validation errors',async t=>{
  t.mock.method(globalThis,'fetch',async()=>({ok:false,json:async()=>({detail:'Настройки изменены в другом окне'})}))
  await assert.rejects(saveStakePlan({}),/Настройки изменены/)
  globalThis.fetch=async()=>({ok:false,json:async()=>({detail:[{msg:'bad value'}]})})
  await assert.rejects(saveStakePlan({}),/проверьте суммы/)
})

test('network failure is not reported as a successful save',async t=>{
  t.mock.method(globalThis,'fetch',async()=>{throw new Error('offline')})
  await assert.rejects(saveStakePlan({}),/offline/)
})
