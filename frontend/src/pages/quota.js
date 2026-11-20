import{getBatch,groupQuota,topUpQuota}from'../api/batch.js';import{header}from'../components/common/header.js';import{table}from'../components/common/table.js';import{badge,quotaBadge}from'../components/common/badge.js';import{esc}from'../utils/html.js';import{ms}from'../utils/format.js';import{notify}from'../hooks/feedback.js';import{session}from'../stores/session.js';

/**
 * 批次额度页：展示所选批次的等额度 / 用量待核 / 已结清状态、两侧实际在库用时，
 * 以及本组当日额度（已发放 / 已用 / 预留 / 余额），并可为本组补额。
 * 不带批次编号时列出本组全部批次供选择。
 */
export async function quotaPage(el){
  const ident=Number(location.hash.split('/')[1]);
  const write=session.get().role!=='viewer';
  const quota=await groupQuota();
  const batch=ident?await getBatch(ident):null;
  const q=batch?.quota;
  const rows=[
    ['本组',esc(quota.group||'')],
    ['额度日期',esc(quota.day||'')],
    ['已发放',ms(quota.granted_ms)],
    ['已用（两侧实际用时）',ms(quota.used_ms)],
    ['预留（在途上限）',ms(quota.reserved_ms)],
    ['可补充余额',ms(quota.balance_ms)],
  ];
  const picker=(quota.batch_ids||[]).length
    ?`<section class="panel"><label>选择本组批次</label><select id="choose-batch"><option value="">请选择批次</option>${quota.batch_ids.map(id=>`<option value="${id}" ${id===ident?'selected':''}>批次 #${id}</option>`).join('')}</select><p class="helper">也可以从运行历史的额度状态列直接进入。</p></section>`
    :'';
  el.innerHTML=header('05','按结算组管理每日查询额度','每个批次开跑前在本组预留上限；余额不足只在本组排队，别组照常执行。查询结束按两侧实际在库用时扣减，跨日仍记在原预留日，余量退回。')+
  `<div class="split"><section class="panel">
    <div class="eyebrow">BATCH QUOTA</div>
    ${batch?`<div class="actions" style="margin-top:8px"><h2 style="margin:0">批次 #${batch.id}</h2>${badge(batch.status)}${quotaBadge(q)}</div>
      <p class="muted" style="margin-top:14px">${esc(batch.name)} · 结算组 ${esc(batch.group||'—')}</p>
      <div class="metrics">
        <div><small>预留上限</small><strong>${ms(q.reserved_ms)}</strong></div>
        <div><small>原始 / 候选实际在库</small><strong>${q.original_ms==null?'—':ms(q.original_ms)+' / '+ms(q.candidate_ms)}</strong></div>
        <div><small>合计实际用时</small><strong>${q.actual_ms==null?'—':ms(q.actual_ms)}</strong></div>
        <div><small>额度日</small><strong style="font-size:18px">${esc(q.day||'—')}</strong></div>
      </div>
      <p class="helper">${q.state==='waiting_quota'?'本组余额不足，批次保持排队；补额后自动继续，不影响其他结算组。':q.state==='reserved'?'批次已预留上限并执行中，结束后按两侧实际用时结算。':q.state==='unverified'?'结算中断且查不到用量凭据，预留继续保留，等待人工核对。':q.state==='settled'?'已按用量凭据结清，余量已退回本组当日余额。':q.state==='unrecorded'?'该批次在额度功能上线前已完成，没有额度凭据；后续批次正常计入。':'等待 worker 领取后按本组余额预留。'}</p>`
    :picker||'<div class="empty">选择一个批次查看额度状态。</div>'}
    ${batch?picker:''}
    ${write&&batch?`<h2 style="margin-top:26px">为本组补额</h2>
      <form id="topup">
        <label>补充毫秒数</label>
        <input name="amount_ms" type="number" min="1" max="600000" step="100" value="24000" required>
        <div class="actions"><button class="primary" type="submit">补充额度</button><a href="#history">返回运行历史</a></div>
      </form>
      <p class="helper">补额只增加当日可补充额度，不改变已用与预留；单次最多 600000 毫秒。补额后等待中的批次会自动继续。</p>`
    :write?'':`<p class="helper">观察员仅可查看额度，不能补额。</p>`}
  </section><aside class="panel"><h2>本组当日额度</h2>${table(['项目','数值'],rows)}<p class="helper">余额 = 已发放 − 已用 − 预留。预留是并发批次开跑前的上限占用，实际用时按两侧查询结束后的在库时间扣减，节余即时退回。</p><p class="helper">只读限制与查询限额保持不变：只允许 SELECT invoices，单条查询最长 2.5 秒、最多 1000 行。</p></aside></div>`;
  const choose=el.querySelector('#choose-batch');
  if(choose)choose.onchange=e=>{if(e.target.value)location.hash='quota/'+e.target.value};
  const form=el.querySelector('#topup');
  if(form)form.onsubmit=async e=>{e.preventDefault();const b=form.querySelector('button[type=submit]');b.disabled=true;
    try{const value=Number(new FormData(form).get('amount_ms'));const v=await topUpQuota(batch.id,value);notify('本组额度已补充，余额 '+ms(v.balance_ms));await quotaPage(el)}
    catch(err){notify(err.message)}finally{b.disabled=false}};
}
