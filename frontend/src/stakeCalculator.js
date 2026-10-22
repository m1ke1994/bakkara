/**
 * Read-only budgeting models; no betting or server integration.
 * 'double' reproduces NewBot's fixed x2 progression.
 * 'recover' sizes the next stake to cover past losses and preserve the
 * starting step's net profit at the chosen constant coefficient.
 */
export function calculateLadder({ stake, odds, steps, mode = 'double' }) {
  const base = Number(stake)
  const k = Number(odds)
  const count = Number(steps)
  if (!Number.isFinite(base) || base <= 0 || !Number.isFinite(k) ||
      k <= 1 || !Number.isInteger(count) || count < 1 || count > 20 ||
      !['double', 'recover'].includes(mode)) return null

  const firstCents = Math.ceil(base * 100 - 1e-8)
  let spent = 0
  let current = firstCents
  const expectedProfit = firstCents * (k - 1)
  const rows = []
  for (let index = 0; index < count; index++) {
    if (index && mode === 'double') current *= 2
    else if (index && mode === 'recover') {
      current = Math.max(firstCents,
        Math.ceil((spent + expectedProfit) / (k - 1) - 1e-8))
    }
    if (!Number.isSafeInteger(current) || !Number.isSafeInteger(spent + current)) return null
    const profit = (current * (k - 1) - spent) / 100
    rows.push({
      step: index + 1,
      stake: current / 100,
      lostBefore: spent / 100,
      bankThroughStep: (spent + current) / 100,
      netIfWin: Math.round(profit * 100) / 100,
    })
    spent += current
  }
  return {
    rows,
    totalBankroll: spent / 100,
    targetNetProfit: Math.round(expectedProfit) / 100,
    anyNegativeProfit: rows.some(row => row.netIfWin < 0),
    breakEvenChance: 100 / k,
  }
}
