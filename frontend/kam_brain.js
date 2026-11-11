const inr=n=>"₹"+Number(n).toLocaleString("en-IN");
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const sign=(n,f=x=>x)=>`<span class="${n<0?"neg":n>0?"pos":""}">${n>0?"+":""}${f(n)}</span>`;
const FAIL={rate_limited:"was busy",forbidden:"is unavailable",timeout:"was slow",server_error:"had an error"};
const DRV_COLOR={
  stock_outs:"var(--loss)",
  pricing_gaps:"var(--gold)",
  visibility_drop:"var(--blue)",
  cancellations:"var(--violet)",
  unexplained_residual:"rgba(232,230,227,.38)"
};
const SUM={
  run_diagnosis:r=>`Found ${r.total_lost_orders} lost orders (${inr(r.total_lost_revenue)})`,
  get_out_of_stock_skus:r=>r.assessable===false?"Stock data missing":`${r.total_oos_count??0} products out of stock`,
  get_pricing_gaps:r=>r.assessable===false?"Competitor price data missing":`${r.overpriced_sku_count??0} overpriced products`,
  get_cancellation_breakdown:r=>r.assessable===false?"Cancellation data missing":`Cancellations ${r.baseline_cancellation_rate_pct??"N/A"}% to ${r.current_cancellation_rate_pct??"N/A"}%`,
  get_visibility_funnel:r=>r.assessable===false?"Visibility data missing":"Checked search visibility",
  build_action_plan:r=>`${r.actions?.length||0} actions, ${r.estimated_combined_recovery_rs||""}`,
  commit_action:r=>`Committed ${r.commitment?.action_id} due ${r.commitment?.due_date}`,
  schedule_followup:r=>`Follow-up: ${r.followup_date}`,
  send_call_summary:()=>"Wrote the call summary",
  get_metric_comparison:r=>`Sales ${r.metrics?.sales_gmv?.delta_pct}%, ASP ${r.metrics?.asp?.delta_pct}%`,
  get_seller_overview:()=>"Loaded the seller account"
};
export function createKamBrainController({rootEl,summaryEl,getBranding}){
  const b={m:null,diag:null,oos:null,price:null,plan:null,commits:[],prior:[],fu:null,tools:[],fail:null,prov:"-",bad:0,follow:false};
  const agentName=()=>getBranding?.()?.agentName||"assistant";
  const agentEsc=()=>esc(agentName());
  const upsert=c=>{const i=b.commits.findIndex(x=>x.action_id===c.action_id);i<0?b.commits.push(c):b.commits[i]=c};
  function table(){
    const m=b.m?.metrics;if(!m)return"<p>Loading the account snapshot…</p>";
    const f=b.m.followup,hd=f?"<th>3-day pace</th>":"";
    const row=(n,a,c,d,p)=>`<tr><td>${n}</td><td>${a}</td><td>${c}</td><td>${d}</td>${f?`<td>${p}</td>`:""}</tr>`;
    const oosObj=m.out_of_stock_skus||{},oosOk=oosObj.assessable!==false&&oosObj.current!=null;
    const oosBase=oosOk?oosObj.baseline:'<span class="empty">Not assessable</span>';
    const oosCurr=oosOk?oosObj.current:'<span class="empty">Not assessable</span>';
    const oosDiff=oosOk?sign(-oosObj.delta,x=>-x):'<span class="empty">Missing stock column</span>';
    const oosFu=f?(f.out_of_stock_skus!=null?`${f.out_of_stock_skus} <small>target ${f.oos_target_max} or fewer</small>`:'<span class="empty">Not assessable</span>'):"";

    const cancObj=m.cancellation_rate_pct||{},cancOk=cancObj.assessable!==false&&cancObj.current!=null;
    const cancBase=cancOk?`${cancObj.baseline}%`:'<span class="empty">Not assessable</span>';
    const cancCurr=cancOk?`${cancObj.current}%`:'<span class="empty">Not assessable</span>';
    const cancDiff=cancOk?sign(-cancObj.delta_pp,x=>-x+" pts"):'<span class="empty">Missing table</span>';
    const cancFu=f?(f.cancellation_rate_pct!=null?`${f.cancellation_rate_pct}% <small>target ${f.cancellation_target_max}% or less</small>`:'<span class="empty">Not assessable</span>'):"";

    const aspDelta=Math.abs(m.asp.delta_pct||0)<=3?"Flat":sign(m.asp.delta_pct,x=>x+"%");
    const prevLbl=esc(b.m.baseline_label||"Last month"),currLbl=esc(b.m.current_label||"This month");
    return`<div class="tbl-wrap"><table class="kb-table"><thead><tr><th>Metric</th><th>${prevLbl}</th><th>${currLbl}</th><th>Change</th>${hd}</tr></thead><tbody>`+
      row("Sales (GMV)",inr(m.sales_gmv.baseline),inr(m.sales_gmv.current),sign(m.sales_gmv.delta_pct,x=>x+"%"),f&&inr(f.sales_gmv_pace)+` <small class="${(f.sales_vs_crisis_pct||0)>=0?"pos":"neg"}">${(f.sales_vs_crisis_pct||0)>=0?"+":""}${f.sales_vs_crisis_pct}%</small>`)+
      row("Orders placed",m.orders.baseline,m.orders.current,sign(m.orders.delta),f&&f.orders_pace)+
      row("Average price",inr(m.asp.baseline),inr(m.asp.current),aspDelta,f&&inr(f.asp))+
      row("Out-of-stock SKUs",oosBase,oosCurr,oosDiff,oosFu)+
      row("Cancellation rate",cancBase,cancCurr,cancDiff,cancFu)+
      `</tbody></table></div><p style="margin-top:10px;color:var(--mute)">${esc(b.m.volume_vs_price_finding||"")}</p>`;
  }
  function prior(){
    if(!b.prior.length)return"";
    return`<section class="card"><h2>Prior Commitments Reviewed (3 Days Ago)<span class="chip ok">3-Day Follow-Up</span></h2>`+
      b.prior.map(c=>`<div class="li"><span><b>${esc(c.action_id)}</b> ${esc(c.action||c.action_summary||"")}<br><small style="color:var(--mute)">${esc(c.progress_note||c.target_metric||"")}</small></span><span>${esc(String(c.status||"").replace("_"," "))}</span></div>`).join("")+`</section>`;
  }
  function diag(){
    if(!b.diag)return`<section class="card locked"><h2>Root-Cause Revenue Attribution</h2><p>${agentEsc()} fills this in after checking your orders, stock and prices.</p></section>`;
    const d=b.diag.drivers||[],miss=b.diag.missing_signals||[];
    const barSegs=d.filter(x=>x.share_pct>0).map((x,i)=>{
      const c=DRV_COLOR[x.driver]||["var(--loss)","var(--gold)","var(--blue)","var(--violet)"][i]||"var(--mute)";
      return`<i style="width:${x.share_pct}%;background:${c}" title="${esc(x.title)}: ${x.share_pct}%"></i>`;
    }).join("");
    const missNote=miss.length?`<p class="missing-note">Missing data signals (${miss.map(esc).join(", ")}) cannot be attributed; any gap remains marked as not assessable or stays in the unexplained residual.</p>`:"";
    return`<section class="card"><h2>Root-Cause Revenue Attribution<span class="chip ok">Live Diagnosis Verified</span></h2>
      <div class="bar">${barSegs||'<i style="width:100%;background:rgba(232,230,227,.18)"></i>'}</div>
      ${d.map((x,i)=>{
        const c=DRV_COLOR[x.driver]||["var(--loss)","var(--gold)","var(--blue)","var(--violet)"][i]||"var(--mute)";
        const conf=esc(x.confidence||"medium");
        const metricTxt=x.assessable===false?"Not assessable":`${x.share_pct}% (${x.lost_orders} orders)`;
        return`<div class="drv"><span style="background:${c}"></span><b>${esc(x.title)} <em class="badge-conf conf-${conf}">${conf}</em></b><b>${metricTxt}</b><small>${esc(x.evidence)}</small></div>`;
      }).join("")}
      ${missNote}
      <div class="big"><div class="neg">-${inr(b.diag.total_lost_revenue)}<small>sales lost in period</small></div><div class="neg">-${b.diag.total_lost_orders}<small>orders lost</small></div></div></section>`;
  }
  function evidence(){
    const o=b.oos?.assessable===false?null:(b.oos?.top_oos_skus||[]).slice(0,4);
    const p=b.price?.assessable===false?null:(b.price?.pricing_gaps||[]).filter(x=>!(b.price.repriced_skus||[]).some(s=>s.sku_id===x.sku_id));
    if(o===null&&p===null)return"";
    if(!o?.length&&!p?.length&&b.oos?.assessable!==false&&b.price?.assessable!==false)return"";
    return`<section class="card two"><div><h2>Out of stock, biggest losses</h2>${o===null?"<p class=\"empty\">Not assessable without stock columns.</p>":(o.map(x=>`<div class="li"><span>${esc(x.sku_id)} ${esc(x.name)}</span><span class="neg">${inr(x.estimated_lost_rev_per_day)}/day</span></div>`).join("")||"<p class=\"empty\">No out-of-stock losses identified.</p>")}</div>
      <div><h2>Priced above competitors</h2>${p===null?"<p class=\"empty\">Not assessable without competitor_lowest_price.</p>":(p.slice(0,5).map(x=>`<div class="li"><span>${esc(x.sku_id)} ${esc(x.name)}</span><span>${inr(x.seller_price)} vs ${inr(x.competitor_lowest_price)}</span></div>`).join("")||"<p class=\"empty\">No overpriced active SKUs flagged.</p>")}</div></section>`;
  }
  function plan(){
    if(!b.plan)return`<section class="card locked"><h2>Prioritised Recovery Plan</h2><p>${agentEsc()} proposes actions once the diagnosis is done, then asks you to commit.</p></section>`;
    return`<section class="card"><h2>Prioritised Recovery Plan<span class="chip">${b.commits.length} committed</span></h2><div class="acts">${(b.plan.actions||[]).map(a=>{
      const c=b.commits.find(x=>x.action_id===a.action_id);
      return`<div class="act ${c?"done":""}"><span class="st ${c?"pos":""}">${c?"Committed, due "+esc(c.due_date):"Due "+esc(a.recommended_due)}</span><b>${esc(a.title)}</b><span>${esc(a.details)}</span><span class="rec">Indicative recovery ${esc(a.estimated_recovery_range_rs)}</span></div>`}).join("")}</div>
      <p style="margin-top:10px;color:var(--mute);font-size:13px">${esc(b.plan.assumptions||"")}</p></section>`;
  }
  function trail(){
    const f=b.fail;
    return`<section class="card"><h2>What ${agentEsc()} checked</h2><div class="chips"><span class="chip">Model ${esc(b.prov)}</span>
      <span class="chip ${b.bad?"bad":"ok"}">${b.bad?`${b.bad} numbers not verified`:"Every number verified"}</span>
      ${f?`<span class="chip warn">Switched models: ${esc(f.from)} ${FAIL[f.reason_code]||"failed"}</span>`:""}</div>
      <div class="trail">${b.tools.slice(-8).map(t=>`<div class="t"><span><code>${esc(t.name)}</code> ${esc(t.s||"")}</span><span>${t.ms==null?"running…":t.ms+" ms"}</span></div>`).join("")||"Nothing yet. Tool calls show up here live."}</div></section>`;
  }
  function render(){rootEl.innerHTML=`<section class="card"><h2>Account snapshot${b.follow?'<span class="chip ok">3-Day Follow-Up</span>':""}</h2>${table()}</section>${prior()}${diag()}${evidence()}${plan()}${trail()}`}
  function summary(d){
    summaryEl.classList.remove("hidden");
    summaryEl.innerHTML=`<section class="card summary-card"><h2>Call Summary &amp; Mock WhatsApp Recap</h2><b>${esc(d.shop_name)}</b>
      <p style="margin-top:8px">Key issues</p><ul>${(d.key_issues||[]).map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
      <p>Committed actions</p><ul>${(d.commitments||[]).map(c=>`<li><b>${esc(c.action_id)}</b> ${esc(c.action_summary)}, ${esc(c.owner)}, due ${esc(c.due_date)}</li>`).join("")||"<li>None yet</li>"}</ul>
      <p>Next call: ${esc(d.followup?.followup_date||"in 3 days")}. Targets: ${esc((d.followup?.success_targets||[]).join("; "))}</p>
      <p style="margin-top:12px">Mock WhatsApp / SMS dispatch</p><pre id="whatsappPreview">${esc(d.whatsapp_text)}</pre>
      <div class="row"><button id="btnCopyWhatsapp" class="btn sm" type="button">Copy message</button><button id="btnDownloadSummary" class="btn sm" type="button">Download .txt</button></div></section>`;
    summaryEl.querySelector("#btnCopyWhatsapp").onclick=e=>navigator.clipboard?.writeText(d.whatsapp_text).then(()=>e.target.textContent="Copied");
    summaryEl.querySelector("#btnDownloadSummary").onclick=()=>{const slug=agentName().toLowerCase().replace(/[^a-z0-9_-]+/g,"-")||"kam";const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([d.whatsapp_text],{type:"text/plain"}));a.download=`${slug}-call-summary.txt`;a.click()};
    summaryEl.scrollIntoView({behavior:"smooth",block:"nearest"});
  }
  render();
  return{
    refresh:render,
    onDemoData(d){Object.assign(b,{diag:null,oos:null,price:null,plan:null,commits:[],prior:[],fu:null,tools:[],fail:null,bad:0});b.m=d.metrics;b.follow=d.metrics?.snapshot==="followup";render()},
    onSessionStarted(p){Object.assign(b,{diag:null,oos:null,price:null,plan:null,commits:[],fu:null,tools:[],fail:null,bad:0});
      b.m=p.demo_data?.metrics||b.m;b.prior=p.session?.prior_commitments||[];b.follow=p.session?.mode==="followup";render()},
    onPhase(){},
    onToolCall(d){b.tools.push({name:d.name,ms:null});render()},
    onToolResult(d){
      const r=d.result||{},t=[...b.tools].reverse().find(x=>x.name===d.name&&x.ms==null);
      if(t){t.ms=Math.round(d.latency_ms);t.s=(SUM[d.name]||(()=>""))(r)}
      if(d.name==="run_diagnosis")b.diag=r;else if(d.name==="get_out_of_stock_skus")b.oos=r;else if(d.name==="get_pricing_gaps")b.price=r;
      else if(d.name==="build_action_plan")b.plan=r;else if(d.name==="get_metric_comparison"&&r.metrics){b.m=r;b.follow=r.snapshot==="followup"}
      else if(d.name==="commit_action"&&r.commitment)upsert(r.commitment);else if(d.name==="schedule_followup")b.fu=r;
      render();
    },
    onFailover(d){b.fail=d;render()},    // sanitized shape only: {from,to,reason_code}
    onLatency(d){b.prov=d.provider||b.prov;b.bad+=(d.ungrounded_numbers||[]).length;render()},
    onDone(d){(d.commitments||[]).forEach(upsert);if(d.followup_schedule)b.fu=d.followup_schedule;render()},
    onSummaryCard:summary
  };
}
