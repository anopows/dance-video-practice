#!/usr/bin/env python3
"""Worker-only waveform benchmark. No network or private fixture media required.

Run with worker + dev extras installed:
  python scripts/benchmark-timing.py --output /tmp/timing-report.json
Optional --baseline-dir must contain an importable old choreo source snapshot.
Metrics concern generated note beginnings, not dance-count/downbeat accuracy.
The first five seconds are deliberately included.
"""
import argparse,importlib.util,json,sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from choreo import pulse_timing as p
from choreo.research_adapter import functions
from timing_controls import CASES,synthesize
from scipy.signal import butter,sosfiltfilt
import librosa


def extract_onsets(audio):
    f=functions()['attacks'];mix=librosa.resample(audio['mix'],orig_sr=44100,target_sr=22050)
    rms=float(np.sqrt(np.mean(mix*mix)));lanes={}
    for name,(low,high) in {'low':(45,300),'mid':(250,2500),'high':(2500,9500)}.items():
        y=sosfiltfilt(butter(3,[low,high],btype='bandpass',fs=22050,output='sos'),mix)
        lanes[name]=f(y,rms,name)
    for name,y in audio.items():
        if name!='mix':lanes[name]=f(librosa.resample(y,orig_sr=44100,target_sr=22050),rms,name)
    return lanes


def measure(events,truth,sounding):
    by_index={e['index']:e for e in events};present=[i for i in sounding if i in by_index]
    errors=np.array([abs(by_index[i]['time']-truth[i])*1000 for i in present])
    wrong=[i for i in present if by_index[i]['status']=='attack' and abs(by_index[i]['time']-truth[i])>.05]
    return dict(sounding=len(sounding),missingSounding=len(sounding)-len(present),
                medianErrorMs=round(float(np.median(errors)),2),p95ErrorMs=round(float(np.percentile(errors,95)),2),
                maxErrorMs=round(float(max(errors)),2),supported=sum(by_index[i]['status']=='attack' for i in present),
                supportedWrongAbove50ms=wrong,firstCount=events[0]['index'],
                strictOrder=all(a['time']<b['time'] and a['index']+1==b['index'] for a,b in zip(events,events[1:])))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline-dir',type=Path);parser.add_argument('--seed',type=int,default=60117)
    parser.add_argument('--tempo-scale',type=float,default=1.)
    parser.add_argument('--suite',choices=['development','validation'],default='development');parser.add_argument('--check',action='store_true')
    parser.add_argument('--case',action='append');args=parser.parse_args();baseline=None
    if args.baseline_dir:
        spec=importlib.util.spec_from_file_location('baseline',args.baseline_dir/'__init__.py',submodule_search_locations=[str(args.baseline_dir)])
        module=importlib.util.module_from_spec(spec);sys.modules['baseline']=module;spec.loader.exec_module(module)
        from baseline import pulse_timing as baseline
    report=dict(scope='Synthetic waveform onset benchmark; not musical or downbeat accuracy',seed=args.seed,
                algorithm=p.ALGORITHM,suite=args.suite,includesFirstFiveSeconds=True,cases=[])
    cases=CASES
    if args.suite=='validation':
        # Different tempi, noise realization, envelope slopes and recording gain.
        # These settings are not used by the detector.
        bpms=(83,127,239,287,201,223,142,161,95,252,104,197)
        cases=[{**c,'bpm':bpm,'rise':c['rise']*1.4,'gain':c.get('gain',1)*.17} for c,bpm in zip(CASES,bpms)]
    cases=[{**c,'bpm':c['bpm']*args.tempo_scale} for c in cases]
    for case in cases:
        if args.case and case['name'] not in args.case:continue
        audio,slots,duration,truth,sounding=synthesize(case,seed=args.seed)
        arrays={f'{lane}_{size}':p.features(y,size) for lane,y in audio.items() for size in p.SIZES}
        lanes=extract_onsets(audio)
        events,q=p.select_clock(audio,arrays,slots,duration,onsets=lanes)
        row=dict(case=case,current=measure(events,truth,sounding))
        row['current']['inventedAttacksInBreak']=[e['index'] for e in events
                    if e['index'] not in sounding and e['status']=='attack']
        if baseline:
            old,_=baseline.select_clock(audio,arrays,slots,duration,onsets=lanes)
            row['baseline']=measure(old,truth,sounding)
        report['cases'].append(row);print(json.dumps(row),flush=True)
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    failures=[]
    for row in report['cases']:
        m=row['current'];name=row['case']['name']
        if m['missingSounding'] or not m['strictOrder'] or m['supportedWrongAbove50ms'] or m['inventedAttacksInBreak']:
            failures.append(name+': lost/false/out-of-order attack')
        if m['firstCount']!=row['case'].get('opening',0):failures.append(name+': wrong entrance')
        if m['medianErrorMs']>30.1 or m['p95ErrorMs']>35:failures.append(name+': waveform error gate')
        if 'baseline' in row and m['medianErrorMs']>row['baseline']['medianErrorMs']+5:
            failures.append(name+': median regressed by more than 5ms')
    report['failures']=failures;report['passed']=not failures
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':not failures,'failures':failures}),flush=True)
    if args.check and failures:raise SystemExit(1)
    return report


if __name__=='__main__':main()
