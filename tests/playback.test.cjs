const test=require('node:test'),assert=require('node:assert/strict');
const {attach}=require('../choreo/static/playback.js');
const {sourceFor,toSourceTime,toMediaTime}=require('../choreo/static/playback.js');
class Media extends EventTarget{playbackRate=1;defaultPlaybackRate=1;preservesPitch=false;}
test('all practice speeds survive metadata, playback and source replacement',()=>{
  const media=new Media(),observed=[],controller=attach(media,{initial:1,onChange:(want,actual)=>observed.push([want,actual])});
  for(const rate of [.25,.5,.75,1]){
    controller.set(rate);
    assert.equal(media.playbackRate,rate);assert.equal(media.defaultPlaybackRate,rate);assert.equal(media.preservesPitch,true);
    for(const event of ['loadedmetadata','canplay','play','ratechange']){
      media.playbackRate=1;media.defaultPlaybackRate=1;media.dispatchEvent(new Event(event));
      assert.equal(media.playbackRate,rate,`restore ${rate} after ${event}`);
      assert.equal(media.defaultPlaybackRate,rate);
    }
  }
  controller.set(0);assert.equal(controller.speed,1);
  assert.ok(observed.every(([requested,actual])=>requested===actual));
});
test('a rate selected before the video loads is retained',()=>{
  const media=new Media(),controller=attach(media);controller.set(.75);
  media.playbackRate=1;media.dispatchEvent(new Event('loadedmetadata'));
  assert.equal(media.playbackRate,.75);
});
test('prepared slow media plays at native speed and retains the original count clock',()=>{
  const media=new Media(),controller=attach(media,{initial:1});
  const urls={mix:'original',bass:'bass'},slow={'.25':'unused','0.25':'quarter','0.5':'half','0.75':'three-quarter'};
  for(const rate of [.25,.5,.75,1,.25,1]){
    controller.set(rate);
    const source=sourceFor(urls,slow,'mix',rate);
    controller.setSourceRate(source.sourceRate);
    assert.equal(media.playbackRate,1);
    assert.equal(media.defaultPlaybackRate,1);
    const originalTime=4.25;
    assert.equal(toSourceTime(toMediaTime(originalTime,source.sourceRate),source.sourceRate),originalTime);
    media.playbackRate=rate;media.dispatchEvent(new Event('loadedmetadata'));
    assert.equal(media.playbackRate,1,'never apply slow motion twice');
  }
  controller.set(.25);
  const solo=sourceFor(urls,slow,'bass',.25);
  controller.setSourceRate(solo.sourceRate);
  assert.equal(solo.url,'bass');assert.equal(media.playbackRate,.25);
  const legacy=sourceFor(urls,{},'mix',.25);
  assert.equal(legacy.url,'original');assert.equal(legacy.sourceRate,1);
});
