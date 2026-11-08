import concurrent.futures
import json
import time
import uuid
from pathlib import Path

import boto3
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from moto import mock_aws

from choreo.api import create_app, COOKIE
from choreo.config import Settings
from choreo.storage import Storage
from choreo.worker import Worker


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        cfg = Settings(origin="https://practice.test", state_dir=tmp_path,
            password_hash=PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash("test-pass"),
            turnstile_site_key="test-only", r2_bucket="practice-test")
        store = Storage.__new__(Storage)
        store.bucket = cfg.r2_bucket
        store.client = boto3.client("s3", region_name="us-east-1")
        store.client.create_bucket(Bucket=store.bucket)
        async def verify(token, action):
            return {"success": token != "bad", "hostname": "evil.test" if token == "wrong-host" else cfg.hostname,
                    "action": "other" if token == "wrong-action" else action}
        app = create_app(cfg, store, verify, testing=True)
        client = TestClient(app, base_url=cfg.origin)
        client.headers["Origin"] = cfg.origin
        yield client, app, cfg, store


def unlock(client):
    r = client.post("/api/login", json={"password": "test-pass", "token": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def begin(client, size=100, **kwargs):
    return client.post("/api/uploads", json={"name": "dance.mp4", "bytes": size,
        "token": uuid.uuid4().hex, "request_id": uuid.uuid4().hex, **kwargs})


def upload_small(client):
    body=b"\x00\x00\x00\x18ftypisom"+b"0"*88
    r=begin(client,len(body));assert r.status_code==200,r.text
    pid=r.json()["id"]
    r=client.put(f"/api/uploads/{pid}/parts/1",content=body,headers={"Content-Type":"application/octet-stream"})
    assert r.status_code==200,r.text
    r=client.post(f"/api/uploads/{pid}/complete",json={});assert r.status_code==200,r.text
    return pid


def test_password_cookie_and_unauthorized_access(room):
    c,app,cfg,s=room
    assert c.get("/api/projects").status_code==401
    assert c.post("/api/login",json={"password":"wrong","token":"fresh"}).status_code==401
    r=unlock(c)
    cookie=r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert "Domain=" not in cookie
    assert c.get("/api/projects").status_code==200
    assert c.post("/api/logout",json={}).status_code==200
    assert c.get("/api/projects").status_code==401


@pytest.mark.parametrize("token",["bad","wrong-host","wrong-action"])
def test_turnstile_checks(room,token):
    c,*_=room
    assert c.post("/api/login",json={"password":"test-pass","token":token}).status_code==400


def test_turnstile_replay_and_login_throttle(room):
    c,*_=room
    assert c.post("/api/login",json={"password":"test-pass","token":"same"}).status_code==200
    assert c.post("/api/login",json={"password":"test-pass","token":"same"}).status_code==400
    for i in range(3):
        assert c.post("/api/login",json={"password":"bad","token":str(i)}).status_code==401
    r=c.post("/api/login",json={"password":"test-pass","token":"next"})
    assert r.status_code==429 and int(r.headers["retry-after"])>0


def test_origin_csrf_and_password_not_in_client_source(room):
    c,*_=room;unlock(c)
    assert begin(c,token="one").status_code==200
    c.headers["Origin"]="https://evil.test"
    assert begin(c).status_code==403
    c.headers["Origin"]="https://practice.test";c.headers["X-CSRF-Token"]="wrong"
    assert begin(c).status_code==403
    for p in (Path(__file__).parents[1]/"choreo/static").iterdir():
        assert "test-pass" not in p.read_text()


def test_single_active_reservation_is_atomic(room):
    c,app,*_=room;unlock(c)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        codes=list(pool.map(lambda _:begin(c).status_code,range(4)))
    assert sorted(codes)==[200,429,429,429]
    with app.state.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]==1


def test_cancelled_uploads_keep_quota_and_idempotent_start(room):
    c,app,*_=room;unlock(c)
    for i in range(3):
        request_id=uuid.uuid4().hex
        r=begin(c,request_id=request_id,token=f"token-{i}")
        assert r.status_code==200,r.text
        assert begin(c,request_id=request_id,token=f"token-{i}").json()["id"]==request_id
        assert c.delete('/api/projects/'+request_id).status_code==200
    r=begin(c)
    assert r.status_code==429 and "upload limit" in r.json()["detail"]


def test_daily_bytes_survive_cancellation(room):
    c,app,*_=room;unlock(c)
    now=time.time()
    with app.state.db.connect(write=True) as db:
        for i in range(4):
            db.execute("INSERT INTO projects(id,name,bytes,created,expires,upload_deadline,status,updated) VALUES (?,?,500000000,?,?,?,'deleted',?)",(str(i),'deleted',now-1000,now+5000,now-100,now))
    assert begin(c).status_code==429


def test_video_preflight_and_server_part_bounds(room):
    c,app,*_=room;unlock(c)
    assert begin(c,name='a.exe').status_code==400
    assert begin(c,size=500_000_001).status_code==422
    pid=begin(c,size=100).json()["id"]
    assert c.put(f'/api/uploads/{pid}/parts/1',content=b'x'*100).status_code==400
    assert c.put(f'/api/uploads/{pid}/parts/2',content=b'x'*100).status_code==400
    assert c.put(f'/api/uploads/{pid}/parts/1',content=b'x'*99).status_code==400
    assert c.post(f'/api/uploads/{pid}/complete',json={}).status_code==400


def test_multipart_resume_completion_and_queue_dedup(room):
    c,app,cfg,store=room;unlock(c)
    pid=begin(c,size=cfg.part_bytes+100).json()["id"]
    first=b'\x00\x00\x00\x18ftypisom'+b'0'*(cfg.part_bytes-12)
    assert c.put(f'/api/uploads/{pid}/parts/1',content=first).status_code==200
    assert c.get('/api/projects/'+pid).json()["parts"]==[1]
    assert c.post(f'/api/uploads/{pid}/complete',json={}).status_code==400
    assert c.put(f'/api/uploads/{pid}/parts/2',content=b'1'*100).status_code==200
    for _ in range(2):
        assert c.post(f'/api/uploads/{pid}/complete',json={}).json()["status"]=='queued'
    assert store.size(store.original(pid))==cfg.part_bytes+100
    with app.state.db.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1


def test_completion_recovers_after_storage_commit(room):
    c,app,cfg,store=room;unlock(c)
    pid=begin(c).json()["id"]
    body=b'\x00\x00\x00\x18ftypisom'+b'0'*88
    c.put(f'/api/uploads/{pid}/parts/1',content=body)
    with app.state.db.connect() as db:
        uid=db.execute('SELECT upload_id FROM projects WHERE id=?',(pid,)).fetchone()[0]
    store.complete(pid,uid,store.parts(pid,uid))
    assert c.post(f'/api/uploads/{pid}/complete',json={}).status_code==200


def ready(c,app):
    pid=upload_small(c)
    analysis={'beats':[0,1,2,3,4,5,6,7],'sourceSha256':'hash','grid':{'id':'grid'},'attack':{'id':'attack','events':[{'index':i} for i in range(8)]}}
    with app.state.db.connect(write=True) as db:
        db.execute("UPDATE projects SET status='ready',analysis=? WHERE id=?",(json.dumps(analysis),pid))
        db.execute("UPDATE jobs SET status='ready'")
    return pid


def test_alignment_revision_and_export_dedup(room):
    c,app,*_=room;unlock(c);pid=ready(c,app)
    body={'revision':0,'anchor':2,'offset':.1,'timingMode':'attack'}
    assert c.put(f'/api/projects/{pid}/alignment',json=body).json()['revision']==1
    assert c.put(f'/api/projects/{pid}/alignment',json=body).status_code==409
    assert c.put(f'/api/projects/{pid}/alignment',json={**body,'revision':1,'anchor':999}).status_code==400
    a=c.post(f'/api/projects/{pid}/exports',json={'revision':1,'speed':.25})
    # The browser saves before every export; unchanged saves must not force a
    # fresh render or consume another export reservation.
    assert c.put(f'/api/projects/{pid}/alignment',json={**body,'revision':1}).json()['revision']==1
    b=c.post(f'/api/projects/{pid}/exports',json={'revision':1,'speed':.25})
    assert a.status_code==200 and a.json()['id']==b.json()['id']
    with app.state.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM requests WHERE bucket='exports:room'").fetchone()[0]==1
    assert c.post(f'/api/projects/{pid}/exports',json={'revision':0,'speed':.25}).status_code==409


def test_expiry_revokes_reads_and_cleanup_aborts_multipart(room):
    c,app,cfg,store=room;unlock(c)
    pid=begin(c).json()['id']
    with app.state.db.connect(write=True) as db:
        db.execute('UPDATE projects SET expires=? WHERE id=?',(time.time()-1,pid))
    assert c.get('/api/projects/'+pid).status_code==404
    assert c.get(f'/api/projects/{pid}/practice').status_code==404
    Worker(cfg,store).cleanup()
    assert not store.client.list_multipart_uploads(Bucket=store.bucket).get('Uploads')
    with app.state.db.connect() as db:
        assert db.execute('SELECT cleanup_done FROM projects').fetchone()[0]==1


def test_export_rates_and_slow_media_contract(room):
    from choreo.slow_audio import ENGINE, SLOW_MEDIA
    c,app,*_=room;unlock(c);pid=ready(c,app)
    url=f'/api/projects/{pid}'
    assert c.post(url+'/exports',json={'revision':0,'speed':.5}).status_code==422
    for rate in (1,.25):
        assert c.post(url+'/exports',json={'revision':0,'speed':rate}).status_code==200
    assert c.get(url+'/practice').json()['speedMedia']=={}
    with app.state.db.connect(write=True) as db:
        analysis=json.loads(db.execute('SELECT analysis FROM projects WHERE id=?',(pid,)).fetchone()[0])
        analysis['slowPlayback']={'engine':ENGINE,'rates':[*SLOW_MEDIA,'../../secret']}
        db.execute('UPDATE projects SET analysis=? WHERE id=?',(json.dumps(analysis),pid))
    payload=c.get(url+'/practice').json()
    assert set(payload['speedMedia'])==set(SLOW_MEDIA)
    for rate,name in SLOW_MEDIA.items():
        assert f'projects/{pid}/{name}' in payload['speedMedia'][rate]
    c.post('/api/logout',json={})
    assert c.get(url+'/practice').status_code==200


def test_restart_requeues_once_then_marks_failed(room):
    c,app,cfg,store=room;unlock(c);pid=upload_small(c)
    w=Worker(cfg,store)
    first=w.claim();assert first['attempt']==0
    w.recover()
    second=w.claim();assert second['attempt']==1
    w.recover()
    with app.state.db.connect() as db:
        assert db.execute('SELECT status FROM jobs').fetchone()[0]=='failed'
        assert db.execute('SELECT status FROM projects').fetchone()[0]=='failed'


def test_share_link_allows_practice_but_not_library_or_project_mutations(room):
    c,app,cfg,store=room;unlock(c);pid=ready(c,app)
    c.post('/api/logout',json={});c.headers.pop('X-CSRF-Token',None)
    r=c.get(f'/api/projects/{pid}/practice')
    assert r.status_code==200 and r.json()['videoUrl']
    assert r.headers['x-robots-tag']=='noindex, nofollow, noarchive'
    assert r.headers['referrer-policy']=='no-referrer'
    assert c.get('/api/projects').status_code==401
    assert c.get(f'/api/projects/{pid}').status_code==401
    assert begin(c).status_code==401
    assert c.put(f'/api/uploads/{pid}/parts/1',content=b'x'*100).status_code==401
    assert c.post(f'/api/uploads/{pid}/complete',json={}).status_code==401
    assert c.delete(f'/api/projects/{pid}').status_code==401
    assert c.put(f'/api/projects/{pid}/alignment',json={'revision':0,'anchor':2,'offset':0,'timingMode':'attack'}).status_code==401
    assert c.get('/api/projects/'+uuid.uuid4().hex+'/practice').status_code==404


def test_guest_exports_local_adjustments_without_changing_shared_counts(room):
    c,app,*_=room;unlock(c);pid=ready(c,app)
    c.post('/api/logout',json={});c.headers.pop('X-CSRF-Token',None)
    url=f'/api/projects/{pid}/exports'
    settings={'anchor':2,'offset':.125,'timingMode':'attack','reviewed':True}
    body={'revision':0,'speed':.25,'settings':settings}
    c.headers['Origin']='https://evil.test'
    assert c.post(url,json=body).status_code==403
    c.headers['Origin']='https://practice.test'
    assert c.post(url,json={**body,'settings':{**settings,'anchor':999}}).status_code==400
    assert c.post(url,json={**body,'settings':{**settings,'offset':1}}).status_code==422
    r=c.post(url,json=body);assert r.status_code==200,r.text
    jid=r.json()['id']
    assert c.post(url,json=body).json()['id']==jid
    with app.state.db.connect(write=True) as db:
        p=db.execute('SELECT revision,alignment FROM projects WHERE id=?',(pid,)).fetchone()
        assert p['revision']==0 and p['alignment']=='{}'
        job=db.execute('SELECT payload FROM jobs WHERE id=?',(jid,)).fetchone()
        assert json.loads(job['payload'])['settings']['offset']==.125
        db.execute("UPDATE jobs SET status='ready',object_key=? WHERE id=?",(f'projects/{pid}/exports/{jid}.mp4',jid))
    assert c.get(url+'/'+jid).json()['url']
    assert c.get(f'/api/projects/{uuid.uuid4().hex}/exports/{jid}').status_code==404
    assert c.get('/api/exports/'+jid).status_code==401
    with app.state.db.connect(write=True) as db:
        db.execute('UPDATE projects SET expires=? WHERE id=?',(time.time()-1,pid))
    assert c.get(url+'/'+jid).status_code==404
    assert c.post(url,json=body).status_code==404


def test_guest_export_budget_is_bounded_and_reuse_is_free(room):
    c,app,*_=room;unlock(c);pid=ready(c,app);c.post('/api/logout',json={})
    url=f'/api/projects/{pid}/exports'
    for i in range(6):
        r=c.post(url,json={'revision':0,'speed':.25,'settings':{'anchor':i,'offset':0,'timingMode':'attack'}})
        assert r.status_code==200,r.text
        with app.state.db.connect(write=True) as db:
            db.execute("UPDATE jobs SET status='ready' WHERE id=?",(r.json()['id'],))
    assert c.post(url,json={'revision':0,'speed':.25,'settings':{'anchor':6,'offset':0,'timingMode':'attack'}}).status_code==429
    assert c.post(url,json={'revision':0,'speed':.25,'settings':{'anchor':0,'offset':0,'timingMode':'attack'}}).status_code==200


def test_new_upload_retention_is_ninety_days(room):
    c,app,*_=room;unlock(c);pid=begin(c).json()['id']
    assert c.get('/api/bootstrap').json()['retentionDays']==90
    with app.state.db.connect() as db:
        p=db.execute('SELECT created,expires FROM projects WHERE id=?',(pid,)).fetchone()
    assert p['expires']-p['created']==90*86400


def test_production_refuses_missing_secrets_and_test_keys(tmp_path):
    with pytest.raises(RuntimeError,match='Missing'):
        create_app(Settings(state_dir=tmp_path))
    cfg=Settings(state_dir=tmp_path,password_hash='$argon2id$example',turnstile_site_key='1x00000000000000000000AA',turnstile_secret='x',r2_endpoint='https://r2.example',r2_access_key='x',r2_secret_key='y')
    with pytest.raises(RuntimeError,match='test keys'):
        cfg.validate_production()


def test_upload_needs_session_but_no_second_human_check(room):
    c,app,*_=room
    assert c.post("/api/uploads",json={"name":"dance.mp4","bytes":100,"request_id":uuid.uuid4().hex}).status_code==401
    unlock(c)
    r=c.post("/api/uploads",json={"name":"dance.mp4","bytes":100,"request_id":uuid.uuid4().hex})
    assert r.status_code==200,r.text


def test_friendly_names_and_rename(room):
    from choreo.api import display_name
    created=time.mktime((2026,11,4,12,0,0,0,0,-1))
    assert display_name("1000093139.mp4",created)=="Clip · 4 Nov"
    assert display_name("PXL_20261104_192459653 (1).mp4",created)=="Clip · 4 Nov"
    assert display_name("Thursday routine.mov",created)=="Thursday routine"
    assert display_name("Already renamed",created)=="Already renamed"
    c,app,*_=room;unlock(c);pid=ready(c,app)
    assert c.get("/api/projects").json()["projects"][0]["name"]=="dance"
    assert c.patch("/api/projects/"+pid,json={"name":"  Thursday \n routine "}).json()["name"]=="Thursday routine"
    assert c.patch("/api/projects/"+pid,json={"name":""}).status_code==422
    assert c.patch("/api/projects/"+pid,json={"name":"   "}).status_code==400
    assert c.get(f"/api/projects/{pid}/practice").json()["name"]=="Thursday routine"
    c.post('/api/logout',json={});c.headers.pop('X-CSRF-Token',None)
    assert c.patch("/api/projects/"+pid,json={"name":"Guest name"}).status_code==401


def test_library_lists_poster_duration_and_step(room):
    c,app,*_=room;unlock(c);pid=ready(c,app)
    with app.state.db.connect(write=True) as db:
        analysis=json.loads(db.execute('SELECT analysis FROM projects WHERE id=?',(pid,)).fetchone()[0])
        db.execute('UPDATE projects SET analysis=? WHERE id=?',(json.dumps({**analysis,'duration':48.2}),pid))
    item=c.get("/api/projects").json()["projects"][0]
    assert item["duration"]==48.2 and f"projects/{pid}/poster.jpg" in item["posterUrl"]
    with app.state.db.connect(write=True) as db:
        db.execute("UPDATE projects SET status='processing',stage='one' WHERE id=?",(pid,))
    item=c.get("/api/projects").json()["projects"][0]
    assert item["stage"]=="one" and "posterUrl" not in item
