"""Associate waveform beginnings with an acoustic event, before consensus.

Spectral peaks identify which note belongs to a pulse; waveform crossings locate
its beginning. Group peaks by bounded span (never single-link chains), then let
each acoustic run choose an event. Original-mix bands establish audibility;
separated instruments may refine it but do not cast extra confidence votes.
"""
from bisect import bisect_left, bisect_right

BANDS = ('low', 'mid', 'high')
STEMS = ('bass', 'drums', 'piano', 'guitar')
# Candidate peak resolution is 64 / 22050 s, with a 1024-sample FFT. The cap
# permits asynchronous ensemble attacks without spanning swung subdivisions.
MAX_CLUSTER_SECONDS = .05
CLUSTER_BEAT_FRACTION = .15
PAIR_SECONDS = .025


class OnsetIndex:
    def __init__(self, lanes):
        self.events = sorted(
            ({**e, 'source': name, 'peakTime': e.get('peakTime', e['time'])}
             for name, values in lanes.items() if name in BANDS + STEMS
             for e in values if not e.get('censored',False) and e.get('strength', 0) >= .4 and e.get('levelDb', -100) >= -30),
            key=lambda e: (e['peakTime'], e['time'], e['source']))
        self.peaks = [e['peakTime'] for e in self.events]

    def refine(self, peak, original, period, left, right, mode, boundary=.5):
        """A symmetric event search: corrections can be early or late.

        This runs *inside* every parameter/prior trial. A trial that cannot
        distinguish neighboring events abstains; the caller retains the pulse
        if the complete ensemble does not agree. No post-consensus time edits.
        """
        if original['score'] < .16:
            return original
        span = min(MAX_CLUSTER_SECONDS, period * CLUSTER_BEAT_FRACTION)
        radius = min(.12, period * .35)
        pool = self.events[bisect_left(self.peaks, min(peak,original['time'])-radius-span):
                           bisect_right(self.peaks, max(peak,original['time'])+radius+span)]
        bands = [e for e in pool if e['source'] in BANDS and left < e['time'] < right]
        clusters = []
        for event in bands:
            # Bound BOTH waveform and spectral diameter: a shared spectral
            # peak cannot drag in the decaying tail of an earlier note.
            compatible=[group for group in clusters if all(
                max(abs(event['peakTime']-e['peakTime']),abs(event['time']-e['time']))<=span+1e-9
                for e in group)]
            if not compatible:
                clusters.append([event])
            else:
                compatible[-1].append(event)
        ranked = sorted(((min(abs(e['time']-original['time']) for e in group), group)
                         for group in clusters), key=lambda item: item[0])
        if not ranked or ranked[0][0] > radius:
            # Supplemental onset extraction can miss a real note (e.g. rapid
            # low bass). Keep the independently measured waveform candidate;
            # it still has to pass the full acoustic/prior consensus.
            return original
        # Two equally plausible notes are uncertainty, not a licence to take
        # the earliest one. A finer FFT hop does not resolve musical ambiguity.
        if len(ranked)>1 and ranked[1][0]-ranked[0][0] < min(.012, period*.04):
            return {**original, 'score': 0., 'source': 'unresolved'}
        group = ranked[0][1]
        candidates = group
        if mode == 'joint':
            matched = [e for e in pool if e['source'] in STEMS and left < e['time'] < right
                       and any(abs(e['time']-b['time']) <= PAIR_SECONDS and
                               abs(e['peakTime']-b['peakTime']) <= PAIR_SECONDS for b in group)]
            # Absence of a usable separation is not absence of an audible note.
            # The original mix remains authoritative; stems never multiply votes.
            # A missing/uncertain stem must never veto an audible mix onset.
            candidates = group + matched
        selected = min(candidates, key=lambda e: (e['time'], e['source']))
        bounds = selected.get('riseRange', [selected['time'], selected['time']])
        onset = bounds[0] + boundary*(bounds[1]-bounds[0]) if boundary != .5 else selected['time']
        if not left < onset < right:
            return {**original, 'score': 0., 'source': 'unresolved'}
        return {**original, 'time': float(onset), 'source': selected['source'],
                'onsetCluster': [min(e['peakTime'] for e in group), max(e['peakTime'] for e in group)],
                'waveformOnset': True}
