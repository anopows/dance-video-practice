import numpy as np
import pytest
from choreo.beat_grid import fit_grid, pulse_slots, detect


def test_missing_beats_and_false_first_downbeat_do_not_slow_or_shift_count_grid():
    # The exact failure in short fast swing: alternating single/double gaps
    # makes the median interval too slow. The false first downbeat must not
    # define every subsequent bar.
    period=.248;origin=.93
    indices=np.array([0,2,3,4,6,7,8,10,11,12,13,14,16,18,20,22,24,26,27,28,30,32])
    beats=np.r_[.4,origin+indices*period+np.sin(indices)*.006]
    downs=np.r_[.4,origin+np.arange(9)*4*period]
    grid,q=fit_grid(beats,downs,9.36)
    assert grid['period']==pytest.approx(period,abs=.001)
    assert grid['origin']==pytest.approx(origin,abs=.01)
    assert not q['needsReview']
    slots=pulse_slots(grid,beats,9.36)
    assert all(b['index']==a['index']+1 for a,b in zip(slots,slots[1:]))
    assert np.diff([s['predicted'] for s in slots])==pytest.approx(period,abs=.001)


def test_breaks_preserve_tempo_and_numbered_slots():
    all_beats=.3+np.arange(100)*.4
    beats=np.r_[all_beats[:24],all_beats[60:]]
    grid,q=fit_grid(beats,all_beats[::4],40)
    assert grid['period']==pytest.approx(.4,abs=.001)
    slots=pulse_slots(grid,beats,40)
    assert len(slots)==100
    counts=[(s['index']%8)+1 for s in slots]
    assert all(b==a%8+1 for a,b in zip(counts,counts[1:]))


def test_irregular_detections_are_flagged_for_review():
    rng=np.random.default_rng(123)
    beats=np.cumsum(rng.uniform(.19,.85,50))
    grid,q=fit_grid(beats,beats[::3],beats[-1]+1)
    assert q['needsReview']


def test_slowed_audio_retry_maps_back_to_source_time():
    calls=[]
    def model(audio,sr):
        calls.append(sr)
        if sr==44100:
            b=.1+np.arange(36)*.25
            return b,b[::4]
        return np.array([]),np.array([])
    grid,q,beats,downs=detect(model,np.zeros(100),22050,18)
    assert q['analysisRate']==2
    assert grid['period']==pytest.approx(.5)
    assert beats[0]==pytest.approx(.2)
    assert not q['needsReview']


def test_long_recording_does_not_accumulate_quantized_period_error():
    period=60/234.03
    beats=np.round((.22+np.arange(690)*period)*50)/50
    grid,q=fit_grid(beats,beats[::4],180)
    assert grid['bpm']==pytest.approx(234.03,abs=.02)
    assert q['beatAgreement']>.98
    assert not q['needsReview']


def test_uncertain_but_usable_grid_remains_reviewable():
    beats=.3+np.arange(40)*.4
    noisy=beats.copy();noisy[1::3]+=.12
    def model(audio,sr):
        return (noisy,beats[::4]) if sr==22050 else (np.array([]),np.array([]))
    grid,q,_,_=detect(model,np.zeros(100),22050,16.3)
    assert grid['period']==pytest.approx(.4,abs=.002)
    assert q['needsReview']
    assert not q['unsupported']
    assert not q['accuracyMeasured']
