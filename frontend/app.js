import{AudioPlaybackQueue}from"/static/audio.js";
import{createKamBrainController}from"/static/kam_brain.js";
import{createVoiceViz}from"/static/viz.js";
const S={inCall:false,sessionId:null,mode:"inbound",language:"en-IN",headphones:false,tts:"auto",orb:"idle",t0:0,timer:null,rec:null,recognizing:false,eou:null,silence:null,reprompted:false,interim:"",final:"",sttEnd:0,abort:null,done:0,hook:null,projectName:"VoiceKAM",agentName:"metis",sellerSalutation:"seller partner"};
const el={};
const $=id=>document.getElementById(id);
const orbCopy=s=>{
  const a=S.agentName;
  if(s==="listening")return["Listening",`Go ahead, ${a} replies after a short pause`];
  if(s==="thinking")return["Checking",`${a} is looking at your store data`];
  if(s==="speaking")return["Speaking",""];
  return["Ready",`Press Start call to talk with ${a}`];
};
const repromptText=lang=>{
  const sal=S.sellerSalutation||"seller partner";
  if(lang==="hi-IN")return`${sal}, क्या आप सुन रहे हैं? आप बोल सकते हैं।`;
  if(lang==="hinglish")return`${sal}, aap line par hain? Aap bata sakte hain.`;
  return`${sal}, are you still there? I am listening whenever you are ready.`;
};
const SR=()=>window.SpeechRecognition||window.webkitSpeechRecognition||null;

const queue=new AudioPlaybackQueue({
  onSpeakingStart(){setOrb("speaking");if(!S.headphones)stopRec();el.btnInterrupt.disabled=false},
  onSpeakingEnd(){el.btnInterrupt.disabled=true;if(S.inCall&&S.orb!=="thinking")listen()},
  onTtsProviderUsed(p){el.ttsBadge.textContent="TTS: "+p}
});

function setOrb(s){
  S.orb=s;const copy=orbCopy(s);el.micOrb.dataset.state=s;el.micOrb.textContent=copy[0];   // micOrb stays text-only
  el.orbLabel.textContent=s==="speaking"?(S.headphones?`${S.agentName} is speaking. Talk to interrupt.`:`${S.agentName} is speaking. Press Space to interrupt.`):copy[1];
}
const showError=m=>{el.errorBannerText.textContent=m;el.errorBanner.classList.remove("hidden")};
function caption(who,text,{interim=false,id=null}={}){
  el.captionsBox.querySelector(".captions-empty")?.remove();
  let n=id&&$(id);
  if(!n){n=document.createElement("div");if(id)n.id=id;n.innerHTML='<div class="caption-who"></div><div class="caption-body"></div>';n.firstChild.textContent=who==="kam"?S.agentName:(S.sellerSalutation||"Seller");el.captionsBox.append(n)}
  n.className=`caption-line ${who}${interim?" interim":""}`;n.lastChild.textContent=text;el.captionsBox.scrollTop=el.captionsBox.scrollHeight;
}
function phases(active,mode=S.mode){
  let seen=false;const fu=mode==="followup";
  el.phaseStrip.querySelectorAll(".phase-pill").forEach(p=>{
    const k=p.dataset.phase;p.classList.remove("active","done","hidden");
    if((!fu&&k==="REVIEW_COMMITMENTS")||(fu&&["UNDERSTAND","CLARIFY","DIAGNOSE"].includes(k)))return p.classList.add("hidden");
    if(k===active){p.classList.add("active");seen=true}else if(!seen)p.classList.add("done");
  });
}
function stopRec(){clearTimeout(S.eou);clearTimeout(S.silence);if(S.rec&&S.recognizing){try{S.rec.stop()}catch{}}S.recognizing=false}
function armSilence(){
  clearTimeout(S.silence);if(!S.inCall||S.reprompted)return;
  S.silence=setTimeout(()=>{
    if(!S.inCall||S.orb!=="listening"||S.reprompted)return;
    S.reprompted=true;const t=repromptText(S.language);caption("kam",t);queue.enqueueSentence(t);
  },6000);
}
function listen(){
  if(!S.inCall)return;
  setOrb("listening");S.interim=S.final="";armSilence();
  const C=SR();if(!C){el.browserBanner.classList.remove("hidden");return}
  if(S.recognizing)return;
  try{
    const r=new C();r.lang=S.language==="hi-IN"?"hi-IN":"en-IN";r.interimResults=true;r.continuous=true;r.maxAlternatives=1;
    r.onstart=()=>{S.recognizing=true};
    r.onspeechstart=()=>{clearTimeout(S.silence);if(S.headphones&&queue.isPlaying)interrupt()};
    r.onresult=e=>{
      clearTimeout(S.silence);let f="",i="";
      for(let k=e.resultIndex;k<e.results.length;k++){const x=e.results[k];x.isFinal?f+=x[0].transcript+" ":i+=x[0].transcript}
      if(f)S.final+=f;S.interim=i;
      const all=(S.final+" "+S.interim).trim();if(!all)return;
      caption("seller",all,{interim:true,id:"seller-live-caption"});
      clearTimeout(S.eou);   // 800 ms of quiet ends the utterance
      S.eou=setTimeout(()=>{
        const send=(S.final+" "+S.interim).trim();S.final=S.interim="";if(!send)return;
        const n=$("seller-live-caption");if(n){n.removeAttribute("id");n.classList.remove("interim")}
        stopRec();say(send);
      },800);
    };
    r.onerror=e=>{S.recognizing=false;if(["not-allowed","service-not-allowed"].includes(e.error))showError("Microphone is blocked. Allow mic access in the browser, or use Type instead.")};
    r.onend=()=>{S.recognizing=false;if(S.inCall&&(S.orb==="listening"||S.headphones))setTimeout(()=>{if(S.inCall&&(S.orb==="listening"||S.headphones)&&!S.recognizing){try{r.start()}catch{}}},180)};
    S.rec=r;r.start();
  }catch(err){showError("Speech recognition is unavailable: "+err.message)}
}
export function interrupt(){   // order matters: audio first, then stream, then mic
  if(!S.inCall)return;
  queue.stopAll();if(S.abort){S.abort.abort();S.abort=null}listen();
}
async function sse(res,on){
  const rd=res.body.getReader(),dec=new TextDecoder();let buf="";
  for(;;){
    const{done,value}=await rd.read();if(done)break;
    buf+=dec.decode(value,{stream:true});const parts=buf.split("\n\n");buf=parts.pop()||"";
    for(const p of parts){let ev="message",d=null;
      for(const l of p.split("\n")){if(l.startsWith("event:"))ev=l.slice(6).trim();else if(l.startsWith("data:")){try{d=JSON.parse(l.slice(5).trim())}catch{}}}
      if(d!==null)on(ev,d)}
  }
}
export async function startCall({modeOverride=null,priorSessionId=null}={}){
  el.errorBanner.classList.add("hidden");queue.stopAll();stopRec();
  if(modeOverride){S.mode=modeOverride;el.selMode.value=modeOverride}else S.mode=el.selMode.value;
  S.language=el.selLanguage.value;S.headphones=el.chkHeadphones.checked;S.tts=el.selTtsProvider.value;
  queue.configure({language:S.language,ttsProvider:S.tts});
  S.inCall=true;S.reprompted=false;el.captionsBox.innerHTML="";el.summaryCardSection.classList.add("hidden");
  el.btnStartCall.disabled=true;el.btnEndCall.disabled=false;
  queue.playRingtone();setOrb("thinking");   // ringtone runs inside the click so audio is unlocked
  clearInterval(S.timer);S.t0=Date.now();
  S.timer=setInterval(()=>{const s=Math.floor((Date.now()-S.t0)/1e3);el.callTimer.textContent=String(Math.floor(s/60)).padStart(2,"0")+":"+String(s%60).padStart(2,"0")},500);
  try{
    const r=await fetch("/api/session/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:S.mode,language:S.language,prior_session_id:priorSessionId})});
    if(!r.ok)throw new Error(`Could not start the call (${r.status})`);
    const p=await r.json();S.sessionId=p.session_id;
    phases(p.session?.phase||"GREETING",p.session?.mode||S.mode);S.hook?.onSessionStarted?.(p);
    await stream(`/api/session/${S.sessionId}/greeting`,{});
  }catch(e){showError(e.message);endCall()}
}
export function endCall(){
  S.inCall=false;clearInterval(S.timer);stopRec();queue.stopAll();S.viz?.stopMic();
  if(S.abort){S.abort.abort();S.abort=null}
  setOrb("idle");el.btnStartCall.disabled=false;el.btnEndCall.disabled=true;el.btnInterrupt.disabled=true;
}
export async function say(text){
  const t=(text||"").trim();if(!t)return;
  if(!S.inCall||!S.sessionId)await startCall();
  el.errorBanner.classList.add("hidden");queue.stopAll();S.reprompted=false;S.sttEnd=performance.now();
  const last=el.captionsBox.lastElementChild;
  if(!last||!last.classList.contains("seller")||last.lastChild.textContent!==t)caption("seller",t);
  setOrb("thinking");queue.scheduleLatencyFiller(600);
  await stream("/api/chat",{session_id:S.sessionId,text:t,language:S.language});
}
async function stream(url,body){
  S.abort?.abort();const ac=new AbortController();S.abort=ac;
  const cid="kam-live-"+Date.now();let text="",gotTok=false,gotAud=false;
  const resume=()=>{if(!queue.isPlaying&&!queue.queue.length&&S.inCall){queue.clearLatencyFiller();listen()}};
  try{
    const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal:ac.signal});
    if(!r.ok)throw new Error(`${S.agentName} could not answer (HTTP ${r.status})`);
    await sse(r,(ev,d)=>{
      const h=S.hook;
      if(ev==="phase"){if(d.mode)S.mode=d.mode;phases(d.phase,d.mode||S.mode);h?.onPhase?.(d)}
      else if(ev==="tool_start")h?.onToolCall?.(d);
      else if(ev==="tool_result")h?.onToolResult?.(d);
      else if(ev==="failover")h?.onFailover?.(d);
      else if(ev==="token"){
        if(!gotTok&&S.sttEnd){gotTok=true;el.latencySttToToken.textContent=Math.round(performance.now()-S.sttEnd)+" ms"}
        text+=d.text||"";caption("kam",text,{id:cid});
      }else if(ev==="sentence")queue.enqueueSentence(d.speakable||d.text,()=>{if(!gotAud&&S.sttEnd){gotAud=true;el.latencySttToAudio.textContent=Math.round(performance.now()-S.sttEnd)+" ms"}});
      else if(ev==="summary_card")h?.onSummaryCard?.(d);
      else if(ev==="latency"){if(d.provider)el.latencyProvider.textContent=d.provider;h?.onLatency?.(d)}
      else if(ev==="done"){
        if(d.text)caption("kam",d.text,{id:cid});
        phases(d.phase,S.mode);S.done++;document.body.dataset.completedStreams=String(S.done);h?.onDone?.(d);resume();
      }else if(ev==="error"){queue.clearLatencyFiller();showError(d.message||`${S.agentName} could not answer.`);if(S.inCall)listen()}
    });
  }catch(e){if(e.name!=="AbortError"){queue.clearLatencyFiller();showError(e.message);if(S.inCall)listen()}}
  finally{if(S.abort===ac)S.abort=null}
}
function applyBranding(branding,seller){
  if(branding){
    if(branding.project_name)S.projectName=branding.project_name;
    if(branding.agent_name)S.agentName=branding.agent_name;
    document.title=`${S.projectName} — ${S.agentName}`;
    if(el.brandName)el.brandName.textContent=S.projectName;
    if(el.footerNotice&&branding.footer_notice)el.footerNotice.textContent=branding.footer_notice;
    if(el.selMode){
      const inOpt=el.selMode.querySelector('option[value="inbound"]');
      const prOpt=el.selMode.querySelector('option[value="proactive"]');
      if(inOpt)inOpt.textContent=`Seller calls ${S.agentName}`;
      if(prOpt)prOpt.textContent=`${S.agentName} calls seller`;
    }
    const ce=el.captionsBox?.querySelector(".captions-empty");
    if(ce)ce.textContent=`Captions appear here as you and ${S.agentName} talk.`;
    if(el.caseFileSection)el.caseFileSection.setAttribute("aria-label",`${S.agentName}'s case file`);
  }
  if(seller){
    S.sellerSalutation=seller.salutation||seller.seller_name||S.sellerSalutation;
    if(el.brandSub&&seller.shop_name)el.brandSub.textContent=`Virtual KAM · ${seller.shop_name}`;
    queue.configure({salutation:S.sellerSalutation});
  }
  setOrb(S.orb);
}
async function init(){
  ["browserBanner","errorBanner","errorBannerText","btnStartCall","btnEndCall","btnInterrupt","callTimer","micOrb","orbLabel","captionsBox","selMode","selLanguage","chkHeadphones","selTtsProvider","btnToggleDebug","debugBox","txtDebugInput","btnSendDebug","phaseStrip","ttsBadge","latencySttToToken","latencySttToAudio","latencyProvider","btnFollowup","kamBrainRoot","summaryCardSection","btnDismissError","brandName","brandSub","footerNotice","caseFileSection"].forEach(i=>el[i]=$(i));
  S.hook=createKamBrainController({rootEl:el.kamBrainRoot,summaryEl:el.summaryCardSection,getBranding:()=>({agentName:S.agentName,projectName:S.projectName})});
  S.viz=createVoiceViz({canvas:$("spectrum"),getState:()=>S.orb,getTts:()=>queue.analyser()});
  phases("GREETING",el.selMode.value);setOrb("idle");
  if(!SR())el.browserBanner.classList.remove("hidden");
  try{
    const[h,d]=await Promise.all([fetch("/api/health"),fetch("/api/demo-data")]);
    let hj=null,dj=null;
    if(h.ok){hj=await h.json();if(!hj.active_llm_chain?.length)showError("No LLM keys are configured. Add GROQ_API_KEY or GEMINI_API_KEY to .env.")}
    if(d.ok){dj=await d.json();S.hook.onDemoData(dj)}
    applyBranding(hj?.branding,dj?.overview?.seller);
  }catch{}
  el.btnStartCall.onclick=()=>startCall();el.btnEndCall.onclick=endCall;el.btnInterrupt.onclick=interrupt;
  el.btnDismissError.onclick=()=>el.errorBanner.classList.add("hidden");
  el.btnFollowup.onclick=()=>startCall({modeOverride:"followup",priorSessionId:S.sessionId});
  el.selMode.onchange=()=>{S.mode=el.selMode.value;phases("GREETING",S.mode)};
  el.micOrb.onclick=()=>{if(!S.inCall)startCall();else if(S.orb==="speaking")interrupt()};
  el.selLanguage.onchange=()=>{
    S.language=el.selLanguage.value;queue.configure({language:S.language});
    if(S.recognizing){stopRec();if(S.orb==="listening"||S.headphones)listen()}
    if(S.sessionId)fetch(`/api/session/${S.sessionId}/settings`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({language:S.language})}).catch(()=>{});
  };
  el.chkHeadphones.onchange=()=>{S.headphones=el.chkHeadphones.checked};
  el.selTtsProvider.onchange=()=>{S.tts=el.selTtsProvider.value;queue.configure({ttsProvider:S.tts})};
  el.btnToggleDebug.onclick=()=>el.debugBox.classList.toggle("hidden");
  el.btnSendDebug.onclick=()=>{const t=el.txtDebugInput.value;el.txtDebugInput.value="";say(t)};
  el.txtDebugInput.onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();el.btnSendDebug.click()}};
  addEventListener("keydown",e=>{
    const ae=document.activeElement;
    if(e.code!=="Space"||ae?.isContentEditable||["INPUT","SELECT","TEXTAREA"].includes(ae?.tagName))return;
    if(S.inCall&&S.orb==="speaking"){e.preventDefault();interrupt()}
  });
}
addEventListener("DOMContentLoaded",init);
