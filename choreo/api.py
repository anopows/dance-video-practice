import asyncio
import hashlib
import hmac
import ipaddress
import html
import sqlite3
import json
import logging
import math
import re
import secrets
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .legal import LegalPages
from .config import settings
from . import config, accounts, quotas
from .limits import EXPORT_QUEUE_LIMIT
from .db import Database, kpi_count, best_count
from . import kpi
from .storage import Storage
from .r2_guard import CostLimit
from .slow_audio import SLOW_MEDIA, ENGINE as SLOW_ENGINE

STATIC = Path(__file__).parent / "static"
COOKIE = "__Host-choreo"
ACTIVE = ("creating", "uploading", "completing", "queued", "processing")
EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def reject_limit(seconds, message="Too many requests. Wait a moment and try again."):
    raise HTTPException(429, message, headers={"Retry-After": str(max(1, math.ceil(seconds)))})


def rate(db, bucket, count, window, now, message=None):
    rows = db.execute("SELECT stamp FROM requests WHERE bucket=? AND stamp>? ORDER BY stamp", (bucket, now-window)).fetchall()
    if len(rows) >= count:
        reject_limit(rows[-count][0]+window-now, *([message] if message else []))
    db.execute("INSERT INTO requests VALUES (?,?)", (bucket, now))


CAMERA_NAME = re.compile(r"^(?:pxl|img|vid|mov|dsc|dcim|video|screen ?recording|signal|whatsapp video)?[\s_\-]*[\d\s_\-().~at]*$", re.I)


def display_name(name, created):
    """Friendly title: drop the extension; camera-number names become "Clip · 4 Nov"."""
    path = Path(name)
    if path.suffix.lower() not in EXTENSIONS:
        return name
    stem = path.stem.strip()
    if not stem or CAMERA_NAME.match(stem):
        day = datetime.fromtimestamp(created, timezone.utc)
        return f"Clip · {day.day} {day.strftime('%b')}"
    return stem


def signature(body):
    return (len(body) >= 12 and body[4:8] == b"ftyp") or body.startswith(b"\x1a\x45\xdf\xa3")


class Login(BaseModel):
    password: str = Field(max_length=128)
    token: str = Field(min_length=1, max_length=2048)


class Upload(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    bytes: int = Field(gt=0, le=500_000_000)
    # Uploads are gated by the signed-in session, quotas and rate limits. The
    # human check happens once, at unlock. Older clients may still send a token.
    token: str | None = Field(default=None, max_length=2048)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CountSettings(BaseModel):
    anchor: int = Field(ge=-32, le=10000)
    offset: float = Field(ge=-.5, le=.5, allow_inf_nan=False)
    timingMode: Literal["attack", "steady"]
    reviewed: bool = True


class Alignment(CountSettings):
    revision: int = Field(ge=0)


class Export(BaseModel):
    revision: int = Field(ge=0)
    speed: Literal[0.25, 1.0]
    settings: CountSettings | None = None


def alignment_value(body, data):
    indices = {e['index'] for e in data['attack']['events']} | set(range(len(data['beats'])))
    if body.anchor not in indices:
        raise HTTPException(400, 'That beat isn\'t in this video. Pick a moment inside the video.')
    return body.model_dump(exclude={'revision'}) | {'schema': 3,
        'sourceSha256': data['sourceSha256'], 'gridId': data['grid']['id'], 'timingId': data['attack']['id']}


def create_app(cfg=None, storage=None, verifier=None, testing=False, mailer=None, google=None):
    cfg = cfg or settings()
    if not testing:
        if mailer is not None or google is not None:
            raise RuntimeError("Authentication stand-ins are test-only")
        cfg.validate_production()
    db = Database(cfg.state_dir / "room.sqlite3")
    db.migrate(cfg)
    store = storage or Storage(cfg)
    legal = LegalPages(cfg.legal_dir)
    app_legal = legal
    ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(title="Five Six", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.db, app.state.storage = db, store
    app.state.legal = app_legal
    part_slots = asyncio.Semaphore(6)

    def ip(request):
        raw = request.client.host if request.client else "unknown"
        # Only the loopback reverse proxy may provide the original client IP.
        if raw == cfg.trusted_proxy:
            raw = request.headers.get("cf-connecting-ip", raw)
        try:
            addr = ipaddress.ip_address(raw)
            raw = str(ipaddress.ip_network(f"{addr}/64", strict=False)) if addr.version == 6 else str(addr)
        except ValueError:
            raw = "unknown"
        return hmac.new(cfg.secret_key.encode(), raw.encode(), "sha256").hexdigest()

    def origin(request):
        if request.headers.get("origin") != cfg.origin:
            raise HTTPException(403, "This request came from another site, so it was blocked.")

    def session(request, mutation=False):
        token = request.cookies.get(COOKIE, "")
        with db.connect() as con:
            row = con.execute("SELECT s.*,u.email,u.email_key,u.status AS user_status,u.upload_hold,u.terms_version FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.hash=? AND s.expires>?", (digest(token),time.time())).fetchone()
        if not row or not row['email'] or row['user_status']=='banned':
            if row:
                with db.connect(write=True) as con:con.execute('DELETE FROM sessions WHERE hash=?',(row['hash'],))
            raise HTTPException(401, "Sign in to continue.")
        if mutation:
            origin(request)
            if not hmac.compare_digest(row['csrf'],request.headers.get('x-csrf-token','')):
                raise HTTPException(403,"Your session changed. Reload the page.")
            if row['terms_version'] < config.TERMS_VERSION and request.url.path not in ('/api/logout','/api/account/terms','/api/account'):
                raise HTTPException(403,'Accept the updated terms to continue.')
        return row

    def viewer(request):
        try:return session(request)
        except HTTPException:return None

    def unlocked(user):
        return bool(user and accounts.is_admin(cfg,user) and user['admin_until']>time.time())

    def project(con, pid):
        row = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not row or row["expires"] <= time.time() or row["status"] in ("deleted", "expired", "removed", "quarantined"):
            raise HTTPException(404, f"This video is no longer available. Videos are removed {cfg.retention_seconds // 86400} days after upload.")
        return row

    def owned_project(con,pid,user):
        row=project(con,pid)
        if row['owner']!=user['user_id']:raise HTTPException(404,'Video not found.')
        return row

    def public_project(con,pid,user):
        row=project(con,pid)
        if row['hold'] and not unlocked(user):
            raise HTTPException(403,'This video is unavailable while it is being reviewed.')
        return row

    async def verify(token, action):
        now = time.time()
        # Atomic reservation also prevents duplicate concurrent verification.
        with db.connect(write=True) as con:
            con.execute("DELETE FROM proofs WHERE stamp<?", (now-600,))
            if con.execute("SELECT 1 FROM proofs WHERE hash=?", (digest(token),)).fetchone():
                raise HTTPException(400, "This security check was already used. Complete it again.")
            con.execute("INSERT INTO proofs VALUES (?,?)", (digest(token), now))
        try:
            if verifier:
                result = await verifier(token, action)
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                        data={"secret": cfg.turnstile_secret, "response": token})
                    r.raise_for_status()
                    result = r.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "The security check isn't available right now. Try again in a minute.")
        if not result.get("success") or result.get("hostname") != cfg.hostname or result.get("action") != action:
            raise HTTPException(400, "The security check failed or expired. Try it again.")

    @app.middleware("http")
    async def security(request, call_next):
        path = request.url.path
        if request.method not in ("GET", "HEAD"):
            max_body = cfg.part_bytes if path.startswith("/api/uploads/") and "/parts/" in path else 8192
            try:
                length = int(request.headers.get("content-length", "0" if request.method == "DELETE" else "-1"))
            except ValueError:
                length = -1
            if not 0 <= length <= max_body:
                return JSONResponse({"detail": "This request is too large."}, status_code=413)
        if path.startswith(("/api/", "/media/", "/auth/")):
            try:
                with db.connect(write=True) as con:
                    rate(con, "api:"+ip(request), 240, 60, time.time())
                    rate(con, "api:global", 6000, 60, time.time())
            except HTTPException as e:
                if e.status_code == 429: best_count(db, kpi.bump, 'http.429')
                return JSONResponse({"detail": e.detail}, status_code=e.status_code, headers=e.headers)
        try: response = await call_next(request)
        except Exception:
            best_count(db, kpi.bump, 'http.5xx'); raise
        if response.status_code == 429: best_count(db, kpi.bump, 'http.429')
        elif response.status_code >= 500: best_count(db, kpi.bump, 'http.5xx')
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "X-Robots-Tag": "index, follow" if path in ("/", "/terms", "/privacy", "/legal") else "noindex, nofollow, noarchive",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; connect-src 'self' https://challenges.cloudflare.com; media-src 'self' https://*.r2.cloudflarestorage.com blob:; img-src 'self' https://*.r2.cloudflarestorage.com data:; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
            "Strict-Transport-Security": "max-age=31536000",
        })
        return response

    @app.exception_handler(CostLimit)
    async def cost_limit(request, exc):
        return JSONResponse({'detail': 'Media access is paused by the storage cost safeguard.'}, status_code=503)

    @app.api_route('/media/{key:path}', methods=['GET', 'HEAD'])
    def guarded_media(key: str, request: Request, expires: int, sig: str, download: int = 0):
        if not store.verify_media(key, expires, download, sig):
            raise HTTPException(403, 'This media link is invalid or expired. Reload the video.')
        visitor=viewer(request)
        with db.connect() as con:
            media_project = public_project(con, key.split('/')[1], visitor)
            if media_project["status"] != "ready": raise HTTPException(404, "Video not found.")
        try:
            status, headers, body = store.open_media(key, request.headers.get('range'), request.method == 'HEAD')
        except store.client.exceptions.ClientError:
            raise HTTPException(503, 'Media is temporarily unavailable.')
        if download:
            headers['Content-Disposition'] = 'attachment; filename="practice-counts.mp4"'
        if body is None:
            return Response(status_code=status, headers=headers)
        def chunks():
            try:
                yield from body.iter_chunks(1024*1024)
            finally:
                body.close()
        return StreamingResponse(chunks(), status_code=status, headers=headers, background=BackgroundTask(body.close))

    @app.post('/hooks/cloudflare-budget')
    async def budget_webhook(request: Request):
        if (not cfg.r2_guard_enabled or not cfg.r2_webhook_secret
                or not hmac.compare_digest(request.headers.get('cf-webhook-auth', ''), cfg.r2_webhook_secret)):
            raise HTTPException(403, 'Forbidden')
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, 'Invalid payload')
        if not isinstance(payload, dict):
            raise HTTPException(400, 'Invalid payload')
        # Cloudflare's documented destination test must never trip production.
        probe_prefix = 'Hello World! This is a test message sent from https://cloudflare.com. If you can see this, your webhook is configured '
        if set(payload) == {'text'} and payload['text'] in {probe_prefix+'properly.', probe_prefix+'correctly.'}:
            return {'ok': True, 'test': True}
        if (not cfg.r2_budget_policy_id or payload.get('policy_id') != cfg.r2_budget_policy_id
                or payload.get('account_id') != cfg.r2_account_id
                or payload.get('alert_type') != 'billing_budget_alert'):
            logging.getLogger(__name__).warning('Rejected budget webhook: fields=%s type=%r text=%r',
                sorted(payload), payload.get('alert_type'), str(payload.get('text', ''))[:300])
            raise HTTPException(400, 'Unexpected alert')
        if payload.get('alert_event') == 'ALERT_STATE_EVENT_END':
            return {'ok': True, 'ignored': True}
        store.guard.stop('Cloudflare $200 account budget alert')
        return {'ok': True, 'stopped': True}

    @app.get("/healthz")
    def health():
        worker, queue, paused, stopped = False, 0, True, True
        try:
            with db.connect() as con:
                flags = dict(con.execute("SELECT key,value FROM flags"))
                heartbeat = float(flags.get("worker_heartbeat", 0))
                worker = 0 <= time.time() - heartbeat <= 120
                queue = con.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','processing')").fetchone()[0]
                paused = flags.get("uploads_paused", "1") == "1"
            guard = getattr(store, "guard", None)
            stopped = bool(guard.status()["stopped"]) if guard else not testing
        except (sqlite3.Error, CostLimit, ValueError, OSError):
            worker, stopped = False, True
        ok = worker and not stopped
        return JSONResponse({"ok": ok, "worker": worker, "queue": queue,
                             "guardStopped": stopped, "uploadsPaused": paused}, status_code=200 if ok else 503)

    @app.get("/terms")
    @app.get("/privacy")
    @app.get("/legal")
    def legal_page(request: Request):
        content = legal.get(request.url.path[1:])
        if content is None:
            raise HTTPException(404, "Legal pages are not available yet.")
        return HTMLResponse(content)

    @app.get("/robots.txt")
    def robots():
        return Response("User-agent: *\nAllow: /$\nAllow: /terms$\nAllow: /privacy$\nAllow: /legal$\n"
                        "Disallow: /practice/\nDisallow: /api/\nDisallow: /auth/\nDisallow: /admin\nDisallow: /media/\n"
                        + "Sitemap: " + cfg.origin + "/sitemap.xml\n", media_type="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        paths = ("/", "/terms", "/privacy", "/legal") if legal.ready else ("/",)
        urls = "".join("<url><loc>" + html.escape(cfg.origin + path) + "</loc></url>" for path in paths)
        return Response('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>", media_type="application/xml")

    accounts.install_routes(app,cfg,db,legal,origin,session,ip,rate,verify,mailer=mailer,google=google)

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        s=viewer(request)
        with db.connect() as con:
            count=con.execute('SELECT count(*) FROM users').fetchone()[0]
            flag=con.execute("SELECT value FROM flags WHERE key='uploads_paused'").fetchone()
            day,month=quotas.usage(con,s['user_id'],time.time()) if s else (0,0)
        user=None
        auth={}
        if s:
            auth={'csrf':s['csrf'],'sessionExpires':s['expires']}
            user={'email':s['email'],'admin':accounts.is_admin(cfg,s),'adminUnlocked':unlocked(s),
                  'termsRequired':s['terms_version']<config.TERMS_VERSION,'uploadHold':bool(s['upload_hold']),
                  'quota':{'dayUsed':day,'dayLimit':cfg.user_uploads_day,'monthUsed':month,'monthLimit':cfg.user_uploads_month}}
        return {'siteKey':cfg.turnstile_site_key,'maxBytes':cfg.max_bytes,'maxDuration':cfg.max_duration,
                'partBytes':cfg.part_bytes,'retentionDays':cfg.retention_seconds//86400,'sessionSeconds':cfg.session_seconds,
                'termsVersion':config.TERMS_VERSION,'emailSignIn':bool(cfg.smtp_host),'googleSignIn':bool(cfg.google_client_id),
                'signupsOpen':legal.ready and count<cfg.signup_cap,'uploadsPaused':not flag or flag[0]=='1',
                'legalReady':legal.ready,'user':user,**auth}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        s = session(request, True)
        with db.connect(write=True) as con:
            con.execute("DELETE FROM sessions WHERE hash=?", (s["hash"],))
        response.delete_cookie(COOKIE, path="/", secure=cfg.cookie_secure, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/api/projects")
    def projects(request: Request):
        user=session(request)
        now = time.time()
        with db.connect() as con:
            rows = con.execute("SELECT id,name,status,stage,bytes,created,expires,error,hold,json_extract(analysis,'$.duration') AS duration FROM projects WHERE owner=? AND expires>? AND status NOT IN ('deleted','expired','removed','quarantined') ORDER BY created DESC", (user['user_id'],now,)).fetchall()
        result = []
        for row in rows:
            item = dict(row) | {"name": display_name(row["name"], row["created"]), "hold": bool(row["hold"])}
            if row["status"] == "ready" and (not row["hold"] or unlocked(user)):
                item["posterUrl"] = store.url(f"projects/{row['id']}/poster.jpg", min(900, max(1, int(row["expires"]-now))))
            result.append(item)
        return {"projects": result}

    @app.post("/api/uploads")
    async def begin(body: Upload, request: Request):
        user=session(request,True)
        if Path(body.name).suffix.lower() not in EXTENSIONS or body.bytes>cfg.max_bytes:
            raise HTTPException(400,"This file type isn't supported. Use MP4, MOV, M4V or WebM.")
        now=time.time()
        def reserve():
            with db.connect(write=True) as con:
                existing=con.execute('SELECT id,status,owner FROM projects WHERE id=?',(body.request_id,)).fetchone()
                if existing:
                    if existing['owner']!=user['user_id']:raise HTTPException(409,'Start the upload again.')
                    return {'id':existing['id'],'status':existing['status']}
                quotas.admit(con,cfg,user,ip(request),body.bytes,now)
                con.execute('INSERT INTO projects(id,name,bytes,created,expires,upload_deadline,status,updated,owner) VALUES (?,?,?,?,?,?,?,?,?)',
                            (body.request_id,display_name(Path(body.name).name,now),body.bytes,now,now+cfg.retention_seconds,now+cfg.upload_seconds,'creating',now,user['user_id']))
                con.execute('INSERT INTO upload_records(project,user_id,email,ip_hash,created) VALUES (?,?,?,?,?)',
                            (body.request_id,user['user_id'],user['email'],ip(request),now))
                kpi_count(con,kpi.bump,'upload.reserved')
                kpi_count(con,kpi.bump,'upload.bytes',body.bytes)
            try:upload_id=store.begin(body.request_id)
            except CostLimit:raise
            except Exception:
                with db.connect(write=True) as con:
                    con.execute("UPDATE projects SET status='failed',error='The upload could not start. Add the video again.' WHERE id=?",(body.request_id,))
                raise HTTPException(503,"The upload couldn't start. Try again in a moment.")
            with db.connect(write=True) as con:
                # A concurrent deletion/ban must not be undone by late storage I/O.
                changed=con.execute("UPDATE projects SET status='uploading',upload_id=? WHERE id=? AND status='creating'",(upload_id,body.request_id)).rowcount
            if not changed:
                store.abort(body.request_id,upload_id)
                raise HTTPException(409,"This upload was cancelled.")
            return {'id':body.request_id,'status':'uploading'}
        try:return await run_in_threadpool(reserve)
        except HTTPException as exc:
            if exc.status_code==429:best_count(db,kpi.bump,'upload.rejected')
            if exc.status_code==503 and exc.detail.startswith('five·six is full'):best_count(db,kpi.bump,'upload.rejected.storage')
            raise

    @app.get("/api/projects/{pid}")
    def status(pid: str, request: Request):
        user=session(request)
        with db.connect() as con:
            row = owned_project(con, pid, user)
            parts = [r[0] for r in con.execute("SELECT number FROM parts WHERE project=? ORDER BY number", (pid,))]
        return {k: row[k] for k in ("id", "bytes", "status", "stage", "error", "expires", "revision")} | {"name": display_name(row["name"], row["created"]), "parts": parts, "partBytes": cfg.part_bytes, "uploadDeadline": row["upload_deadline"]}

    @app.put("/api/uploads/{pid}/parts/{number}")
    async def part(pid: str, number: int, request: Request):
        user=session(request, True)
        with db.connect() as con:
            row = owned_project(con, pid, user)
        if row["status"] != "uploading" or row["upload_deadline"] <= time.time():
            raise HTTPException(409, "This upload has expired. Add the video again.")
        n = math.ceil(row["bytes"]/cfg.part_bytes)
        if not 1 <= number <= n:
            raise HTTPException(400, "The upload was interrupted. Choose the same file to continue.")
        expected = min(cfg.part_bytes, row["bytes"]-(number-1)*cfg.part_bytes)
        if int(request.headers.get("content-length", -1)) != expected:
            raise HTTPException(400, "The upload was interrupted. Choose the same file to continue.")
        if part_slots.locked():
            reject_limit(2, "The upload is busy. Retrying…")
        async with part_slots:
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > expected:
                    raise HTTPException(413, "The upload was interrupted. Choose the same file to continue.")
            if len(data) != expected:
                raise HTTPException(400, "The upload was interrupted. Choose the same file to continue.")
            if number == 1 and not signature(data):
                raise HTTPException(400, "This doesn't look like a video file.")
            def save_part():
                with db.connect(write=True) as con:
                    current = owned_project(con, pid, user)
                    if current["status"] != "uploading" or current["upload_deadline"] <= time.time():
                        raise HTTPException(409, "This upload has expired. Add the video again.")
                    rate(con, f"part:{pid}:{number}", 10, 1800, time.time())
                    etag = store.part(pid, row["upload_id"], number, bytes(data))
                    con.execute("INSERT OR REPLACE INTO parts VALUES (?,?,?,?)", (pid, number, etag, expected))
                return {"number": number}
            return await run_in_threadpool(save_part)

    @app.post("/api/uploads/{pid}/complete")
    def complete(pid: str, request: Request):
        user=session(request, True)
        with db.connect(write=True) as con:
            row = owned_project(con, pid, user)
            if row["status"] in ("queued", "processing", "ready"):
                return {"id": pid, "status": row["status"]}
            if row["status"] not in ("uploading", "completing") or row["upload_deadline"] <= time.time():
                raise HTTPException(409, "This upload has expired. Add the video again.")
            # HEAD handles a crash after R2 completion but before SQLite commit.
            try:
                size = store.size(store.original(pid))
            except store.client.exceptions.ClientError as e:
                if e.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                    raise
                size = None
            if size is None:
                parts = store.parts(pid, row["upload_id"])
                n = math.ceil(row["bytes"]/cfg.part_bytes)
                if len(parts) != n or any(p["PartNumber"] != i+1 or p["Size"] != min(cfg.part_bytes, row["bytes"]-i*cfg.part_bytes) for i,p in enumerate(parts)):
                    raise HTTPException(400, "Some of the upload is missing. Choose the same file to continue.")
                store.complete(pid, row["upload_id"], parts)
                size = store.size(store.original(pid))
            if size != row["bytes"]:
                raise HTTPException(400, "The uploaded file doesn't match. Add the video again.")
            now = time.time()
            con.execute("UPDATE projects SET status='queued',updated=? WHERE id=?", (now, pid))
            kpi_count(con, kpi.bump, 'upload.completed')
            con.execute("INSERT OR IGNORE INTO jobs (id,project,kind,status,created,updated) VALUES (?,?,'analyze','queued',?,?)", (secrets.token_hex(16), pid, now, now))
        return {"id": pid, "status": "queued"}

    @app.delete("/api/projects/{pid}")
    def remove(pid: str, request: Request):
        user=session(request, True)
        with db.connect(write=True) as con:
            owned_project(con, pid, user)
            con.execute("UPDATE projects SET status='deleted',cleanup_done=0,updated=? WHERE id=?", (time.time(), pid))
            kpi_count(con, kpi.bump, 'project.deleted')
            con.execute("UPDATE jobs SET status='cancelled' WHERE project=? AND status='queued'", (pid,))
        return {"ok": True}

    @app.patch("/api/projects/{pid}")
    def rename(pid: str, body: Rename, request: Request):
        user=session(request, True)
        name = " ".join(body.name.split())
        if not name or any(ord(ch) < 32 for ch in name):
            raise HTTPException(400, "Give the video a name.")
        with db.connect(write=True) as con:
            owned_project(con, pid, user)
            con.execute("UPDATE projects SET name=?,updated=? WHERE id=?", (name, time.time(), pid))
        return {"name": name}

    @app.get("/api/projects/{pid}/practice")
    def practice(pid: str, request: Request, refresh: bool = False):
        # The unlisted, random 128-bit project URL grants access to this video.
        # Library enumeration, uploads, deletion and shared edits stay private.
        visitor=viewer(request)
        with db.connect() as con:
            row = public_project(con, pid, visitor)
        if row["status"] != "ready":
            raise HTTPException(409, "This video is still being prepared. Try again in a few minutes.")
        if not refresh:
            best_count(db, kpi.bump, 'practice.opened')
            best_count(db, kpi.seen, 'uniq.practice', ip(request), cfg.secret_key)
        ttl = min(900, max(1, int(row["expires"]-time.time())))
        analysis = json.loads(row["analysis"])
        media = {"mix": store.url(f"projects/{pid}/practice.mp4", ttl)}
        for name in analysis.get("soloInstruments", []):
            if name in ("bass", "drums", "piano", "guitar"):
                media[name] = store.url(f"projects/{pid}/{name}.mp4", ttl)
        speed_media = {}
        slow = analysis.get("slowPlayback", {})
        if slow.get("engine") == SLOW_ENGINE:
            speed_media = {rate: store.url(f"projects/{pid}/{filename}", ttl)
                           for rate, filename in SLOW_MEDIA.items() if rate in slow.get("rates", [])}
        return {"owner": bool(visitor and visitor["user_id"] == row["owner"]), "speedMedia": speed_media, "analysis": analysis, "alignment": json.loads(row["alignment"]),
            "revision": row["revision"], "name": display_name(row["name"], row["created"]), "expires": row["expires"],
            "videoUrl": media["mix"], "mediaUrls": media,
            "posterUrl": store.url(f"projects/{pid}/poster.jpg", ttl), "urlExpires": time.time()+ttl}

    @app.put("/api/projects/{pid}/alignment")
    def alignment(pid: str, body: Alignment, request: Request):
        user=session(request, True)
        with db.connect(write=True) as con:
            row = owned_project(con, pid, user)
            if row["status"] != "ready":
                raise HTTPException(409, "This video is still being prepared. Try again in a few minutes.")
            if row["revision"] != body.revision:
                raise HTTPException(409, "The counts were changed in another window. Reload to see the latest version.")
            data = json.loads(row["analysis"])
            value = alignment_value(body, data)
            # Export saves alignment first. An unchanged save must retain its
            # revision so the existing render can be reused.
            if value == json.loads(row["alignment"]):
                return {"revision": row["revision"]}
            con.execute("UPDATE projects SET alignment=?,revision=revision+1 WHERE id=?", (json.dumps(value), pid))
            kpi_count(con, kpi.bump, 'alignment.saved')
        return {"revision": body.revision+1}

    @app.post("/api/projects/{pid}/exports")
    def export(pid: str, body: Export, request: Request):
        # A link visitor can export their own bounded count adjustments without
        # writing them to the shared project. Cross-origin job creation is denied.
        origin(request)
        visitor=viewer(request)
        with db.connect(write=True) as con:
            row = public_project(con, pid, visitor)
            if row["status"] != "ready" or row["revision"] != body.revision:
                raise HTTPException(409, "Your count changes haven't saved yet. Try again in a moment.")
            data = json.loads(row["analysis"])
            if data.get("countQuality", {}).get("unsupported"):
                raise HTTPException(409, "No steady beat was found, so a video with counts can't be made for this clip.")
            chosen = alignment_value(body.settings, data) if body.settings else json.loads(row["alignment"])
            payload = json.dumps({"renderer": "counts-r3-v5", "revision": body.revision, "speed": body.speed, "settings": chosen}, sort_keys=True)
            old = con.execute("SELECT * FROM jobs WHERE project=? AND kind='export' AND payload=?", (pid, payload)).fetchone()
            if old:
                kpi_count(con, kpi.bump, 'export.reused')
                return {"id": old["id"], "status": old["status"]}
            rate(con, "exports:"+ip(request), 6, 3600, time.time())
            rate(con, "exports:"+pid, 10, 3600, time.time())
            rate(con, "exports:global", 60, 3600, time.time())
            if con.execute("SELECT COUNT(*) FROM jobs WHERE kind='export' AND status IN ('queued','processing')").fetchone()[0] >= EXPORT_QUEUE_LIMIT:
                reject_limit(30, "Too many videos are being made right now. Try again in a few minutes.")
            jid, now = secrets.token_hex(16), time.time()
            con.execute("INSERT INTO jobs (id,project,kind,status,created,updated,payload) VALUES (?,?,'export','queued',?,?,?)", (jid, pid, now, now, payload))
            kpi_count(con, kpi.bump, 'export.requested')
        return {"id": jid, "status": "queued"}

    def export_result(jid, pid, request):
        visitor=viewer(request)
        with db.connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=? AND kind='export'", (jid,)).fetchone()
            if not row or (pid is not None and row['project'] != pid):
                raise HTTPException(404, "This download is no longer available.")
            p = public_project(con, row["project"], visitor)
        result = {"id": jid, "status": row["status"], "error": row["error"]}
        if row["status"] == "ready":
            result["url"] = store.url(row["object_key"], min(900, max(1, int(p["expires"]-time.time()))), download=True)
        return result

    @app.get("/api/projects/{pid}/exports/{jid}")
    def shared_export_status(pid: str, jid: str, request: Request):
        return export_result(jid, pid, request)

    @app.get("/")
    def index():
        best_count(db, kpi.bump, 'page.home')
        return FileResponse(STATIC / "index.html")

    @app.get("/practice/{pid}")
    def player(pid: str):
        best_count(db, kpi.bump, 'page.practice')
        return FileResponse(STATIC / "practice.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
