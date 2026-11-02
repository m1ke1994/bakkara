<script setup>
import { ref } from 'vue'
const props = defineProps({ api: { type: String, default: '' }, initialError: { type: String, default: '' } })
const emit = defineEmits(['authorized'])
const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
async function login() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const response = await fetch(props.api + '/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ email: email.value, password: password.value }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || 'Не удалось войти')
    password.value = ''
    emit('authorized', data.email)
  } catch (ex) {
    error.value = ex.message || 'Ошибка подключения'
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <main class="login-shell">
    <div class="glow glow-a"></div><div class="glow glow-b"></div>
    <div class="login-layout">
      <section class="login-story">
        <div class="brand"><span class="brand-icon">B</span> BAKKARA <span>CONTROL</span></div>
        <div class="eyebrow-login"><span class="status-dot"></span> PRIVATE ANALYTICS PLATFORM</div>
        <h1>Контроль каждой<br/><em>раздачи.</em></h1>
        <p>Персональная платформа наблюдения за баккарой. Прогнозы мастей, история раздач и аналитика в режиме реального времени.</p>
        <div class="login-metrics"><span>01&nbsp; МОНИТОРИНГ</span><span>02&nbsp; СТРАТЕГИИ</span><span>03&nbsp; ИСТОРИЯ</span></div>
      </section>
      <form class="login-card" @submit.prevent="login">
        <div class="card-top"><span>АВТОРИЗАЦИЯ</span><span>◈ PRIVATE ACCESS</span></div>
        <h2>Вход в систему</h2>
        <p>Введите персональные данные для доступа к панели.</p>
        <label for="dashboard-email">Электронная почта</label>
        <input id="dashboard-email" v-model.trim="email" type="email" autocomplete="username" placeholder="name@example.com" required />
        <label for="dashboard-password">Пароль</label>
        <input id="dashboard-password" v-model="password" type="password" autocomplete="current-password" placeholder="Введите пароль" required />
        <div v-if="error || initialError" role="alert" class="login-error">{{ error || initialError }}</div>
        <button type="submit" :disabled="busy">{{ busy ? 'Проверка…' : 'Войти в панель' }} <span>→</span></button>
        <div class="card-bottom"><span>Защищённый доступ HTTPS</span><span>BAKKARA / 2026</span></div>
      </form>
    </div>
  </main>
</template>
<style scoped>
.login-shell{min-height:100vh;display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden;padding:56px clamp(20px,5vw,100px);background:radial-gradient(ellipse at 80% 0%,#122a46 0%,transparent 45%),#07101b;color:#f3f7ff}
.login-layout{position:relative;z-index:1;display:grid;grid-template-columns:minmax(0,1.2fr) minmax(340px,.8fr);gap:clamp(40px,8vw,160px);width:min(1180px,100%);align-items:center}
.glow{position:absolute;width:450px;height:450px;filter:blur(110px);opacity:.2;pointer-events:none;border-radius:50%}
.glow-a{left:-260px;bottom:-180px;background:#126d9d}.glow-b{right:-260px;top:-160px;background:#2462bb}
.brand{display:flex;align-items:center;gap:12px;font-size:14px;letter-spacing:.18em;font-weight:800;color:#fff}.brand span:last-child{color:#8cb2cd}.brand-icon{display:grid;place-items:center;height:36px;width:36px;border-radius:10px;background:linear-gradient(135deg,#6cdeef,#3187df);color:#041220;font-size:20px;letter-spacing:0}
.eyebrow-login{display:flex;align-items:center;gap:12px;margin-top:98px;color:#84c4dc;font-size:11px;font-weight:750;letter-spacing:.22em}.status-dot{width:7px;height:7px;background:#70ebba;border-radius:50%;box-shadow:0 0 15px #70ebba}
.login-story h1{margin:24px 0;font-size:clamp(47px,5.3vw,78px);line-height:1.08;letter-spacing:-.06em;font-weight:750}.login-story h1 em{font-style:normal;color:#6fcfeb}.login-story p{max-width:525px;color:#a2b5ce;font-size:17px;line-height:1.8}.login-metrics{display:flex;flex-wrap:wrap;gap:24px;margin-top:80px;color:#718da8;font-size:10px;font-weight:800;letter-spacing:.11em}
.login-card{border:1px solid #2c3f56;padding:clamp(26px,3vw,44px);background:linear-gradient(160deg,rgba(22,37,57,.98),rgba(12,22,37,.96));border-radius:20px;box-shadow:0 25px 100px #0005}
.card-top,.card-bottom{display:flex;justify-content:space-between;gap:10px;color:#7a9ab8;font-size:10px;font-weight:800;letter-spacing:.1em}.card-top span:first-child{color:#8ccbea}
.login-card h2{font-size:30px;letter-spacing:-.04em;margin:44px 0 8px}.login-card p{color:#98adc6;font-size:14px;margin:0 0 36px;line-height:1.6}
.login-card label{display:block;color:#c2d5e6;font-size:12px;font-weight:700;margin:20px 0 9px}
.login-card input{width:100%;background:#0b1727;border:1px solid #334c67;border-radius:10px;padding:16px;color:#f3f7ff;font:inherit;outline:none}.login-card input:focus{border-color:#6acbea;box-shadow:0 0 0 3px #6acbea20}
.login-card button{margin-top:30px;width:100%;display:flex;justify-content:space-between;align-items:center;border-radius:10px;padding:17px 20px;background:#69cbe8;color:#071527;font-size:14px;font-weight:850}.login-card button:disabled{opacity:.65}.login-card button span{font-size:20px}
.login-error{margin-top:18px;color:#ff9d9d;font-size:13px}.card-bottom{margin-top:42px;padding-top:20px;border-top:1px solid #2c3f56;letter-spacing:0}
@media(max-width:850px){.login-layout{grid-template-columns:1fr;gap:48px;max-width:520px}.eyebrow-login{margin-top:45px}.login-story h1{font-size:50px}.login-metrics{margin-top:32px}.login-card{padding:28px}.login-shell{align-items:flex-start;padding-top:36px}}
</style>
