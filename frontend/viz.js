// >>> CONFIG ---------------------------------------------------------------
const USE_MIC=true;     // false skips the parallel getUserMedia analyser (listening then breathes instead)
const BARS=84;
const STOPS=[[0,"#ffb04d"],[.3,"#ff5aa5"],[.64,"#d23bf0"],[1,"#5b34e8"]];   // crest to base: amber, hot pink, magenta, violet
const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;

export function createVoiceViz({canvas,getState,getTts}){
  const g=canvas.getContext("2d");
  const lv=new Float32Array(BARS),tg=new Float32Array(BARS),sm=new Float32Array(BARS);
  const env=Float32Array.from({length:BARS},(_,i)=>Math.exp(-Math.pow((i/(BARS-1)*2-1)/.58,2)));   // centre-peaked silhouette
  const ph=Float32Array.from({length:BARS},()=>Math.random()*6.28);
  let W=0,H=0,bufT=null,bufM=null,micAn=null,micCtx=null,micStream=null,micBusy=false,micFail=false,wantMic=false;

  // >>> MIC ANALYSER (read-only, never routed to speakers) -------------------
  async function startMic(){
    micBusy=true;
    try{
      const s=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
      if(!wantMic){s.getTracks().forEach(t=>t.stop());micBusy=false;return}
      const C=window.AudioContext||window.webkitAudioContext,c=new C(),a=c.createAnalyser();
      a.fftSize=256;a.smoothingTimeConstant=.7;c.createMediaStreamSource(s).connect(a);
      micStream=s;micCtx=c;micAn=a;
    }catch{micFail=true}
    micBusy=false;
  }
  function stopMic(){
    wantMic=false;micStream?.getTracks().forEach(t=>t.stop());micCtx?.close().catch(()=>{});
    micStream=micCtx=micAn=null;
  }

  // >>> TARGET HEIGHTS PER STATE ---------------------------------------------
  const bin=(buf,i)=>{const x=Math.abs(i/(BARS-1)*2-1);return(buf[1+Math.floor(Math.pow(x,.8)*30)]||0)/255};   // low frequencies sit at the centre
  function targets(st,t){
    const tt=reduce?0:t;
    if(st==="speaking"){
      const an=getTts?.();
      if(an){
        if(!bufT||bufT.length!==an.frequencyBinCount)bufT=new Uint8Array(an.frequencyBinCount);
        an.getByteFrequencyData(bufT);
        for(let i=0;i<BARS;i++)tg[i]=Math.min(1,Math.pow(bin(bufT,i),1.2)*1.9*(.3+.7*env[i]))+.03*env[i];
      }else{    // browser voice cannot be analysed: speech-like simulated envelope
        const s=.5+.5*Math.sin(t*6.1)*Math.sin(t*2.3+1);
        for(let i=0;i<BARS;i++)tg[i]=(.3+.5*Math.abs(Math.sin(t*9+ph[i])))*(.35+.65*s)*Math.pow(env[i],.9);
      }
    }else if(st==="listening"){
      if(micAn){
        if(!bufM||bufM.length!==micAn.frequencyBinCount)bufM=new Uint8Array(micAn.frequencyBinCount);
        micAn.getByteFrequencyData(bufM);
        for(let i=0;i<BARS;i++)tg[i]=Math.min(1,Math.pow(bin(bufM,i),1.1)*2.4*(.3+.7*env[i]))+.05*env[i];
      }else for(let i=0;i<BARS;i++)tg[i]=(.11+.05*Math.sin(tt*2.4+i*.3))*env[i];
    }else if(st==="thinking"){
      for(let i=0;i<BARS;i++)tg[i]=(.14+.14*Math.sin(tt*4.2-i*.28))*(.4+.6*env[i])+.03;
    }else{
      for(let i=0;i<BARS;i++)tg[i]=(.045+.03*Math.sin(tt*1.1+i*.22))*(.5+.5*env[i]);
    }
    if(st==="speaking"||st==="listening"){for(let i=1;i<BARS-1;i++)sm[i]=(tg[i-1]+2*tg[i]+tg[i+1])/4;for(let i=1;i<BARS-1;i++)tg[i]=sm[i]}   // neighbour smoothing fills the silhouette
  }

  // >>> DRAW -----------------------------------------------------------------
  function draw(st){
    g.clearRect(0,0,W,H);
    const cy=Math.round(H*.6),mh=H*.5,bw=W/BARS;
    g.globalAlpha=st==="idle"?.6:1;g.globalCompositeOperation="lighter";
    const body=g.createLinearGradient(0,cy-mh,0,cy);STOPS.forEach(([o,c])=>body.addColorStop(o,c));
    const refl=g.createLinearGradient(0,cy,0,cy+mh*.5);refl.addColorStop(0,"rgba(210,59,240,.32)");refl.addColorStop(1,"rgba(91,52,232,0)");
    const rr=(y,h,up)=>{g.beginPath();for(let i=0;i<BARS;i++){const hh=Math.max(2,lv[i]*mh*(up?1:.5)),x=i*bw+bw*.2,w=bw*.6;g.roundRect?g.roundRect(x,up?cy-hh:y,w,hh,w/2):g.rect(x,up?cy-hh:y,w,hh)}g.fill()};
    g.shadowColor="rgba(225,75,240,.85)";g.shadowBlur=18;g.fillStyle=body;rr(0,0,true);
    g.shadowBlur=0;g.fillStyle=refl;rr(cy+3,0,false);
    const line=g.createLinearGradient(0,0,W,0);   // glowing contour over the bar tops
    line.addColorStop(0,"rgba(255,176,77,0)");line.addColorStop(.5,"rgba(255,176,77,.95)");line.addColorStop(1,"rgba(255,176,77,0)");
    g.beginPath();for(let i=0;i<BARS;i++){const x=i*bw+bw/2,y=cy-Math.max(2,lv[i]*mh);i?g.lineTo(x,y):g.moveTo(x,y)}
    g.strokeStyle=line;g.lineWidth=1.6;g.shadowColor="rgba(255,154,61,.9)";g.shadowBlur=14;g.stroke();
    g.shadowBlur=0;const base=g.createLinearGradient(0,0,W,0);   // horizon line
    base.addColorStop(0,"rgba(124,92,255,0)");base.addColorStop(.5,"rgba(255,90,165,.55)");base.addColorStop(1,"rgba(124,92,255,0)");
    g.fillStyle=base;g.fillRect(0,cy,W,1);g.globalAlpha=1;g.globalCompositeOperation="source-over";
  }

  // >>> LOOP + RESIZE --------------------------------------------------------
  function size(){
    const dpr=Math.min(devicePixelRatio||1,2);W=canvas.clientWidth;H=canvas.clientHeight;
    canvas.width=Math.round(W*dpr);canvas.height=Math.round(H*dpr);g.setTransform(dpr,0,0,dpr,0,0);
  }
  new ResizeObserver(size).observe(canvas);size();
  const t0=performance.now();
  (function frame(now){
    requestAnimationFrame(frame);
    if(!W||!H)return;
    const st=getState?.()||"idle";
    if(USE_MIC&&st==="listening"){wantMic=true;if(!micAn&&!micBusy&&!micFail)startMic()}
    targets(st,(now-t0)/1000);
    for(let i=0;i<BARS;i++)lv[i]+=(tg[i]-lv[i])*(tg[i]>lv[i]?.5:.11);   // fast attack, slow release
    draw(st);
  })(t0);
  return{stopMic};
}
