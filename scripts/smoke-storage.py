"""Admin-only storage/worker smoke test using an explicitly supplied fixture.

Run as the choreo service user with its EnvironmentFile. This creates a real
multipart object and queues one normal worker job. It does not expose an HTTP
test endpoint or replace the separate browser/Turnstile upload test.
"""
import json
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from choreo.config import settings
from choreo.db import Database
from choreo.storage import Storage
from choreo.accounts import normalize_email,key_hash
from choreo.quotas import admit

cfg = settings()
cfg.validate_production()
source = Path(sys.argv[1])
size = source.stat().st_size
if not 0 < size <= cfg.max_bytes:
    raise SystemExit("Fixture exceeds the room's input limit.")
db=Database(cfg.state_dir/"room.sqlite3");db.migrate(cfg)
store = Storage(cfg)
pid, jid = secrets.token_hex(16), secrets.token_hex(16)
now=time.time()
with db.connect(write=True) as con:
    user=dict(con.execute('SELECT * FROM users WHERE email_key=?',(normalize_email(cfg.admin_email)[1],)).fetchone());user['user_id']=user['id']
    admit(con,cfg,user,key_hash(cfg,'ip','host-smoke'),size,now)
    con.execute("INSERT INTO projects(id,name,bytes,created,expires,upload_deadline,status,updated,owner) VALUES (?,?,?,?,?,?,'creating',?,?)",(pid,'Deployment check.mp4',size,now,now+cfg.retention_seconds,now+1800,now,user['id']))
    con.execute('INSERT INTO upload_records(project,user_id,email,ip_hash,created) VALUES (?,?,?,?,?)',(pid,user['id'],user['email'],key_hash(cfg,'ip','host-smoke'),now))
upload = store.begin(pid)
try:
    with source.open("rb") as stream:
        number = 1
        while body := stream.read(cfg.part_bytes):
            store.part(pid, upload, number, body)
            number += 1
    parts = store.parts(pid, upload)
    store.complete(pid, upload, parts)
    if store.size(store.original(pid)) != size:
        raise RuntimeError("R2 size did not match the fixture.")
    now = time.time()
    with db.connect(write=True) as con:
        con.execute("UPDATE projects SET status='queued',upload_id=?,updated=? WHERE id=?",(upload,now,pid))
        con.execute("INSERT INTO jobs (id,project,kind,status,created,updated) VALUES (?,?,'analyze','queued',?,?)",
                    (jid, pid, now, now))
except BaseException:
    store.abort(pid, upload)
    store.delete_project(pid)
    with db.connect(write=True) as con:con.execute("UPDATE projects SET status='failed',cleanup_done=1,stored_bytes=0,original_stored=0 WHERE id=?",(pid,))
    raise
print(json.dumps({"project": pid, "job": jid, "multipart_bytes": size, "parts": len(parts)}))
