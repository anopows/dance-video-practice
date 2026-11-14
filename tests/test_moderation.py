import json,time,uuid
import pytest
from fastapi import HTTPException
from test_service import room,unlock,ready,begin
from choreo.worker import Worker


def stepup(c):
    r=c.post('/api/admin/unlock',json={'password':'test-pass','token':uuid.uuid4().hex});assert r.status_code==200,r.text


def report(c,pid,reason='illegal',contact=''):
    return c.post(f'/api/projects/{pid}/reports',json={'reason':reason,'details':'Please investigate this video.','contact':contact,'token':uuid.uuid4().hex})


def test_illegal_report_hold_revokes_all_routes_but_unlocked_admin(room):
    c,app,cfg,store=room;unlock(c);pid=ready(c,app)
    key=f'projects/{pid}/practice.mp4';store.client.put_object(Bucket=store.bucket,Key=key,Body=b'video')
    url=store.url(key,900)
    jid=c.post(f'/api/projects/{pid}/exports',json={'revision':0,'speed':1}).json()['id']
    assert report(c,pid).status_code==200
    for path in [f'/api/projects/{pid}/practice',f'/api/projects/{pid}/exports/{jid}',url]:assert c.get(path).status_code==403
    assert c.post(f'/api/projects/{pid}/exports',json={'revision':0,'speed':1}).status_code==403
    assert c.get('/api/projects').json()['projects'][0]['hold']
    assert report(c,pid).status_code==200
    assert len([m for m in c.messages if m['Subject'].startswith('five·six report:')])==1
    assert c.get('/api/admin/overview').status_code==403
    stepup(c)
    assert c.get(f'/api/projects/{pid}/practice').status_code==200
    assert c.get(url).status_code==200
    assert c.get('/api/admin/overview').json()['openReports']==2
    assert c.get('/api/admin/reports').json()['reports'][0]['videoHold']==1
    assert c.get('/api/admin/users').status_code==200
    assert c.get('/api/admin/projects').status_code==200
    with app.state.db.connect(write=True) as db:db.execute('UPDATE sessions SET admin_until=0')
    assert c.get('/api/admin/overview').status_code==403


def test_copyright_contact_and_admin_actions(room):
    c,app,cfg,store=room;unlock(c);pid=ready(c,app);stepup(c)
    assert report(c,pid,'copyright').status_code==400
    assert report(c,pid,'copyright','rights@example.com').status_code==200
    assert not c.get('/api/projects').json()['projects'][0]['hold']
    url=f'/api/admin/projects/{pid}/action'
    for action,expected in [('restore',409),('hold',200),('hold',409),('restore',200),('remove',200),('remove',409)]:
        assert c.post(url,json={'action':action,'reason':'Reviewed by operator'}).status_code==expected
    with app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM audit WHERE target=?',(pid,)).fetchone()[0]==3
        assert con.execute('SELECT status FROM reports').fetchone()[0]=='dismissed'
    assert any('queued for deletion' in m.get_body(preferencelist=('plain',)).get_content() for m in c.messages)
    Worker(cfg,store).cleanup()
    assert not store.inventory(pid)


def test_quarantine_survives_expiry_and_is_unreachable(room):
    c,app,cfg,store=room;unlock(c);pid=ready(c,app);stepup(c)
    assert c.post(f'/api/admin/projects/{pid}/action',json={'action':'quarantine','reason':'Preservation pending procedure'}).status_code==200
    assert c.get(f'/api/projects/{pid}/practice').status_code==404
    assert c.get(f'/api/projects/{pid}').status_code==404
    with app.state.db.connect(write=True) as db:db.execute('UPDATE projects SET expires=0 WHERE id=?',(pid,))
    Worker(cfg,store).cleanup();assert store.inventory(pid)
    with pytest.raises(HTTPException):app.state.moderation.release_quarantine(pid,True)
    with app.state.db.connect(write=True) as db:db.execute('UPDATE projects SET expires=? WHERE id=?',(time.time()+100,pid))
    app.state.moderation.release_quarantine(pid,True)
    assert c.get(f'/api/projects/{pid}/practice').status_code==200
    app.state.moderation.project_action(pid,'quarantine','preserve','cli')
    app.state.moderation.release_quarantine(pid,False)
    Worker(cfg,store).cleanup();assert not store.inventory(pid)


def test_ban_revokes_identity_and_keeps_quarantine(room):
    c,app,cfg,store=room;unlock(c,'other@example.com')
    with app.state.db.connect(write=True) as db:db.execute("UPDATE flags SET value='0' WHERE key='uploads_paused'")
    pid=ready(c,app)
    with app.state.db.connect() as db:uid=db.execute("SELECT id FROM users WHERE email='other@example.com'").fetchone()[0]
    app.state.moderation.user_action(uid,'ban','Repeated misuse','cli')
    assert c.get('/api/projects').status_code==401
    assert c.get(f'/api/projects/{pid}/practice').status_code==404
    count=len(c.messages)
    assert c.post('/api/auth/email',json={'email':'other@example.com','terms':1,'token':'banned'}).status_code==200
    assert len(c.messages)==count
    app.state.moderation.user_action(uid,'unban','Review complete','cli')
    unlock(c,'other@example.com')
    assert c.get('/api/projects').json()['projects']==[]


def test_legal_gate_blocks_resume_and_new_signup(room):
    c,app,cfg,*_=room;unlock(c);stepup(c)
    (cfg.legal_dir/'terms.html').write_text('{{PENDING}}');app.state.legal._until=0
    assert not c.get('/api/bootstrap').json()['signupsOpen']
    assert c.post('/api/admin/flags',json={'uploadsPaused':False,'reason':'Open'}).status_code==409
    count=len(c.messages)
    c.post('/api/auth/email',json={'email':'new@example.com','terms':1,'token':'closed'})
    assert len(c.messages)==count
