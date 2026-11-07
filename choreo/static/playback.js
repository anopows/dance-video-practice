(function(root){
  'use strict';
  const rates=[.25,.5,.75,1];
  const toSourceTime=(time,sourceRate=1)=>time*sourceRate;
  const toMediaTime=(time,sourceRate=1)=>time/sourceRate;
  function sourceFor(media,speedMedia,listen,speed){
    const enhanced=listen==='mix'&&speed!==1&&typeof speedMedia?.[String(speed)]==='string';
    const sourceRate=enhanced?speed:1;
    return {url:enhanced?speedMedia[String(speed)]:media[listen],sourceRate,key:`${listen}:${sourceRate}`};
  }
  // A pre-rendered 25% file plays at native 1x. Public clocks and controls still
  // use the original recording's time, so seeking/loops/counts cannot drift.
  function attach(video,{initial=.5,onChange=()=>{},onError=()=>{}}={}){
    let requested=rates.includes(initial)?initial:.5,sourceRate=1;
    function apply(){
      try{
        const effective=requested/sourceRate;
        video.defaultPlaybackRate=effective;video.playbackRate=effective;
        for(const property of ['preservesPitch','webkitPreservesPitch','mozPreservesPitch'])if(property in video)video[property]=true;
        onChange(requested,video.playbackRate*sourceRate);
      }catch(error){onError('This browser could not change playback speed. Try reloading the video.');}
    }
    function set(rate){if(!rates.includes(rate))return;requested=rate;apply();}
    function setSourceRate(rate){if(!rates.includes(rate))throw new RangeError('Invalid source rate');sourceRate=rate;apply();}
    for(const event of ['loadedmetadata','canplay','play'])video.addEventListener(event,apply);
    video.addEventListener('ratechange',()=>{if(Math.abs(video.playbackRate-requested/sourceRate)>.001)apply();else onChange(requested,video.playbackRate*sourceRate);});
    apply();return {set,setSourceRate,restore:apply,get speed(){return requested;},get sourceRate(){return sourceRate;}};
  }
  const api={attach,sourceFor,toSourceTime,toMediaTime};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.PracticePlayback=api;
})(typeof globalThis!=='undefined'?globalThis:this);
