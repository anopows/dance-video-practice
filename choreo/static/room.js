(() => {
  'use strict';
  const $=id=>document.getElementById(id), wait=ms=>new Promise(r=>setTimeout(r,ms));
  let cfg,csrf='',file=null,projectId=null,busy=false,cancelled=false,loginWidget,uploadWidget,pollTimer;
  let loginToken='',uploadToken='';
  const message=t=>{$('message').textContent=t;$('message').hidden=!t;};
  async function api(path,opts={}){
    const r=await fetch(path,{...opts,headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{}),...opts.headers}});
    const data=await r.json();
    if(!r.ok){const e=new Error(typeof data.detail==='string'?data.detail:'Please check your input and try again.');e.status=r.status;e.retry=Number(r.headers.get('Retry-After')||0);throw e;}
    return data;
  }
  function resetCheck(kind){if(!window.turnstile)return;if(kind==='login'){loginToken='';turnstile.reset(loginWidget);$('login-submit').disabled=true;}else{uploadToken='';turnstile.reset(uploadWidget);$('upload').disabled=true;}}
  async function widget(kind){
    for(let i=0;i<100&&!window.turnstile;i++)await wait(100);
    if(!window.turnstile){message('The human check could not load. Allow challenges.cloudflare.com and reload.');return;}
    const id=turnstile.render('#'+kind+'-check',{sitekey:cfg.siteKey,action:kind,theme:'dark',
      callback:token=>{if(kind==='login'){loginToken=token;$('login-submit').disabled=false;}else{uploadToken=token;$('upload').disabled=!file||busy;}},
      'expired-callback':()=>{if(kind==='login'){loginToken='';$('login-submit').disabled=true;}else{uploadToken='';$('upload').disabled=true;}},
      'error-callback':()=>{message('The human check failed to load. Please reload and try again.');}});
    if(kind==='login')loginWidget=id;else uploadWidget=id;
  }
  async function showRoom(){
    $('unlock').hidden=true;$('upload-panel').hidden=false;$('logout').hidden=false;$('library').hidden=false;
    const ret=new URLSearchParams(location.search).get('return');
    if(/^\/practice\/[a-f0-9]{32}$/.test(ret||'')){location.assign(ret);return;}
    await library();
  }
  $('login-form').addEventListener('submit',async e=>{
    e.preventDefault();message('');$('login-submit').disabled=true;
    try{const r=await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value,token:loginToken})});csrf=r.csrf;$('password').value='';await showRoom();}
    catch(error){message(error.message+(error.retry?` Try again in ${Math.ceil(error.retry/60)} minutes.`:''));resetCheck('login');}
  });
  $('logout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});location.assign('/');}catch(e){message(e.message);}});
  async function choose(f){
    if(busy)return;file=null;$('upload').disabled=true;message('');$('file-info').textContent='';
    $('upload-actions').hidden=true;if(!f)return;
    if(!/\.(mp4|mov|m4v|webm)$/i.test(f.name)||!f.size||f.size>cfg.maxBytes){message('Choose an MP4, MOV, M4V or WebM up to 500 MB.');return;}
    const bytes=new Uint8Array(await f.slice(0,16).arrayBuffer());
    const ftyp=String.fromCharCode(...bytes.slice(4,8))==='ftyp';
    const ebml=[0x1a,0x45,0xdf,0xa3].every((b,i)=>bytes[i]===b);
    if(!ftyp&&!ebml){message('The file contents do not look like a supported video.');return;}
    // These checks are convenience only. The server independently validates
    // actual tracks and decodes the entire video in its isolated worker.
    file=f;$('upload-actions').hidden=false;$('file-info').textContent=`${f.name} · ${(f.size/1e6).toFixed(1)} MB`;
    $('upload').disabled=!uploadToken;
    if(uploadWidget===undefined)await widget('upload');
  }
  $('file').addEventListener('change',()=>choose($('file').files[0]));
  $('dropzone').addEventListener('dragover',e=>{e.preventDefault();$('dropzone').classList.add('dragging');});
  $('dropzone').addEventListener('dragleave',()=>$('dropzone').classList.remove('dragging'));
  $('dropzone').addEventListener('drop',e=>{e.preventDefault();$('dropzone').classList.remove('dragging');choose(e.dataTransfer.files[0]);});
  async function sendParts(){
    busy=true;cancelled=false;$('resume').hidden=true;$('upload').disabled=true;$('file').disabled=true;
    try{
      const status=await api('/api/projects/'+projectId);
      if(status.status!=='uploading'){await watch(projectId);return;}
      if(!file||file.size!==status.bytes)throw new Error('Select the same video again before resuming.');
      const total=Math.ceil(file.size/cfg.partBytes),done=new Set(status.parts);
      for(let i=1;i<=total;i++){
        if(cancelled)return;
        if(!done.has(i)){
          const part=file.slice((i-1)*cfg.partBytes,Math.min(i*cfg.partBytes,file.size));
          for(let attempt=0;;attempt++){
            try{await api(`/api/uploads/${projectId}/parts/${i}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:part});break;}
            catch(e){if(attempt>=2||[400,401,403,409,413].includes(e.status))throw e;await wait(Math.max(e.retry*1000,1000*(attempt+1)));if(cancelled)return;}
          }
        }
        $('progress').value=i/total*100;$('progress-detail').textContent=`${Math.round(i/total*100)}% uploaded · keep this tab open until the upload finishes`;
      }
      if(cancelled)return;
      await api(`/api/uploads/${projectId}/complete`,{method:'POST',body:'{}'});
      await watch(projectId);
    }catch(e){message(e.message);$('resume').hidden=false;$('progress-title').textContent='Upload paused';}
    finally{busy=false;$('file').disabled=false;}
  }
  $('upload').addEventListener('click',async()=>{
    if(!file||!uploadToken||busy)return;
    $('upload-actions').hidden=true;
    message('');busy=true;$('upload').disabled=true;
    try{
      const request_id=crypto.randomUUID().replaceAll('-','');
      const result=await api('/api/uploads',{method:'POST',body:JSON.stringify({name:file.name,bytes:file.size,token:uploadToken,request_id})});
      projectId=result.id;sessionStorage.setItem('choreo-active',projectId);
      $('progress-panel').hidden=false;$('progress-title').textContent='Uploading your video';$('progress').value=0;$('cancel').hidden=false;
      resetCheck('upload');await sendParts();
    }catch(e){busy=false;$('upload-actions').hidden=false;message(e.message);resetCheck('upload');}
  });
  $('resume').addEventListener('click',sendParts);
  $('cancel').addEventListener('click',async()=>{
    cancelled=true;clearTimeout(pollTimer);
    try{await api('/api/projects/'+projectId,{method:'DELETE'});sessionStorage.removeItem('choreo-active');$('progress-panel').hidden=true;message('Video cancelled. Its upload reservation still counts toward the room limits.');await library();}
    catch(e){message(e.message);}
    busy=false;$('file').disabled=false;$('upload').disabled=!file||!uploadToken;
  });
  async function watch(id){
    clearTimeout(pollTimer);projectId=id;$('progress-panel').hidden=false;$('progress').removeAttribute('value');$('cancel').hidden=false;
    try{
      const p=await api('/api/projects/'+id);
      if(p.status==='ready'){sessionStorage.removeItem('choreo-active');location.assign('/practice/'+id);return;}
      if(p.status==='failed'){sessionStorage.removeItem('choreo-active');$('progress-panel').hidden=true;message(p.error||'This video could not be processed.');await library();return;}
      if(p.status==='uploading'){
        $('progress-title').textContent='An upload is waiting';$('progress-detail').textContent='Reselect the same video to resume it, or cancel and start again.';$('resume').hidden=false;return;
      }
      $('progress-title').textContent=p.stage||'Waiting for processing';$('progress-detail').textContent='You can leave this page now. Your video will appear here when ready.';
      pollTimer=setTimeout(()=>watch(id),2500);
    }catch(e){message(e.message);pollTimer=setTimeout(()=>watch(id),10000);}
  }
  async function library(){
    const data=await api('/api/projects');$('projects').replaceChildren();
    if(!data.projects.length){const p=document.createElement('p');p.className='muted';p.textContent='Your first video will appear here.';$('projects').append(p);}
    for(const p of data.projects){
      const row=document.createElement('article');row.className='project-row';const info=document.createElement('div'),title=document.createElement('strong'),detail=document.createElement('p');title.textContent=p.name;detail.className='small muted';detail.textContent=p.status==='ready'?`Ready · expires ${new Date(p.expires*1000).toLocaleString()}`:(p.error||p.status);info.append(title,detail);row.append(info);
      if(p.status==='ready'){const a=document.createElement('a');a.href='/practice/'+p.id;a.textContent='Practice →';row.append(a);}
      else if(['uploading','creating','queued','processing'].includes(p.status)){const b=document.createElement('button');b.textContent=p.status==='uploading'?'Resume':'View progress';b.addEventListener('click',()=>watch(p.id));row.append(b);}
      const remove=document.createElement('button');remove.textContent='Delete';remove.className='delete';remove.addEventListener('click',async()=>{if(confirm('Delete this video and its exports?')){try{await api('/api/projects/'+p.id,{method:'DELETE'});await library();}catch(e){message(e.message);}}});row.append(remove);$('projects').append(row);
    }
  }
  (async()=>{try{cfg=await api('/api/bootstrap');$('retention-note').textContent=`Up to 500 MB / 10 min · Kept for ${cfg.retentionDays} days.`;csrf=cfg.csrf||'';if(csrf)await showRoom();else await widget('login');}catch(e){message('Could not reach the room: '+e.message);}})();
})();
