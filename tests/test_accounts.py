import concurrent.futures, re, time, uuid
from urllib.parse import urlparse,parse_qs
import pytest
from fastapi.testclient import TestClient
from test_service import room,unlock,ready,begin
from choreo.accounts import normalize_email,create_session,digest,resolve_account,valid_claims
from choreo.db import Database
from choreo.config import Settings


def test_identity_aliases_are_mailbox_specific():
    assert normalize_email(' A.B+tag@googlemail.com ')[1]=='ab@gmail.com'
    assert normalize_email('a+b@example.com')[1]=='a+b@example.com'
    for email in ('','x','ü@example.com','x@y','a@sub.mailinator.com'):
        with pytest.raises(ValueError):normalize_email(email)


def test_link_get_is_safe_and_confirmation_is_one_use(room):
    c,app,*_=room
    c.post('/api/auth/email',json={'email':'admin@example.com','token':'fresh','terms':1})
    token=re.search('#t=([A-Za-z0-9_-]+)',c.messages[-1].get_body(preferencelist=('plain',)).get_content()).group(1)
    assert c.get('/auth/email#t='+token).status_code==200
    with app.state.db.connect() as db:assert db.execute('SELECT used FROM login_tokens').fetchone()[0] is None
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        codes=list(pool.map(lambda _:c.post('/api/auth/email/confirm',json={'token':token}).status_code,range(2)))
    assert sorted(codes)==[200,400]
    with app.state.db.connect() as db:assert db.execute('SELECT hash FROM sessions').fetchone()[0]!=c.cookies.get('__Host-choreo')


def test_signup_closed_preserves_existing_signin(room):
    c,app,cfg,*_=room;cfg.signup_cap=1
    n=len(c.messages)
    assert c.post('/api/auth/email',json={'email':'new@example.com','token':'new','terms':1}).status_code==200
    assert len(c.messages)==n
    unlock(c)
    assert c.get('/api/bootstrap').json()['user']['admin']


def test_cross_account_owner_routes_and_public_practice(room):
    c,app,cfg,store=room;unlock(c);pid=ready(c,app)
    assert c.get(f'/api/projects/{pid}/practice').json()['owner']
    c.post('/api/logout',json={});unlock(c,'other@example.com')
    assert not c.get('/api/projects').json()['projects']
    for method,path,body in [('get',f'/api/projects/{pid}',None),('delete',f'/api/projects/{pid}',None),('patch',f'/api/projects/{pid}',{'name':'stolen'}),('put',f'/api/projects/{pid}/alignment',{'revision':0,'anchor':0,'offset':0,'timingMode':'attack'}),('post',f'/api/uploads/{pid}/complete',{})]:
        assert c.request(method,path,json=body).status_code==404
    assert c.put(f'/api/uploads/{pid}/parts/1',content=b'x'*100).status_code==404
    assert c.get(f'/api/projects/{pid}/practice').json()['owner'] is False
    assert c.post(f'/api/projects/{pid}/exports',json={'revision':0,'speed':1}).status_code==200
    # A foreign upload ID must not leak the owner's status.
    assert begin(c,request_id=pid).status_code==409


def test_terms_and_account_delete_preserve_records_and_quarantine(room):
    c,app,*_=room;unlock(c,'other@example.com')
    with app.state.db.connect(write=True) as db:
        db.execute("UPDATE flags SET value='0' WHERE key='uploads_paused'")
    pid=ready(c,app)
    with app.state.db.connect(write=True) as db:
        db.execute("UPDATE projects SET status='quarantined',hold=1 WHERE id=?",(pid,))
        db.execute("UPDATE users SET terms_version=0 WHERE email='other@example.com'")
    assert begin(c).status_code==403
    assert c.post('/api/account/terms',json={'terms':1}).status_code==200
    assert c.request('DELETE','/api/account',json={'confirm':'other@example.com'}).status_code==200
    assert c.get('/api/projects').status_code==401
    with app.state.db.connect() as db:
        assert db.execute('SELECT status FROM projects WHERE id=?',(pid,)).fetchone()[0]=='quarantined'
        assert db.execute('SELECT count(*) FROM upload_records').fetchone()[0]==1


def test_session_cap_and_expiry(room):
    c,app,cfg,*_=room
    with app.state.db.connect(write=True) as db:
        uid=db.execute('SELECT id FROM users').fetchone()[0]
        tokens=[create_session(db,cfg,uid)[0] for _ in range(11)]
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==10
        assert db.execute('SELECT 1 FROM sessions WHERE hash=?',(digest(tokens[0]),)).fetchone() is None
        db.execute('UPDATE sessions SET expires=0')
    c.cookies.set('__Host-choreo',tokens[-1])
    assert c.get('/api/projects').status_code==401


def test_migration_preserves_projects_and_is_atomic(tmp_path):
    db=Database(tmp_path/'old.sqlite3')
    with db.connect(write=True) as con:
        con.execute("INSERT INTO projects(id,name,bytes,created,expires,upload_deadline,status,updated) VALUES ('p','Dance',100,1,99999999999,2,'ready',1)")
        con.execute("INSERT INTO sessions VALUES ('old','csrf',99999999999)")
    with pytest.raises(ValueError):db.migrate(Settings(admin_email='invalid'))
    with db.connect() as con:assert con.execute('PRAGMA user_version').fetchone()[0]==0
    cfg=Settings(admin_email='admin@example.com');db.migrate(cfg);db.migrate(cfg)
    with db.connect() as con:
        assert con.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
        assert con.execute('SELECT owner FROM projects').fetchone()[0]==con.execute('SELECT id FROM users').fetchone()[0]
        assert con.execute("SELECT value FROM flags WHERE key='uploads_paused'").fetchone()[0]=='1'


def test_google_claim_contract(room):
    _,_,cfg,*_=room;cfg.google_client_id='client'
    good={'sub':'stable-id','nonce':'nonce','email':'user@gmail.com','email_verified':True,'aud':'client','iss':'https://accounts.google.com','iat':time.time(),'exp':time.time()+300}
    assert valid_claims(good,cfg,'nonce')
    for key,value in [('aud','other'),('nonce','wrong'),('email_verified','true'),('iss','evil'),('exp',0),('iat',time.time()+1000),('sub','')]:
        assert not valid_claims({**good,key:value},cfg,'nonce')
    assert not valid_claims({**good,'aud':['client','other']},cfg,'nonce')


def test_admin_stepup_and_old_password_route(room):
    c,app,*_=room;unlock(c)
    assert c.post('/api/admin/unlock',json={'password':'bad','token':'admin-bad'}).status_code==401
    assert c.post('/api/admin/unlock',json={'password':'test-pass','token':'admin-ok'}).status_code==200
    assert c.get('/api/bootstrap').json()['user']['adminUnlocked']
    c.post('/api/logout',json={});unlock(c,'other@example.com')
    assert c.post('/api/admin/unlock',json={'password':'test-pass','token':'other'}).status_code==404
