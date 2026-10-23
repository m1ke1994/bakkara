/** NewBot-style editable row. Each next stake is rounded down to whole roubles. */
export function generateStakes(initial,multiplier,steps){
  const a=Number(initial),m=Number(multiplier),n=Number(steps)
  if(!Number.isFinite(a)||a<1||!Number.isFinite(m)||m<=1||m>10||!Number.isInteger(n)||n<1||n>20)return null
  const result=[Math.round(a*100)/100]
  while(result.length<n){const next=Math.floor(result.at(-1)*m+1e-8);if(!Number.isSafeInteger(next)||next<1)return null;result.push(next)}
  return result
}
export function describeStakes(stakes,odds=1.9){
  const k=Number(odds)
  if(!Array.isArray(stakes)||!stakes.length||stakes.length>20||!Number.isFinite(k)||k<=1||stakes.some(v=>!Number.isFinite(Number(v))||Number(v)<=0||Math.abs(Number(v)*100-Math.round(Number(v)*100))>1e-5))return null
  let lost=0;const rows=[]
  stakes.forEach((raw,index)=>{const stake=Number(raw);rows.push({step:index+1,stake,lostBefore:lost,bankThroughStep:Math.round((lost+stake)*100)/100,netIfWin:Math.round((stake*(k-1)-lost)*100)/100});lost=Math.round((lost+stake)*100)/100})
  return {rows,totalBankroll:lost,negativeSteps:rows.filter(r=>r.netIfWin<0).map(r=>r.step)}
}
