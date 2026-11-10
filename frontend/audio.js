const FILLER={"hi-IN":"जी, एक सेकंड, मैं आपका डेटा देख रही हूँ।",hinglish:"Ji, ek second, main aapka data dekh rahi hoon.","en-IN":"One moment Rajesh ji, checking your account data."};
const synth=()=>("speechSynthesis" in window?window.speechSynthesis:null);
export class AudioPlaybackQueue{
  constructor(cb={}){this.cb=cb;this.queue=[];this.isPlaying=false;this.aborted=false;this.language="en-IN";this.ttsProvider="auto";this.fillerTimer=null;this.fillerOn=false;this.ctrls=new Set();this.gen=0;this.resolveCur=null;this.audio=null;this.ctx=null;this.an=null;this.src=null}
  configure({language,ttsProvider}){if(language)this.language=language;if(ttsProvider)this.ttsProvider=ttsProvider}
  ensureCtx(){try{if(!this.ctx){const C=window.AudioContext||window.webkitAudioContext;if(!C)return;this.ctx=new C();this.an=this.ctx.createAnalyser();this.an.fftSize=256;this.an.smoothingTimeConstant=.78;this.an.connect(this.ctx.destination)}if(this.ctx.state==="suspended")this.ctx.resume().catch(()=>{})}catch{}}
  tap(a){try{this.ensureCtx();if(!this.ctx||this.ctx.state!=="running")return;this.src=this.ctx.createMediaElementSource(a);this.src.connect(this.an)}catch{this.src=null}}   // if the context is not running, audio plays untapped
  analyser(){return this.src?this.an:null}   // null while browser TTS speaks, the visualizer then simulates
  playRingtone(){this.ensureCtx();try{const c=new(window.AudioContext||window.webkitAudioContext)(),t=c.currentTime;for(let i=0;i<2;i++){const o=c.createOscillator(),g=c.createGain(),s=t+i*.35;o.frequency.value=440;g.gain.setValueAtTime(.0001,s);g.gain.exponentialRampToValueAtTime(.06,s+.03);g.gain.exponentialRampToValueAtTime(.0001,s+.25);o.connect(g);g.connect(c.destination);o.start(s);o.stop(s+.26)}setTimeout(()=>c.close().catch(()=>{}),900)}catch{}}
  scheduleLatencyFiller(ms=600){this.clearLatencyFiller();this.fillerTimer=setTimeout(()=>{if(this.isPlaying||this.queue.length)return;this.fillerOn=true;this.speak(FILLER[this.language]||FILLER["en-IN"],true).finally(()=>{this.fillerOn=false})},ms)}
  clearLatencyFiller(){clearTimeout(this.fillerTimer);this.fillerTimer=null}
  enqueueSentence(text,onFirst){
    const t=(text||"").trim();if(!t)return;
    this.clearLatencyFiller();
    if(this.fillerOn){synth()?.cancel();this.fillerOn=false}   // fix: filler never talks over the first server sentence
    this.aborted=false;
    const c=new AbortController();this.ctrls.add(c);
    const p=this.fetchTts(t,c.signal).finally(()=>this.ctrls.delete(c));  // prefetch N+1 while N plays
    this.queue.push({text:t,p,onFirst});
    if(!this.isPlaying)this.drain();
  }
  async fetchTts(text,signal){
    if(this.ttsProvider==="browser")return{browser:true,provider:"browser"};
    try{
      const r=await fetch("/api/tts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({text,language:this.language,provider:this.ttsProvider}),signal});
      if(!r.ok||(r.headers.get("content-type")||"").includes("application/json"))return{browser:true,provider:"browser"};   // 200 + JSON = use browser voice
      return{blob:await r.blob(),provider:r.headers.get("X-TTS-Provider")||"edge-tts"};
    }catch{return{browser:true,provider:"browser"}}
  }
  async drain(){
    if(this.isPlaying)return;
    const g=++this.gen;this.isPlaying=true;this.cb.onSpeakingStart?.();
    while(this.queue.length&&!this.aborted&&g===this.gen){
      const it=this.queue.shift(),r=await it.p;
      if(this.aborted||g!==this.gen)break;
      it.onFirst?.();this.cb.onTtsProviderUsed?.(r.provider);
      await(r.browser?this.speak(it.text):this.play(r.blob));
    }
    if(g===this.gen){this.isPlaying=false;this.cb.onSpeakingEnd?.()}
  }
  play(blob){return new Promise(res=>{
    if(this.aborted)return res();
    const u=URL.createObjectURL(blob),a=new Audio(u);this.audio=a;let d=false;this.tap(a);
    const done=()=>{if(d)return;d=true;if(this.resolveCur===done)this.resolveCur=null;if(this.audio===a)this.audio=null;URL.revokeObjectURL(u);if(this.src){try{this.src.disconnect()}catch{}this.src=null}res()};
    this.resolveCur=done;a.onended=done;a.onerror=done;a.play().catch(done);
  })}
  speak(text,filler=false){return new Promise(res=>{
    const s=synth();if(!s||(this.aborted&&!filler))return res();
    let d=false;const done=()=>{if(d)return;d=true;if(!filler&&this.resolveCur===done)this.resolveCur=null;res()};
    try{
      s.cancel();const u=new SpeechSynthesisUtterance(text);u.lang=this.language==="hi-IN"?"hi-IN":"en-IN";u.rate=1.03;
      const v=s.getVoices(),m=v.find(x=>x.lang.toLowerCase().includes(u.lang.toLowerCase()))||v.find(x=>x.lang.toLowerCase().startsWith("en-in"));if(m)u.voice=m;
      if(!filler)this.resolveCur=done;u.onend=done;u.onerror=done;s.speak(u);
    }catch{done()}
  })}
  stopAll(){   // invariant: call this before aborting the chat stream or restarting the mic
    this.clearLatencyFiller();this.gen++;this.aborted=true;this.queue=[];
    this.ctrls.forEach(c=>c.abort());this.ctrls.clear();
    if(this.audio){try{this.audio.pause();this.audio.currentTime=0}catch{}this.audio=null}
    if(this.resolveCur){const f=this.resolveCur;this.resolveCur=null;f()}
    try{synth()?.cancel()}catch{}
    this.fillerOn=false;this.isPlaying=false;
  }
}
