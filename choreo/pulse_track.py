"""Keep coherent model beats on the recording's changing tempo.

The straight grid is a fallback, not ground truth. A sequence fit rejects extra
detections, assigns integer slots across missing beats and votes on bar phase.
Only well-supported sequences replace the global fit. No audio-specific offset
or BPM prior is used, and missing beats never reset the count.
"""
import numpy as np


def _path(beats, period):
    score=np.ones(len(beats));parents=np.full(len(beats),-1,dtype=int)
    steps=np.zeros(len(beats),dtype=int)
    gaps=np.diff(beats)
    local=gaps/np.maximum(1,np.rint(gaps/period))
    local=np.array([np.median(local[max(0,i-3):min(len(local),i+3)]) for i in range(len(beats))])
    for i in range(1,len(beats)):
        # A bounded lookback skips isolated extra detections while still
        # bridging arbitrarily long gaps between neighbouring detections.
        start=max(0,i-12);delta=beats[i]-beats[start:i]
        # Across a break, the tempo on either side estimates the missing pulse
        # count better than the whole-recording average during gradual drift.
        expected=np.where(delta>4*period,(local[start:i]+local[i])/2,period)
        count=np.maximum(1,np.rint(delta/expected)).astype(int)
        error=np.abs(delta/count/expected-1)
        valid=(delta>=period*.65)&(error<=.18)
        value=score[start:i]+1-2*error-.12*np.minimum(count-1,4)
        value[~valid]=-np.inf
        j=int(np.argmax(value))
        if value[j]>score[i]:
            score[i]=value[j];parents[i]=start+j;steps[i]=count[j]
    j=int(np.argmax(score));path=[]
    while j>=0:
        path.append(j);j=parents[j]
    path=np.array(path[::-1]);indices=np.r_[0,np.cumsum(steps[path[1:]])]
    return path,indices


def track_grid(beats, downs, duration, grid, quality):
    beats=np.asarray(beats,float);downs=np.asarray(downs,float)
    if len(beats)<8 or len(downs)<2:return grid,quality
    # Local intervals are tempo evidence even when an accelerando makes a
    # whole-recording straight line disagree with the majority of detections.
    candidates=[grid['period']]
    for gaps,divisor in ((np.diff(beats),1),(np.diff(downs),4)):
        for k in (1,2,3,4):
            candidates.extend(np.quantile(gaps/(divisor*k),[.2,.5,.8]))
    best=None
    for period in np.unique(np.round(candidates,6)):
        if not .15<=period<=2:continue
        path,indices=_path(beats,period)
        if len(path)<8:continue
        times=beats[path]
        matched=np.argmin(abs(downs[:,None]-times),axis=1)
        near=abs(downs-times[matched])<min(.06,period*.18)
        votes=np.bincount(indices[matched[near]]%4,minlength=4)
        phase=int(np.argmax(votes));bar_agreement=float(votes[phase]/len(downs))
        agreement=len(path)/len(beats)
        occupancy=len(path)/(indices[-1]+1)
        span=float(np.ptp(times)/duration)
        # Do not reward a subdivision with three invented beats between every
        # observed beat. Missing notes/breaks remain allowed, with honest quality.
        score=.45*agreement+.35*bar_agreement+.2*occupancy
        if best is None or score>best[0]:
            best=(score,path,indices-phase,bar_agreement,agreement,occupancy,span)
    if best is None:return grid,quality
    score,path,indices,bar_agreement,agreement,occupancy,span=best
    # A very sparse/ambiguous chain must not displace an already supported fit.
    if agreement<.8 or bar_agreement<.65 or occupancy<.5 or span<.55:
        return grid,quality
    times=beats[path]
    period=float(np.linalg.lstsq(np.column_stack([np.ones(len(indices)),indices]),times,rcond=None)[0][1])
    origin=float(np.interp(0,indices,times))
    # Keep precise detector times for attack association. The average period
    # remains available for the explicit steady-grid mode and diagnostics.
    result={**grid,'origin':origin,'period':period,'bpm':60/period,
            'source':'Tempo-following beat sequence with 4/4 bar consensus',
            'pulseTrack':{'indices':indices.tolist(),'times':times.tolist()},
            'detectedBeatCoverage':agreement}
    q={**quality,'beatAgreement':round(agreement,4),'barAgreement':round(bar_agreement,4),
       'supportedSpan':round(span,4),'slotCoverage':round(occupancy,4),'score':round(score,4),
       'needsReview':bool(agreement<.9 or bar_agreement<.75 or occupancy<.6),
       'tempoTracking':True,'globalFitAgreement':quality['beatAgreement']}
    return result,q


def times_at(grid, indices):
    """Interpolate missing pulses; extend edges using nearby tempo, not a mean."""
    indices=np.asarray(indices,float)
    track=grid.get('pulseTrack')
    if not track:return grid['origin']+indices*grid['period']
    x=np.asarray(track['indices']);y=np.asarray(track['times'])
    periods=np.diff(y)/np.diff(x)
    first=float(np.median(periods[:8]));last=float(np.median(periods[-8:]))
    return np.where(indices<x[0],y[0]+(indices-x[0])*first,
           np.where(indices>x[-1],y[-1]+(indices-x[-1])*last,np.interp(indices,x,y)))


def slots(grid, beats, duration):
    track=grid['pulseTrack'];x=np.asarray(track['indices']);y=np.asarray(track['times'])
    # At most the complete recording's beat count; include negative pickups.
    periods=np.diff(y)/np.diff(x)
    first=float(np.median(periods[:8]));last=float(np.median(periods[-8:]))
    indices=np.arange(x[0]-int(np.ceil(y[0]/first)),x[-1]+int(np.ceil((duration-y[-1])/last))+1)
    times=times_at(grid,indices);observed=set(x)
    return [{'index':int(i),'predicted':float(t),'detected':bool(i in observed),
             'opening':bool(t<y[0]-first*.4)}
            for i,t in zip(indices,times) if 0<=t<duration]


def phase_bars(grid,duration):
    """Musical bar observations use the same varying tempo as playback counts."""
    pulse=slots(grid,[],duration) if grid.get('pulseTrack') else None
    count=(max(s['index'] for s in pulse)+1 if pulse else int(np.ceil((duration-grid['origin'])/grid['period'])))
    indices=np.arange(max(1,count));beats=times_at(grid,indices)
    bars=[]
    for j,idx in enumerate(range(0,len(beats),4)):
        start,end=times_at(grid,[idx,idx+4]);complete=bool(end<=duration)
        bars.append({'index':j,'beatIndex':idx,'start':float(start),'end':min(duration,float(end)),
                     'beatCount':4,'valid':complete,'complete':complete,'edge':False,'reason':None})
    return beats,bars


def opening_start(analysis, slots, dt, energy_dt):
    """Audible opening pulses need not pass every precise attack-timing test.

    Two of three pulse-associated onsets plus an audible level suffice. Energy
    alone cannot start counting in leading noise. Interior breaks keep counting.
    """
    opening=next((i for i,s in enumerate(slots) if not s.get('opening')),len(slots))
    if not opening:return 0
    curve=analysis['curves']['mix'];energy=analysis['energy']['mix']
    threshold=max(1e-5,float(np.percentile(energy,90))*.025)
    gaps=np.diff([s['predicted'] for s in slots])
    periods=np.array([np.median(gaps[max(0,i-2):min(len(gaps),i+2)]) for i in range(len(slots))])
    evidence=[]
    for i,slot in enumerate(slots[:opening+2]):
        t=slot['predicted'];width=min(.09,periods[i]*.23)
        a=max(0,int((t-width)/dt));z=min(len(curve),int((t+width)/dt)+1)
        lo=max(0,int((t-width)/energy_dt));hi=min(len(energy),int((t+width)/energy_dt)+1)
        evidence.append(bool(np.max(curve[a:z],initial=0)>=.16 and np.max(energy[lo:hi],initial=0)>threshold))
    for i in range(opening):
        if evidence[i] and sum(evidence[i:i+3])>=2:return i
    return opening


