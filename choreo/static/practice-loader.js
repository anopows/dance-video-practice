(async()=>{
  'use strict';
  const pid=location.pathname.split('/').pop();let csrf='',revision=0,refreshTimer;
  async function api(path,options={}){
    const r=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{})}});
    let d={};try{d=await r.json();}catch{}
    if(!r.ok){const e=new Error(typeof d.detail==='string'?d.detail:'Something went wrong. Try again.');e.status=r.status;throw e;}
    return d;
  }
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  function fail(message){
    const p=document.getElementById('error');p.hidden=false;p.textContent=message;
    document.getElementById('video-name').textContent='Video unavailable';
    const logo=document.getElementById('home-logo');logo.hidden=false;
  }
  try{
    const config=await api('/api/bootstrap');csrf=config.csrf||'';
    const payload=await api(`/api/projects/${pid}/practice`);revision=payload.revision;
    const D=payload.analysis,localKey=`choreo-alignment:${pid}:${D.sourceSha256}`;
    let local=null;
    // Link visitors keep their own count fixes in this browser only.
    if(!csrf){try{const saved=JSON.parse(localStorage.getItem(localKey));if(window.PracticeCore.validateSettings(saved,D))local=saved;}catch{}}
    window.PRACTICE_DATA=D;window.PRACTICE_SHARED=payload.alignment;window.PRACTICE_SAVED=local||payload.alignment;
    window.PRACTICE_MEDIA=payload.mediaUrls;window.PRACTICE_SPEED_MEDIA=payload.speedMedia||{};
    function localSave(value){
      const saved={...value,schema:3,sourceSha256:D.sourceSha256,gridId:D.grid.id,timingId:D.attack.id};
      try{localStorage.setItem(localKey,JSON.stringify(saved));return {scope:'device'};}catch{return {scope:'visit'};}
    }
    let saves=Promise.resolve();
    window.ROOM={
      pid,payload,
      get owner(){return !!csrf;},
      hasLocal:!!local,
      shareUrl:location.origin+'/practice/'+pid,
      save(value){
        const task=saves.catch(()=>{}).then(async()=>{
          if(!csrf)return localSave(value);
          try{const r=await api(`/api/projects/${pid}/alignment`,{method:'PUT',body:JSON.stringify({revision,...value})});revision=r.revision;return {scope:'shared'};}
          catch(e){if(e.status!==401)throw e;csrf='';return localSave(value);}
        });
        saves=task;return task;
      },
      clearLocal(){try{localStorage.removeItem(localKey);}catch{}},
      async rename(name){const r=await api(`/api/projects/${pid}`,{method:'PATCH',body:JSON.stringify({name})});payload.name=r.name;return r.name;},
      async remove(){await api(`/api/projects/${pid}`,{method:'DELETE'});},
      async exportVideo(speed,settings,onStatus=()=>{}){
        // Signed-in uploaders save first so the shared counts match the download.
        if(csrf)await window.ROOM.save(settings);
        let job=await api(`/api/projects/${pid}/exports`,{method:'POST',body:JSON.stringify({revision,speed,settings})});
        while(['queued','processing'].includes(job.status)){
          onStatus(job.status);await wait(4000);
          job=await api(`/api/projects/${pid}/exports/${job.id}`);
        }
        if(job.status!=='ready')throw new Error(job.error||"The video couldn't be made. Try again in a few minutes.");
        if(!job.url)job=await api(`/api/projects/${pid}/exports/${job.id}`);
        return job.url;
      }
    };
    // Signed media links last at most 15 minutes; refresh them before they lapse.
    async function refresh(){
      const next=await api(`/api/projects/${pid}/practice`);
      window.PRACTICE_MEDIA=next.mediaUrls;window.PRACTICE_SPEED_MEDIA=next.speedMedia||{};
      window.refreshRoomMedia?.();schedule(next.urlExpires);
    }
    function schedule(expires){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh().catch(e=>{const p=document.getElementById('error');p.hidden=false;p.textContent=e.message;}),Math.max(30000,expires*1000-Date.now()-90000));}
    schedule(payload.urlExpires);
    const app=document.createElement('script');app.src='/static/app.js?v=6';document.head.append(app);
  }catch(e){fail(e.message);}
})();
