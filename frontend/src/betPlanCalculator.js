/** NewBot-style editable row. Each next stake is rounded down to whole roubles. */
export function generateStakes(initial,multiplier,steps){
  const a=Number(initial),m=Number(multiplier),n=Number(steps)
  if(!Number.isFinite(a)||a<1||!Number.isFinite(m)||m<=1||m>10||!Number.isInteger(n)||n<1||n>20)return null
  const result=[Math.round(a*100)/100]
  while(result.length<n){const next=Math.floor(result.at(-1)*m+1e-8);if(!Number.isSafeInteger(next)||next<1)return null;result.push(next)}
  return result
}
export function describeStakes(stakes,odds=1.9){
  const k=decimalUnits(odds,4),amounts=Array.isArray(stakes)?stakes.map(v=>decimalUnits(v,2)):[]
  if(!amounts.length||amounts.length>20||k===null||k<=10000n||k>100000000n||amounts.some(v=>v===null||v<=0n||v>100000000000n))return null
  let lost=0n;const rows=[]
  amounts.forEach((cents,index)=>{
    const net=cents*(k-10000n)-lost*10000n
    // Monetary half-up rounding, including negative results. Arithmetic is
    // decimal throughout; convert only final display values to JS numbers.
    const rounded=(net<0n?-1n:1n)*((abs(net)+5000n)/10000n)
    rows.push({step:index+1,stake:Number(cents)/100,lostBefore:Number(lost)/100,bankThroughStep:Number(lost+cents)/100,netIfWin:Number(rounded)/100})
    lost+=cents
  })
  return {rows,totalBankroll:Number(lost)/100,negativeSteps:rows.filter(r=>r.netIfWin<0).map(r=>r.step)}
}

function abs(value){return value<0n?-value:value}
function decimalUnits(value,scale){
  if(!['number','string'].includes(typeof value))return null
  const text=String(value)
  if(text.length>32||!new RegExp(`^\\d+(\\.\\d{1,${scale}})?$`).test(text))return null
  const [whole,fraction='']=text.split('.')
  return BigInt(whole)*10n**BigInt(scale)+BigInt(fraction.padEnd(scale,'0'))
}
