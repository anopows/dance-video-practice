import sqlite3, time
import pytest
from test_service import room
from choreo.backup import backup
from choreo.r2_guard import R2Guard

def test_legal_pages_and_indexing(room, tmp_path):
    c, app, cfg, _ = room
    app.state.legal.directory=tmp_path/'legal'; app.state.legal.directory.mkdir(exist_ok=True)
    for old in app.state.legal.directory.glob('*.html'):old.unlink()
    app.state.legal._until=0
    assert c.get('/terms').status_code==404
    assert not c.get('/api/bootstrap').json()['legalReady']
    for name in ('terms','privacy','legal'):
        (app.state.legal.directory/(name+'.html')).write_text('<html><div id="report">{{OPERATOR}}</div></html>')
    app.state.legal._until=0
    assert c.get('/privacy').status_code==404
    for name in ('terms','privacy','legal'):
        (app.state.legal.directory/(name+'.html')).write_text('<html><div id="report">Test contact</div></html>')
    app.state.legal._until=0
    assert c.get('/api/bootstrap').json()['legalReady']
    assert c.get('/privacy').headers['x-robots-tag']=='index, follow'
    assert '/privacy' in c.get('/sitemap.xml').text

def test_health_is_local_and_tracks_worker_and_guard(room, tmp_path):
    c, app, cfg, store = room
    # Guard is initialized by the shared fixture.
    assert c.get('/healthz').status_code==503
    with app.state.db.connect(write=True) as con:
        con.execute('INSERT OR REPLACE INTO flags VALUES (?,?,?)',('worker_heartbeat',str(time.time()),time.time()))
    assert c.get('/healthz').json()['ok']
    store.guard.stop('test')
    assert c.get('/healthz').status_code==503
    assert c.get('/healthz').json()['guardStopped']

def test_online_backup_restores_both_databases(room, tmp_path):
    _,app,cfg,_=room
    # Guard is initialized by the shared fixture.
    with app.state.db.connect(write=True) as con:
        con.execute('INSERT INTO flags VALUES (?,?,?)',('test','retained',time.time()))
    backup(tmp_path/'copies',cfg.state_dir)
    for name in ('room.sqlite3','r2-guard.sqlite3'):
        with sqlite3.connect(tmp_path/'copies'/name) as con:
            assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert (tmp_path/'copies'/name).stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):backup(tmp_path/'copies',cfg.state_dir)
