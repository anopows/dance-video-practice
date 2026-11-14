"""Event association controls. Audio-level controls live in benchmark_timing.py."""
import pytest
from choreo.attack_candidates import OnsetIndex


def onset(time, peak=None, **kwargs):
    return dict(time=time,peakTime=time if peak is None else peak,strength=.9,levelDb=-10,**kwargs)


def resolve(lanes, peak=1., period=.4, mode='joint', boundary=.5):
    return OnsetIndex(lanes).refine(peak,dict(time=peak,source='mix',score=.8),
                                  period,peak-period/2,peak+period/2,mode,boundary)


@pytest.mark.parametrize('bpm',[70,110,180,260,320])
@pytest.mark.parametrize('direction',[-1,1])
def test_note_beginning_can_precede_or_follow_original_estimate(bpm,direction):
    period=60/bpm;t=1+direction*.04
    result=resolve({'high':[onset(t,1)],'drums':[onset(t,1)]},period=period)
    assert result['time']==t and result['waveformOnset']


def test_quiet_earlier_instrument_in_same_event_and_louder_swung_pickup():
    lanes={'high':[onset(.96,.99)],'drums':[onset(.961,.99)],
           'low':[onset(.88,.89),onset(1.01,1.02)],
           'bass':[onset(.88,.89),onset(1.01,1.02)]}
    for mode in ('mix','joint'):
        assert resolve(lanes,mode=mode)['time']==pytest.approx(.96)


def test_stem_bleed_needs_original_mix_evidence():
    result=resolve({'bass':[onset(.95)],'high':[onset(1.01)]})
    assert result['time']==1.01 and result['source']=='high'


@pytest.mark.parametrize('failure',['no_mix','quiet','weak','censored','equidistant'])
def test_unsupported_or_ambiguous_event_abstains(failure):
    lanes={'high':[onset(1.)]}
    if failure=='no_mix':lanes={'drums':[onset(1.)]}
    if failure=='quiet':lanes['high'][0]['levelDb']=-45
    if failure=='weak':lanes['high'][0]['strength']=.1
    if failure=='censored':lanes['high'][0]['censored']=True
    if failure=='equidistant':lanes={'high':[onset(.95),onset(1.05)]}
    result=resolve(lanes)
    if failure=='equidistant':assert result['score']==0
    else:assert result==dict(time=1.,source='mix',score=.8)


def test_uncertain_waveform_rise_is_carried_through_parameter_trials():
    lanes={'mid':[onset(.99,1.,riseRange=[.95,1.03])]}
    assert [resolve(lanes,boundary=b)['time'] for b in (0,.5,1)]==[.95,.99,1.03]


def test_correlated_duplicates_do_not_change_selection():
    lanes={'high':[onset(.97,.99)],'mid':[onset(1.01,1.02)],'drums':[onset(.971,.99)]}
    before=resolve(lanes)
    lanes['mid']*=20;lanes['drums']*=20
    assert resolve(lanes)==before


def test_cluster_cannot_chain_through_a_pickup():
    # Successive 30 ms links would connect all three notes with single-linkage.
    lanes={'high':[onset(.91),onset(.94),onset(.97),onset(1.)]}
    result=resolve(lanes)
    assert result['time']==.97


def test_weak_base_acoustics_cannot_be_promoted_by_onset_metadata():
    original=dict(time=1.,source='unresolved',score=0.)
    assert OnsetIndex({'high':[onset(.98)]}).refine(1.,original,.4,.8,1.2,'mix')==original


def test_shared_spectral_peak_does_not_merge_a_previous_note_tail():
    lanes={'low':[onset(.938,.982)],'high':[onset(1.,.982)]}
    assert resolve(lanes)['time']==1.


def test_missing_early_stem_cannot_suppress_an_audible_mix_attack():
    lanes={'high':[onset(.96,.99)],'low':[onset(1.01,1.02)],'bass':[onset(1.01,1.02)]}
    assert resolve(lanes,mode='joint')['time']==.96
