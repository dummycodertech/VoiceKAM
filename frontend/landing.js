// Landing helpers. Reads /api/scenarios and drives the existing #selScenario; touches nothing else.
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const scrollBehavior=()=>matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth";
async function init(){
  addEventListener("keydown",e=>{
    if(e.defaultPrevented||(e.key!==" "&&e.code!=="Space"))return;
    const a=e.target?.closest?.('header.top a[href^="#"], .cta a[href^="#"]');
    if(a){e.preventDefault();a.click()}
  });
  const box=$("heroStores");if(!box)return;
  try{
    const r=await fetch("/api/scenarios");if(!r.ok)throw 0;
    const d=await r.json(),list=Array.isArray(d)?d:(d.scenarios||[]);if(!list.length)throw 0;
    box.innerHTML=list.map(s=>`<button class="store" type="button" data-id="${esc(s.scenario_id)}"><span class="sid">${esc(s.scenario_id)}</span><span><b>${esc(s.title)}</b><small>${esc(s.description)}</small></span><i aria-hidden="true">Call &rarr;</i></button>`).join("");
  }catch{box.innerHTML='<p class="empty">Demo stores appear once the server is awake. Refresh in a few seconds.</p>';return}
  box.addEventListener("click",e=>{
    const b=e.target.closest(".store");if(!b)return;
    $("btnDataModeDemo")?.click();
    const sel=$("selScenario");
    if(sel){sel.value=b.dataset.id;sel.dispatchEvent(new Event("change"))}
    $("demo")?.scrollIntoView({behavior:scrollBehavior()});
  });
}
addEventListener("DOMContentLoaded",init);
