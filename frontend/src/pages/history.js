import{batches}from'../stores/batch.js';import{groupQuota}from'../api/batch.js';import{header}from'../components/common/header.js';import{table}from'../components/common/table.js';import{badge,quotaBadge}from'../components/common/badge.js';import{esc}from'../utils/html.js';import{ms}from'../utils/format.js';
export async function historyPage(el){
  const rows=await batches.load();
  const quota=await groupQuota().catch(()=>null);
  el.innerHTML=header('03','运行记录按批次留痕','任务由 Redis 分发到独立 worker；每个批次保存状态、候选 SQL 与执行证据。开跑前在本组预留额度上限，余额不足只在本组排队，别组照常执行。')+
  (quota?`<div class="metrics"><div><small>本组 · ${esc(quota.group||'')}</small><strong>${ms(quota.balance_ms)}</strong></div><div><small>当日已用</small><strong>${ms(quota.used_ms)}</strong></div><div><small>在途预留</small><strong>${ms(quota.reserved_ms)}</strong></div><div><small>已发放</small><strong>${ms(quota.granted_ms)}</strong></div></div>`:'')+
  `<div class="panel"><div class="actions" style="margin-top:0;margin-bottom:20px"><button id="refresh">刷新队列</button><a href="#review">新建复核 →</a><a href="#quota">额度管理 →</a><span class="muted">${rows.length} 个批次</span></div>${table(['编号 / 批次','基准样本','提交时间','运行状态','额度状态','执行证据'],rows.map(x=>['<span class="mono">#'+x.id+'</span><br>'+esc(x.name),'#'+x.sample_id,esc(x.created_at),badge(x.status),`<a href="#quota/${x.id}">${quotaBadge(x.quota)}</a>`,['passed','different','failed'].includes(x.status)?`<a href="#evidence/${x.id}">打开证据 →</a>`:'后台处理中，请刷新']))}</div>`;
  el.querySelector('#refresh').onclick=()=>historyPage(el);
}
