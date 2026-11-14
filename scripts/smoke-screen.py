#!/usr/bin/env python3
"""Host smoke gate: synthetic non-person clip rejects; an authorized dance clip passes."""
import argparse,json,subprocess,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from choreo.screen import screen,Rejected
p=argparse.ArgumentParser(description=__doc__);p.add_argument('dance',type=Path);p.add_argument('--models',type=Path,default=Path('/var/lib/choreo/models'));a=p.parse_args()
with tempfile.TemporaryDirectory(prefix='screen-smoke-') as temp:
    folder=Path(temp)
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','smptebars=size=640x360:rate=30','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','10','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(folder/'practice.mp4')],check=True)
    try:screen(folder,a.models)
    except Rejected as exc:
        if exc.code!='no_person':raise
    else:raise SystemExit('Synthetic clip must reject no_person')
    (folder/'practice.mp4').unlink();(folder/'practice.mp4').symlink_to(a.dance.resolve());started=time.monotonic();result=screen(folder,a.models)
    print(json.dumps({'elapsedSeconds':time.monotonic()-started,**result}))
