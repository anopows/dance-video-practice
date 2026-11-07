"""Fit a continuous 4/4 pulse without treating missed detections as slower beats.

Beat This can alternate single/double intervals, and its first downbeat can be
an outlier. Search tempo and bar phase jointly, then fit only supported slots.
Quality values describe agreement with detections, not musical ground truth.
"""
import numpy as np


def fit_grid(beats, downbeats, duration, rate=1):
    beats=np.asarray(beats,dtype=float);downs=np.asarray(downbeats,dtype=float)
    if len(beats)<8 or len(downs)<2:return None
    # Sources of hypotheses: neighbouring beats and complete 4/4 bars. Skipped
    # detections retain integer positions. No median-interval tempo assumption.
    candidates=[]
    for values,divisor in ((beats,1),(downs,4)):
        gaps=np.diff(values)
        for k in range(1,5):candidates.extend(gaps/(divisor*k))
    candidates=np.asarray(candidates);candidates=candidates[(candidates>=.15)&(candidates<=2)]
    candidates=np.unique(np.round(candidates,3))
    # Over a long recording, 1 ms of period quantization can accumulate into
    # whole beats. Add long-span slope estimates from integer gap hypotheses.
    refined=[]
    for initial in candidates:
        steps=np.maximum(0,np.rint(np.diff(beats)/initial))
        indices=np.r_[0,np.cumsum(steps)]
        if indices[-1]>0:
            slope=np.linalg.lstsq(np.column_stack([np.ones(len(indices)),indices]),beats,rcond=None)[0][1]
            if .15<=slope<=2 and abs(slope/initial-1)<.08:refined.append(slope)
    candidates=np.unique(np.round(np.r_[candidates,refined],6))
    if not len(candidates):return None
    seeds=downs[np.linspace(0,len(downs)-1,min(32,len(downs))).astype(int)]
    best=None
    for initial in candidates:
        for seed in seeds:
            period=float(initial);origin=float(seed)
            for _ in range(3):
                tolerance=min(.06*rate,max(.025*rate,period*.13))
                bidx=np.rint((beats-origin)/period);didx=4*np.rint((downs-origin)/(4*period))
                bm=abs(beats-origin-bidx*period)<tolerance
                dm=abs(downs-origin-didx*period)<tolerance
                if bm.sum()<4:break
                x=np.r_[bidx[bm],didx[dm],didx[dm]];y=np.r_[beats[bm],downs[dm],downs[dm]]
                fitted=np.linalg.lstsq(np.column_stack([np.ones(len(x)),x]),y,rcond=None)[0]
                if not .15<=fitted[1]<=2 or abs(fitted[1]/initial-1)>.08:break
                origin,period=map(float,fitted)
            tolerance=min(.06*rate,max(.025*rate,period*.13))
            br=np.abs(beats-origin-np.rint((beats-origin)/period)*period)
            dr=np.abs(downs-origin-4*np.rint((downs-origin)/(4*period))*period)
            precision=float(np.mean(br<tolerance));bar_precision=float(np.mean(dr<tolerance))
            score=.65*float(np.mean(np.exp(-.5*(br/tolerance)**2)))+.35*float(np.mean(np.exp(-.5*(dr/tolerance)**2)))
            # Exact faster subdivisions must not win ties against an equally
            # supported pulse. A small penalty is not a swing-tempo prior.
            score-=.005/period
            if best is None or score>best['score']:
                best=dict(origin=origin,period=period,score=score,precision=precision,barPrecision=bar_precision,
                          residual=br,downResidual=dr,tolerance=tolerance)
    if best is None:return None
    origin,period=best['origin'],best['period'];matched=downs[best['downResidual']<best['tolerance']]
    if len(matched):origin+=4*round((float(matched[0])-origin)/(4*period))*period
    # Keep the first supported bar in the recording; leading pickup slots are
    # extrapolated separately and keep their negative beat indices.
    origin%=4*period
    supported=beats[best['residual']<best['tolerance']]
    span=float(np.ptp(supported)/duration) if len(supported)>1 else 0
    quality={'beatAgreement':round(best['precision'],4),'barAgreement':round(best['barPrecision'],4),
             'supportedSpan':round(span,4),'score':round(best['score'],4),
             'needsReview':bool(best['precision']<.85 or best['barPrecision']<.7 or span<.55 or len(matched)<3),
             'accuracyMeasured':False}
    grid={'origin':float(origin),'period':period,'bpm':60/period,'source':'Robust beat and 4/4 bar fit',
          'detectorResidual90Ms':round(float(np.quantile(best['residual'],.9))*1000,1),
          'detectedBeatCoverage':best['precision'],'rule':'One continuous 1–8 sequence; missing detections keep their slots'}
    return grid,quality


def detect(model, audio, sample_rate, duration, progress=lambda _:None):
    """Retry rate views only when the original detections do not support a grid.

    Rate views alter analysis only. Always map predictions to original media
    seconds; never speed up the stored video or reset the count sequence.
    """
    results=[]
    for rate in (1.,2.,4.,.75):
        if rate!=1 and duration/rate<6:continue
        progress('Checking music pulse' if rate==1 else 'Checking an alternative audio rate')
        b,d=model(audio,round(sample_rate*rate))
        beats=np.asarray(b)*rate;downs=np.asarray(d)*rate
        beats=beats[(beats>=0)&(beats<duration)];downs=downs[(downs>=0)&(downs<duration)]
        fit=fit_grid(beats,downs,duration,rate)
        if fit:
            grid,quality=fit;quality['analysisRate']=rate
            results.append((grid,quality,beats,downs))
            if rate==1 and not quality['needsReview']:break
    if not results:return None
    best=max(results,key=lambda item:(not item[1]['needsReview'],item[1]['score']))
    q=best[1]
    q['unsupported']=q['beatAgreement']<.6 or q['barAgreement']<.4 or q['supportedSpan']<.4
    best[1]['alternatives']=[{'rate':q['analysisRate'],'bpm':round(g['bpm'],3),'score':q['score'],'needsReview':q['needsReview']} for g,q,_,_ in results]
    return best


def pulse_slots(grid, beats, duration):
    """One slot per grid beat, including gaps and possible audible pickups."""
    origin,period=grid['origin'],grid['period']
    beats=np.asarray(beats)
    residual=abs(beats-origin-np.rint((beats-origin)/period)*period)
    supported=beats[residual<min(.12,period*.2)]
    first=float(supported[0]) if len(supported) else origin
    result=[]
    for index in range(int(np.ceil(-origin/period)),int(np.ceil((duration-origin)/period))):
        t=float(origin+index*period)
        result.append({'index':index,'predicted':t,'detected':bool(np.min(abs(beats-t))<min(.08,period*.2)),
                       'opening':bool(t<first-period*.4)})
    return result
