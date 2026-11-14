import json, sys, time
from test_service import room, unlock, ready
from choreo import kpi

def totals(app):
    with app.state.db.connect() as c: return dict(c.execute('SELECT metric,sum(value) FROM kpi_daily GROUP BY metric'))

def test_login_upload_practice_export(room, tmp_path, monkeypatch):
    c,app,cfg,store=room
    unlock(c);pid=ready(c,app)
    for _ in range(2): assert c.get(f'/api/projects/{pid}/practice').status_code==200
    for _ in range(2): assert c.post(f'/api/projects/{pid}/exports',json={'revision':0,'speed':1}).status_code==200
    got=totals(app)
    assert all(got[k]==1 for k in ('signin.email.requested','signin.email.ok','upload.reserved','upload.completed','uniq.practice','export.requested','export.reused'))
    assert got['practice.opened']==2 and got['upload.bytes']==100
    from choreo import kpi_export
    monkeypatch.setenv('CHOREO_STATE_DIR',str(cfg.state_dir))
    monkeypatch.setattr(sys,'argv',['kpi_export',str(tmp_path/'stats.json')]); kpi_export.main()
    payload=json.loads((tmp_path/'stats.json').read_text())
    assert payload['site']=='fivesix' and payload['schema']==1 and payload['gauges']['stored_bytes']==100

def test_instrumentation_failure_does_not_break_login(room, monkeypatch):
    c,app,*_=room
    monkeypatch.setattr(kpi,'bump',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('failed')))
    unlock(c)

def test_worker_ready_and_retry_failure(room, monkeypatch):
    from choreo import worker
    c,app,cfg,storage=room;unlock(c)
    from test_service import upload_small
    pid=upload_small(c);w=worker.Worker(cfg,storage)
    monkeypatch.setattr(storage,'download',lambda key,path,size:size)
    monkeypatch.setattr(storage,'upload',lambda *args:None)
    class Success:
        returncode=0
        def __init__(self,*args,**kwargs):
            (w.jobs/job['id']/'analysis.json').write_text('{}')
            (w.jobs/job['id']/'screen.json').write_text('{"version":1,"result":"pass"}')
            for name in ('practice.mp4','poster.jpg','bass.mp4','drums.mp4','piano.mp4','guitar.mp4'):(w.jobs/job['id']/name).write_bytes(b'output')
        def poll(self): return 0
    job=w.claim(); monkeypatch.setattr(worker.subprocess,'Popen',Success);w.process(job)
    got=totals(app);assert got['job.analyze.ready']==1
    assert sum(v for k,v in got.items() if k.startswith('queue_s.le.'))==1
    assert sum(v for k,v in got.items() if k.startswith('analyze_s.le.'))==1
    with app.state.db.connect(write=True) as con:
        now=time.time();con.execute("INSERT INTO jobs(id,project,kind,status,created,updated,payload) VALUES (?,?, 'export','queued',?,?,?)",('e'*32,pid,now,now,'{}'))
    monkeypatch.setattr(storage,'download',lambda *args: (_ for _ in ()).throw(RuntimeError('storage unavailable')))
    w.process(w.claim());w.process(w.claim())
    got=totals(app);assert got['job.export.retried']==1 and got['job.export.failed']==1


def test_refresh_does_not_count_as_practice(room):
    c,app,*_=room;unlock(c);pid=ready(c,app)
    assert c.get(f'/api/projects/{pid}/practice').status_code==200
    for _ in range(2): assert c.get(f'/api/projects/{pid}/practice?refresh=1').status_code==200
    got=totals(app);assert got['practice.opened']==1 and got['uniq.practice']==1

def test_crash_recovery_and_cleanup_prune(room):
    from choreo.worker import Worker
    from test_service import upload_small
    c,app,cfg,storage=room;unlock(c);pid=upload_small(c);w=Worker(cfg,storage)
    with app.state.db.connect(write=True) as con:
        con.execute("INSERT INTO kpi_seen VALUES(?,?,?)",(kpi.day_at(time.time()-86400),'uniq.practice','old'))
        con.execute("UPDATE jobs SET status='processing',attempt=1 WHERE project=?",(pid,))
        con.execute("UPDATE projects SET status='processing' WHERE id=?",(pid,))
    w.recover();assert totals(app)['job.analyze.retried']==1
    assert w.claim() is not None
    assert not any(k.startswith('queue_s.le.') for k in totals(app))
    w.recover();assert totals(app)['job.analyze.failed']==1
    w.cleanup()
    with app.state.db.connect() as con: assert con.execute("SELECT 1 FROM kpi_seen WHERE hash='old'").fetchone() is None

def test_exporter_new_gauges(room, tmp_path, monkeypatch):
    c,app,cfg,storage=room;unlock(c);ready(c,app)
    from choreo import kpi_export
    monkeypatch.setenv('CHOREO_STATE_DIR',str(cfg.state_dir));monkeypatch.setattr(sys,'argv',['kpi_export',str(tmp_path/'stats.json')]);kpi_export.main()
    g=json.loads((tmp_path/'stats.json').read_text())['gauges']
    assert g['projects_ready']==1 and g['queue_limit']==4
