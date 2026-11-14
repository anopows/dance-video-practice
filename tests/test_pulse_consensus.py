"""Requires worker scipy; verifies acceptance, including abstentions and onset range."""
import pytest
pytest.importorskip('scipy')
import numpy as np
from choreo import pulse_timing as p


def summary(values, abstain=()):
    n=len(values)
    return p.summarize_slot(dict(runs=np.array(values)[:,None],
        configs=[{'audioEvidence':'mix' if i<n/2 else 'joint'} for i in range(n)],
        details=[[dict(source='unresolved' if i in abstain else 'high',score=0 if i in abstain else .8,pulseSupport=.8)] for i in range(n)]),0)


def test_abstentions_cannot_vote_for_unchanged_timestamps():
    assert not summary([1.]*72,range(24))['stable']
    assert summary([1.]*72)['stable']
    assert not summary([1.]*72,range(72))['stable']


def test_conflicting_notes_and_slow_rises_are_uncertain():
    assert not summary([.94]*36+[1.06]*36)['stable']
    assert not summary([.95]*24+[1.]*24+[1.04]*24)['stable']


@pytest.mark.parametrize('acoustic_stable',[False,True])
def test_every_prior_group_must_accept_final_onset(monkeypatch,acoustic_stable):
    slots=[dict(index=i,predicted=.2+i*.4,detected=True) for i in range(4)]
    def analyze(audio,arrays,priors,onset_index):
        assert onset_index is not None
        values=np.tile([s['predicted']-.03 for s in slots],(72,1))
        details=[[dict(source='high',score=.8,pulseSupport=.8) for _ in slots] for _ in range(72)]
        # One perturbed prior can no longer resolve which note owns slot 2.
        if priors[0]>.23:
            for row in details:row[2].update(score=0,source='unresolved')
        base_details=[[dict(source='mix',score=.8,pulseSupport=.8) for _ in slots] for _ in range(24)]
        if not acoustic_stable and priors[0]>.23:
            for row in base_details:row[2].update(score=0,source='unresolved')
        base=dict(runs=np.tile([s['predicted']-.02 for s in slots],(24,1)),details=base_details,
                  configs=[{'audioEvidence':'mix' if i<12 else 'joint'} for i in range(24)])
        return dict(runs=values,details=details,window={},base=base,
                    configs=[{'audioEvidence':'mix' if i<36 else 'joint'} for i in range(72)])
    monkeypatch.setattr(p,'analyze_signals',analyze)
    events,q=p.select_clock({}, {},slots,2.,onsets={})
    assert events[2]['status']==('attack' if acoustic_stable else 'pulse')
    assert events[2]['time']==pytest.approx(slots[2]['predicted']-(.02 if acoustic_stable else 0))
    assert all(e['time']==pytest.approx(e['predicted']-.03) for i,e in enumerate(events) if i!=2)
    assert q['runs']==360 and q['onsetsInsideConsensus']
