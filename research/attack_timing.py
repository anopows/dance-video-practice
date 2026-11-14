"""Analysis definitions loaded by choreo.research_adapter, not a standalone script."""

def attacks(y,reference_rms,source,offset=0):
    """Spectral peak candidates, refined to a local energy-rise threshold.

    A small hop is sampling resolution, not a promise of equivalent accuracy.
    Refinement uses the waveform energy, not a minimum in spectral novelty.
    """
    if len(y)<1024 or np.max(abs(y))<1e-7:return []
    env=librosa.onset.onset_strength(y=y,sr=SR,hop_length=HOP,n_fft=1024)
    peaks=librosa.onset.onset_detect(onset_envelope=env,sr=SR,hop_length=HOP,
                                  backtrack=False,delta=.08,wait=round(.055*SR/HOP))
    if not len(peaks):return []
    # Approximately 3 ms RMS, examined on a 1 ms time axis. No fixed cue lead.
    step=22
    rms=np.sqrt(np.maximum(0,uniform_filter1d(y.astype(float)**2,size=67)))[::step]
    peak_scale=max(float(np.percentile(env[peaks],85)),1e-9)
    result=[]
    for frame in peaks:
        centre=int(frame*HOP/step)
        a=max(0,centre-round(.045*SR/step));z=min(len(rms),centre+round(.030*SR/step)+1)
        if z<=a:continue
        peak=a+int(np.argmax(rms[a:z]))
        floor_at=max(0,peak-round(.100*SR/step))
        valley=floor_at+int(np.argmin(rms[floor_at:peak+1]))
        top=float(rms[peak]);floor=float(rms[valley]);rise=top-floor
        level=20*math.log10(max(top,1e-12)/max(reference_rms,1e-9))
        if level < -36 or rise < max(reference_rms*.002,top*.08):continue
        threshold=floor+.12*rise
        # Follow the final continuous rising segment to avoid preceding notes.
        below=np.flatnonzero(rms[valley:peak+1]<=threshold)
        start=valley+int(below[-1])+1 if len(below) else valley
        time=start*step/SR+offset
        bounds=[]
        for fraction in (.08,.25):
            below=np.flatnonzero(rms[valley:peak+1]<=floor+fraction*rise)
            crossing=valley+int(below[-1])+1 if len(below) else valley
            bounds.append(round(crossing*step/SR+offset,6))
        if result and time-result[-1]['time']<.035:continue
        strength=min(1.,float(env[frame])/peak_scale)
        result.append({'time':round(time,6),'source':source,'strength':round(strength,4),
                       'levelDb':round(level,1),'censored':bool(start<=floor_at+1 or peak<=a+1 or peak>=z-2),'riseRange':bounds,'peakTime':round(frame*HOP/SR+offset,6)})
    return result


def pulse_slots(raw,grid,duration):
    """Continuous integer labels; repair missing detections, not bar resets."""
    period=grid['period'];detected=np.asarray(raw['beats'],float)
    first_index=int(round((detected[0]-grid['origin'])/period))
    slots=[{'index':first_index,'predicted':float(detected[0]),'detected':True}]
    for t in detected[1:]:
        prev=slots[-1]
        gap=t-prev['predicted']
        if gap<period*.48:continue  # reject duplicate/sub-beat detections
        steps=max(1,int(round(gap/period)))
        for j in range(1,steps+1):
            slots.append({'index':prev['index']+j,'predicted':float(prev['predicted']+gap*j/steps),'detected':j==steps})
    # Include possible opening beats, but only display them if audible evidence
    # supports a consecutive entrance (checked after matching below).
    prefix=[];n=1
    while slots[0]['predicted']-n*period>=0:
        prefix.append({'index':first_index-n,'predicted':slots[0]['predicted']-n*period,'detected':False,'opening':True});n+=1
    slots=list(reversed(prefix))+slots
    while slots[-1]['predicted']+period<duration:
        last=slots[-1];slots.append({'index':last['index']+1,'predicted':last['predicted']+period,'detected':False})
    return slots


def match_attacks(slots,candidates,period,duration):
    times=np.array([c['time'] for c in candidates])
    out=[];used=set()
    for i,slot in enumerate(slots):
        b=slot['predicted']
        # Non-overlapping ownership regions guarantee a strike cannot be used
        # for two counts. Candidate evidence may be before OR after the prior.
        left=(slots[i-1]['predicted']+b)/2 if i else 0
        right=(b+slots[i+1]['predicted'])/2 if i+1<len(slots) else duration
        radius=min(.155,period*.40)
        lo=max(left,b-radius,0);hi=min(right,b+min(.07,radius),duration)
        ids=np.arange(np.searchsorted(times,lo),np.searchsorted(times,hi,side='right'))
        valid=[int(j) for j in ids if int(j) not in used and candidates[j]['strength']>=.16]
        selected=None
        if valid:
            # Prefer the first substantial rise supported by another audible
            # lane in the same short cluster. The lanes can share bleed, so
            # this is a rejection heuristic, not independent confidence.
            firm=[j for j in valid if candidates[j]['strength']>=.35]
            supported=[j for j in firm if any(candidates[k]['source']!=candidates[j]['source'] and
                       abs(candidates[k]['time']-candidates[j]['time'])<=.028 for k in firm if k!=j)]
            instruments=[j for j in firm if candidates[j]['source'] in ('bass','drums','piano','guitar')]
            def score(j):
                c=candidates[j];distance=abs(c['time']-b)/max(.025,radius)
                return c['strength']*(.6+.4*max(0,1-distance))
            best=max(valid,key=score);best_score=score(best)
            # Earliest credible member of the winning attack cluster, not the
            # earliest noise anywhere in the whole beat window.
            cluster=[j for j in valid if abs(candidates[j]['time']-candidates[best]['time'])<=.030 and score(j)>=best_score*.65]
            # When separated rhythm sources are available, do not let a melody
            # attack in a generic mix band displace their timing.
            primary=[j for j in instruments if j in supported] or instruments
            selected=min(primary or supported or cluster,key=lambda j:candidates[j]['time'])
            used.add(selected)
        c=candidates[selected] if selected is not None else None
        out.append({**slot,'time':float(c['time']) if c else b,
                    'source':c['source'] if c else 'pulse','strength':c['strength'] if c else 0,
                    'status':'attack' if c else 'pulse'})
    # Opening extrapolation is evidence-gated. Never invent a silent count-in.
    opening=[i for i,e in enumerate(out) if e.get('opening')]
    first=None
    for i in opening:
        if out[i]['status']=='attack' and i+1<len(out) and out[i+1]['status']=='attack':first=i;break
    if opening:out=out[first if first is not None else len(opening):]
    assert all(a['time']<b['time'] and a['index']+1==b['index'] for a,b in zip(out,out[1:]))
    return out
