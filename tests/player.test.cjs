const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../choreo/static/core.js');
const events = Array.from({length:32},(_,i)=>({index:i-1,time:.2+i*.5+(i%2?-.01:.01),source:'bass'}));
const data = {sourceSha256:'source',duration:16.5,defaultAnchor:0,grid:{id:'grid',origin:.71,period:.5},
  beats:Array.from({length:32},(_,i)=>.71+i*.5),attack:{id:'attack',events,beats:events.map(e=>e.time)}};
test('attack timing keeps every count in order, including pickup beats',()=>{
  assert.equal(C.eventAt(data,0,0).count,null);
  for(let i=0;i<32;i++)assert.equal(C.eventAt(data,events[i].time,0).count,((events[i].index%8)+8)%8+1);
  assert.equal(C.eventAt(data,events[9].time,0).count,1);
});
test('manual offset shifts time without changing beat identity',()=>{
  assert.equal(C.eventAt(data,events[3].time+.1,0,.1).index,2);
  assert.equal(C.nearestIndex(data,events[0].time,0,'attack'),-1);
  assert.equal(C.anchorTime(data,-1,'attack'),events[0].time);
});
test('eight-count loops use real attack boundaries',()=>{
  const loop=C.eventLoop(data,events[4].time,0,0,data.duration,8,'attack');
  assert.deepEqual(loop,[events[1].time,events[9].time]);
});
test('saved alignment accepts a pickup beat but rejects another video or grid',()=>{
  const settings={sourceSha256:'source',gridId:'grid',anchor:-1,offset:.13,reviewed:true};
  assert.deepEqual(C.validateSettings(settings,data),{anchor:-1,offset:.13,reviewed:true});
  assert.equal(C.validateSettings({...settings,sourceSha256:'other'},data),null);
  assert.equal(C.validateSettings({...settings,gridId:'other'},data),null);
});
test('frame stepping matches the normalized 30 fps video',()=>{
  assert.ok(Math.abs(C.frameTarget(1,1,30)-31/30)<1e-10);
  assert.equal(C.frameTarget(0,-1,30),0);
});
