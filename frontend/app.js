import{AudioPlaybackQueue}from"/static/audio.js";
import{createKamBrainController}from"/static/kam_brain.js";
import{createVoiceViz}from"/static/viz.js";
const S={
  inCall:false,sessionId:null,mode:"inbound",language:"en-IN",headphones:false,tts:"auto",
  orb:"idle",t0:0,timer:null,rec:null,recTimer:null,recognizing:false,recFatal:false,audioCaptureRetried:false,
  sttMode:"webspeech",whisperFallback:false,
  mr:null,mrStream:null,mrCtx:null,mrRaf:null,mrSpoke:false,mrFinish:null,mrTimer:null,
  eou:null,silence:null,reprompted:false,userTurns:0,
  interim:"",final:"",sttEnd:0,abort:null,done:0,hook:null,viz:null,
  projectName:"VoiceKAM",agentName:"Metis",sellerSalutation:"",
  dataSource:"demo",scenarioId:"A",datasetId:null,scenarios:[]
};
const el={};
const $=id=>document.getElementById(id);
const canWhisperFallback=()=>Boolean(S.whisperFallback&&navigator.mediaDevices?.getUserMedia&&window.MediaRecorder);
const orbCopy=s=>{
  const a=S.agentName;
  if(s==="listening"){
    if(S.recFatal)return["Mic off","Microphone unavailable — fix browser permission or use Type instead"];
    if(S.sttMode==="whisper")return["Listening",S.mrSpoke?"Hearing you… pause or tap orb to send":`Listening via backup mic, speak to ${a}`];
    return["Listening",`Go ahead, ${a} replies after a short pause`];
  }
  if(s==="thinking")return["Checking",`${a} is looking at your store data`];
  if(s==="speaking")return["Speaking",""];
  return["Ready",`Press Start call to talk with ${a}`];
};
const repromptText=lang=>{
  const rawSal=(S.sellerSalutation||"").trim();
  const sal=rawSal&&rawSal.toLowerCase()!=="seller partner"?rawSal:"";
  if(lang==="hi-IN")return sal?`${sal}, क्या आप सुन रहे हैं? आप बोल सकते हैं।`:"क्या आप सुन रहे हैं? आप बोल सकते हैं।";
  if(lang==="hinglish")return sal?`${sal}, aap line par hain? Aap bata sakte hain.`:"Aap line par hain? Aap bata sakte hain.";
  return sal?`${sal}, are you still there? I am listening whenever you are ready.`:"Are you still there? I am listening whenever you are ready.";
};
const SR=()=>window.SpeechRecognition||window.webkitSpeechRecognition||null;
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function readApiError(res,fallback){
  try{
    const txt=await res.text();
    if(!txt)return fallback;
    const j=JSON.parse(txt);
    if(Array.isArray(j.errors)&&j.errors.length)return j.errors.join("; ");
    return j.detail||j.message||fallback;
  }catch{return fallback}
}

const queue=new AudioPlaybackQueue({
  onSpeakingStart(){setOrb("speaking");if(!S.headphones)stopRec();el.btnInterrupt.disabled=false},
  onSpeakingEnd(){el.btnInterrupt.disabled=true;if(S.inCall&&S.orb!=="thinking")listen()},
  onTtsProviderUsed(p){el.ttsBadge.textContent="TTS: "+p}
});

function setOrb(s){
  S.orb=s;const copy=orbCopy(s);el.micOrb.dataset.state=s;el.micOrb.textContent=copy[0];   // micOrb stays text-only
  el.orbLabel.textContent=s==="speaking"?(S.headphones?`${S.agentName} is speaking. Talk to interrupt.`:`${S.agentName} is speaking. Press Space to interrupt.`):copy[1];
  S.viz?.onStateChange?.(s);
}
const showError=m=>{el.errorBannerText.textContent=m;el.errorBanner.classList.remove("hidden")};
function showUploadErrors(errs){
  const list=(Array.isArray(errs)?errs:[errs]).filter(Boolean);
  if(!list.length){el.uploadError.classList.add("hidden");el.uploadError.innerHTML="";return}
  el.uploadError.innerHTML=`<b>Could not load dataset</b><ul>${list.map(e=>`<li>${esc(e)}</li>`).join("")}</ul>`;
  el.uploadError.classList.remove("hidden");
}
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
function stopWhisperRec(){
  clearTimeout(S.mrTimer);S.mrTimer=null;
  if(S.mrRaf){cancelAnimationFrame(S.mrRaf);S.mrRaf=null}
  S.mrFinish=null;S.mrSpoke=false;
  const mr=S.mr;S.mr=null;
  if(mr&&mr.state!=="inactive"){try{mr.onstop=null;mr.stop()}catch{}}
  S.mrStream?.getTracks().forEach(t=>t.stop());S.mrStream=null;
  S.mrCtx?.close().catch(()=>{});S.mrCtx=null;
}
function stopRec(){
  clearTimeout(S.eou);clearTimeout(S.silence);clearTimeout(S.recTimer);S.recTimer=null;
  const r=S.rec;S.rec=null;
  if(r){try{r.onend=null;r.onerror=null;r.stop()}catch{}}
  S.recognizing=false;
  stopWhisperRec();
  S.viz?.stopMic?.();
}
function warmMicPermission(){
  if(!navigator.mediaDevices?.getUserMedia)return;
  navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}}).then(s=>{
    s.getTracks().forEach(t=>t.stop());
    if(S.inCall&&S.orb==="listening"&&S.recFatal){
      S.recFatal=false;el.errorBanner.classList.add("hidden");listen();
    }
  }).catch(()=>{});
}
function armSilence(){
  clearTimeout(S.silence);if(!S.inCall||S.reprompted)return;
  const waitMs=S.userTurns===0?8000:6000;
  S.silence=setTimeout(()=>{
    if(!S.inCall||S.orb!=="listening"||S.reprompted)return;
    S.reprompted=true;const t=repromptText(S.language);caption("kam",t);queue.enqueueSentence(t);
  },waitMs);
}
async function startWhisperRec(){
  if(!S.inCall||S.orb!=="listening"||S.mr||S.recFatal)return;
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){
    S.recFatal=true;setOrb("listening");showError("Browser microphone capture is unavailable. Switch to Chrome/Edge or use Type instead.");return;
  }
  S.viz?.disableMic?.();
  let stream;
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
  }catch(err){
    if(!S.inCall)return;
    const n=err?.name||"";
    S.recFatal=true;setOrb("listening");
    if(n==="NotAllowedError"||n==="PermissionDeniedError")showError("Microphone permission is blocked. Allow mic access in your browser address bar, or use Type instead.");
    else showError("Could not access your microphone ("+(n||err?.message||"device error")+"). Check Windows Sound/Privacy settings, or use Type instead.");
    return;
  }
  if(!S.inCall||S.orb!=="listening"||S.sttMode!=="whisper"){stream.getTracks().forEach(t=>t.stop());return}
  const mime=["audio/webm;codecs=opus","audio/webm","audio/ogg;codecs=opus","audio/mp4",""].find(m=>!m||MediaRecorder.isTypeSupported(m));
  let mr;
  try{mr=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream)}
  catch{mr=new MediaRecorder(stream)}
  const AC=window.AudioContext||window.webkitAudioContext;
  let ctx=null,an=null,buf=null;
  if(AC){
    try{ctx=new AC();an=ctx.createAnalyser();an.fftSize=1024;ctx.createMediaStreamSource(stream).connect(an);buf=new Uint8Array(an.fftSize)}catch{}
  }
  const chunks=[];let lastVoiceAt=0,startedAt=performance.now(),finishing=false;
  S.mr=mr;S.mrStream=stream;S.mrCtx=ctx;S.mrSpoke=false;
  const finish=()=>{
    if(finishing||S.mr!==mr)return;
    finishing=true;
    if(S.mrRaf){cancelAnimationFrame(S.mrRaf);S.mrRaf=null}
    S.mrFinish=null;
    if(mr.state!=="inactive"){try{mr.stop()}catch{}}
  };
  S.mrFinish=finish;
  mr.ondataavailable=e=>{if(e.data&&e.data.size>0)chunks.push(e.data)};
  mr.onstop=async()=>{
    const spoke=S.mrSpoke;
    stream.getTracks().forEach(t=>t.stop());ctx?.close().catch(()=>{});
    if(S.mr===mr){S.mr=null;S.mrStream=null;S.mrCtx=null;S.mrSpoke=false;S.mrFinish=null}
    if(!S.inCall||S.orb!=="listening")return;
    const blob=new Blob(chunks,{type:mr.mimeType||mime||"audio/webm"});
    if(!spoke||blob.size<1000){startWhisperRec();return}
    setOrb("thinking");
    try{
      const fd=new FormData();
      fd.append("audio",blob,"speech.webm");
      fd.append("language",S.language==="hi-IN"?"hi-IN":"en-IN");
      const resp=await fetch("/api/stt",{method:"POST",body:fd});
      if(!resp.ok)throw new Error(await readApiError(resp,`Speech transcription failed (${resp.status})`));
      const data=await resp.json();
      const txt=(data?.text||"").trim();
      if(txt){say(txt)}else if(S.inCall){listen()}
    }catch(err){
      showError(err?.message||"Could not transcribe microphone audio.");
      if(S.inCall)listen();
    }
  };
  try{mr.start(250)}catch(err){stream.getTracks().forEach(t=>t.stop());ctx?.close().catch(()=>{});S.mr=null;S.recFatal=true;setOrb("listening");showError("Could not start audio recorder: "+err.message);return}
  const tick=now=>{
    if(S.mr!==mr||finishing)return;
    if(an&&buf){
      an.getByteTimeDomainData(buf);
      let sum=0;for(let i=0;i<buf.length;i++){const v=(buf[i]-128)/128;sum+=v*v}
      const rms=Math.sqrt(sum/buf.length);
      if(rms>0.025){
        lastVoiceAt=now;
        if(!S.mrSpoke){S.mrSpoke=true;clearTimeout(S.silence);setOrb("listening")}
      }
    }
    if(S.mrSpoke&&(now-lastVoiceAt>950||now-startedAt>14000)){finish();return}
    if(!S.mrSpoke&&now-startedAt>18000){
      stopWhisperRec();
      if(S.inCall&&S.orb==="listening")startWhisperRec();
      return;
    }
    S.mrRaf=requestAnimationFrame(tick);
  };
  S.mrRaf=requestAnimationFrame(tick);
}
function startSpeechRec(langOverride=null){
  const C=SR();
  if(!C){
    if(canWhisperFallback()){S.sttMode="whisper";setOrb("listening");startWhisperRec();return}
    el.browserBanner.classList.remove("hidden");return;
  }
  if(S.recognizing||S.rec||S.recFatal||!S.inCall)return;
  let fatal=false,switching=false,delay=180;
  try{
    const r=new C();
    r.lang=langOverride||(S.language==="hi-IN"?"hi-IN":"en-IN");
    r.interimResults=true;r.continuous=true;r.maxAlternatives=1;
    r.onstart=()=>{if(S.rec===r)S.recognizing=true};
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
    r.onerror=e=>{
      S.recognizing=false;
      const err=e?.error||"";
      if(err==="no-speech")return;
      if(err==="aborted"){delay=260;return}
      if(err==="not-allowed"){
        fatal=true;S.recFatal=true;setOrb("listening");
        showError("Microphone permission is blocked. Click the lock/mic icon in your browser address bar to Allow Microphone, or use Type instead.");
        return;
      }
      if(err==="service-not-allowed"||err==="network"){
        if(canWhisperFallback()){
          fatal=true;switching=true;S.sttMode="whisper";
          try{r.onend=null;r.stop()}catch{}
          S.rec=null;setOrb("listening");startWhisperRec();return;
        }
        fatal=true;S.recFatal=true;setOrb("listening");
        showError(err==="network"
          ?"Browser speech recognition could not reach its cloud service (network/firewall blocked). Use Chrome/Edge online, or use Type instead."
          :"Speech recognition service is blocked in this browser. Allow mic/speech access in browser settings, or use Type instead.");
        return;
      }
      if(err==="audio-capture"){
        if(!S.audioCaptureRetried){
          S.audioCaptureRetried=true;S.viz?.disableMic?.();delay=300;return;
        }
        if(canWhisperFallback()){
          fatal=true;switching=true;S.sttMode="whisper";
          try{r.onend=null;r.stop()}catch{}
          S.rec=null;setOrb("listening");startWhisperRec();return;
        }
        fatal=true;S.recFatal=true;setOrb("listening");
        showError("No working microphone audio detected. Check Windows Sound/Privacy microphone settings or close other apps using your mic, or use Type instead.");
        return;
      }
      if(err==="language-not-supported"&&r.lang!=="en-IN"){
        fatal=true;switching=true;S.rec=null;startSpeechRec("en-IN");return;
      }
      fatal=true;S.recFatal=true;setOrb("listening");
      showError("Microphone error ("+(err||"unknown")+"). Check mic permissions or use Type instead.");
    };
    r.onend=()=>{
      S.recognizing=false;
      if(S.rec===r)S.rec=null;
      if(!fatal&&!switching&&!S.recFatal&&S.sttMode==="webspeech"&&S.inCall&&(S.orb==="listening"||S.headphones)){
        clearTimeout(S.recTimer);
        S.recTimer=setTimeout(()=>{if(!S.recFatal&&S.sttMode==="webspeech"&&S.inCall&&(S.orb==="listening"||S.headphones)&&!S.recognizing&&!S.rec)startSpeechRec(langOverride)},delay);
      }
    };
    S.rec=r;r.start();
  }catch(err){
    if(canWhisperFallback()){S.sttMode="whisper";setOrb("listening");startWhisperRec();return}
    S.recFatal=true;setOrb("listening");showError("Speech recognition is unavailable: "+err.message);
  }
}
function listen(){
  if(!S.inCall)return;
  setOrb("listening");S.interim=S.final="";armSilence();
  if(S.sttMode==="whisper"||!SR()){
    if(canWhisperFallback()){S.sttMode="whisper";setOrb("listening");startWhisperRec();return}
    el.browserBanner.classList.remove("hidden");return;
  }
  startSpeechRec();
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

function renderDatasetSummary(d){
  if(!el.datasetSummary||!d)return;
  const ds=d.dataset||{},sel=d.overview?.seller||{},comp=d.metrics?.completeness||{};
  const miss=comp.missing_fields||[];
  const srcTag=S.dataSource==="upload"?"Custom upload":`Demo ${esc(S.scenarioId)}`;
  const sigBadge=miss.length
    ?`<span class="chip warn">Partial signals (missing: ${esc(miss.join(", "))})</span>`
    :'<span class="chip ok">All signals ready</span>';
  el.datasetSummary.innerHTML=
    `<span class="chip">${srcTag}</span>`+
    `<span><b>${esc(sel.shop_name||ds.shop_name||"Custom Store")}</b> · ${esc(sel.seller_name||"Seller")}${sel.city?` (${esc(sel.city)})`:""}</span>`+
    `<span><b>${ds.sku_count??sel.active_skus??0} SKUs</b> · ${esc(ds.period_prev_label||"Last period")} → ${esc(ds.period_curr_label||"Current period")}</span>`+
    sigBadge;
}

function setDatasetControlsDisabled(disabled){
  [el.btnDataModeDemo,el.btnDataModeUpload,el.selScenario,el.fileSkuDataset,el.fileCancelDataset,el.txtUploadShop,el.txtUploadSeller,el.btnValidateUpload].forEach(n=>{
    if(n)n.disabled=disabled;
  });
}

async function loadDemoPreview(params){
  const q=new URLSearchParams(params);
  const r=await fetch(`/api/demo-data?${q.toString()}`);
  if(!r.ok)throw new Error(await readApiError(r,`Could not load dataset (${r.status})`));
  const dj=await r.json();
  S.hook?.onDemoData(dj);
  applyBranding(null,dj?.overview?.seller);
  renderDatasetSummary(dj);
}

function setDataSourceMode(mode){
  S.dataSource=mode;
  const isDemo=mode==="demo";
  el.btnDataModeDemo?.classList.toggle("active",isDemo);
  el.btnDataModeDemo?.setAttribute("aria-selected",String(isDemo));
  el.btnDataModeUpload?.classList.toggle("active",!isDemo);
  el.btnDataModeUpload?.setAttribute("aria-selected",String(!isDemo));
  el.demoScenarioBox?.classList.toggle("hidden",!isDemo);
  el.customUploadBox?.classList.toggle("hidden",isDemo);
  showUploadErrors([]);
  if(isDemo){
    loadDemoPreview({scenario:S.scenarioId}).catch(e=>showError(e.message));
  }else if(S.datasetId){
    loadDemoPreview({dataset_id:S.datasetId}).catch(e=>showError(e.message));
  }
}

async function handleValidateUpload(){
  showUploadErrors([]);
  if(el.uploadStatus)el.uploadStatus.textContent="";
  const f=el.fileSkuDataset?.files?.[0];
  if(!f){
    showUploadErrors(["Please select a SKU CSV or JSON file first."]);
    return false;
  }
  const fd=new FormData();
  fd.append("file",f);
  const cf=el.fileCancelDataset?.files?.[0];
  if(cf)fd.append("cancellations_file",cf);
  if(el.txtUploadShop?.value.trim())fd.append("shop_name",el.txtUploadShop.value.trim());
  if(el.txtUploadSeller?.value.trim())fd.append("seller_name",el.txtUploadSeller.value.trim());

  try{
    const r=await fetch("/api/data/validate",{method:"POST",body:fd});
    let vj=null;
    try{vj=await r.json()}catch{}
    if(!r.ok||!vj?.valid){
      const errs=vj?.errors?.length?vj.errors:[vj?.detail||`Upload rejected (HTTP ${r.status})`];
      showUploadErrors(errs);
      S.datasetId=null;
      return false;
    }
    S.datasetId=vj.dataset_id;
    if(el.uploadStatus)el.uploadStatus.textContent=`Loaded ${vj.summary?.sku_count||0} SKUs in RAM`;
    await loadDemoPreview({dataset_id:S.datasetId});
    return true;
  }catch(e){
    showUploadErrors([e.message||"Network error while validating upload."]);
    return false;
  }
}

export async function startCall({modeOverride=null,priorSessionId=null}={}){
  el.errorBanner.classList.add("hidden");queue.stopAll();stopRec();
  if(S.dataSource==="upload"&&!S.datasetId){
    showUploadErrors(["Please upload and validate a CSV or JSON file first, or switch to a demo scenario."]);
    showError("Upload a valid CSV/JSON dataset first, or switch back to Demo Scenario.");
    return;
  }
  if(modeOverride){S.mode=modeOverride;el.selMode.value=modeOverride}else S.mode=el.selMode.value;
  S.language=el.selLanguage.value;S.headphones=el.chkHeadphones.checked;S.tts=el.selTtsProvider.value;
  queue.configure({language:S.language,ttsProvider:S.tts});
  S.inCall=true;S.reprompted=false;S.userTurns=0;S.recFatal=false;S.audioCaptureRetried=false;S.sttMode=SR()?"webspeech":"whisper";
  S.viz?.resetMic?.();warmMicPermission();
  el.captionsBox.innerHTML="";el.summaryCardSection.classList.add("hidden");
  el.btnStartCall.disabled=true;el.btnEndCall.disabled=false;
  setDatasetControlsDisabled(true);
  queue.playRingtone();setOrb("thinking");   // ringtone runs inside the click so audio is unlocked
  clearInterval(S.timer);S.t0=Date.now();
  S.timer=setInterval(()=>{const s=Math.floor((Date.now()-S.t0)/1e3);el.callTimer.textContent=String(Math.floor(s/60)).padStart(2,"0")+":"+String(s%60).padStart(2,"0")},500);
  try{
    const reqBody={
      mode:S.mode,
      language:S.language,
      prior_session_id:priorSessionId,
      scenario_id:S.dataSource==="demo"?S.scenarioId:null,
      dataset_id:S.dataSource==="upload"?S.datasetId:null
    };
    if(S.dataSource==="upload"){
      const upShop=el.txtUploadShop?.value.trim();
      const upSeller=el.txtUploadSeller?.value.trim();
      if(upShop)reqBody.shop_name=upShop;
      if(upSeller)reqBody.seller_name=upSeller;
    }
    const r=await fetch("/api/session/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(reqBody)});
    if(!r.ok)throw new Error(await readApiError(r,`Could not start the call (${r.status})`));
    const p=await r.json();S.sessionId=p.session_id;
    phases(p.session?.phase||"GREETING",p.session?.mode||S.mode);S.hook?.onSessionStarted?.(p);
    if(p.demo_data){applyBranding(null,p.demo_data?.overview?.seller);renderDatasetSummary(p.demo_data)}
    await stream(`/api/session/${S.sessionId}/greeting`,{});
  }catch(e){showError(e.message);endCall()}
}
export function endCall(){
  S.inCall=false;S.recFatal=false;S.audioCaptureRetried=false;clearInterval(S.timer);stopRec();queue.stopAll();S.viz?.stopMic();
  if(S.abort){S.abort.abort();S.abort=null}
  setOrb("idle");el.btnStartCall.disabled=false;el.btnEndCall.disabled=true;el.btnInterrupt.disabled=true;
  setDatasetControlsDisabled(false);
}
export async function say(text){
  const t=(text||"").trim();if(!t)return;
  if(!S.inCall||!S.sessionId){await startCall();if(!S.inCall||!S.sessionId)return}
  el.errorBanner.classList.add("hidden");queue.stopAll();S.userTurns++;S.sttEnd=performance.now();
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
    if(!r.ok)throw new Error(await readApiError(r,`${S.agentName} could not answer (HTTP ${r.status})`));
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
    if(el.privacyNotice&&branding.privacy_notice)el.privacyNotice.textContent=branding.privacy_notice;
    if(el.selMode){
      const inOpt=el.selMode.querySelector('option[value="inbound"]');
      const prOpt=el.selMode.querySelector('option[value="proactive"]');
      if(inOpt)inOpt.textContent=`Seller calls ${S.agentName}`;
      if(prOpt)prOpt.textContent=`${S.agentName} calls seller`;
    }
    const ce=el.captionsBox?.querySelector(".captions-empty");
    if(ce)ce.textContent=`Captions appear here as you and ${S.agentName} talk.`;
    if(el.caseFileSection)el.caseFileSection.setAttribute("aria-label",`${S.agentName}'s case file`);
    queue.configure({agentName:S.agentName});
    S.hook?.refresh?.();
  }
  if(seller){
    const rawSal=(seller.salutation||seller.seller_name||"").trim();
    S.sellerSalutation=rawSal&&rawSal.toLowerCase()!=="seller partner"?rawSal:"";
    if(el.brandSub){
      const sh=(seller.shop_name||"").trim();
      el.brandSub.textContent=sh&&sh.toLowerCase()!=="custom uploaded store"?`Virtual KAM · ${sh}`:"Account intelligence assistant";
    }
    queue.configure({salutation:S.sellerSalutation||"Seller"});
  }
  setOrb(S.orb);
}
async function init(){
  ["browserBanner","errorBanner","errorBannerText","btnStartCall","btnEndCall","btnInterrupt","callTimer","micOrb","orbLabel","captionsBox","selMode","selLanguage","chkHeadphones","selTtsProvider","btnToggleDebug","debugBox","txtDebugInput","btnSendDebug","phaseStrip","ttsBadge","latencySttToToken","latencySttToAudio","latencyProvider","btnFollowup","kamBrainRoot","summaryCardSection","btnDismissError","brandName","brandSub","footerNotice","caseFileSection","btnDataModeDemo","btnDataModeUpload","demoScenarioBox","customUploadBox","selScenario","scenarioDesc","privacyNotice","fileSkuDataset","fileCancelDataset","txtUploadShop","txtUploadSeller","btnValidateUpload","uploadStatus","uploadError","datasetSummary"].forEach(i=>el[i]=$(i));
  S.hook=createKamBrainController({rootEl:el.kamBrainRoot,summaryEl:el.summaryCardSection,getBranding:()=>({agentName:S.agentName,projectName:S.projectName})});
  S.viz=createVoiceViz({canvas:$("spectrum"),getState:()=>S.orb,getTts:()=>queue.analyser()});
  phases("GREETING",el.selMode.value);setOrb("idle");
  try{
    const[h,sc,d]=await Promise.all([fetch("/api/health"),fetch("/api/scenarios"),fetch("/api/demo-data?scenario=A")]);
    let hj=null,dj=null;
    if(h.ok){
      hj=await h.json();
      S.whisperFallback=Boolean(hj?.features?.whisper_stt_fallback);
      if(!hj.active_llm_chain?.length)showError("No LLM keys are configured. Add GROQ_API_KEY or GEMINI_API_KEY to .env.");
    }
    if(sc.ok){
      const sj=await sc.json();
      S.scenarios=sj.scenarios||[];
      if(el.selScenario&&S.scenarios.length){
        el.selScenario.innerHTML=S.scenarios.map(s=>`<option value="${esc(s.scenario_id)}">${esc(s.scenario_id)}: ${esc(s.title)}</option>`).join("");
        el.selScenario.value=S.scenarioId;
        const activeSc=S.scenarios.find(s=>s.scenario_id===S.scenarioId)||S.scenarios[0];
        if(el.scenarioDesc&&activeSc)el.scenarioDesc.textContent=activeSc.description;
      }
    }
    if(d.ok){dj=await d.json();S.hook.onDemoData(dj);renderDatasetSummary(dj)}
    applyBranding(hj?.branding,dj?.overview?.seller);
  }catch{}
  if(!SR()&&!canWhisperFallback())el.browserBanner.classList.remove("hidden");

  el.btnDataModeDemo.onclick=()=>setDataSourceMode("demo");
  el.btnDataModeUpload.onclick=()=>setDataSourceMode("upload");
  el.selScenario.onchange=()=>{
    S.scenarioId=el.selScenario.value||"A";
    const sc=S.scenarios.find(x=>x.scenario_id===S.scenarioId);
    if(el.scenarioDesc)el.scenarioDesc.textContent=sc?sc.description:"";
    if(S.dataSource==="demo")loadDemoPreview({scenario:S.scenarioId}).catch(e=>showError(e.message));
  };
  el.btnValidateUpload.onclick=()=>handleValidateUpload();

  el.btnStartCall.onclick=()=>startCall();el.btnEndCall.onclick=endCall;el.btnInterrupt.onclick=interrupt;
  el.btnDismissError.onclick=()=>el.errorBanner.classList.add("hidden");
  el.btnFollowup.onclick=()=>startCall({modeOverride:"followup",priorSessionId:S.sessionId});
  el.selMode.onchange=()=>{S.mode=el.selMode.value;phases("GREETING",S.mode)};
  el.micOrb.onclick=()=>{
    if(!S.inCall){startCall();return}
    if(S.orb==="speaking"){interrupt();return}
    if(S.orb==="listening"){
      if(S.sttMode==="whisper"&&S.mr&&S.mrSpoke&&S.mrFinish){S.mrFinish();return}
      if(S.recFatal||(!S.recognizing&&!S.mr)){S.recFatal=false;S.audioCaptureRetried=false;el.errorBanner.classList.add("hidden");listen()}
    }
  };
  el.selLanguage.onchange=()=>{
    S.language=el.selLanguage.value;queue.configure({language:S.language});
    if(S.recognizing||S.mr){stopRec();if(S.orb==="listening"||S.headphones)listen()}
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
