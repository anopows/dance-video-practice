"""Offline pulse-associated attack timing with conservative automatic fallback.

Reuses the acoustic reviewer tested on synthetic attacks, pickups and silence.
This is signal-processing evidence, not calibrated musical correctness.
Instrument stems are optional and correlated; they never become independent votes.
"""
from pathlib import Path
import hashlib, inspect, json, math, subprocess
from collections import Counter
import numpy as np
from scipy.ndimage import uniform_filter1d, gaussian_filter1d
from scipy.signal import find_peaks

SR=44100; HOP=128; DT=HOP/SR; ESTEP=44; EDT=ESTEP/SR
SIZES=(1024,2048)
ALL_METHODS=('complex','melflux','rms','hfc_rise')
METHOD_IDS=(0,2,3)
WEIGHTS=(.06,.12,.24,.36)
PRIOR_SHIFTS_MS=(-40,-20,0,20,40)
MATCH_SECONDS=.04
ALGORITHM='pulse-consensus-v3'

def digest(path):
    with Path(path).open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()

def decode(path,offset=0):
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-ac','1','-ar',str(SR),'-f','f32le','-'])
    return np.pad(np.frombuffer(raw,dtype='<f4'),(round(offset*SR),0))

def features(y,size):
    import essentia.standard as es
    window=es.Windowing(type='hann');fft=es.FFT(size=size);polar=es.CartesianToPolar()
    detectors=[es.OnsetDetection(method=k if k!='hfc_rise' else 'hfc',sampleRate=SR) for k in ALL_METHODS]
    rows=[]
    for frame in es.FrameGenerator(np.asarray(y,dtype=np.float32),frameSize=size,hopSize=HOP,startFromZero=False):
        magnitude,phase=polar(fft(window(frame)))
        rows.append([detector(magnitude,phase) for detector in detectors])
    result=np.array(rows).T
    result[3]=np.maximum(0,np.diff(result[3],prepend=0));result[:,:4]=0
    return result

def evidence_curves(feature_arrays):
    lanes=tuple(dict.fromkeys(k.rsplit("_",1)[0] for k in feature_arrays))
    length=min(value.shape[1] for value in feature_arrays.values())
    curves={}
    for lane in lanes:
        for size in SIZES:
            for method in METHOD_IDS:
                f=feature_arrays[f'{lane}_{size}'][method,:length]
                scale=max(float(np.percentile(f,95)),1e-12)
                peaks,props=find_peaks(f/scale,height=.2,prominence=.12,distance=round(.04/DT))
                curve=np.zeros(length)
                # Localize each spectral change without rewarding arbitrarily
                # high amplitudes; correlated views are averaged within a source.
                for shift in range(-20,21):
                    indices=peaks+shift;valid=(indices>=0)&(indices<length)
                    values=np.minimum(props['prominences'][valid],1)*np.exp(-.5*(shift*DT/.015)**2)
                    curve[indices[valid]]=np.maximum(curve[indices[valid]],values)
                curves[lane,size,method]=curve
    return np.arange(length)*DT,curves

def local_periods(priors):
    gaps=np.diff(priors)
    return np.array([np.median(gaps[max(0,i-2):min(len(gaps),i+2)]) for i in range(len(priors))])

def learned_window(acoustic,times,priors):
    """One mix-based phase distribution; correlated stems do not cast votes."""
    peaks,props=find_peaks(acoustic,prominence=.08,distance=round(.045/DT))
    if len(priors)<8 or len(peaks)<8:return {'lo':-.45,'hi':.35,'peak':0.,'supported':False}
    near=np.clip(np.searchsorted(priors,times[peaks]),1,len(priors)-1)
    near=np.where(abs(priors[near]-times[peaks])<abs(priors[near-1]-times[peaks]),near,near-1)
    phase=(times[peaks]-priors[near])/local_periods(priors)[near]
    edges=np.linspace(-.5,.5,101);centers=(edges[1:]+edges[:-1])/2
    hist=gaussian_filter1d(np.histogram(phase,edges,weights=props['prominences'])[0],2,mode='wrap')
    central=np.flatnonzero((centers>-.25)&(centers<.2));peak=central[np.argmax(hist[central])]
    if hist[peak]<1.3*np.mean(hist):return {'lo':-.45,'hi':.35,'peak':0.,'supported':False}
    l=np.flatnonzero((centers>=centers[peak]-.35)&(centers<=centers[peak]-.07))
    r=np.flatnonzero((centers>=centers[peak]+.07)&(centers<=centers[peak]+.3))
    return {'lo':float(centers[l[np.argmin(hist[l])]]),'hi':float(centers[r[np.argmin(hist[r])]]),
            'peak':float(centers[peak]),'supported':True}

def pulse_path(acoustic,times,priors,weight,window=None):
    """No candidate switch times/identities enter this optimization."""
    offsets=np.arange(-.155,.101,.005)
    positions=priors[:,None]+offsets[None,:]
    left=np.r_[0,(priors[:-1]+priors[1:])/2]
    right=np.r_[(priors[:-1]+priors[1:])/2,times[-1]]
    current=np.interp(positions,times,acoustic)
    # Repetition follows actual neighboring pulse times, including local tempo
    # changes. Current evidence is required; repetition cannot create an attack.
    repeats=[]
    for delta in (-2,-1,1,2):
        neighbor=np.clip(np.arange(len(priors))+delta,0,len(priors)-1)
        repeats.append(np.interp(positions+priors[neighbor,None]-priors[:,None],times,acoustic))
    emission=current*(.75+.25*np.mean(repeats,axis=0))-.04*(offsets/.155)**2
    if window and window['supported']:
        phase=offsets[None,:]/local_periods(priors)[:,None]
        outside=np.maximum(0,np.maximum(window['lo']-phase,phase-window['hi']))
        emission-=.12*np.minimum(outside/.1,2)
    emission[(positions<left[:,None]+.001)|(positions>right[:,None]-.001)]=-1e6
    cost=weight*np.minimum(((offsets[:,None]-offsets[None,:])/.035)**2,9)
    previous=emission[0];parents=[]
    for current in emission[1:]:
        scores=previous[:,None]-cost;parents.append(np.argmax(scores,axis=0));previous=current+scores.max(axis=0)
    j=int(np.argmax(previous));selected=[positions[-1,j]]
    for i,parent in reversed(list(enumerate(parents))):
        j=int(parent[j]);selected.append(positions[i,j])
    selected=np.array(selected[::-1])
    peaks,_=find_peaks(acoustic,distance=round(.03/DT),prominence=.035)
    for i,t in enumerate(selected):
        nearby=peaks[(np.abs(times[peaks]-t)<.025)&(times[peaks]>left[i])&(times[peaks]<right[i])]
        if len(nearby):selected[i]=times[nearby[np.argmax(acoustic[nearby])]]
    return selected

def refine_attack(t,index,priors,time_axis,lane_curves,energy,allowed):
    left=0 if index==0 else (priors[index-1]+priors[index])/2+.001
    right=time_axis[-1] if index+1==len(priors) else (priors[index]+priors[index+1])/2-.001
    neighbors=[k for k in range(max(0,index-4),min(len(priors),index+5)) if k!=index]
    candidates=[]
    for lane in allowed:
        novelty=float(np.interp(t,time_axis,lane_curves[lane]))
        if novelty<.22:continue
        rms=energy[lane]
        a=max(0,int((t-.018)/EDT));z=min(len(rms),int((t+.018)/EDT)+1)
        if z<=a:continue
        peak=a+int(np.argmax(rms[a:z]));lo=max(0,int(max(left,t-.075)/EDT))
        if lo>=peak:continue
        valley=lo+int(np.argmin(rms[lo:peak+1]));floor=float(rms[valley]);top=float(rms[peak]);rise=top-floor
        if top<1e-9 or rise<top*.12:continue
        crossing=np.flatnonzero(rms[valley:peak+1]<=floor+.15*rise)
        onset=(valley+int(crossing[-1])+1)*EDT if len(crossing) else valley*EDT
        # Nearby pulse evidence is a role heuristic, not instrument identity.
        persistence=[]
        for k in neighbors:
            center=t+priors[k]-priors[index]
            start=max(0,round((center-.03)/DT));end=min(len(time_axis),round((center+.03)/DT)+1)
            persistence.append(float(np.max(lane_curves[lane][start:end],initial=0)))
        persistent=float(np.mean(persistence)) if persistence else 0
        candidates.append({'time':float(np.clip(onset,left,right)),'source':lane,
            'score':novelty*(.55+.45*persistent),'novelty':novelty,
            'riseFraction':rise/top,'peak':peak*EDT,'pulseSupport':persistent,
            'censored':onset<=left+.002 or valley<=lo+1})
    if not candidates:return {'time':float(np.clip(t,left,right)),'source':'unresolved','score':0,'novelty':0,'pulseSupport':0,'censored':True}
    best=max(candidates,key=lambda c:c['score'])
    group=[c for c in candidates if c['score']>=best['score']*.75 and abs(c['time']-best['time'])<.035]
    return min(group,key=lambda c:c['time'])

def analyze_signals(audio,feature_arrays,priors):
    lanes=tuple(audio)
    priors=np.asarray(priors,float)
    time_axis,curves=evidence_curves(feature_arrays)
    window=learned_window(np.mean([curves['mix',s,m] for s in SIZES for m in METHOD_IDS],axis=0),time_axis,priors)
    energy={lane:np.sqrt(np.maximum(0,uniform_filter1d(np.asarray(audio[lane],float)**2,size=529)))[::ESTEP] for lane in lanes}
    runs=[];configs=[];details=[]
    for sizes in ((1024,),(2048,),(1024,2048)):
        lane_curves={lane:np.mean([curves[lane,size,method] for size in sizes for method in METHOD_IDS],axis=0) for lane in lanes}
        for mode in ('mix','joint'):
            allowed=('mix',) if mode=='mix' else lanes
            acoustic=lane_curves['mix'] if mode=='mix' or len(lanes)==1 else .65*lane_curves['mix']+.35*np.max([lane_curves[k] for k in lanes[1:]],axis=0)
            for weight in WEIGHTS:
                peaks=pulse_path(acoustic,time_axis,priors,weight,window)
                result=[refine_attack(t,i,priors,time_axis,lane_curves,energy,allowed) for i,t in enumerate(peaks)]
                refined=np.array([r['time'] for r in result])
                assert np.all(np.diff(refined)>0),'Pulse ownership must prevent reversed counts'
                runs.append(refined);details.append(result)
                configs.append({'frameSizes':list(sizes),'audioEvidence':mode,'continuityWeight':weight})
    all_curves={lane:np.mean([curves[lane,size,method] for size in SIZES for method in METHOD_IDS],axis=0) for lane in lanes}
    return {'times':time_axis,'curves':all_curves,'energy':energy,'runs':np.array(runs),'configs':configs,'details':details,'window':window}

def summarize_slot(analysis,index):
    values=analysis['runs'][:,index]
    # Select a modal cluster, not the median between two distinct attacks.
    neighborhoods=[np.abs(values-t)<=MATCH_SECONDS for t in values]
    selected=max(neighborhoods,key=lambda mask:(int(mask.sum()),-float(np.std(values[mask]))))
    center=float(np.median(values[selected]));selected=np.abs(values-center)<=MATCH_SECONDS
    center=float(np.median(values[selected]));support=int(selected.sum())
    mode_centers={mode:float(np.median(values[[c['audioEvidence']==mode for c in analysis['configs']]])) for mode in ('mix','joint')}
    source_agreement=abs(mode_centers['mix']-mode_centers['joint'])<=MATCH_SECONDS
    lo,hi=np.quantile(values,[.1,.9]);width=float(hi-lo)
    strong=support>=20 and width<=.06 and source_agreement
    selected_details=[r[index] for r,match in zip(analysis['details'],selected) if match]
    sources=Counter(d['source'] for d in selected_details if d['source']!='unresolved')
    evidence=float(np.mean([d['score'] for d in selected_details]))
    if evidence<.16:strong=False
    return {'time':center,'range':[float(lo),float(hi)],'stableRuns':support,'totalRuns':len(values),
            'sourceAgreement':source_agreement,'mixOnlyMedian':mode_centers['mix'],'withStemsMedian':mode_centers['joint'],
            'stable':strong,'source':sources.most_common(1)[0][0] if sources else 'unresolved',
            'evidence':evidence,'pulseSupport':float(np.mean([d['pulseSupport'] for d in selected_details])),
            'runs':values.tolist()}

def feature_cache(audio, directory, progress=lambda _:None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    # Content-based identity includes the feature implementation and runtime.
    from importlib.metadata import version, PackageNotFoundError
    try: runtime=version('essentia')
    except PackageNotFoundError: runtime=version('essentia-tensorflow')
    identity={'featureCode':hashlib.sha256(inspect.getsource(features).encode()).hexdigest(),
              'methods':ALL_METHODS,'essentia':runtime,
              'sr':SR,'hop':HOP,'sizes':SIZES,
              'audio':{k:hashlib.sha256(np.asarray(v,dtype='<f4').tobytes()).hexdigest() for k,v in audio.items()}}
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    path=directory/f'{key}.npz'
    if path.exists():
        with np.load(path) as cached:return {k:cached[k] for k in cached.files}
    values={}
    for lane,y in audio.items():
        for size in SIZES:
            progress(f'Acoustic evidence: {lane}, {size} samples')
            values[f'{lane}_{size}']=features(y,size)
    temporary=path.with_suffix('.tmp.npz');np.savez_compressed(temporary,**values);temporary.replace(path)
    path.with_suffix('.json').write_text(json.dumps(identity,indent=2)+'\n')
    return values

def select_clock(audio, arrays, slots, duration, progress=lambda _:None):
    """Preserve numbered slots; accept attacks only when all prior groups agree."""
    if not slots:return [],{'algorithm':ALGORITHM,'counts':0,'supportedAttacks':0,'pulseFallbacks':0}
    priors=np.asarray([s['predicted'] for s in slots],float)
    if not np.isfinite(priors).all() or np.any(np.diff(priors)<=0):raise ValueError('Pulse times must be finite and increasing')
    if any(b['index']!=a['index']+1 for a,b in zip(slots,slots[1:])):raise ValueError('Count indices must be consecutive')
    if len(priors)<2:
        return [{**s,'time':s['predicted'],'source':'pulse','strength':0,'status':'pulse','timingConfidence':'insufficient_context'} for s in slots],{'algorithm':ALGORITHM,'counts':len(slots),'supportedAttacks':0,'pulseFallbacks':len(slots)}
    analyses={}
    for shift in PRIOR_SHIFTS_MS:
        progress(f'Checking 24 acoustic settings; beat prior {shift:+d} ms')
        analyses[shift]=analyze_signals(audio,arrays,priors+shift/1000)
    output=[]
    for i,slot in enumerate(slots):
        summaries={shift:summarize_slot(a,i) for shift,a in analyses.items()};base=summaries[0]
        stable=all(s['stable'] and abs(s['time']-base['time'])<=MATCH_SECONDS for s in summaries.values())
        left=0 if i==0 else (priors[i-1]+priors[i])/2
        right=duration if i+1==len(priors) else (priors[i]+priors[i+1])/2
        stable=stable and left<base['time']<right
        output.append({**slot,'time':round(base['time'],6) if stable else float(slot['predicted']),
                       'source':base['source'] if stable else 'pulse','strength':round(base['evidence'],4) if stable else 0,
                       'status':'attack' if stable else 'pulse','timingConfidence':'supported' if stable else 'uncertain',
                       'candidateTime':round(base['time'],6),'candidateSource':base['source'],
                       'settingsNearby':base['stableRuns'],'settingsPerPrior':base['totalRuns'],
                       'passingPriorGroups':sum(s['stable'] and abs(s['time']-base['time'])<=MATCH_SECONDS for s in summaries.values()),
                       'timingRange':[round(min(s['range'][0] for s in summaries.values()),6),round(max(s['range'][1] for s in summaries.values()),6)],
                       'reason':'Repeated acoustic attack supported across settings' if stable else 'Competing or weak attacks; continuing the estimated pulse'})
    # Do not display extrapolated silent opening counts. Once counting begins,
    # breaks keep the same sequence and the pulse supplies missing attacks.
    opening=sum(bool(s.get('opening')) for s in slots)
    start=opening
    for i in range(opening):
        if output[i]['status']=='attack' and i+1<len(output) and output[i+1]['status']=='attack':start=i;break
    output=output[start:]
    assert all(a['time']<b['time'] and a['index']+1==b['index'] for a,b in zip(output,output[1:]))
    quality={'algorithm':ALGORITHM,'counts':len(output),'supportedAttacks':sum(e['status']=='attack' for e in output),
             'pulseFallbacks':sum(e['status']=='pulse' for e in output),'runs':120,'learnedWindow':analyses[0]['window'],
             'rule':'Learned pulse window, local repetition, complex/RMS/HFC evidence; all five prior groups must pass. Ambiguity uses the pulse.',
             'accuracyMeasured':False,'confidenceMeaning':'Parameter robustness; not a probability of musical correctness',
             'sourcesGrouped':True,'localTempoContext':True}
    return output,quality

