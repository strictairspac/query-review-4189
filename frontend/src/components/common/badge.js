import{STATUSES,QUOTA_STATES}from'../../constants/enums.js';import{esc}from'../../utils/html.js';
export const badge=s=>'<span class="badge '+esc(s)+'">'+esc(STATUSES[s]||s)+'</span>';
export const quotaBadge=r=>'<span class="badge quota '+esc(r.state)+'">'+esc(QUOTA_STATES[r.state]||r.label||r.state)+'</span>';
