'use strict';
const $ = id => document.getElementById(id);
const styles = ['Orbit', 'Spektrum', 'Wellenform', 'Aurora'];
const palettes = {
  Amethyst: ['#82a6ff','#b388ff','#ff9fd5'],
  Glut: ['#ffb35c','#ff766c','#e94b98'],
  Gletscher: ['#67c3ff','#75f1ef','#c4ffcc'],
  Mondlicht: ['#8a96b4','#cad1ec','#ffffff']
};
const state = { style: 'Orbit', color: 'Amethyst', gain: 1.4, smooth: 0.65 };
try {
  const saved = JSON.parse(localStorage.getItem('sonora-mic-settings') || '{}');
  if (styles.includes(saved.style)) state.style = saved.style;
  if (Object.hasOwn(palettes, saved.color)) state.color = saved.color;
  for (const [k,min,max] of [['gain',.5,3],['smooth',0,.9]]) {
    if (Number.isFinite(saved[k])) state[k] = Math.max(min,Math.min(max,saved[k]));
  }
} catch {}
function persist() { try { localStorage.setItem('sonora-mic-settings',JSON.stringify(state)); } catch {} }
const canvas = $('screen');
const ctx = canvas.getContext('2d');
const tau = Math.PI*2;
const bands = new Float32Array(96);
let session=null, generation=0, phase='preview', last=0;
let freq, wave, edges, bass=0;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
function status(p, label, message) {
  phase=p; $('mode').textContent=label; $('help').textContent=message;
  $('mic').textContent=p==='live'?'Mikrofon stoppen':p==='pending'?'Abbrechen':'Mikrofon starten';
  document.body.classList.toggle('live',p==='live');
}
function release() {
  generation++;
  if (session) {
    session.stream?.getTracks().forEach(t=>t.stop());
    session.source?.disconnect();
    session.audio.close().catch(()=>{});
    session=null;
  }
  bands.fill(0); bass=0; $('level').value=0;
}
function stop(label='Vorschau',msg='Mikrofon aus. Du kannst die Darstellung wählen oder das Mikrofon erneut starten.') {
  release(); status('preview',label,msg);
}
async function start() {
  if (phase==='pending'||phase==='live') { stop(); return; }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    stop('Kein Zugriff','Öffne die HTTPS-Adresse direkt in Safari, um das Mikrofon zu verwenden.'); return;
  }
  const token=++generation;
  status('pending','Freigabe ausstehend','Erlaube Safari den Mikrofonzugriff. Du kannst die Anfrage hier abbrechen.');
  try {
    const Audio = window.AudioContext || window.webkitAudioContext;
    const audio = new Audio();
    session={audio};
    // Resume within the initiating touch gesture, before the permission prompt.
    const resumed=audio.resume();
    const capture=navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false, noiseSuppression:false, autoGainControl:false},video:false}).then(stream=>{
      if(token!==generation){stream.getTracks().forEach(t=>t.stop());return null;}
      session.stream=stream; return stream;
    });
    const [,stream]=await Promise.all([resumed,capture]);
    if(token!==generation || !stream) return;
    const analyser=audio.createAnalyser();
    analyser.fftSize=2048; analyser.smoothingTimeConstant=.22;
    analyser.minDecibels=-82; analyser.maxDecibels=-17;
    const source=audio.createMediaStreamSource(stream);
    source.connect(analyser);
    Object.assign(session,{source,analyser});
    freq=new Uint8Array(analyser.frequencyBinCount);wave=new Uint8Array(analyser.fftSize);
    const hzPerBin=audio.sampleRate/analyser.fftSize;
    edges=Array.from({length:bands.length+1},(_,i)=>Math.min(freq.length-1,Math.max(1,Math.round(42*(Math.min(15000,audio.sampleRate/2)/42)**(i/bands.length)/hzPerBin))));
    stream.getAudioTracks()[0].addEventListener('ended',()=>{if(token===generation)stop('Unterbrochen','Die Audioquelle wurde getrennt. Starte das Mikrofon erneut.');});
    status('live','Mikrofon live','Das Mikrofon reagiert auf den Raumklang. Audio wird ausschließlich auf diesem Gerät verarbeitet.');
    audio.onstatechange=()=>{if(token===generation && phase==='live' && ['suspended','interrupted'].includes(audio.state))stop('Unterbrochen','Safari hat das Audio unterbrochen. Tippe auf „Mikrofon starten“, um fortzufahren.');};
  } catch(err) {
    if(token!==generation)return;
    const denied=err.name==='NotAllowedError'||err.name==='SecurityError';
    stop('Mikrofon aus',denied?'Mikrofonzugriff gesperrt. Erlaube das Mikrofon in Safaris Website-Einstellungen und versuche es erneut.':err.name==='NotFoundError'?'Es wurde kein Mikrofon gefunden.':'Das Mikrofon ist gerade nicht verfügbar. Prüfe die Audioquelle und versuche es erneut.');
  }
}
$('mic').addEventListener('click',start);
window.addEventListener('pagehide',()=>stop());
document.addEventListener('visibilitychange',()=>{if(document.hidden && phase!=='preview')stop('Pausiert','Beim Verlassen der Seite wurde das Mikrofon freigegeben. Du kannst es wieder starten.');});
function pressed(id,key){for(const b of $(id).children)b.setAttribute('aria-pressed',String(b.dataset.value===state[key]));}
function previewBand(i,t) {
  const peak = (c,s)=>Math.exp(-((i-c)**2)/s);
  return .07 + .38*peak(15+5*Math.sin(t*.8),70) + .43*peak(53+7*Math.cos(t*.5),170)
    + .2*(.5+.5*Math.sin(i*.45+t))*Math.abs(Math.cos(i*.09-t*.4));
}
function input(t,dt) {
  if(phase!=='live'){
    for(let i=0;i<bands.length;i++)bands[i]=previewBand(i,t);
    bass=.38+.10*Math.sin(t);return;
  }
  session.analyser.getByteFrequencyData(freq);session.analyser.getByteTimeDomainData(wave);
  const decay=Math.exp(-dt/(.035+.38*state.smooth));
  for(let i=0;i<bands.length;i++){
    let max=0;
    for(let k=edges[i];k<Math.max(edges[i]+1,edges[i+1]);k++)max=Math.max(max,freq[k]);
    bands[i]=Math.max(Math.min(1,max/255*state.gain*.82),bands[i]*decay);
  }
  bass=bands.subarray(0,18).reduce((a,b)=>a+b,0)/18;
  let energy=0;for(const s of wave)energy+=((s-128)/128)**2;
  $('level').value=Math.min(1,Math.sqrt(energy/wave.length)*3);
}
function draw(g,w,h,style,colors,t,mini=false){
  g.clearRect(0,0,w,h);
  const cy=mini?h*.5:h*.47,cx=w*.5, side=Math.min(w,h), r=side*(mini?.245:.202);
  const grad=g.createLinearGradient(w*.12,h*.15,w*.88,h*.82);
  colors.forEach((c,i)=>grad.addColorStop(i*.5,c));
  g.strokeStyle=grad;g.fillStyle=grad;g.lineCap='round';
  const v=i=>mini?previewBand(i,1):bands[i];
  if(style==='Orbit'){
    if(!mini){
      const halo=g.createRadialGradient(cx,cy,r*.4,cx,cy,r*2.1);
      halo.addColorStop(0,colors[1]+'06');halo.addColorStop(.46,colors[1]+'22');halo.addColorStop(1,colors[1]+'00');
      g.fillStyle=halo;g.fillRect(0,0,w,h);
      for(let j=0;j<56;j++){
        const a=j*2.39996+t*.012, d=r*(1.25+(Math.sin(j*91.13)*.5+.5)*.91);
        g.fillStyle=colors[j%3]+(j%3?'40':'80');g.beginPath();g.arc(cx+Math.cos(a)*d,cy+Math.sin(a)*d,j%4===0?1.3:.7,0,tau);g.fill();
      }
    }
    const n=mini?48:248;
    g.lineWidth=mini?1:Math.max(1.2,r*.014);
    g.shadowColor=colors[1];g.shadowBlur=mini?0:6;
    g.beginPath();
    for(let j=0;j<n;j++){
      const a=j/n*tau+t*.045;
      // Spread nearby low-frequency bands around the full circle while
      // keeping each band's bars grouped into small, readable clusters.
      const position=j/n*96;
      const band=Math.floor(position), within=position-band;
      const k=(band*37)%96;
      const next=(k+37)%96;
      const level=v(k)*(1-within)+v(next)*within;
      const start=r*(1+.022*Math.sin(a*5+t*.25)), end=start+r*(.055+.78*level);
      g.moveTo(cx+Math.cos(a)*start,cy+Math.sin(a)*start);
      g.lineTo(cx+Math.cos(a)*end,cy+Math.sin(a)*end);
    }
    g.stroke();g.shadowBlur=0;g.lineWidth=mini?.7:1;
    for(const mult of [.92,.965]){g.globalAlpha=.5;g.beginPath();g.arc(cx,cy,r*mult,0,tau);g.stroke();}g.globalAlpha=1;
  }else if(style==='Spektrum'){
    const n=mini?20:72,step=w*.8/n;g.fillStyle=grad;
    for(let i=0;i<n;i++){const bar=2+v(Math.floor(i/n*96))*h*.53,x=w*.1+i*step;
      g.fillRect(x,cy+h*.1-bar,Math.max(1,step*.62),bar);
      g.globalAlpha=.20;g.fillRect(x,cy+h*.1+3,Math.max(1,step*.62),bar*.34);g.globalAlpha=1;}
  }else if(style==='Wellenform'){
    g.lineWidth=mini?1.5:2.3;g.beginPath();
    const n=mini?54:400;
    for(let i=0;i<n;i++){const pos=i/(n-1),x=w*(.08+.84*pos);
      const amp=!mini&&phase==='live'?(wave[Math.floor(pos*(wave.length-1))]-128)/128*state.gain:
        Math.sin(pos*35+t)*Math.cos(pos*11-t*.6)*.7;
      const y=cy+Math.max(-1,Math.min(1,amp))*h*.32*Math.sin(Math.PI*pos);i?g.lineTo(x,y):g.moveTo(x,y);}
    g.stroke();
  }else{
    const layers=mini?5:20;
    for(let j=0;j<layers;j++){const depth=j/layers;g.strokeStyle=colors[Math.floor(depth*3)];g.globalAlpha=.20+.55*depth;g.lineWidth=mini?1:1.6;
      g.beginPath();
      for(let i=0;i<96;i++){const x=i/95, a=(.12+v(i))*(.7+.3*Math.cos(depth*3));
        const y=cy+h*(Math.sin(x*8+depth*3+t*.45)*a*.34+(depth-.5)*.23)*Math.sin(x*Math.PI);
        i?g.lineTo(x*w,y):g.moveTo(0,y);}
      g.stroke();}g.globalAlpha=1;
  }
}
function refresh(){
  $('title').textContent=state.style;$('colorName').textContent=state.color;
  $('gainOut').value=state.gain.toLocaleString('de-DE')+'×';$('smoothOut').value=Math.round(state.smooth*100)+' %';
  pressed('styles','style');pressed('colors','color');
  for(const b of $('styles').children){const c=b.firstChild;draw(c.getContext('2d'),c.width,c.height,b.dataset.value,palettes[state.color],0,true);}
  last=0;
}
for(const name of styles){
  const b=document.createElement('button'),pic=document.createElement('canvas');
  pic.width=140;pic.height=64;pic.setAttribute('aria-hidden','true');
  b.dataset.value=name;b.append(pic,document.createTextNode(name));b.onclick=()=>{state.style=name;refresh();persist();};$('styles').append(b);
}
for(const [name,colors] of Object.entries(palettes)){
  const b=document.createElement('button'),dot=document.createElement('i');
  b.setAttribute('aria-label',name);b.dataset.value=name;dot.style.background='linear-gradient(135deg,'+colors.join(',')+')';
  b.append(dot);b.onclick=()=>{state.color=name;refresh();persist();};$('colors').append(b);
}
for(const key of ['gain','smooth']){
  $(key).value=state[key];$(key).oninput=()=>{state[key]=Number($(key).value);refresh();persist();};
}
function focusMode(on){document.body.classList.toggle('focused',on);$('expand').setAttribute('aria-pressed',String(on));$('expand').textContent=on?'Studio anzeigen':'Großansicht';}
$('expand').onclick=()=>focusMode(!document.body.classList.contains('focused'));
window.addEventListener('keydown',e=>{if(e.key==='Escape')focusMode(false);});
canvas.onclick=()=>{state.style=styles[(styles.indexOf(state.style)+1)%styles.length];refresh();persist();};
function frame(now){
  requestAnimationFrame(frame);
  const idle=phase!=='live', interval=reduced.matches && idle?250:1000/30;
  if(now-last<interval-1||document.hidden)return;
  const dt=Math.max(.001,Math.min(.12,(now-last)/1000));last=now;
  const w=canvas.clientWidth,h=canvas.clientHeight, ratio=Math.min(devicePixelRatio||1,2);
  if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
  ctx.setTransform(ratio,0,0,ratio,0,0);const t=reduced.matches?0:now/1000;
  input(t,dt);draw(ctx,w,h,state.style,palettes[state.color],t);
}
refresh();requestAnimationFrame(frame);
