"""Deterministic waveform controls, with ground truth independent of detection."""
import numpy as np

CASES = [
    dict(name='slow-piano', bpm=72, kind='tone', rise=.012),
    dict(name='medium-bass', bpm=118, kind='bass', rise=.006),
    dict(name='fast-percussion', bpm=264, kind='noise', rise=.002),
    dict(name='very-fast', bpm=312, kind='bass', rise=.005),
    dict(name='quiet-first-loud-late', bpm=176, kind='ensemble', rise=.005),
    dict(name='swung-louder-pickups', bpm=190, kind='bass', rise=.006, pickups=True),
    dict(name='tempo-drift-missing-notes', bpm=154, kind='bass', rise=.006, drift=.2, missing=(5,6)),
    dict(name='quiet-noisy-recording', bpm=136, kind='noise', rise=.004, gain=.08, noise=.006),
    dict(name='soft-instrument-entrance', bpm=100, kind='tone', rise=.09),
    dict(name='early-model-prior', bpm=218, kind='ensemble', rise=.005, bias=-.03),
    dict(name='late-model-prior', bpm=88, kind='bass', rise=.006, bias=.06),
    dict(name='silent-opening', bpm=170, kind='noise', rise=.002, opening=4),
]


def synthesize(case, sr=44100, seed=60117, count=16):
    rng=np.random.default_rng(seed)
    periods=60/case['bpm']*np.linspace(1,1+case.get('drift',0),count)
    truth=.4+np.r_[0,np.cumsum(periods[:-1])]
    duration=float(truth[-1]+.7)
    audio={k:np.zeros(round(duration*sr),dtype=np.float32) for k in ('mix','bass','drums','piano','guitar')}
    missing=set(case.get('missing',()))|set(range(case.get('opening',0)))
    def add(lane,time,kind,amplitude,rise):
        x=np.arange(round(min(.24,periods.min()*.75)*sr))/sr
        carrier=rng.normal(size=len(x)) if kind=='noise' else np.sin(2*np.pi*(87 if kind=='bass' else 440)*x)
        signal=amplitude*carrier*np.minimum(x/max(rise,1/sr),1)*np.exp(-x/(.055 if kind=='bass' else .035))
        at=round(time*sr);audio[lane][at:at+len(signal)]+=signal[:len(audio[lane])-at]
    for i,t in enumerate(truth):
        if i in missing:continue
        kind=case['kind'];lane={'noise':'drums','bass':'bass','tone':'piano','ensemble':'drums'}[kind]
        add(lane,t,'noise' if kind=='ensemble' else kind,.14 if kind=='ensemble' else .4,case['rise'])
        if kind=='ensemble':add('bass',t+.045,'bass',1.,.006)
        if case.get('pickups'):add('guitar',t+periods[i]*2/3,'tone',1.2,.002)
    audio['mix']=sum(audio[k] for k in ('bass','drums','piano','guitar'))
    audio['mix']+=case.get('noise',0)*rng.normal(size=len(audio['mix']))
    for name in audio:audio[name]*=case.get('gain',1.)
    slots=[dict(index=i,predicted=float(t+case.get('bias',.025)),detected=i not in missing,
                opening=i<case.get('opening',0)) for i,t in enumerate(truth)]
    return audio,slots,duration,truth,sorted(set(range(count))-missing)
