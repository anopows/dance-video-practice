(function(root){
  'use strict';
  const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
  function upperBound(values,t){let a=0,b=values.length;while(a<b){const m=(a+b)>>1;if(values[m]<=t)a=m+1;else b=m;}return a;}
  // Media seeks can return a timestamp one microsecond before the requested
  // value. Tolerate that quantization without snapping visibly early frames.
  const beatIndex=(beats,t,offset=0)=>upperBound(beats,t-offset+1e-5)-1;
  const gridBeatIndex=(grid,t,offset=0)=>Math.floor((t-offset-grid.origin+1e-5)/grid.period);
  const gridBeatTime=(grid,index)=>grid.origin+index*grid.period;
  function eventTimes(data,mode='attack'){return mode==='attack'&&data.attack?data.attack.beats:data.beats;}
  function eventAt(data,t,anchor,offset=0,mode='attack'){
    if(mode!=='attack'||!data.attack){
      const index=gridBeatIndex(data.grid,t,offset),time=gridBeatTime(data.grid,index)+offset;
      return {index,ordinal:index,count:countAt(index,anchor),time,next:time+data.grid.period,source:'pulse'};
    }
    const ordinal=beatIndex(data.attack.beats,t,offset),e=data.attack.events[ordinal];
    if(!e)return {index:null,ordinal:-1,count:null,time:0,next:data.attack.beats[0]+offset,source:null};
    return {...e,ordinal,count:((e.index-anchor)%8+8)%8+1,time:e.time+offset,
            next:(data.attack.beats[ordinal+1]??data.duration)+offset};
  }
  function anchorTime(data,anchor,mode='attack'){
    return mode==='attack'&&data.attack?(data.attack.events.find(e=>e.index===anchor)?.time??gridBeatTime(data.grid,anchor)):gridBeatTime(data.grid,anchor);
  }
  function nearestIndex(data,time,offset=0,mode='attack'){
    const i=nearestBeat(eventTimes(data,mode),time,offset);
    return mode==='attack'&&data.attack?data.attack.events[i].index:i;
  }
  function stepTime(data,time,direction,offset=0,mode='attack'){
    const beats=eventTimes(data,mode),i=beatIndex(beats,time,offset);
    const target=direction>0?i+1:(i>=0&&Math.abs(time-beats[i]-offset)<.025?i-1:i);
    return beats[clamp(target,0,beats.length-1)]+offset;
  }
  function eventLoop(data,time,anchor,offset,duration,size=8,mode='attack'){
    const p=eventAt(data,time,anchor,offset,mode),index=p.index??0;
    const first=anchor+Math.floor((index-anchor)/size)*size;
    const start=anchorTime(data,first,mode)+offset,end=anchorTime(data,first+size,mode)+offset;
    return [clamp(start,0,duration),clamp(end,0,duration)];
  }
  function nearestBeat(beats,t,offset=0){const p=t-offset,i=upperBound(beats,p);if(!beats.length)return -1;if(i===0)return 0;if(i===beats.length)return i-1;return p-beats[i-1]<=beats[i]-p?i-1:i;}
  const countAt=(index,anchor)=>index<0?null:((index-anchor)%8+8)%8+1;
  const accent=count=>count===8?3:count===4?2:[2,6].includes(count)?1:0;
  // Instrument flashes use acoustic time, independently of a count-only offset.
  function attackFlash(lane,time,style){
    let lo=0,hi=lane.length;while(lo<hi){const mid=(lo+hi)>>1;if(lane[mid].time<=time+1e-5)lo=mid+1;else hi=mid;}
    const event=lane[lo-1];if(!event)return 0;
    const age=Math.max(0,time-event.time);
    if(age>=style.flashDurationSeconds)return 0;
    return (1-age/style.flashDurationSeconds)*(style.minimumFlashGain+style.strengthFlashGain*event.strength);
  }
  function guideFrame(data,style,time,anchor,offset=0,mode='attack'){
    return {count:eventAt(data,time,anchor,offset,mode).count,
      flashes:Object.fromEntries(style.instrumentOrder.map(k=>[k,attackFlash(data.attack.lanes[k],time,style)]))};
  }
  const frameTarget=(time,direction,fps=30)=>Math.max(0,Math.floor(time*fps+1e-4)+direction)/fps;
  function loopFor(beats,time,anchor,offset,duration,size=8){
    const index=Math.max(0,beatIndex(beats,time,offset));
    const first=anchor+Math.floor((index-anchor)/size)*size;
    const start=first<0?0:beats[Math.min(first,beats.length-1)]+offset;
    const end=first+size<beats.length?beats[Math.max(0,first+size)]+offset:duration;
    return [clamp(start,0,duration),clamp(end,0,duration)];
  }
  function timeLabel(t){const x=Math.max(0,Number.isFinite(t)?t:0),ticks=Math.floor(x*10+1e-4);return `${Math.floor(ticks/600)}:${String(Math.floor(ticks/10)%60).padStart(2,'0')}.${ticks%10}`;}
  function validateSettings(value,data){
    if(!value||value.sourceSha256!==data.sourceSha256)return null;
    if(!Number.isFinite(value.offset)||Math.abs(value.offset)>.5)return null;
    let {anchor,offset}=value;
    const legacy=!!data.grid&&!value.gridId;
    const sourceBeats=legacy?data.detectedBeats:data.beats;
    if(!Array.isArray(sourceBeats)||!Number.isInteger(anchor)||(!(anchor>=0&&anchor<sourceBeats.length)&&!data.attack?.events?.some(e=>e.index===anchor)))return null;
    if(data.grid&&value.gridId&&value.gridId!==data.grid.id)return null;
    if(legacy){
      if(value.reviewed===true){
        const chosenTime=sourceBeats[anchor]+offset;
        anchor=nearestBeat(data.beats,chosenTime);offset=chosenTime-data.beats[anchor];
        if(Math.abs(offset)>.5)return null;
      }else anchor=data.defaultAnchor;
    }
    return {anchor,offset,reviewed:value.reviewed===true};
  }
  const api={clamp,beatIndex,gridBeatIndex,gridBeatTime,eventTimes,eventAt,anchorTime,nearestIndex,stepTime,eventLoop,nearestBeat,countAt,accent,attackFlash,guideFrame,frameTarget,loopFor,timeLabel,validateSettings};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.PracticeCore=api;
})(typeof window!=='undefined'?window:globalThis);
