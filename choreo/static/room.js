(() => {
  'use strict';
  const $=id=>document.getElementById(id),wait=ms=>new Promise(r=>setTimeout(r,ms));
  let cfg,csrf='',file=null,projectId=null,busy=false,cancelled=false,loginWidget,loginToken='',pollTimer,libTimer,resuming=null,cardProject=null;
  const STEPS=['upload','beat','one','slowmo'];
  const STEP_NAME={upload:'Uploading',beat:'Hearing the beat',one:'Finding the 1',slowmo:'Preparing slow-mo'};
  const ACTIVE=['creating','uploading','completing','queued','processing'];

  // ---------- helpers ----------
  function waitText(seconds){const m=Math.ceil(seconds/60);return m<60?`${m} minute${m===1?'':'s'}`:`${Math.ceil(m/60)} hours`;}
  function message(text){$('message').textContent=text;$('message').hidden=!text;}
  function explain(e){return e.retry&&!/try again|wait for/i.test(e.message)?`${e.message} Try again in ${waitText(e.retry)}.`:e.message;}
  let toastTimer;
  function toast(text){const t=$('toast');t.textContent=text;t.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.hidden=true,2400);}
  function daysLeft(e){const d=Math.ceil((e*1000-Date.now())/86400000);return d<=1?(d<=0?'Removed today':'Removed tomorrow'):`${d} days left`;}
  const fmtDur=s=>{s=Math.max(0,Math.round(s));return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;};
  const mb=b=>`${Math.max(1,Math.round(b/1e6))} MB`;
  async function api(path,opts={}){
    const r=await fetch(path,{...opts,headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{}),...opts.headers}});
    let data={};try{data=await r.json();}catch{}
    if(!r.ok){const e=new Error(typeof data.detail==='string'?data.detail:'Something went wrong. Try again.');e.status=r.status;e.retry=Number(r.headers.get('Retry-After')||0);throw e;}
    return data;
  }
  function show(view){
    for(const id of ['welcome','unlock','library','progress'])$(id).hidden=id!==view;
    $('signout').hidden=!csrf;
    document.title=view==='library'||view==='progress'?'Your videos · five·six':'five·six';
    if(view!=='library')clearTimeout(libTimer);
  }
  function stepFor(p){
    if(['creating','uploading','completing'].includes(p.status))return ['upload',''];
    if(p.status==='queued')return ['beat','Waiting to start'];
    const s=p.stage||'';
    if(s==='check')return ['upload','Checking the video'];
    if(STEPS.includes(s))return [s,s==='one'?'Working out where each eight starts':''];
    // Stage text from older workers.
    if(/beat/i.test(s))return ['beat',''];
    if(/eight|instrument|attack|align/i.test(s))return ['one','Working out where each eight starts'];
    if(/slow|saving/i.test(s))return ['slowmo',''];
    return ['upload','Checking the video'];
  }

  // ---------- sheets ----------
  for(const d of document.querySelectorAll('dialog.sheet')){
    d.addEventListener('click',e=>{if(e.target!==d)return;const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();});
    for(const b of d.querySelectorAll('[data-close]'))b.addEventListener('click',()=>d.close());
  }
  function open(id){for(const d of document.querySelectorAll('dialog[open]'))d.close();$(id).showModal();}

  // ---------- unlock ----------
  async function widget(){
    if(loginWidget!==undefined)return;
    for(let i=0;i<100&&!window.turnstile;i++)await wait(100);
    if(!window.turnstile){message("The security check didn't load. Allow challenges.cloudflare.com in your blocker, then reload.");return;}
    loginWidget=turnstile.render('#login-check',{sitekey:cfg.siteKey,action:'login',theme:'dark',
      callback:token=>{loginToken=token;$('login-submit').disabled=false;},
      'expired-callback':()=>{loginToken='';$('login-submit').disabled=true;},
      'error-callback':()=>message("The security check didn't finish. Reload the page and try again.")});
  }
  function resetCheck(){if(window.turnstile&&loginWidget!==undefined){loginToken='';turnstile.reset(loginWidget);$('login-submit').disabled=true;}}
  function showUnlock(){message('');show('unlock');widget();$('password').focus();}
  $('start').addEventListener('click',showUnlock);
  $('unlock-back').addEventListener('click',()=>{message('');show('welcome');});
  $('login-form').addEventListener('submit',async e=>{
    e.preventDefault();message('');$('login-submit').disabled=true;
    try{const r=await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value,token:loginToken})});csrf=r.csrf;$('password').value='';await showRoom();}
    catch(error){message(explain(error));resetCheck();}
  });
  $('signout').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});location.assign('/');}catch(e){message(e.message);}});

  // ---------- library ----------
  async function showRoom(){
    const ret=new URLSearchParams(location.search).get('return');
    if(/^\/practice\/[a-f0-9]{32}$/.test(ret||'')){location.assign(ret);return;}
    show('library');await library();
    const active=sessionStorage.getItem('choreo-active');
    if(active)watch(active);
  }
  function card(p){
    const row=document.createElement('article');row.className='card';
    const openBtn=document.createElement('button');openBtn.className='open';
    const thumb=document.createElement('span');thumb.className='thumb';
    if(p.posterUrl){const img=document.createElement('img');img.src=p.posterUrl;img.alt='';img.loading='lazy';thumb.append(img);}
    if(p.duration){const em=document.createElement('em');em.textContent=fmtDur(p.duration);thumb.append(em);}
    const t=document.createElement('span');t.className='t';
    const title=document.createElement('b');title.textContent=p.name;t.append(title);
    const chip=(cls,text)=>{const s=document.createElement('span');s.className='st '+cls;s.textContent=text;t.append(s);};
    if(p.status==='ready'){
      const small=document.createElement('small');small.textContent=daysLeft(p.expires);t.append(small);
      openBtn.addEventListener('click',()=>location.assign('/practice/'+p.id));openBtn.setAttribute('aria-label',`Open ${p.name}`);
    }else if(p.status==='uploading'&&!(busy&&projectId===p.id)){
      chip('paused','Upload paused · Resume');openBtn.addEventListener('click',()=>watch(p.id));
    }else if(ACTIVE.includes(p.status)){
      const [step]=stepFor(p);chip('work',`${STEP_NAME[step]} · ${STEPS.indexOf(step)+1} of 4`);openBtn.addEventListener('click',()=>watch(p.id));
    }else{
      chip('fail',"Couldn't process");
      openBtn.addEventListener('click',()=>{cardProject=p;$('error-title').textContent=`Couldn't process “${p.name}”`;$('error-text').textContent=p.error||'Something went wrong with this video. Try again, or ask the person who runs this site.';open('error-sheet');});
    }
    openBtn.append(thumb,t);row.append(openBtn);
    const more=document.createElement('button');more.className='ib';more.setAttribute('aria-label',`More for ${p.name}`);
    more.innerHTML='<svg class="i"><use href="#i-more"/></svg>';
    more.addEventListener('click',()=>{cardProject=p;$('card-title').textContent=p.name;$('card-share').hidden=p.status!=='ready';open('card-sheet');});
    row.append(more);
    return row;
  }
  async function library(){
    clearTimeout(libTimer);
    let data;
    try{data=await api('/api/projects');}catch(e){if(e.status===401){csrf='';show('welcome');return;}message(e.message);return;}
    const list=$('projects');list.replaceChildren();
    if(!data.projects.length){const p=document.createElement('p');p.className='empty';p.textContent='No videos yet. Add a practice clip to get started.';list.append(p);}
    for(const p of data.projects)list.append(card(p));
    if(data.projects.some(p=>ACTIVE.includes(p.status))&&!$('library').hidden)libTimer=setTimeout(library,4000);
  }
  $('card-share').addEventListener('click',async()=>{
    const url=location.origin+'/practice/'+cardProject.id;$('card-sheet').close();
    try{await navigator.clipboard.writeText(url);toast('Link copied');}catch{toast("Couldn't copy. Open the video and tap Share.");}
  });
  $('card-rename').addEventListener('click',()=>{open('rename-sheet');$('rename-input').value=cardProject.name;$('rename-input').select();});
  $('rename-form').addEventListener('submit',async e=>{
    e.preventDefault();$('rename-error').hidden=true;
    try{await api('/api/projects/'+cardProject.id,{method:'PATCH',body:JSON.stringify({name:$('rename-input').value})});$('rename-sheet').close();toast('Renamed');library();}
    catch(err){$('rename-error').hidden=false;$('rename-error').textContent=err.message;}
  });
  $('card-delete').addEventListener('click',()=>{$('delete-title').textContent=`Delete “${cardProject.name}”?`;$('delete-error').hidden=true;open('delete-sheet');});
  async function remove(sheet,errorEl){
    try{await api('/api/projects/'+cardProject.id,{method:'DELETE'});$(sheet).close();toast('Deleted');library();}
    catch(err){if(errorEl){$(errorEl).hidden=false;$(errorEl).textContent=err.message;}else message(err.message);}
  }
  $('confirm-delete').addEventListener('click',()=>remove('delete-sheet','delete-error'));
  $('error-delete').addEventListener('click',()=>remove('error-sheet'));

  // ---------- choosing a file ----------
  async function choose(f){
    message('');if(!f||busy)return;
    if(resuming){
      if(f.size!==resuming.bytes){message(`That's a different file. Choose “${resuming.name}” to continue.`);return;}
      file=f;await sendParts();return;
    }
    if(!/\.(mp4|mov|m4v|webm)$/i.test(f.name)){message("This file type isn't supported. Use MP4, MOV, M4V or WebM.");return;}
    if(!f.size||f.size>cfg.maxBytes){message(`This file is over ${Math.round(cfg.maxBytes/1e6)} MB. Trim it or export at a lower quality.`);return;}
    const bytes=new Uint8Array(await f.slice(0,16).arrayBuffer());
    const ftyp=String.fromCharCode(...bytes.slice(4,8))==='ftyp',ebml=[0x1a,0x45,0xdf,0xa3].every((b,i)=>bytes[i]===b);
    if(!ftyp&&!ebml){message("This doesn't look like a video file.");return;}
    // Convenience checks only. The server validates and decodes the whole video.
    file=f;await begin();
  }
  $('file').addEventListener('change',()=>{const f=$('file').files[0];$('file').value='';choose(f);});
  $('dropzone').addEventListener('dragover',e=>{e.preventDefault();$('dropzone').classList.add('dragging');});
  $('dropzone').addEventListener('dragleave',()=>$('dropzone').classList.remove('dragging'));
  $('dropzone').addEventListener('drop',e=>{e.preventDefault();$('dropzone').classList.remove('dragging');choose(e.dataTransfer.files[0]);});

  // ---------- upload & progress ----------
  function steps(step,sub=''){
    const at=STEPS.indexOf(step);
    for(const li of $('stepper').children){
      const i=STEPS.indexOf(li.dataset.step);li.className=i<at?'done':i===at?'now':'';
      li.querySelector('small').textContent=i===at?sub:'';
    }
  }
  function progressView(name,meta){show('progress');$('progress-name').textContent=name;$('progress-meta').textContent=meta;$('resume').hidden=true;$('cancel').hidden=false;}
  function paused(p){
    resuming={id:p.id,name:p.name,bytes:p.bytes};projectId=p.id;
    progressView(p.name,mb(p.bytes));steps('upload','Paused');
    $('progress-note').textContent='Upload paused. Choose the same file again to continue where you left off.';$('resume').hidden=false;
  }
  async function begin(){
    busy=true;message('');
    try{
      const request_id=crypto.randomUUID().replaceAll('-','');
      const result=await api('/api/uploads',{method:'POST',body:JSON.stringify({name:file.name,bytes:file.size,request_id})});
      projectId=result.id;sessionStorage.setItem('choreo-active',projectId);
      progressView(file.name.replace(/\.[^.]+$/,''),mb(file.size));steps('upload','0%');
      busy=false;await sendParts();
    }catch(e){busy=false;message(explain(e));}
  }
  async function sendParts(){
    busy=true;cancelled=false;$('resume').hidden=true;
    try{
      const status=await api('/api/projects/'+projectId);
      progressView(status.name,mb(status.bytes));
      if(status.status!=='uploading'){busy=false;resuming=null;await watch(projectId);return;}
      if(!file||file.size!==status.bytes)throw new Error(`Choose “${status.name}” again to continue.`);
      $('progress-note').textContent='Keep this tab open until the upload finishes.';
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
        steps('upload',`${Math.round(i/total*100)}%`);
      }
      if(cancelled)return;
      await api(`/api/uploads/${projectId}/complete`,{method:'POST',body:'{}'});
      resuming=null;busy=false;await watch(projectId);
    }catch(e){
      busy=false;message(e.status===409?e.message:'The upload was interrupted. Choose the same file to continue.');
      try{const p=await api('/api/projects/'+projectId);if(p.status==='uploading')paused(p);}catch{}
    }finally{busy=false;}
  }
  async function watch(id){
    clearTimeout(pollTimer);projectId=id;
    try{
      const p=await api('/api/projects/'+id);
      if(p.status==='ready'){sessionStorage.removeItem('choreo-active');location.assign('/practice/'+id);return;}
      if(p.status==='failed'){sessionStorage.removeItem('choreo-active');show('library');message(p.error||'Something went wrong with this video. Try again, or ask the person who runs this site.');library();return;}
      if(p.status==='uploading'){if(!busy)paused(p);return;}
      if($('progress').hidden||$('progress-name').textContent!==p.name)progressView(p.name,mb(p.bytes));
      const [step,sub]=stepFor(p);steps(step,sub);
      $('progress-note').textContent='You can close this tab. The video will be waiting in your library.';
      pollTimer=setTimeout(()=>watch(id),2500);
    }catch(e){
      if(e.status===404){sessionStorage.removeItem('choreo-active');show('library');library();return;}
      message(e.message);pollTimer=setTimeout(()=>watch(id),10000);
    }
  }
  $('cancel').addEventListener('click',async()=>{
    const wasUploading=busy||!!resuming;cancelled=true;clearTimeout(pollTimer);
    try{
      await api('/api/projects/'+projectId,{method:'DELETE'});sessionStorage.removeItem('choreo-active');
      resuming=null;file=null;busy=false;show('library');
      toast(wasUploading?"Upload cancelled. It still counts toward today's upload limit.":'Cancelled');library();
    }catch(e){message(e.message);}
  });
  $('progress-back').addEventListener('click',()=>{clearTimeout(pollTimer);if(!busy)resuming=null;show('library');library();});

  // ---------- start ----------
  (async()=>{
    try{
      cfg=await api('/api/bootstrap');csrf=cfg.csrf||'';
      $('limits').textContent=`MP4, MOV or WebM · up to ${Math.round(cfg.maxBytes/1e6)} MB and ${Math.round(cfg.maxDuration/60)} min · kept ${cfg.retentionDays} days`;
      const hours=Math.round((cfg.sessionSeconds||28800)/3600);
      $('unlock-note').textContent=`Stays unlocked on this device for ${hours>=48?Math.round(hours/24)+' days':hours+' hours'}.`;
      if(csrf)await showRoom();
      else if(new URLSearchParams(location.search).get('return'))showUnlock();
      else show('welcome');
    }catch(e){show('welcome');message("Can't reach five·six right now. Check your connection and reload.");}
  })();
})();
