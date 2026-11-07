(async()=>{
  const pid=location.pathname.split('/').pop();let csrf='',revision=0,payload,refreshTimer;
  async function api(path,options={}){
    const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}});
    const d=await r.json();if(!r.ok){const e=new Error(typeof d.detail==='string'?d.detail:'Request failed.');e.status=r.status;throw e;}return d;
  }
  async function refresh(){
    payload=await api(`/api/projects/${pid}/practice`);
    window.PRACTICE_MEDIA=payload.mediaUrls;window.PRACTICE_SPEED_MEDIA=payload.speedMedia||{};
    window.refreshRoomMedia();
    clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh().catch(showError),Math.max(30000,(payload.urlExpires*1000-Date.now()-90000)));
  }
  function showError(e){const p=document.getElementById('error');p.hidden=false;p.textContent=e.message;}
  try{
    const config=await api('/api/bootstrap');csrf=config.csrf||'';
    payload=await api(`/api/projects/${pid}/practice`);revision=payload.revision;
    window.PRACTICE_DATA=payload.analysis;window.PRACTICE_SAVED=payload.alignment;
    const localKey=`choreo-alignment:${pid}:${payload.analysis.sourceSha256}`;
    if(!csrf){
      const back=document.getElementById('back-link');back.textContent='＋ Upload';back.setAttribute('aria-label','Upload your own video');
      try{const saved=JSON.parse(localStorage.getItem(localKey));if(window.PracticeCore.validateSettings(saved,payload.analysis))window.PRACTICE_SAVED=saved;}catch{}
    }
    function localSave(value){
      const saved={...value,schema:3,sourceSha256:payload.analysis.sourceSha256,gridId:payload.analysis.grid.id,timingId:payload.analysis.attack.id};
      try{localStorage.setItem(localKey,JSON.stringify(saved));return {scope:'device'};}catch{return {scope:'visit'};}
    }
    window.PRACTICE_STYLE=await api('/static/guide-style.json');window.PRACTICE_MEDIA=payload.mediaUrls;window.PRACTICE_SPEED_MEDIA=payload.speedMedia||{};
    for(const option of document.getElementById('listen').options)option.disabled=!payload.mediaUrls[option.value];
    document.getElementById('video').src=payload.videoUrl;document.getElementById('video').poster=payload.posterUrl;
    document.getElementById('room-video-name').textContent=payload.name;
    document.getElementById('expiry').textContent='Available until '+new Date(payload.expires*1000).toLocaleString();
    let saves=Promise.resolve();
    window.saveRoomAlignment=value=>{const task=saves.catch(()=>{}).then(async()=>{
      if(!csrf)return localSave(value);
      try{const r=await api(`/api/projects/${pid}/alignment`,{method:'PUT',body:JSON.stringify({revision,...value})});revision=r.revision;return {scope:'shared'};}
      catch(e){if(e.status!==401)throw e;csrf='';return localSave(value);}
    });saves=task;return task;};
    const share=document.getElementById('share');share.disabled=false;
    const shareUrl=location.origin+'/practice/'+pid;document.getElementById('share-url').value=shareUrl;
    share.addEventListener('click',async()=>{
      try{
        if(navigator.share){await navigator.share({title:'Practice with counts',url:shareUrl});return;}
        await navigator.clipboard.writeText(shareUrl);share.textContent='Copied';setTimeout(()=>share.textContent='Share',2200);
      }catch(e){if(e.name==='AbortError')return;document.getElementById('share-fallback').hidden=false;document.getElementById('tools').showModal();document.getElementById('share-url').select();}
    });
    const app=document.createElement('script');app.src='/static/app.js?v=5';document.head.append(app);
    refreshTimer=setTimeout(()=>refresh().catch(showError),Math.max(30000,payload.urlExpires*1000-Date.now()-90000));
    for(const button of document.querySelectorAll('[data-export]'))button.addEventListener('click',async()=>{
      button.disabled=true;const status=document.getElementById('export-status');status.textContent='Saving alignment and preparing your count video…';
      try{
        await window.saveRoomAlignment(window.currentAlignment);
        let job=await api(`/api/projects/${pid}/exports`,{method:'POST',body:JSON.stringify({revision,speed:Number(button.dataset.export),settings:window.currentAlignment})});
        while(['queued','processing'].includes(job.status)){await new Promise(r=>setTimeout(r,4000));job=await api(`/api/projects/${pid}/exports/${job.id}`);status.textContent=job.status==='queued'?'Export queued. You can keep practicing.':'Rendering your count video…';}
        if(job.status!=='ready')throw new Error(job.error||'Export failed.');
        if(!job.url)job=await api(`/api/projects/${pid}/exports/${job.id}`);
        status.replaceChildren();const a=document.createElement('a');a.href=job.url;a.textContent='Download count video';a.rel='noreferrer';status.append(a);
      }catch(e){status.textContent=e.message;}finally{button.disabled=false;}
    });
  }catch(e){showError(e);}
})();
