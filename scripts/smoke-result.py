"""Inspect a synthetic smoke-test result, verify private playback, queue an export.

Run like smoke-storage.py, as choreo with the production EnvironmentFile.
Signed media URLs and credentials are deliberately never printed.
"""
import json
from pathlib import Path
import re
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from choreo.config import settings
from choreo.db import Database
from choreo.storage import Storage

pid = sys.argv[1]
if not re.fullmatch(r"[a-f0-9]{32}", pid):
    raise SystemExit("Invalid project ID.")
cfg = settings(); cfg.validate_production()
db = Database(cfg.state_dir/"room.sqlite3")
with db.connect() as con:
    project = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
if not project or project["name"] != "Deployment check.mp4" or project["status"] != "ready":
    raise SystemExit("The named deployment fixture is not ready.")
store = Storage(cfg)
prefix = f"projects/{pid}/"
sizes = {name: store.size(prefix+name) for name in
         ("original", "practice.mp4", "poster.jpg", "analysis.json", "bass.mp4", "drums.mp4", "piano.mp4", "guitar.mp4")}
with httpx.Client(timeout=20) as client:
    unsigned = client.get(f"{cfg.r2_endpoint}/{cfg.r2_bucket}/{prefix}practice.mp4", headers={"Range": "bytes=0-31"})
    short_url = store.url(prefix+"practice.mp4", 3)
    playback = client.get(short_url, headers={"Range": "bytes=0-31", "Origin": cfg.origin})
    time.sleep(5)
    expired = client.get(short_url, headers={"Range": "bytes=0-31"})
checks = {"unsigned_status": unsigned.status_code, "signed_range_status": playback.status_code,
          "signed_range_bytes": len(playback.content), "expired_status": expired.status_code,
          "cors_origin_matches": playback.headers.get("access-control-allow-origin") == cfg.origin}
if unsigned.status_code not in (400, 403) or checks != {
        "unsigned_status": unsigned.status_code, "signed_range_status": 206, "signed_range_bytes": 32,
        "expired_status": 403, "cors_origin_matches": True}:
    raise SystemExit(json.dumps(checks))
payload = json.dumps({"renderer":"counts-r3-v4", "revision": project["revision"], "speed": .25, "settings": json.loads(project["alignment"])}, sort_keys=True)
with db.connect(write=True) as con:
    old = con.execute("SELECT id FROM jobs WHERE project=? AND kind='export' AND payload=?", (pid, payload)).fetchone()
    jid = old["id"] if old else secrets.token_hex(16)
    if not old:
        now = time.time()
        con.execute("INSERT INTO jobs (id,project,kind,status,created,updated,payload) VALUES (?,?,'export','queued',?,?,?)", (jid, pid, now, now, payload))
print(json.dumps({"project": pid, "object_sizes": sizes, "playback_checks": checks, "export_job": jid}))
