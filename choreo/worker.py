"""Single durable worker. Only this orchestrator can contact object storage."""
import fcntl
import json
import logging
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import time

from .config import settings
from .db import Database, kpi_count
from . import kpi
from .storage import Storage
from .r2_guard import CostLimit
from .quotas import STORED_BYTES_SQL
from .mail import Mailer
from .moderation import audit
from datetime import datetime,timezone,timedelta
from .slow_audio import SLOW_MEDIA

LOG = logging.getLogger("choreo.worker")
ROOT = Path(__file__).resolve().parents[1]


def limits():
    resource.setrlimit(resource.RLIMIT_CPU, (3500, 3600))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2_000_000_000, 2_000_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def sandbox(folder, models):
    args = [str(ROOT/"bin/bwrap"), "--unshare-all", "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session", "--clearenv",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/etc/alternatives", "/etc/alternatives", "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--ro-bind", str(ROOT), str(ROOT), "--ro-bind", str(models), "/models",
        "--bind", str(folder), "/job", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--chdir", str(ROOT)]
    for key, value in {"PATH": f"{ROOT}/.venv/bin:/usr/bin:/bin", "HOME": "/tmp",
        "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", "TORCH_HOME": "/models/torch",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": "/tmp/hf",
        "NUMBA_CACHE_DIR": "/tmp/numba", "OMP_NUM_THREADS": "3", "MKL_NUM_THREADS": "3",
        "OPENBLAS_NUM_THREADS": "3", "MPLCONFIGDIR": "/tmp/matplotlib", "LANG": "C.UTF-8"}.items():
        args.extend(["--setenv", key, value])
    return args+[str(ROOT/".venv/bin/python"), "-m", "choreo.pipeline", "/job"]


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


class Worker:
    def __init__(self, cfg, storage=None):
        self.cfg = cfg
        self.db = Database(cfg.state_dir/"room.sqlite3")
        self.db.migrate(cfg)
        self.last_kind = "export"
        self.store = storage or Storage(cfg)
        self.mail = Mailer(cfg,self.db)
        self.jobs = cfg.state_dir/"jobs"
        self.jobs.mkdir(mode=0o700, exist_ok=True)

    def heartbeat(self):
        with self.db.connect(write=True) as con:
            con.execute("INSERT OR REPLACE INTO flags VALUES (?,?,?)", ("worker_heartbeat", str(time.time()), time.time()))

    def cleanup(self):
        now = time.time()
        with self.db.connect(write=True) as con:
            kpi_count(con,kpi.prune,now=now)
            expired=con.execute("UPDATE projects SET status='expired',cleanup_done=0 WHERE expires<=? AND status NOT IN ('deleted','expired','removed','quarantined')", (now,)).rowcount
            if expired: kpi_count(con,kpi.bump,'project.expired',expired)
            abandoned=con.execute("UPDATE projects SET status='failed',error='This upload took too long and expired. Add the video again.' WHERE status IN ('uploading','creating','completing') AND upload_deadline<=?", (now,)).rowcount
            if abandoned: kpi_count(con,kpi.bump,'upload.abandoned',abandoned)
            rows = con.execute("SELECT * FROM projects WHERE status IN ('deleted','expired','failed','removed') AND cleanup_done=0").fetchall()
            con.execute("DELETE FROM requests WHERE stamp<?", (now-86400,))
            con.execute("DELETE FROM proofs WHERE stamp<?", (now-600,))
            con.execute("DELETE FROM sessions WHERE expires<?", (now,))
            con.execute('DELETE FROM login_tokens WHERE expires<?',(now-86400,))
            con.execute('DELETE FROM oauth_states WHERE created<?',(now-600,))
            con.execute('DELETE FROM egress WHERE day<?',((datetime.now(timezone.utc)-timedelta(days=1)).strftime('%Y-%m-%d'),))
            con.execute('DELETE FROM upload_records WHERE created<?',(now-180*86400,))
            con.execute('DELETE FROM reports WHERE resolved<?',(now-365*86400,))
            con.execute('DELETE FROM audit WHERE at<?',(now-365*86400,))
            old=[p[0] for p in con.execute('SELECT id FROM projects WHERE cleanup_done=1 AND updated<?',(now-180*86400,))]
            for pid in old:
                con.execute('DELETE FROM parts WHERE project=?',(pid,));con.execute('DELETE FROM jobs WHERE project=?',(pid,));con.execute('DELETE FROM projects WHERE id=?',(pid,))
            originals=con.execute("SELECT id FROM projects WHERE status='ready' AND original_stored=1 AND updated<?",(now-300,)).fetchall()
        for row in originals:self.delete_original(row['id'])
        for p in rows:
            try:
                if p["upload_id"]:
                    self.store.abort(p["id"], p["upload_id"])
                self.store.delete_project(p["id"])
                with self.db.connect(write=True) as con:
                    con.execute("UPDATE projects SET cleanup_done=1,stored_bytes=0,original_stored=0,analysis=NULL,alignment='{}',name=CASE WHEN status='failed' THEN name ELSE 'Deleted video' END WHERE id=?", (p["id"],))
                    con.execute("UPDATE jobs SET status='cancelled' WHERE project=? AND status='queued'", (p["id"],))
                    con.execute("DELETE FROM parts WHERE project=?", (p["id"],))
                    if p["status"] in ("deleted", "expired", "removed"):
                        con.execute("DELETE FROM jobs WHERE project=?", (p["id"],))
            except Exception:
                LOG.exception("Cleanup failed for project %s; will retry", p["id"])

    def recover(self):
        # Called after obtaining an exclusive filesystem lock. There is no
        # surviving worker to race with; output uploads use deterministic keys.
        with self.db.connect(write=True) as con:
            rows=con.execute("SELECT kind, CASE WHEN attempt<2 THEN 'retried' ELSE 'failed' END AS outcome, count(*) AS n FROM jobs WHERE status='processing' GROUP BY kind, outcome").fetchall()
            for row in rows:
                if row['kind'] in ('analyze','export'):
                    kpi_count(con,kpi.bump,'job.'+row['kind']+'.'+row['outcome'],row['n'])
            con.execute("UPDATE jobs SET status='queued' WHERE status='processing' AND attempt<2")
            con.execute("UPDATE jobs SET status='failed',error='Processing stopped twice on this video. Try a shorter clip or a different file.' WHERE status='processing'")
            con.execute("UPDATE projects SET status='queued' WHERE status='processing' AND id IN (SELECT project FROM jobs WHERE kind='analyze' AND status='queued')")
            con.execute("UPDATE projects SET status='failed',error='Processing stopped partway. Add the video again to retry.' WHERE status='processing'")
        for p in self.jobs.iterdir():
            if p.is_dir() and len(p.name) == 32:
                shutil.rmtree(p)

    def claim(self):
        if getattr(self.store,'guard',None):
            try:
                if self.store.guard.status()['stopped']:return None
            except CostLimit:return None
        with self.db.connect(write=True) as con:
            job = con.execute("SELECT j.* FROM jobs j JOIN projects p ON p.id=j.project WHERE j.status='queued' AND p.expires>? AND p.hold=0 AND p.status NOT IN ('deleted','expired','failed','removed','quarantined') ORDER BY (j.kind=?) ASC,j.created LIMIT 1", (time.time(),self.last_kind)).fetchone()
            if not job:
                return None
            self.last_kind=job["kind"]
            con.execute("UPDATE jobs SET status='processing',attempt=attempt+1,updated=? WHERE id=?", (time.time(), job["id"]))
            if job['attempt'] == 0:
                kpi_count(con,kpi.observe,'queue_s',max(0,time.time()-job['created']))
            if job["kind"] == "analyze":
                con.execute("UPDATE projects SET status='processing',updated=?,stage='check' WHERE id=?", (time.time(), job["project"]))
            return dict(job)

    def process(self, job):
        process_started = time.monotonic()
        folder = self.jobs/job["id"]
        folder.mkdir(mode=0o700, exist_ok=True)
        process = None
        try:
            if shutil.disk_usage(folder).free < 5_000_000_000:
                raise RuntimeError("Insufficient free processing space")
            with self.db.connect() as con:
                p = dict(con.execute("SELECT * FROM projects WHERE id=?", (job["project"],)).fetchone())
            prefix = f"projects/{p['id']}/"
            if job["kind"] == "analyze":
                total = self.store.download(prefix+"original", folder/"original", p["bytes"])
                if total != p["bytes"]:
                    raise RuntimeError("Original size changed")
            else:
                self.store.download(prefix+"practice.mp4", folder/"practice.mp4", 2_000_000_000)
                (folder/"analysis.json").write_text(p["analysis"])
                (folder/"export.json").write_text(job["payload"])
            with (folder/"process.log").open("wb") as log:
                process = subprocess.Popen(sandbox(folder, self.cfg.state_dir/"models"),
                    stdout=log, stderr=log, start_new_session=True, preexec_fn=limits,
                    env={"PATH": "/usr/bin:/bin"})
                started = time.monotonic()
                while process.poll() is None:
                    self.heartbeat()
                    if time.monotonic()-started > 3600:
                        raise RuntimeError("Processing time limit exceeded")
                    with self.db.connect(write=True) as con:
                        live = con.execute("SELECT status,expires,hold FROM projects WHERE id=?", (p["id"],)).fetchone()
                        if not live or live["status"] in ("deleted", "expired", "removed", "quarantined") or live["hold"] or live["expires"] <= time.time():
                            raise RuntimeError("Project cancelled or expired")
                        con.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time(), job["id"]))
                        progress = folder/"progress.json"
                        if progress.exists() and job["kind"] == "analyze":
                            stage = json.loads(progress.read_text()).get("step", "check")
                            con.execute("UPDATE projects SET stage=?,updated=? WHERE id=?", (stage, time.time(), p["id"]))
                    time.sleep(2)
                if process.returncode:
                    # Detailed diagnostics stay server-side. User-facing error
                    # messages come only from our bounded validation contract.
                    LOG.error("Job %s failed: %s", job["id"], (folder/"process.log").read_text(errors="replace")[-6000:])
                    raise RuntimeError("Sandbox failed")
            with self.db.connect() as con:
                live = con.execute("SELECT status,expires,hold FROM projects WHERE id=?", (p["id"],)).fetchone()
            if not live or live["expires"] <= time.time() or live["hold"] or live["status"] in ("deleted", "expired", "removed", "quarantined"):
                raise RuntimeError("Project expired before publication")
            if job['kind']=='analyze':
                analysis=json.loads((folder/'analysis.json').read_text())
                screening=json.loads((folder/'screen.json').read_text())
                if screening.get('version')!=1 or screening.get('result')!='pass':raise RuntimeError('Missing passing content screen')
                names=['practice.mp4','poster.jpg','analysis.json']+[name+'.mp4' for name in ('bass','drums','piano','guitar')]
                names += [name for rate,name in SLOW_MEDIA.items() if rate in analysis.get('slowPlayback',{}).get('rates',[])]
                objects={prefix+name:folder/name for name in names}
            else:
                objects={prefix+f"exports/{job['id']}.mp4":folder/'export.mp4'}
            # Account for deterministic replacements and reserve all output bytes
            # before uploading. Partial failures remain conservatively counted.
            inventory={o['Key']:o['Size'] for o in self.store.inventory(p['id']) if o['Key']!=prefix+'original'}
            inventory.update({key:path.stat().st_size for key,path in objects.items()})
            stored=sum(inventory.values())
            with self.db.connect(write=True) as con:
                live=con.execute('SELECT * FROM projects WHERE id=?',(p['id'],)).fetchone()
                if not live or live['hold'] or live['status'] not in ('ready','processing') or live['expires']<=time.time():raise RuntimeError('Publication cancelled')
                if con.execute(STORED_BYTES_SQL).fetchone()[0]-live['stored_bytes']+stored>self.cfg.storage_cap_bytes:
                    raise RuntimeError('Storage capacity exceeded')
                con.execute('UPDATE projects SET stored_bytes=? WHERE id=?',(stored,p['id']))
            for key,path in objects.items():
                mime='video/mp4' if path.suffix=='.mp4' else 'image/jpeg' if path.suffix=='.jpg' else 'application/json'
                self.store.upload(key,path,mime)
            with self.db.connect(write=True) as con:
                live=con.execute('SELECT * FROM projects WHERE id=?',(p['id'],)).fetchone()
                if not live or live['hold'] or live['status'] not in ('ready','processing') or live['expires']<=time.time():raise RuntimeError('Publication cancelled')
                if job['kind']=='analyze':
                    screen=(folder/'screen.json').read_text() if (folder/'screen.json').exists() else None
                    con.execute("UPDATE projects SET status='ready',analysis=?,screen=?,error=NULL,stage=NULL,updated=? WHERE id=?",(json.dumps(analysis,allow_nan=False),screen,time.time(),p['id']))
                    con.execute('UPDATE upload_records SET source_sha256=? WHERE project=?',(analysis.get('sourceSha256'),p['id']))
                key=next(iter(objects)) if job['kind']=='export' else None
                con.execute("UPDATE jobs SET status='ready',object_key=?,updated=? WHERE id=?",(key,time.time(),job['id']))
                kpi_count(con,kpi.bump,'job.'+job['kind']+'.ready')
                kpi_count(con,kpi.observe,job['kind']+'_s',time.monotonic()-process_started)
            if job['kind']=='analyze':self.delete_original(p['id'])
        except Exception as exc:
            if process:
                stop(process)
            validation=False;code=None;notify=None
            error="Something went wrong with this video. Try again, or ask the person who runs this site."
            if (folder/'error.json').exists():
                detail=json.loads((folder/'error.json').read_text());error=detail['message'];validation=detail['validation'];code=detail.get('code')
            screen=(folder/'screen.json').read_text() if (folder/'screen.json').exists() else None
            retry=not validation and job['attempt']<1
            with self.db.connect(write=True) as con:
                live=con.execute('SELECT * FROM projects WHERE id=?',(job['project'],)).fetchone()
                cancelled=not live or live['status'] in ('deleted','expired','removed','quarantined') or live['expires']<=time.time()
                paused=not cancelled and (live['hold'] or isinstance(exc,CostLimit))
                status='cancelled' if cancelled else 'queued' if paused or retry else 'failed'
                con.execute('UPDATE jobs SET status=?,error=?,updated=?,attempt=attempt-? WHERE id=?',(status,error,time.time(),int(paused),job['id']))
                if not cancelled and not paused:
                    kpi_count(con,kpi.bump,'job.'+job['kind']+('.retried' if retry else '.failed'))
                if job['kind']=='analyze' and not cancelled:
                    con.execute("UPDATE projects SET status=?,error=?,screen=coalesce(?,screen),reject_code=?,updated=? WHERE id=? AND status='processing'",('queued' if paused or retry else 'failed',error,screen,code,time.time(),job['project']))
                    if code in ('nsfw','no_person'):kpi_count(con,kpi.bump,'screen.rejected.'+code)
                    if code=='nsfw':
                        rejected=con.execute("SELECT id FROM projects WHERE owner=? AND reject_code='nsfw' AND created>? ORDER BY created DESC",(live['owner'],time.time()-30*86400)).fetchall()
                        if len(rejected)>=3 and con.execute('UPDATE users SET upload_hold=1,hold_reason=? WHERE id=? AND upload_hold=0',('auto:nsfw',live['owner'])).rowcount:
                            audit(con,'system','upload_hold',live['owner'],'auto:nsfw')
                            user=con.execute('SELECT email FROM users WHERE id=?',(live['owner'],)).fetchone()
                            notify=(live['owner'],user['email'],[r['id'] for r in rejected[:3]])
            if notify:
                uid,email,ids=notify
                self.mail.notify('upload_hold_user',uid,email,'Uploads paused on your five·six account',f'Three of your recent uploads were stopped by our automatic content check, so uploads are paused while we review. Your existing videos still work. If you think this is a mistake, write to {self.cfg.abuse_email}.')
                self.mail.notify('upload_hold_admin',uid,self.cfg.abuse_email,'five·six: upload hold on '+email,f'User: {uid}\nEmail: {email}\nVideos: {", ".join(ids)}')
            LOG.exception("Job %s did not complete", job["id"])
        finally:
            if process:
                stop(process)
            shutil.rmtree(folder, ignore_errors=True)

    def delete_original(self,pid):
        try:
            # Serialize the small deletion with quarantine actions. Once a
            # preservation hold exists, cleanup must not remove the original.
            with self.db.connect(write=True) as con:
                p=con.execute("SELECT original_stored FROM projects WHERE id=? AND status='ready'",(pid,)).fetchone()
                if not p or not p['original_stored']:return
                self.store.delete_original(pid)
                con.execute('UPDATE projects SET original_stored=0 WHERE id=?',(pid,))
        except Exception:
            LOG.exception('Original cleanup failed for %s; will retry',pid)

    def run(self):
        with (self.cfg.state_dir/"worker.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.recover()
            while True:
                self.heartbeat()
                if getattr(self.store,'guard',None):
                    try:stopped=bool(self.store.guard.status()['stopped'])
                    except CostLimit:stopped=True
                    if stopped:
                        time.sleep(60)
                        continue
                self.cleanup()
                job = self.claim()
                if job:
                    self.process(job)
                else:
                    time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    config = settings()
    config.validate_production()
    Worker(config).run()
