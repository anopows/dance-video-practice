const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=name=>fs.readFileSync(path.join(__dirname,'../choreo/static',name),'utf8');
test('signed-in visitors save locally and export without changing owner alignment',async()=>{
 const requests=[],saved=new Map(),elements=new Map();const element=id=>{if(!elements.has(id))elements.set(id,{hidden:true,textContent:''});return elements.get(id);};
 const payload={owner:false,revision:0,analysis:{sourceSha256:'sha',grid:{id:'grid'},attack:{id:'attack'}},alignment:{anchor:0},mediaUrls:{},urlExpires:Date.now()/1000+1000};
 const context={location:{pathname:'/practice/'+'a'.repeat(32),origin:'https://test'},localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)},document:{getElementById:element,createElement:()=>({}),head:{append(){}}},setTimeout(){return 1;},clearTimeout(){},console,
 fetch:async(url,options={})=>{requests.push({url,options});return {ok:true,json:async()=>url==='/api/bootstrap'?{csrf:'visitor-session'}:url.endsWith('/practice')?payload:{id:'export',status:'ready',url:'download'}};}};
 context.window={PracticeCore:{validateSettings:()=>true}};vm.createContext(context);await vm.runInContext(source('practice-loader.js'),context);
 assert.equal(context.window.ROOM.owner,false);await context.window.ROOM.save({anchor:2});assert.equal(saved.size,1);
 await context.window.ROOM.exportVideo(1,{anchor:2});assert.equal(requests.filter(r=>r.options.method==='PUT').length,0);
 assert.equal(requests.filter(r=>r.url.endsWith('/exports')).length,1);
});

test('sign-in buttons require consent, email and a fresh human-check token',async()=>{
 const elements=new Map(),callbacks={};function element(id){if(!elements.has(id))elements.set(id,{value:'',checked:false,disabled:false,hidden:true,parentElement:{},children:[],classList:{add(){},remove(){}},focus(){},addEventListener(event,fn){callbacks[id+':'+event]=fn;},querySelector(){return {};}});return elements.get(id);}
 const cfg={siteKey:'test',maxBytes:500000000,maxDuration:600,retentionDays:90,sessionSeconds:2592000,legalReady:true,signupsOpen:true,emailSignIn:true,googleSignIn:true,termsVersion:1,user:null};let check;
 const context={document:{getElementById:element,querySelectorAll:()=>[],querySelector:()=>element('add-label')},URLSearchParams,location:{search:''},setTimeout,clearTimeout,fetch:async()=>({ok:true,json:async()=>cfg}),console};
 context.window={turnstile:{render:(selector,opts)=>{check=opts;return 1;},reset(){}}};context.turnstile=context.window.turnstile;vm.createContext(context);vm.runInContext(source('room.js'),context);await new Promise(r=>setImmediate(r));
 assert.equal(element('login-submit').disabled,true);assert.equal(element('google-signin').disabled,true);
 callbacks['start:click']();await new Promise(r=>setImmediate(r));assert.equal(check.action,'signin');
 element('email').value='dancer@example.com';callbacks['email:input']();check.callback('token');assert.equal(element('login-submit').disabled,true);
 element('consent').checked=true;callbacks['consent:change']();assert.equal(element('login-submit').disabled,false);assert.equal(element('google-signin').disabled,false);
 check['expired-callback']();assert.equal(element('login-submit').disabled,true);element('consent').checked=false;callbacks['consent:change']();assert.equal(element('google-signin').disabled,true);
});
