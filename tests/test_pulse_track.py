import numpy as np
import pytest

from choreo.beat_grid import detect, fit_grid, pulse_slots
from choreo.pulse_track import track_grid, times_at, phase_bars, opening_start
from beat_fixture import VIEWS, DURATION


def test_reported_clip_keeps_early_beats_and_does_not_double_tempo():
    calls=[]
    def model(audio,sr):
        rate=sr/22050;calls.append(rate)
        view=VIEWS.get(str(int(rate) if rate==int(rate) else rate))
        return (np.array(view['beats'])/rate,np.array(view['downs'])/rate) if view else ([],[])
    grid,q,beats,_=detect(model,np.zeros(1),22050,DURATION)
    assert calls==[1.]
    assert 170<grid['bpm']<185
    slots=pulse_slots(grid,beats,DURATION)
    assert len(slots)==178
    assert slots[0]['predicted']==pytest.approx(.1)
    assert not slots[0]['opening']
    # At 3.9 s playback must already be on a numbered pulse. The model places
    # the surrounding beats at 3.76/4.10; this is not a hand-entered 3.9s offset.
    assert slots[11]['predicted']<3.9<slots[12]['predicted']
    assert q['barAgreement']==1 and not q['needsReview']
    assert [s['predicted'] for s in slots]==pytest.approx(VIEWS['1']['beats'])


def test_rate_views_use_identical_source_time_tolerance():
    view=VIEWS['4']
    normal=fit_grid(view['beats'],view['downs'],DURATION,1)
    fast=fit_grid(view['beats'],view['downs'],DURATION,4)
    assert fast==normal
    assert fast[1]['beatAgreement']<.9


@pytest.mark.parametrize('missing,extra',[((),False),((3,9,45,46,47,90),False),((),True),(tuple(range(40,80)),False)])
def test_tempo_drift_missing_beats_extra_onsets_and_break_keep_numbered_slots(missing,extra):
    truth=.1+np.r_[0,np.cumsum(np.linspace(.32,.36,177))]
    beats=np.delete(truth,missing)
    if extra:beats=np.sort(np.r_[beats,truth[22]+.12])
    downs=truth[::4];duration=truth[-1]+.2
    grid,q=track_grid(beats,downs,duration,*fit_grid(beats,downs,duration))
    slots=pulse_slots(grid,beats,duration)
    assert q['tempoTracking']
    assert [s['index'] for s in slots]==list(range(len(truth)))
    assert max(abs(s['predicted']-truth[s['index']]) for s in slots)<.05
    assert all(not slots[i]['detected'] for i in missing)
    phase, bars=phase_bars(grid,duration)
    assert phase==pytest.approx([s['predicted'] for s in slots])
    for bar in bars:
        assert bar['start']==pytest.approx(phase[bar['beatIndex']])


def test_false_first_downbeat_does_not_determine_bar_phase():
    beats=.2+np.arange(60)*.35
    downs=np.r_[beats[1],beats[4::4]]
    grid,q=track_grid(beats,downs,21.1,*fit_grid(beats,downs,21.1))
    assert grid['origin']==pytest.approx(.2)
    assert times_at(grid,[0,4])==pytest.approx(beats[[0,4]])


def opening_fixture():
    slots=[{'predicted':.2+i*.4,'opening':i<8} for i in range(12)]
    curve=np.zeros(500);energy=np.zeros(500)
    return slots,curve,energy


def test_weak_but_repeated_audible_opening_does_not_wait_for_perfect_attacks():
    slots,curve,energy=opening_fixture()
    for s in slots[2:]:
        i=round(s['predicted']/.01);curve[i]=.2;energy[i:i+8]=.03
    a={'curves':{'mix':curve},'energy':{'mix':energy}}
    assert opening_start(a,slots,.01,.01)==2


@pytest.mark.parametrize('noise,isolated',[(False,False),(True,False),(True,True)])
def test_leading_silence_noise_or_one_click_does_not_start_counts(noise,isolated):
    slots,curve,energy=opening_fixture()
    if noise:energy[:]=.003
    if isolated:curve[20]=.9
    a={'curves':{'mix':curve},'energy':{'mix':energy}}
    assert opening_start(a,slots,.01,.01)==8
