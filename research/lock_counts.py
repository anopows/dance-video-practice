"""Analysis definitions loaded by choreo.research_adapter, not a standalone script."""

def fit_grid(raw, reference_bpm=None):
    beats=np.array(raw['beats'])
    origin=float(raw['bars'][0]['start'])
    first=int(raw['bars'][0]['beatIndex'])
    if reference_bpm:
        period=60/float(reference_bpm);source='Artist-supplied tempo'
    else:
        initial=float(np.median(np.diff(beats)))
        # Preserve missing-beat gaps in the integer labels during tempo fitting.
        steps=np.maximum(1,np.rint(np.diff(beats)/initial)).astype(int)
        k=np.r_[0,np.cumsum(steps)];k=k-k[first]
        fitted=least_squares(lambda p:origin+k*p[0]-beats,[initial],
                             loss='soft_l1',f_scale=.04,
                             bounds=([initial*.9],[initial*1.1]))
        period=float(fitted.x[0]);source='One constant tempo fitted to detected beats'
    nearest=np.rint((beats-origin)/period)
    residual=beats-(origin+nearest*period)
    return {'origin':origin,'period':period,'bpm':60/period,'source':source,
            'detectorResidual90Ms':round(float(np.quantile(abs(residual),.9))*1000,1),
            'detectedBeatCoverage':float(np.mean(abs(residual)<.08)),
            'rule':'origin + integer * period; no count insertion, deletion or phase reset'}


def fixed_bars(grid,duration):
    origin,period=grid['origin'],grid['period']
    count=max(1,math.ceil((duration-origin)/period))
    beats=origin+np.arange(count)*period
    bars=[]
    for j,idx in enumerate(range(0,count,4)):
        start=origin+idx*period;end=min(duration,origin+(idx+4)*period)
        complete=origin+(idx+4)*period<=duration
        bars.append({'index':j,'beatIndex':idx,'start':start,'end':end,'beatCount':4,
                     'valid':complete,'complete':complete,'edge':False,'reason':None})
    return beats,bars


def locked_model_phase(observations,eligible):
    """Maximum evidence over exactly two global phases; never local resets."""
    binary=np.column_stack([observations[:,0],logsumexp(observations[:,1:],axis=1)])
    binary-=logsumexp(binary,axis=1,keepdims=True)
    parity=np.arange(len(binary))%2
    scores=[float(np.mean(np.where(parity==p,binary[:,1],binary[:,0])[eligible])) for p in (0,1)]
    return int(scores[1]>scores[0]),scores
