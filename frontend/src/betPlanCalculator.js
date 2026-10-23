/** Calculator-only progression. Round each stake up to a kopeck. */
export function calculateLadder({ stake, multiplier = 2, odds, steps }) {
  const base=Number(stake), factor=Number(multiplier), price=Number(odds), n=Number(steps)
  if (!Number.isFinite(base) || base<=0 || !Number.isFinite(factor) || factor<=1 || factor>10 ||
      !Number.isFinite(price) || price<=1 || !Number.isInteger(n) || n<1 || n>20) return null
  let current=Math.ceil(base*100-1e-8), lost=0
  const first=current, rows=[]
  for (let i=0; i<n; i++) {
    if (i) current=Math.ceil(current*factor-1e-8)
    if (!Number.isSafeInteger(current) || !Number.isSafeInteger(current+lost)) return null
    rows.push({step:i+1, stake:current/100, lostBefore:lost/100,
      bankThroughStep:(lost+current)/100, netIfWin:Math.round(current*(price-1)-lost)/100})
    lost+=current
  }
  return { rows, totalBankroll:lost/100,
    profitFirstStep:Math.round(first*(price-1))/100,
    breakEvenChance:100/price,
    negativeSteps:rows.filter(r=>r.netIfWin<0).map(r=>r.step) }
}
