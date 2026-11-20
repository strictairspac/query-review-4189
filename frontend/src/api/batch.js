import{api,post}from'./client.js';

export const listBatches=()=>api('/batches');
export const getBatch=id=>api('/batches/'+id);
export const createBatch=data=>post('/batches',data);
export const groupQuota=day=>api('/batches/quota'+(day?'?day='+encodeURIComponent(day):''));
export const topUpQuota=(batchId,amountMs)=>post('/batches/'+batchId+'/quota/topup',{amount_ms:amountMs});
