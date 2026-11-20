export const ms=v=>{if(v==null)return'—';const n=Number(v);return (Math.round(n*10)/10).toLocaleString('zh-CN')+' ms'}
export const seconds=v=>{const n=Number(v);return Math.round(n*10)/10}
