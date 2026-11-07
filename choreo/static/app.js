(() => {
  'use strict';
  const D=window.PRACTICE_DATA,C=window.PracticeCore,P=window.PracticePlayback,$=id=>document.getElementById(id);
  const video=$('video'),canvas=$('count-canvas'),ctx=canvas.getContext('2d');
  const saved=window.PRACTICE_SAVED||{},valid=C.validateSettings(saved,D),mode=saved.timingMode==='steady'?'steady':'attack';
  const offset=mode==='attack'&&saved.timingId&&saved.timingId!==D.attack.id?0:valid?.offset??0;
  const state={anchor:valid?.anchor??D.defaultAnchor,offset,timingMode:mode,offsets:{steady:0,attack:0},reviewed:valid?.reviewed??false,listen:'mix',loop:null};
  state.offsets[mode]=offset;
  let frameId=0,pendingSource=null,sourceKey='mix:1',endedLoopPending=false,saveTimer,initialSpeed=1;
  const speedKey='choreo-speed:'+D.sourceSha256,colors=['#99a693','#d2ed96','#e7be70','#f4bd79'];
  try{const rate=Number(localStorage.getItem(speedKey));if([.25,.5,.75,1].includes(rate))initialSpeed=rate;}catch{}
  const say=text=>$('status').textContent=text;
  const duration=()=>D.duration;
  const time=()=>pendingSource?.time??P.toSourceTime(video.currentTime||0,playback.sourceRate);
  function error(text=''){$('error').hidden=!text;$('error').textContent=text;}
  const playback=window.PracticePlayback.attach(video,{initial:initialSpeed,onError:error,onChange:(requested,actual)=>{
    for(const button of document.querySelectorAll('[data-speed]'))button.setAttribute('aria-pressed',String(Math.abs(Number(button.dataset.speed)-actual)<.001));
    video.dataset.requestedSpeed=String(requested);
  }});
  function alignment(){window.currentAlignment={anchor:state.anchor,offset:state.offset,timingMode:state.timingMode,reviewed:state.reviewed};}
  function save(){
    alignment();clearTimeout(saveTimer);$('save-status').textContent='Saving…';
    saveTimer=setTimeout(async()=>{try{const result=await window.saveRoomAlignment({...window.currentAlignment});$('save-status').textContent=result?.scope==='device'?'Saved on this device':result?.scope==='visit'?'For this visit only':'Saved';}catch(e){$('save-status').textContent='Not saved';error(e.message);}},450);
  }
  function draw(t){
    const e=C.eventAt(D,t,state.anchor,state.offset,state.timingMode),count=e.count;
    ctx.fillStyle='#101411';ctx.fillRect(0,0,720,96);
    for(let n=1;n<=8;n++){
      const x=10+(n-1)*88,level=C.accent(n),active=count===n,color=colors[level];
      ctx.fillStyle=active?color:'#222c24';ctx.beginPath();ctx.roundRect(x,10,80,76,10);ctx.fill();
      ctx.fillStyle=active?'#101411':color;ctx.font='700 42px system-ui, sans-serif';ctx.textAlign='center';ctx.fillText(String(n),x+40,63);
      if(level)ctx.fillRect(x+14,77,52,level+1);
      if(level===3)ctx.fillRect(x+14,72,52,1);
    }
    ctx.fillStyle='#46523e';ctx.fillRect(358,24,2,48);
    canvas.dataset.count=count??'';canvas.setAttribute('aria-label',count===null?'Waiting for the first beat':`Music count ${count}`);
  }
  function render(){
    const t=time();$('clock').textContent=`${C.timeLabel(t)} / ${C.timeLabel(duration())}`;
    $('play').textContent=video.paused||video.ended?'Play':'Pause';
    $('seek').value=String(t);$('seek').setAttribute('aria-valuetext',C.timeLabel(t));draw(t);
  }
  function showAlignment(){
    $('anchor-status').textContent=`${state.reviewed?'Your':'Suggested'} 1 at ${C.timeLabel(C.anchorTime(D,state.anchor,state.timingMode)+state.offset)}`;
    $('offset-value').textContent=`${state.offset>0?'+':''}${Math.round(state.offset*1000)} ms`;
    $('offset').value=String(Math.round(state.offset*1000));
  }
  function stopLoop(){state.loop=null;$('loop-off').disabled=true;$('loop-status').textContent='Loop off';}
  function seek(t,{manual=true,pause=true}={}){
    const target=C.clamp(t,0,duration());if(manual&&state.loop&&(target<state.loop[0]||target>=state.loop[1]))stopLoop();
    if(pendingSource){pendingSource.time=target;if(pause)pendingSource.play=false;render();return;}
    if(video.readyState<1)return;if(pause)video.pause();video.currentTime=P.toMediaTime(target,playback.sourceRate);render();
  }
  async function play(){try{await video.play();if(!video.error)error();}catch(e){if(e.name!=='AbortError')error('Tap Play to start the video.');}}
  function tick(){frameId=0;if(state.loop&&!video.paused&&!video.seeking&&time()>=state.loop[1])video.currentTime=P.toMediaTime(state.loop[0],playback.sourceRate);render();if(!video.paused&&!video.ended)frameId=requestAnimationFrame(tick);}
  function schedule(){if(!frameId)frameId=requestAnimationFrame(tick);}
  function changeSource({force=false}={}){
    state.listen=$('listen').value;
    const selected=P.sourceFor(window.PRACTICE_MEDIA,window.PRACTICE_SPEED_MEDIA,state.listen,playback.speed);
    $('audio-quality').textContent=selected.sourceRate!==1?'Enhanced slow audio':state.listen!=='mix'?'Instrument estimate':playback.speed===1?'Original audio':'Standard slow audio';
    if(!force&&selected.key===sourceKey)return;
    if(!selected.url){error('This audio source is unavailable.');return;}
    pendingSource={time:time(),play:pendingSource?.play??!video.paused};sourceKey=selected.key;
    video.pause();$('play').disabled=true;$('seek').disabled=true;error();
    playback.setSourceRate(selected.sourceRate);video.dataset.sourceRate=String(selected.sourceRate);
    video.src=selected.url;video.load();render();
  }
  window.refreshRoomMedia=()=>changeSource({force:true});
  function ready(){
    const pending=pendingSource;pendingSource=null;
    $('play').disabled=false;$('seek').disabled=false;$('seek').max=String(duration());
    if(pending)video.currentTime=P.toMediaTime(C.clamp(pending.time,0,duration()),playback.sourceRate);
    playback.restore();render();if(pending?.play)play();
  }
  $('play').addEventListener('click',()=>video.paused?play():video.pause());
  $('seek').addEventListener('input',e=>seek(Number(e.target.value),{pause:false}));
  $('listen').addEventListener('change',()=>changeSource());
  for(const button of document.querySelectorAll('[data-speed]'))button.addEventListener('click',()=>{
    playback.set(Number(button.dataset.speed));changeSource();try{localStorage.setItem(speedKey,String(playback.speed));}catch{}
    say(`Playback speed ${playback.speed*100}%`);
  });
  $('prev-beat').addEventListener('click',()=>seek(C.stepTime(D,time(),-1,state.offset,state.timingMode)));
  $('next-beat').addEventListener('click',()=>seek(C.stepTime(D,time(),1,state.offset,state.timingMode)));
  $('prev-frame').addEventListener('click',()=>seek(C.frameTarget(time(),-1)));
  $('next-frame').addEventListener('click',()=>seek(C.frameTarget(time(),1)));
  $('loop-current').addEventListener('click',()=>{
    state.loop=C.eventLoop(D,time(),state.anchor,state.offset,duration(),Number($('loop-size').value),state.timingMode);
    $('loop-off').disabled=false;$('loop-status').textContent=`${C.timeLabel(state.loop[0])} → ${C.timeLabel(state.loop[1])}`;
    seek(state.loop[0],{manual:false,pause:false});$('tools').close();
  });
  $('loop-off').addEventListener('click',stopLoop);
  $('timing').value=state.timingMode;$('timing').addEventListener('change',()=>{
    state.offsets[state.timingMode]=state.offset;state.timingMode=$('timing').value;state.offset=state.offsets[state.timingMode];stopLoop();showAlignment();render();save();
  });
  $('set-one').addEventListener('click',()=>{state.anchor=C.nearestIndex(D,time(),state.offset,state.timingMode);state.reviewed=true;stopLoop();seek(C.anchorTime(D,state.anchor,state.timingMode)+state.offset);showAlignment();save();});
  $('reset-one').addEventListener('click',()=>{state.anchor=D.defaultAnchor;state.offset=0;state.offsets[state.timingMode]=0;state.reviewed=false;stopLoop();showAlignment();render();save();});
  $('offset').addEventListener('input',e=>{state.offset=Number(e.target.value)/1000;state.offsets[state.timingMode]=state.offset;stopLoop();showAlignment();render();save();});
  $('more').addEventListener('click',()=>{video.pause();$('tools').showModal();});$('close-tools').addEventListener('click',()=>$('tools').close());
  $('tools').addEventListener('click',e=>{if(e.target===$('tools')){const r=$('tools').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('tools').close();}});
  video.addEventListener('loadedmetadata',ready);
  video.addEventListener('play',()=>{render();schedule();});
  video.addEventListener('pause',()=>{if(frameId)cancelAnimationFrame(frameId);frameId=0;render();});
  for(const event of ['seeking','seeked','timeupdate'])video.addEventListener(event,render);
  video.addEventListener('ended',()=>{if(state.loop&&!endedLoopPending){endedLoopPending=true;video.currentTime=P.toMediaTime(state.loop[0],playback.sourceRate);play().finally(()=>endedLoopPending=false);}else render();});
  video.addEventListener('error',()=>{video.dataset.mediaError=String(video.error?.code??'unknown');error('Video could not load. Reload, or choose Full mix in Tools.');$('play').disabled=true;});
  document.addEventListener('keydown',e=>{if($('tools').open||['INPUT','SELECT','BUTTON','A','TEXTAREA'].includes(e.target.tagName))return;if(e.code==='Space'){e.preventDefault();video.paused?play():video.pause();}else if(e.code==='ArrowRight'){$('next-beat').click();e.preventDefault();}else if(e.code==='ArrowLeft'){$('prev-beat').click();e.preventDefault();}});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){render();if(!video.paused)schedule();}});
  if(D.countQuality?.unsupported){
    $('count-canvas').hidden=true;
    for(const id of ['prev-beat','next-beat','loop-current','set-one','reset-one','timing','offset'])$(id).disabled=true;
    for(const b of document.querySelectorAll('[data-export]'))b.disabled=true;
    $('count-warning').hidden=false;$('count-warning').textContent='Music is unclear · counts unavailable for this clip';
  }else if(D.countQuality?.needsReview){$('count-warning').hidden=false;$('count-warning').textContent='Counts need a quick check · adjust in Tools';}
  alignment();showAlignment();if(video.readyState>=1)ready();else render();changeSource();
})();
