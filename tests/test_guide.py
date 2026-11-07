"""Browser/export parity across attack boundaries, speeds and count offsets."""
import json
from pathlib import Path
import subprocess

import pytest

from choreo.render import STYLE, guide_frame, make_strip

ROOT = Path(__file__).resolve().parents[1]


def test_browser_and_export_share_count_and_instrument_clocks():
    events = [{"index": i-1, "time": .2+i*.5+(i % 2)*.02, "source": "bass"} for i in range(24)]
    lanes = {name: [{"time": .13+i*.43+j*.027, "strength": (i % 5)/4} for i in range(20)]
             for j, name in enumerate(STYLE["instrumentOrder"])}
    data = {"sourceSha256": "synthetic", "duration": 12, "bpm": 120, "defaultAnchor": 0,
            "grid": {"id": "grid", "origin": .7, "period": .5},
            "beats": [.7+i*.5 for i in range(24)],
            "attack": {"id": "attack", "events": events, "beats": [e["time"] for e in events],
                       "lanes": lanes, "labels": {"bass": "Bass estimate"}}}
    times = [e["time"]+delta for e in events for delta in (-.00002, 0, .04, .16)]
    cases = [{"t": t, "anchor": -1, "offset": offset, "mode": mode}
             for t in times for offset in (-.13, 0, .17) for mode in ("attack", "steady")]
    cases += [{"t": (i+.5)/30*speed, "anchor": 3, "offset": .13, "mode": "attack"}
              for speed in (.25, 1) for i in range(360)]
    script = """
const fs=require('node:fs');
const C=require('./choreo/static/core.js');
const {data,style,cases}=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(cases.map(x=>C.guideFrame(data,style,x.t,x.anchor,x.offset,x.mode))));
"""
    browser = json.loads(subprocess.check_output(["node", "-e", script], cwd=ROOT,
        input=json.dumps({"data": data, "style": STYLE, "cases": cases}).encode()))
    for case, web in zip(cases, browser, strict=True):
        export = guide_frame(data, case["t"], case["anchor"], case["offset"], case["mode"])
        assert export["count"] == web["count"]
        assert export["flashes"] == pytest.approx(web["flashes"], abs=1e-9)
    assert guide_frame(data, .2, 0, -.2)["flashes"] == guide_frame(data, .2, 0, .2)["flashes"]
    image = make_strip(data, 2, 0, .1, .5, True)
    assert image.size == (STYLE["width"], STYLE["height"])
