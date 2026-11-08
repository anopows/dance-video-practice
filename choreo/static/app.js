(() => {
  'use strict';
  const R=window.ROOM,D=window.PRACTICE_DATA,C=window.PracticeCore,P=window.PracticePlayback,$=id=>document.getElementById(id);
  const video=$('video'),canvas=$('count-canvas'),fixCanvas=$('fix-canvas'),track=$('track-canvas');
  const owner=R.owner,unsupported=!!D.countQuality?.unsupported;
  const store={get(k){try{return localStorage.getItem(k);}catch{return null;}},set(k,v){try{localStorage.setItem(k,v);}catch{}}};

  // ---------- text helpers ----------
  const fmtDur=s=>{s=Math.max(0,Math.round(s));return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;};
  const fmtDate=e=>new Date(e*1000).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'});
  function daysLeft(e){const d=Math.ceil((e*1000-Date.now())/86400000);return d<=1?(d<=0?'Removed today':'Removed tomorrow'):`${d} days left`;}
  const say=text=>{$('status').textContent=text;};
  let toastTimer;
  function toast(text){const t=$('toast');t.textContent=text;t.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.hidden=true,2200);}
  function error(text=''){$('error').hidden=!text;$('error').textContent=text;}

  // ---------- header ----------
  function setName(name){$('video-name').textContent=name;document.title=`${name} · five·six`;}
  setName(R.payload.name);
  $('video-sub').textContent=owner?`${fmtDur(D.duration)} · ${daysLeft(R.payload.expires)}`:'Shared with you';
  $('back').hidden=!owner;$('home-logo').hidden=owner;
  for(const el of document.querySelectorAll('.owner-only'))el.hidden=!owner;
  video.poster=R.payload.posterUrl;
  $('share').disabled=false;$('more').disabled=false;

  // ---------- state ----------
  const saved=window.PRACTICE_SAVED||{},valid=C.validateSettings(saved,D),mode=saved.timingMode==='steady'?'steady':'attack';
  const offset0=mode==='attack'&&saved.timingId&&saved.timingId!==D.attack.id?0:valid?.offset??0;
  const state={anchor:valid?.anchor??D.defaultAnchor,offset:offset0,timingMode:mode,offsets:{steady:0,attack:0},
    reviewed:valid?.reviewed??false,listen:'mix',loop:null,loopLen:8,frameMode:store.get('five-six-frame-mode')==='1'||unsupported,localFix:!owner&&R.hasLocal};
  state.offsets[mode]=offset0;
  let frameId=0,pendingSource=null,sourceKey='',endedLoopPending=false,saveTimer,eightTimes=[],lastPlaying=null;
  const speedKey='choreo-speed:'+D.sourceSha256;let initialSpeed=1;
  {const rate=Number(store.get(speedKey));if([.25,.5,.75,1].includes(rate))initialSpeed=rate;}
  const playback=P.attach(video,{initial:initialSpeed,onError:error,onChange:(requested,actual)=>{
    for(const b of document.querySelectorAll('[data-speed]'))b.setAttribute('aria-pressed',String(Math.abs(Number(b.dataset.speed)-actual)<.001));
    video.dataset.requestedSpeed=String(requested);
  }});
  const time=()=>pendingSource?.time??P.toSourceTime(video.currentTime||0,playback.sourceRate);
  const current=()=>({anchor:state.anchor,offset:state.offset,timingMode:state.timingMode,reviewed:state.reviewed});
  function alignment(){window.currentAlignment=current();}

  // ---------- count strip (matches render.py make_strip) ----------
  const COL={bg:'#0C0C0E',cell:'#18181C',active:'#F4F2EC',muted:'#8F8E96',brass:'#E8B44C',brassOn:'#9A6A10'};
  const W=720,H=96,PAD=6,GAP=8,CW=(W-2*PAD-7*GAP)/8,TOP=6,BOT=H-6,BARH={1:2,2:5,3:8},BARW={1:.44,2:.44,3:.62};
  for(const cv of [canvas,fixCanvas]){cv.width=W*2;cv.height=H*2;}
  function drawCounts(cv,count){
    const ctx=cv.getContext('2d');ctx.setTransform(2,0,0,2,0,0);ctx.globalAlpha=1;ctx.fillStyle=COL.bg;ctx.fillRect(0,0,W,H);
    ctx.globalAlpha=count==null?.45:1;
    for(let n=1;n<=8;n++){
      const x=PAD+(n-1)*(CW+GAP),level=C.accent(n),on=count===n;
      ctx.fillStyle=on?COL.active:COL.cell;ctx.beginPath();ctx.roundRect(x,TOP,CW,BOT-TOP,12);ctx.fill();
      ctx.fillStyle=on?COL.bg:n===1?COL.active:COL.muted;ctx.font='800 40px system-ui, -apple-system, "Segoe UI", sans-serif';
      ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(n),x+CW/2,(TOP+BOT)/2-2);
      if(level){const w=CW*BARW[level],h=BARH[level],b=BOT-10;ctx.fillStyle=on?COL.brassOn:COL.brass;ctx.beginPath();ctx.roundRect(x+(CW-w)/2,b-h,w,h,Math.min(2,h/2));ctx.fill();}
      if(n===1){ctx.fillStyle=on?COL.brassOn:COL.brass;ctx.beginPath();ctx.arc(x+CW/2,TOP+11,3.5,0,Math.PI*2);ctx.fill();}
    }
    ctx.globalAlpha=1;
    if(cv.dataset.count!==String(count??'')){cv.dataset.count=count??'';cv.setAttribute('aria-label',count==null?'Waiting for the first beat':`Count ${count} of 8`);}
  }

  // ---------- timeline: eight markers, repeat band, progress ----------
  function computeEights(){
    const out=[],m=(i)=>((i-state.anchor)%8+8)%8===0;
    if(state.timingMode==='attack'&&D.attack){for(const e of D.attack.events)if(m(e.index))out.push(e.time+state.offset);}
    else{const g=D.grid,n=Math.ceil((D.duration-g.origin)/g.period);for(let i=0;i<=n;i++)if(m(i))out.push(g.origin+i*g.period+state.offset);}
    eightTimes=unsupported?[]:out.filter(t=>t>=0&&t<=D.duration);
  }
  function drawTrack(t){
    const r=track.getBoundingClientRect(),dpr=window.devicePixelRatio||1,w=Math.max(1,Math.round(r.width*dpr)),h=Math.max(1,Math.round(r.height*dpr));
    if(track.width!==w||track.height!==h){track.width=w;track.height=h;}
    const ctx=track.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
    const x0=8,span=Math.max(1,r.width-16),mid=r.height/2,X=s=>x0+span*C.clamp(s/D.duration,0,1);
    ctx.clearRect(0,0,r.width,r.height);
    if(state.loop){ctx.fillStyle='rgba(232,180,76,.22)';ctx.strokeStyle=COL.brass;ctx.lineWidth=1;ctx.beginPath();ctx.roundRect(X(state.loop[0]),mid-8,Math.max(3,X(state.loop[1])-X(state.loop[0])),16,4);ctx.fill();ctx.stroke();}
    ctx.fillStyle='#232328';ctx.beginPath();ctx.roundRect(x0,mid-2,span,4,2);ctx.fill();
    if(eightTimes.length){const every=Math.max(1,Math.ceil(5/(span/eightTimes.length)));ctx.fillStyle='rgba(255,255,255,.25)';
      eightTimes.forEach((e,i)=>{if(i%every===0)ctx.fillRect(Math.round(X(e))-.5,mid-5,1,10);});}
    ctx.fillStyle=COL.active;ctx.beginPath();ctx.roundRect(x0,mid-2,Math.max(0,X(t)-x0),4,2);ctx.fill();
  }

  // ---------- render loop ----------
  function setPlayIcons(playing){
    if(playing===lastPlaying)return;lastPlaying=playing;
    for(const [btn,icon] of [[$('play'),$('play-icon')],[$('fix-play'),$('fix-play-icon')]]){
      icon.setAttribute('href',playing?'#i-pause':'#i-play');btn.setAttribute('aria-label',playing?'Pause':'Play');
    }
  }
  function render(){
    const t=time(),count=C.eventAt(D,t,state.anchor,state.offset,state.timingMode).count;
    if(!unsupported){drawCounts(canvas,count);if($('fix-sheet').open)drawCounts(fixCanvas,count);}
    $('clock').textContent=`${C.timeLabel(t)} / ${fmtDur(D.duration)}`;
    setPlayIcons(!(video.paused||video.ended)&&!pendingSource);
    $('seek').value=String(t);$('seek').setAttribute('aria-valuetext',C.timeLabel(t));
    drawTrack(t);
  }
  function tick(){
    frameId=0;
    if(state.loop&&!video.paused&&!video.seeking&&time()>=state.loop[1])video.currentTime=P.toMediaTime(state.loop[0],playback.sourceRate);
    render();if(!video.paused&&!video.ended)frameId=requestAnimationFrame(tick);
  }
  function schedule(){if(!frameId)frameId=requestAnimationFrame(tick);}

  // ---------- transport ----------
  async function play(){try{await video.play();if(!video.error)error();}catch(e){if(e.name!=='AbortError')error('Tap Play to start.');}}
  const toggle=()=>video.paused?play():video.pause();
  function seek(t,{manual=true,pause=true}={}){
    const target=C.clamp(t,0,D.duration);
    if(manual&&state.loop&&(target<state.loop[0]-.01||target>=state.loop[1]))stopRepeat();
    if(pendingSource){pendingSource.time=target;if(pause)pendingSource.play=false;render();return;}
    if(video.readyState<1)return;if(pause)video.pause();video.currentTime=P.toMediaTime(target,playback.sourceRate);render();
  }
  function step(direction){
    if(state.frameMode)seek(C.frameTarget(time(),direction));
    else seek(C.stepTime(D,time(),direction,state.offset,state.timingMode));
  }
  function updateStepLabels(){
    const unit=state.frameMode?'Frame':'Beat';
    for(const [id,dir] of [['prev-step','Back'],['next-step','Forward']]){$(id).querySelector('span').textContent=unit;$(id).setAttribute('aria-label',`${dir} one ${unit.toLowerCase()}`);}
    $('frame-mode').setAttribute('aria-checked',String(state.frameMode));
  }
  $('play').addEventListener('click',toggle);$('fix-play').addEventListener('click',toggle);
  $('seek').addEventListener('input',e=>seek(Number(e.target.value),{pause:false}));
  $('prev-step').addEventListener('click',()=>step(-1));$('next-step').addEventListener('click',()=>step(1));
  $('frame-mode').addEventListener('click',()=>{if(unsupported)return;state.frameMode=!state.frameMode;store.set('five-six-frame-mode',state.frameMode?'1':'0');updateStepLabels();});
  for(const b of document.querySelectorAll('[data-speed]'))b.addEventListener('click',()=>{
    playback.set(Number(b.dataset.speed));changeSource();store.set(speedKey,String(playback.speed));say(`Speed ${playback.speed*100}%`);
  });

  // ---------- repeat ----------
  function loopRange(from,size){
    const p=C.eventAt(D,from+1e-3,state.anchor,state.offset,state.timingMode);
    const index=p.index??(state.timingMode==='attack'&&D.attack?D.attack.events[0]?.index:0)??0;
    let first=state.anchor+Math.floor((index-state.anchor)/8)*8;
    // An eight that starts before the video (pickup) is incomplete: use the first full one.
    if(C.anchorTime(D,first,state.timingMode)+state.offset<0)first+=8;
    const start=C.clamp(C.anchorTime(D,first,state.timingMode)+state.offset,0,D.duration);
    let end=C.clamp(C.anchorTime(D,first+size,state.timingMode)+state.offset,0,D.duration);
    if(!(end>start))end=D.duration;
    return [start,end];
  }
  function updateRepeat(){
    const on=!!state.loop;
    $('repeat').setAttribute('aria-pressed',String(on));$('repeat-bar').hidden=!on;$('repeat-tag').hidden=!on;
    if(on){
      $('repeat-range').textContent=`Repeating ${C.timeLabel(state.loop[0])} – ${C.timeLabel(state.loop[1])}`;
      $('repeat-tag').textContent=`REPEAT 1–${state.loopLen}`;
      for(const c of document.querySelectorAll('[data-len]'))c.setAttribute('aria-pressed',String(Number(c.dataset.len)===state.loopLen));
    }
    render();
  }
  function setRepeat(size,from){state.loopLen=size;state.loop=loopRange(from,size);updateRepeat();}
  function stopRepeat(){if(!state.loop)return;state.loop=null;updateRepeat();say('Repeat off');}
  $('repeat').addEventListener('click',()=>{
    if(state.loop){stopRepeat();return;}
    setRepeat(8,time());seek(state.loop[0],{manual:false,pause:false});say('Repeating this eight');
  });
  for(const c of document.querySelectorAll('[data-len]'))c.addEventListener('click',()=>{
    setRepeat(Number(c.dataset.len),state.loop?state.loop[0]:time());
    const t=time();if(t<state.loop[0]||t>=state.loop[1])seek(state.loop[0],{manual:false,pause:false});
  });
  $('repeat-stop').addEventListener('click',stopRepeat);
  function refreshLoop(){if(state.loop)setRepeat(state.loopLen,state.loop[0]);}

  // ---------- audio sources ----------
  function changeSource({force=false}={}){
    const selected=P.sourceFor(window.PRACTICE_MEDIA,window.PRACTICE_SPEED_MEDIA,state.listen,playback.speed);
    updateNotice();
    if(!force&&selected.key===sourceKey)return;
    if(!selected.url){
      if(state.listen!=='mix'){state.listen='mix';updateSolo();toast("This sound isn't available. Switched back to the full band.");changeSource();}
      else error("The video didn't load. Reload the page to try again.");
      return;
    }
    pendingSource={time:time(),play:pendingSource?.play??(!video.paused&&!!sourceKey)};sourceKey=selected.key;
    video.pause();$('play').disabled=true;$('seek').disabled=true;error();
    playback.setSourceRate(selected.sourceRate);video.dataset.sourceRate=String(selected.sourceRate);
    video.src=selected.url;video.load();render();
  }
  window.refreshRoomMedia=()=>changeSource({force:true});
  function ready(){
    const pending=pendingSource;pendingSource=null;
    $('play').disabled=false;$('seek').disabled=false;$('seek').max=String(D.duration);
    if(pending)video.currentTime=P.toMediaTime(C.clamp(pending.time,0,D.duration),playback.sourceRate);
    playback.restore();render();if(pending?.play)play();
  }

  // ---------- notices ----------
  function updateNotice(){
    let text='',action='',grey=true,handler=null;
    if(unsupported){text='No steady beat found, so counts are hidden. Slow-mo and frame steps still work.';}
    else if(D.countQuality?.needsReview&&!state.reviewed){text='The counts might be off on this clip.';action='Check the 1 ›';grey=false;handler=()=>openSheet('fix-sheet');}
    else if(state.localFix){text='Your count fixes are saved on this device only.';action='Use original';handler=useOriginal;}
    else if(state.listen==='mix'&&playback.speed!==1&&typeof window.PRACTICE_SPEED_MEDIA?.[String(playback.speed)]!=='string'){text="Smooth slow-mo audio isn't ready for this speed yet, so it may sound choppy.";}
    $('notice').hidden=!text;$('notice').classList.toggle('grey',grey);$('notice-text').textContent=text;
    $('notice-action').hidden=!action;$('notice-action').textContent=action;noticeHandler=handler;
  }
  let noticeHandler=null;
  $('notice-action').addEventListener('click',()=>noticeHandler?.());

  // ---------- sheets ----------
  function openSheet(id){
    for(const d of document.querySelectorAll('dialog[open]'))if(d.id!==id)d.close();
    const d=$(id);if(!d.open)d.showModal();
    if(id==='fix-sheet'){showAlignment();render();}
  }
  for(const d of document.querySelectorAll('dialog.sheet')){
    d.addEventListener('click',e=>{if(e.target!==d)return;const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();});
    for(const b of d.querySelectorAll('[data-close]'))b.addEventListener('click',()=>d.close());
  }
  for(const b of document.querySelectorAll('[data-open]'))b.addEventListener('click',()=>openSheet(b.dataset.open));
  $('more').addEventListener('click',()=>openSheet('more-sheet'));

  // ---------- fix the counts ----------
  function showAlignment(){
    const at=C.timeLabel(C.anchorTime(D,state.anchor,state.timingMode)+state.offset);
    const who=!state.reviewed?'found automatically':(owner||state.localFix)?'set by you':'set by hand';
    $('anchor-status').textContent=`The 1 is at ${at} (${who}).`;
    $('offset-value').textContent=`${state.offset>0?'+':state.offset<0?'−':''}${Math.abs(Math.round(state.offset*1000))} ms`;
    $('mode-attack').setAttribute('aria-checked',String(state.timingMode==='attack'));
    $('mode-steady').setAttribute('aria-checked',String(state.timingMode==='steady'));
  }
  function changed(){computeEights();refreshLoop();showAlignment();updateNotice();render();save();}
  function save(){
    alignment();clearTimeout(saveTimer);const status=$('save-status');status.classList.remove('bad');status.textContent='Saving…';
    saveTimer=setTimeout(async()=>{
      try{
        const result=await R.save({...window.currentAlignment});
        if(result?.scope==='shared')status.textContent='Saved. Everyone with the link sees this.';
        else{state.localFix=true;updateNotice();status.textContent=result?.scope==='device'?'Saved on this device. Others still see the original counts.':'Kept until you close this page.';}
      }catch(e){status.classList.add('bad');status.textContent="Couldn't save your change. Check your connection and try again.";}
    },450);
  }
  $('set-one').addEventListener('click',()=>{state.anchor=C.nearestIndex(D,time(),state.offset,state.timingMode);state.reviewed=true;changed();say('The 1 is set');});
  function nudge(ms){state.offset=C.clamp(Math.round((state.offset+ms/1000)*1000)/1000,-.5,.5);state.offsets[state.timingMode]=state.offset;changed();}
  $('earlier').addEventListener('click',()=>nudge(-10));$('later').addEventListener('click',()=>nudge(10));
  function setMode(next){if(next===state.timingMode)return;state.offsets[state.timingMode]=state.offset;state.timingMode=next;state.offset=state.offsets[next];changed();}
  $('mode-attack').addEventListener('click',()=>setMode('attack'));$('mode-steady').addEventListener('click',()=>setMode('steady'));
  $('reset-one').addEventListener('click',()=>{state.anchor=D.defaultAnchor;state.offset=0;state.offsets={steady:0,attack:0};state.reviewed=false;changed();});
  function useOriginal(){
    R.clearLocal();state.localFix=false;
    const shared=window.PRACTICE_SHARED||{},v=C.validateSettings(shared,D),m=shared.timingMode==='steady'?'steady':'attack';
    state.anchor=v?.anchor??D.defaultAnchor;state.offset=v?.offset??0;state.timingMode=m;state.offsets={steady:0,attack:0};state.offsets[m]=state.offset;state.reviewed=v?.reviewed??false;
    alignment();computeEights();refreshLoop();showAlignment();updateNotice();render();toast('Back to the original counts');
  }

  // ---------- hear one instrument ----------
  const NAMES={mix:'Full band',bass:'Bass',drums:'Drums',piano:'Piano',guitar:'Guitar'};
  function updateSolo(){
    const missing=[];
    for(const b of document.querySelectorAll('[data-listen]')){
      const key=b.dataset.listen,available=!!window.PRACTICE_MEDIA[key];
      b.disabled=!available;b.setAttribute('aria-checked',String(state.listen===key));if(!available)missing.push(NAMES[key]);
    }
    $('solo-missing').hidden=!missing.length;
    $('solo-missing').textContent=missing.length===1?`${missing[0]} isn't available for this video.`:`${missing.slice(0,-1).join(', ')} and ${missing.at(-1).toLowerCase()} aren't available for this video.`;
    $('solo-tag').hidden=state.listen==='mix';$('solo-tag').textContent=`${NAMES[state.listen].toUpperCase()} ONLY`;
  }
  for(const b of document.querySelectorAll('[data-listen]'))b.addEventListener('click',()=>{state.listen=b.dataset.listen;updateSolo();changeSource();});

  // ---------- download ----------
  for(const opt of document.querySelectorAll('[data-export]')){
    const speed=Number(opt.dataset.export),meta=opt.querySelector('.opt-meta'),action=opt.querySelector('.opt-action'),prog=opt.querySelector('.prog');
    const base=`${speed*100}% · ${fmtDur(D.duration/speed)}`;meta.textContent=base;
    if(unsupported){action.querySelector('button').disabled=true;continue;}
    action.addEventListener('click',async e=>{
      if(!(e.target instanceof HTMLButtonElement))return;
      opt.classList.add('busy');action.hidden=true;prog.hidden=false;meta.style.color='';meta.textContent='Making your video… keep practising';
      try{
        alignment();
        const url=await R.exportVideo(speed,{...window.currentAlignment},status=>{meta.textContent=status==='queued'?'Waiting for other downloads to finish…':'Making your video… keep practising';});
        meta.textContent=base;const a=document.createElement('a');a.className='btn small light';a.href=url;a.rel='noreferrer';a.textContent='Save video';action.replaceChildren(a);
        if(!$('download-sheet').open)toast('Your video is ready in Download');
      }catch(err){meta.textContent=err.message;meta.style.color='var(--red)';action.replaceChildren(Object.assign(document.createElement('button'),{className:'btn small light',textContent:'Try again'}));}
      finally{opt.classList.remove('busy');prog.hidden=true;action.hidden=false;}
    });
  }
  if(unsupported)$('download-note').textContent="No steady beat was found, so a video with counts can't be made for this clip.";

  // ---------- share ----------
  $('share-url').value=R.shareUrl;$('share-expiry').textContent=`The link stops working on ${fmtDate(R.payload.expires)}.`;
  $('share').addEventListener('click',async()=>{
    if(navigator.share&&matchMedia('(pointer: coarse)').matches){
      try{await navigator.share({title:`${R.payload.name} with counts`,url:R.shareUrl});return;}catch(e){if(e.name==='AbortError')return;}
    }
    openSheet('share-sheet');
  });
  $('copy-link').addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText(R.shareUrl);$('copy-link').textContent='Copied';setTimeout(()=>$('copy-link').textContent='Copy link',2000);}
    catch{$('share-url').select();}
  });

  // ---------- rename & delete (signed-in uploaders) ----------
  $('rename-sheet').addEventListener('close',()=>{$('rename-error').hidden=true;});
  document.querySelector('[data-open="rename-sheet"]').addEventListener('click',()=>{$('rename-input').value=R.payload.name;$('rename-input').select();});
  $('rename-form').addEventListener('submit',async e=>{
    e.preventDefault();
    try{setName(await R.rename($('rename-input').value));$('rename-sheet').close();toast('Renamed');}
    catch(err){$('rename-error').hidden=false;$('rename-error').textContent=err.message;}
  });
  $('confirm-delete').addEventListener('click',async()=>{
    $('confirm-delete').disabled=true;
    try{await R.remove();location.assign('/');}
    catch(err){$('delete-error').hidden=false;$('delete-error').textContent=err.message;$('confirm-delete').disabled=false;}
  });

  // ---------- video events & keys ----------
  video.addEventListener('loadedmetadata',ready);
  video.addEventListener('play',()=>{render();schedule();});
  video.addEventListener('pause',()=>{if(frameId)cancelAnimationFrame(frameId);frameId=0;render();});
  for(const ev of ['seeking','seeked','timeupdate'])video.addEventListener(ev,render);
  video.addEventListener('ended',()=>{if(state.loop&&!endedLoopPending){endedLoopPending=true;video.currentTime=P.toMediaTime(state.loop[0],playback.sourceRate);play().finally(()=>endedLoopPending=false);}else render();});
  video.addEventListener('error',()=>{video.dataset.mediaError=String(video.error?.code??'unknown');error("The video didn't load. Reload the page to try again.");$('play').disabled=true;});
  document.addEventListener('keydown',e=>{
    if(document.querySelector('dialog[open]')&&!$('fix-sheet').open)return;
    if(['INPUT','SELECT','BUTTON','A','TEXTAREA'].includes(e.target.tagName))return;
    if(e.code==='Space'){e.preventDefault();toggle();}
    else if(e.code==='ArrowRight'){e.preventDefault();step(1);}
    else if(e.code==='ArrowLeft'){e.preventDefault();step(-1);}
    else if(e.key==='.'){seek(C.frameTarget(time(),1));}
    else if(e.key===','){seek(C.frameTarget(time(),-1));}
  });
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){render();if(!video.paused)schedule();}});
  window.addEventListener('resize',render);

  // ---------- start ----------
  if(unsupported){
    canvas.hidden=true;$('repeat').disabled=true;$('frame-mode').disabled=true;
    for(const id of ['set-one','reset-one','earlier','later','mode-attack','mode-steady'])$(id).disabled=true;
    document.querySelector('[data-open="fix-sheet"]').disabled=true;
  }
  alignment();computeEights();updateStepLabels();showAlignment();updateSolo();updateRepeat();changeSource({force:true});
})();
