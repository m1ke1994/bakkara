const api = import.meta.env?.VITE_API_BASE_URL || 'http://127.0.0.1:8010'

async function request(method, plan) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10000)
  try {
    const response = await fetch(`${api}/api/stake-plan`, {
      method,
      signal: controller.signal,
      cache: 'no-store',
      ...(plan ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(plan) } : {}),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail
      : 'Сервер отклонил параметры: проверьте суммы, множитель и количество шагов')
    return data
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Сервер не ответил. Проверьте сохранённый ряд перед повторной попыткой')
    if (error instanceof TypeError) throw new Error('Сервер недоступен. Проверьте соединение и повторите попытку')
    if (error instanceof SyntaxError) throw new Error('Сервер вернул некорректный ответ. Настройки не подтверждены')
    throw error
  } finally { clearTimeout(timeout) }
}

export const loadStakePlan = () => request('GET')
export const saveStakePlan = plan => request('PUT', plan)
