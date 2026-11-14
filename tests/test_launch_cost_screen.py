import json,math,sqlite3,time,uuid
import pytest
from test_service import room,unlock,ready,upload_small,begin
from choreo.r2_guard import R2Guard,CostLimit
from choreo.screen import decision,Rejected,screen
from choreo.worker import Worker


def test_guard_month_rollover_preserves_stop_and_migration(tmp_path,monkeypatch):
    guard=R2Guard.initialize(tmp_path/'guard.sqlite3',operations=10);guard.reserve(operations=7);guard.stop('manual')
    monkeypatch.setattr(R2Guard,'month',staticmethod(lambda:'2027-01'))
    with pytest.raises(CostLimit):guard.reserve(operations=1)
    state=guard.status();assert state['period']=='2027-01' and state['used_operations']==0 and state['stopped']=='manual'
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE limits(id INTEGER PRIMARY KEY,operations INTEGER,upload_bytes INTEGER,media_bytes INTEGER,used_operations INTEGER,used_upload_bytes INTEGER,used_media_bytes INTEGER,stopped TEXT,stopped_at REAL)")
        db.execute("INSERT INTO limits VALUES(1,100,100,100,9,8,7,'keep',123)")
    legacy=R2Guard(path);legacy.migrate_monthly();first=legacy.status();legacy.migrate_monthly()
    assert legacy.status()==first and first['used_operations']==9 and first['stopped']=='keep' and first['operations']==5_000_000


def test_per_video_cap_and_head(room):
    c,app,cfg,store=room;unlock(c);pid=ready(c,app);cfg.project_egress_day_bytes=10
    key=f'projects/{pid}/practice.mp4';store.client.put_object(Bucket=store.bucket,Key=key,Body=b'0123456789')
    url=store.url(key,900)
    assert c.head(url).status_code==200
    with app.state.db.connect() as con:assert con.execute('SELECT count(*) FROM egress').fetchone()[0]==0
    assert c.get(url,headers={'Range':'bytes=0-5'}).status_code==206
    denied=c.get(url);assert denied.status_code==429 and int(denied.headers['retry-after'])>0
    assert c.get(url,headers={'Range':'bytes=6-9'}).status_code==206
    assert c.get(url,headers={'Range':'bytes=0-0'}).status_code==429


def test_storage_cap_and_guard_stop(room):
    c,app,cfg,store=room;unlock(c);cfg.storage_cap_bytes=100
    assert begin(c).status_code==503
    cfg.storage_cap_bytes=2_000_000_000_000;pid=upload_small(c);store.guard.stop('review')
    assert Worker(cfg,store).claim() is None
    with app.state.db.connect() as con:assert con.execute('SELECT attempt FROM jobs').fetchone()[0]==0


@pytest.mark.parametrize('n',[1,40,300])
def test_screen_boundaries(n):
    count=min(n,max(3,math.ceil(.05*n)))
    assert decision([.9]*count+[0]*(n-count))['result']=='nsfw'
    assert decision([.9]*(count-1)+[0]*(n-count+1),[True]*n)['result']=='pass'
    people=max(1,math.ceil(.1*n))
    assert decision([0]*n,[True]*people+[False]*(n-people))['result']=='pass'
    assert decision([0]*n,[True]*(people-1)+[False]*(n-people+1))['result']=='no_person'


def test_screen_error_is_closed_and_writes_summary(tmp_path,monkeypatch):
    import choreo.screen as module
    monkeypatch.setattr(module,'run',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('missing model or decoder')))
    with pytest.raises(RuntimeError):screen(tmp_path,tmp_path)
    assert json.loads((tmp_path/'screen.json').read_text())['result']=='error'
    assert not (tmp_path/'screen').exists()


def pipeline_stub(monkeypatch,worker,job,code=None):
    import choreo.worker as module
    class Process:
        returncode=2 if code else 0
        def __init__(self,*a,**k):
            folder=worker.jobs/job['id']
            (folder/'screen.json').write_text(json.dumps({'version':1,'result':code or 'pass'}))
            if code:(folder/'error.json').write_text(json.dumps({'message':str(Rejected(code)),'validation':True,'code':code}))
            else:
                (folder/'analysis.json').write_text(json.dumps({'sourceSha256':'sha'}))
                for name in ('practice.mp4','poster.jpg','bass.mp4','drums.mp4','piano.mp4','guitar.mp4'):(folder/name).write_bytes(b'clean')
        def poll(self):return self.returncode
    monkeypatch.setattr(module.subprocess,'Popen',Process)


def test_worker_derived_accounting_deletes_original_and_retries_cleanup(room,monkeypatch):
    c,app,cfg,store=room;unlock(c);pid=upload_small(c);worker=Worker(cfg,store);job=worker.claim();pipeline_stub(monkeypatch,worker,job)
    real=store.delete_original;monkeypatch.setattr(store,'delete_original',lambda pid: (_ for _ in ()).throw(RuntimeError('temporary')))
    worker.process(job)
    with app.state.db.connect() as con:
        p=con.execute('SELECT * FROM projects').fetchone();assert p['status']=='ready' and p['original_stored']==1
        assert p['stored_bytes']==sum(x['Size'] for x in store.inventory(pid) if x['Key']!=store.original(pid))
        assert con.execute('SELECT source_sha256 FROM upload_records').fetchone()[0]=='sha'
    monkeypatch.setattr(store,'delete_original',real)
    with app.state.db.connect(write=True) as con:con.execute('UPDATE projects SET updated=?',(time.time()-301,))
    worker.cleanup()
    assert all(x['Key']!=store.original(pid) for x in store.inventory(pid))
    with app.state.db.connect() as con:assert con.execute('SELECT original_stored FROM projects').fetchone()[0]==0


def test_third_rejection_holds_account_and_sends_two_notices(room,monkeypatch):
    c,app,cfg,store=room;unlock(c);worker=Worker(cfg,store);worker.mail=app.state.mail
    for _ in range(3):
        pid=upload_small(c);job=worker.claim();pipeline_stub(monkeypatch,worker,job,'nsfw');worker.process(job);worker.cleanup()
        with app.state.db.connect() as con:
            p=con.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone();assert p['status']=='failed' and p['reject_code']=='nsfw' and p['cleanup_done']==1
        assert not store.inventory(pid)
    assert begin(c).status_code==403
    assert len([m for m in c.messages if 'upload hold on' in m['Subject'] or m['Subject']=='Uploads paused on your five·six account'])==2


def test_alternating_jobs(room):
    c,app,cfg,store=room;unlock(c);pid=ready(c,app);now=time.time()
    with app.state.db.connect(write=True) as con:
        for i,kind in enumerate(['analyze','analyze','export','export']):
            con.execute("INSERT INTO jobs(id,project,kind,status,created,updated,payload) VALUES (?,?,?,'queued',?,?,?)",(uuid.uuid4().hex,pid,kind,now+i,now,json.dumps({'i':i})))
    worker=Worker(cfg,store);assert [worker.claim()['kind'] for _ in range(4)]==['analyze','export','analyze','export']

@pytest.mark.parametrize('limit,code,fragment',[
    ('hold',403,'Uploads are paused for this account'),('paused',503,'Uploads are paused right now'),
    ('active',429,'Your other video'),('day',429,'24 hours'),('month',429,'30 days'),
    ('ip',429,'upload limit'),('site',429,'upload limit'),('bytes',429,'upload limit'),('queue',429,'busy')])
def test_each_upload_admission_gate(room,limit,code,fragment):
    c,app,cfg,store=room;unlock(c,'dancer@example.com');now=time.time()
    with app.state.db.connect(write=True) as con:
        uid=con.execute("SELECT id FROM users WHERE email='dancer@example.com'").fetchone()[0]
        if limit!='paused':con.execute("UPDATE flags SET value='0' WHERE key='uploads_paused'")
        if limit=='hold':con.execute('UPDATE users SET upload_hold=1 WHERE id=?',(uid,))
        if limit in ('active','day','month','site','bytes'):
            total={'active':1,'day':5,'month':20,'site':1,'bytes':1}[limit]
            if limit=='site':cfg.site_uploads_day=1
            if limit=='bytes':cfg.site_upload_bytes_day=100
            for i in range(total):
                con.execute('INSERT INTO projects(id,name,bytes,created,expires,upload_deadline,status,updated,owner) VALUES (?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex,'Previous',100,now-(90000 if limit=='month' else 10),now+10000,now+1000,'queued' if limit=='active' else 'deleted',now,uid if limit in ('active','day','month') else 'another'))
        if limit=='ip':
            from choreo.accounts import key_hash
            # TestClient has a non-IP peer, canonicalized to unknown by api.ip.
            import hmac
            hashed=hmac.new(cfg.secret_key.encode(),b'unknown','sha256').hexdigest()
            for _ in range(cfg.ip_uploads_day):con.execute('INSERT INTO upload_records(project,user_id,email,ip_hash,created) VALUES (?,?,?,?,?)',(uuid.uuid4().hex,'other','other@example.com',hashed,now))
        if limit=='queue':
            for i in range(cfg.analyze_queue_limit):con.execute("INSERT INTO jobs(id,project,kind,status,created,updated,payload) VALUES (?,?,'analyze','queued',?,?,?)",(uuid.uuid4().hex,'other'+str(i),now,now,'{}'))
    response=begin(c);assert response.status_code==code,response.text
    assert fragment in response.json()['detail']
    if code in (429,503):assert int(response.headers['retry-after'])>0



def test_export_admission_ignores_full_analysis_queue(room):
    c,app,cfg,store=room;unlock(c);pid=ready(c,app);now=time.time()
    with app.state.db.connect(write=True) as con:
        for i in range(cfg.analyze_queue_limit):
            con.execute("INSERT INTO jobs(id,project,kind,status,created,updated,payload) VALUES (?,?,'analyze','queued',?,?,?)",(uuid.uuid4().hex,pid,now+i,now,json.dumps({'i':i})))
    response=c.post(f'/api/projects/{pid}/exports',json={'revision':0,'speed':1})
    assert response.status_code==200,response.text
    assert response.json()['status']=='queued'
