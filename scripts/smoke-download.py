"""Verify attachment delivery for the latest synthetic fixture export.

Run as choreo with the production EnvironmentFile, like smoke-result.py.
This reads 32 bytes through a fresh signed HTTPS URL and never prints the URL.
"""
import json
from pathlib import Path
import re
import sys

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
with Database(cfg.state_dir/"room.sqlite3").connect() as con:
    project = con.execute("SELECT name,status FROM projects WHERE id=?", (pid,)).fetchone()
    job = con.execute("SELECT id,status,object_key,payload FROM jobs WHERE project=? AND kind='export' ORDER BY created DESC LIMIT 1", (pid,)).fetchone()
if not project or project["name"] != "Deployment check.mp4" or project["status"] != "ready" or not job or job["status"] != "ready":
    raise SystemExit("The named deployment fixture's export is not ready.")
url = Storage(cfg).url(job["object_key"], 60, download=True)
try:
    with httpx.stream("GET", url, headers={"Range": "bytes=0-31"}, timeout=20) as response:
        count = 0
        for chunk in response.iter_bytes():
            count += len(chunk)
            if count > 32:
                break
        checks = {"status": response.status_code, "range_bytes": count,
                  "attachment": response.headers.get("content-disposition") == 'attachment; filename="practice-counts.mp4"',
                  "video_type": response.headers.get("content-type") == "video/mp4"}
except httpx.HTTPError as error:
    raise SystemExit(f"Download request failed: {type(error).__name__}") from None
print(json.dumps({"job": job["id"], "settings": json.loads(job["payload"]), "checks": checks}))
if checks != {"status": 206, "range_bytes": 32, "attachment": True, "video_type": True}:
    raise SystemExit(1)
