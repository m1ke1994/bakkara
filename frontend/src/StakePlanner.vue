<script setup>
import {computed, ref} from 'vue'
import {calculateLadder} from './betPlanCalculator.js'

const amount=ref(15)
const multiplier=ref(2)
const odds=ref(1.9)
const steps=ref(8)
const plan=computed(()=>calculateLadder({stake:amount.value,multiplier:multiplier.value,odds:odds.value,steps:steps.value}))
const money=v=>new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(v)+' ₽'
</script>

<template>
  <section class="stake-planner" id="calculator" aria-label="Калькулятор шагов">
    <header class="planner-header">
      <div><small>ПАРАМЕТРЫ СТРАТЕГИИ</small><h2>Калькулятор шагов</h2></div>
      <span>Точность до копейки</span>
    </header>
    <div class="stake-inputs">
      <label>Начальная ставка, ₽ <input v-model.number="amount" type="number" min="0.01" step="0.01" /></label>
      <label>Множитель шага × <input v-model.number="multiplier" type="number" min="1.01" max="10" step="0.01" /></label>
      <label>Коэффициент БК <input v-model.number="odds" type="number" min="1.01" step="0.01" /></label>
      <label>Количество шагов <input v-model.number="steps" type="number" min="1" max="20" step="1" /></label>
    </div>
    <p class="planner-note">Множитель ×2 или ×2,5 увеличивает сумму следующей ставки. Коэффициент БК (например, 1,90) определяет выплату. Это разные параметры.</p>
    <p v-if="!plan" class="planner-warning" role="alert">Проверьте параметры: положительная сумма, множитель 1,01–10, коэффициент БК больше 1, целое число шагов 1–20.</p>
    <template v-else>
      <div class="planner-summary">
        <article><small>Полный бюджет серии</small><strong>{{money(plan.totalBankroll)}}</strong></article>
        <article><small>Прибыль при WIN на шаге 1</small><strong>{{money(plan.profitFirstStep)}}</strong></article>
        <article><small>Безубыточная вероятность</small><strong>{{plan.breakEvenChance.toFixed(2)}}%</strong></article>
      </div>
      <p v-if="plan.negativeSteps.length" class="planner-warning" role="alert">WIN на шагах {{plan.negativeSteps.join(', ')}} не покрывает предыдущие проигрыши.</p>
      <div class="planner-scroll"><table>
        <thead><tr><th>Шаг</th><th>Ставка</th><th>Проиграно до шага</th><th>Нужный бюджет</th><th>Итог серии при WIN</th></tr></thead>
        <tbody><tr v-for="row in plan.rows" :key="row.step"><td>{{row.step}}</td><td>{{money(row.stake)}}</td>
          <td>{{money(row.lostBefore)}}</td><td>{{money(row.bankThroughStep)}}</td>
          <td :class="row.netIfWin>=0 ? 'win' : 'lose'">{{money(row.netIfWin)}}</td></tr></tbody>
      </table></div>
      <p class="planner-note">Калькулятор показывает размеры ставок и исход серии, но не вероятность выигрыша и не отправляет ставки.</p>
    </template>
  </section>
</template>

<style scoped>
.stake-planner{color:#e8f3ff;background:#101722;border:1px solid #283750;border-radius:12px;padding:22px;margin:16px 0}
.planner-header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;border-bottom:1px solid #283750;padding-bottom:16px}
.planner-header h2{margin:6px 0 0;font-size:1.4rem}.planner-header small{color:#e4b86e;letter-spacing:1px}.planner-header span{color:#8ca5c3;font-size:.85rem}
.stake-inputs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}
.stake-inputs label{display:flex;flex-direction:column;gap:8px;color:#9eb2c9;font-size:.84rem;font-weight:650}
.stake-inputs input{width:100%;box-sizing:border-box;border:1px solid #344961;border-radius:7px;background:#111e2e;color:#fff;padding:10px;font:inherit}
.planner-note{color:#9eb2c9;font-size:.87rem;line-height:1.5}.planner-warning{color:#ff9fa9;font-weight:650}
.planner-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0}
.planner-summary article{display:grid;gap:8px;border:1px solid #283750;padding:14px;border-radius:8px}
.planner-summary small{color:#9eb2c9}.planner-summary strong{font-size:1.25rem}
.planner-scroll{overflow:auto;max-height:460px;border:1px solid #283750;border-radius:6px}
.planner-scroll table{border-collapse:collapse;width:100%;min-width:740px;text-align:left}
.planner-scroll th,.planner-scroll td{padding:11px 14px;border-bottom:1px solid #283750;font-variant-numeric:tabular-nums}
.planner-scroll th{position:sticky;top:0;background:#182334;color:#b4c7de}
.win{color:#5ce7b1;font-weight:750}.lose{color:#ff8f98;font-weight:750}
@media(max-width:860px){.stake-inputs{grid-template-columns:repeat(2,minmax(0,1fr))}.planner-summary{grid-template-columns:1fr}}
@media(max-width:440px){.stake-inputs{grid-template-columns:1fr}}
</style>
