<script setup>
import {computed,ref,onMounted} from 'vue'
import {generateStakes,describeStakes} from './betPlanCalculator.js'

const initial=ref(15),multiplier=ref(2),steps=ref(8),odds=ref(1.9)
const row=ref(generateStakes(15,2,8))
const notice=ref(''),saved=ref(false)
const money=v=>Number(v).toLocaleString('ru-RU',{maximumFractionDigits:2})+' ₽'
const report=computed(()=>describeStakes(row.value,odds.value))
const valid=computed(()=>report.value!==null && row.value.length===Number(steps.value) &&
  Number(initial.value)>=1 && Number(multiplier.value)>1 && Number(multiplier.value)<=10)
function calculate(){
  const next=generateStakes(initial.value,multiplier.value,steps.value)
  if(!next){notice.value='Некорректная сумма, множитель или количество шагов';return}
  row.value=next;saved.value=false;notice.value='Ряд рассчитан. Каждый шаг можно поправить вручную.'
}
function edit(index,value){
  const n=Number(value)
  row.value=row.value.map((v,i)=>i===index?n:v)
  saved.value=false
}
function apply(){
  if(!valid.value)return
  localStorage.setItem('bakkara.calculator.v2',JSON.stringify({initial:initial.value,multiplier:multiplier.value,steps:steps.value,row:row.value,odds:odds.value}))
  saved.value=true;notice.value='Ряд сохранён для калькулятора в этом браузере. Отправка ставок не включена.'
}
onMounted(()=>{
  try{
    const old=JSON.parse(localStorage.getItem('bakkara.calculator.v2')||'null')
    if(old&&generateStakes(old.initial,old.multiplier,old.steps)&&describeStakes(old.row,old.odds)){
      initial.value=old.initial;multiplier.value=old.multiplier;steps.value=old.steps
      row.value=old.row;odds.value=old.odds;saved.value=true
    }
  }catch{}
})
</script>
<template>
  <section class="planner" id="calculator" aria-label="Калькулятор шагов">
    <header class="panel-head">
      <div><span class="overline">STRATEGY</span><h2>Настройка ряда ставок</h2></div>
      <strong :class="valid?'positive':'negative'">{{valid?'Бюджет ряда: '+money(report.totalBankroll):'Проверьте значения'}}</strong>
    </header>
    <div class="editor">
      <label>Первоначальная ставка<input v-model.number="initial" type="number" min="1" step="0.01"></label>
      <label>Множитель следующей ставки<input v-model.number="multiplier" type="number" min="1.01" max="10" step="0.01"></label>
      <label>Количество шагов<input v-model.number="steps" type="number" min="1" max="20" step="1"></label>
      <button type="button" @click="calculate">Рассчитать ряд</button>
    </div>
    <div class="sequence">
      <label v-for="(v,index) in row" :key="index">Шаг {{index+1}}
        <input type="number" min="1" step="0.01" :value="v" :aria-label="'Шаг '+(index+1)"
          @input="edit(index,$event.target.value)">
      </label>
    </div>
    <div class="fine-details">
      <details>
        <summary>Итог при WIN, сумма потерь и коэффициент БК</summary>
        <label>Коэффициент выплаты БК <input v-model.number="odds" type="number" min="1.01" step="0.01"></label>
        <div v-if="report" class="results">
          <article v-for="v in report.rows" :key="v.step">
            <span>Шаг {{v.step}} · {{money(v.stake)}}</span>
            <small>Бюджет до шага: {{money(v.bankThroughStep)}}</small>
            <strong :class="v.netIfWin>=0?'positive':'negative'">Итог при WIN: {{money(v.netIfWin)}}</strong>
          </article>
        </div>
        <p v-if="report?.negativeSteps?.length" class="negative">Поздний WIN не покрывает предыдущие потери на шагах: {{report.negativeSteps.join(', ')}}</p>
      </details>
    </div>
    <footer>
      <span>Ряд формируется как в NewBot: последующие суммы округляются вниз до целых ₽. Ручные правки имеют приоритет.</span>
      <button class="apply" type="button" :disabled="!valid" @click="apply">{{saved?'Настройки сохранены':'Применить настройки'}}</button>
    </footer>
    <p v-if="notice" class="notice">{{notice}}</p>
  </section>
</template>
<style scoped>
.planner{background:#101722;color:#ecf5ff;border:1px solid #26354a;border-radius:10px;margin:18px 0}
.panel-head{padding:20px 22px 16px;display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid #28354a;flex-wrap:wrap}
.overline{font-size:.74rem;font-weight:800;letter-spacing:.13em;color:#ffd078}
h2{font-size:1.35rem;margin:6px 0 0}.positive{color:#33dcaa}.negative{color:#ff8298}
.editor{display:flex;flex-wrap:wrap;gap:12px;align-items:end;padding:18px 22px}
.editor label{display:grid;gap:7px;flex:0 1 168px}.editor label,.sequence label{font-size:.79rem;color:#96acc9;font-weight:650}
.editor input,.sequence input,.fine-details input{width:100%;min-width:0;box-sizing:border-box;background:#111d2d;color:#ecf4ff;border:1px solid #2b3b53;border-radius:6px;padding:10px;font:inherit}
.editor button,footer button{border:1px solid #2d415a;background:#172339;color:#eaf4ff;padding:10px 16px;border-radius:7px;cursor:pointer;font-weight:650}
.editor button:disabled,footer button:disabled{opacity:.45;cursor:not-allowed}
.sequence{padding:15px 22px;display:flex;flex-wrap:wrap;gap:12px;border-top:1px solid #26354a}
.sequence label{display:grid;gap:7px;flex:1 1 100px;max-width:130px}
.sequence input{font-weight:700}
.fine-details{padding:6px 22px}.fine-details summary{cursor:pointer;color:#a3bad3;font-size:.8rem}
.fine-details label{display:flex;align-items:center;gap:10px;margin:12px 0;color:#a3bad3;font-size:.82rem}
.fine-details label input{max-width:95px}.results{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.results article{display:grid;gap:5px;flex:1 1 135px;padding:10px;border:1px solid #26354a}
.results small{color:#a3bad3}
footer{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:14px 22px;font-size:.8rem;color:#8ea7c6;border-top:1px solid #26354a}
footer button.apply{background:transparent;color:#35deb0;border-color:#2b6c61}
.notice{font-size:.78rem;color:#96acc9;padding:0 22px 12px;margin:0}
@media(max-width:700px){.editor label{flex:1 1 42%}.sequence label{flex:1 1 28%}}
</style>