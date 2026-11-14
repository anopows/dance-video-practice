"""Shared, transactional moderation actions for the web and host CLI."""
import json
import secrets
import time
from datetime import datetime,timezone
from pathlib import Path
from typing import Literal
from fastapi import HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field,field_validator
from starlette.concurrency import run_in_threadpool
from .accounts import is_admin,key_hash,normalize_email
from .quotas import STORED_BYTES_SQL
from .db import best_count,kpi_count
from . import kpi

CONFLICT='That action no longer applies. Reload.'


def audit(con,actor,action,target,detail=''):
    if actor!='system':kpi_count(con,kpi.bump,'admin.action')
    con.execute('INSERT INTO audit(at,actor,action,target,detail) VALUES (?,?,?,?,?)',(time.time(),actor,action,target,detail))


class Reason(BaseModel):
    reason:str=Field(min_length=1,max_length=500)
    @field_validator('reason')
    @classmethod
    def trimmed(cls,value):
        if not value.strip():raise ValueError('A reason is required.')
        return value.strip()

class ProjectAction(Reason):action:Literal['hold','restore','remove','quarantine']
class UserAction(Reason):action:Literal['ban','unban','release_hold']
class Flags(Reason):uploadsPaused:bool
class Dismiss(BaseModel):
    note:str=Field(min_length=1,max_length=500)
    @field_validator('note')
    @classmethod
    def trimmed(cls,value):
        if not value.strip():raise ValueError('A note is required.')
        return value.strip()
class Report(BaseModel):
    reason:Literal['illegal','copyright','privacy','other']
    details:str=Field(min_length=1,max_length=1500)
    contact:str=Field(default='',max_length=254)
    token:str=Field(min_length=1,max_length=2048)
    @field_validator('details')
    @classmethod
    def trimmed(cls,value):
        if not value.strip():raise ValueError('Describe the issue.')
        return value.strip()


class Moderation:
    def __init__(self,cfg,db,mail,legal):self.cfg,self.db,self.mail,self.legal=cfg,db,mail,legal

    def project_action(self,pid,action,reason,actor):
        now=time.time();notification=None
        with self.db.connect(write=True) as con:
            p=con.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
            if not p:raise HTTPException(404,'Video not found.')
            allowed=p['status']=='ready' and (action not in ('hold','restore') or bool(p['hold'])==(action=='restore'))
            if not allowed:raise HTTPException(409,CONFLICT)
            if action in ('hold','restore'):
                con.execute('UPDATE projects SET hold=?,updated=? WHERE id=?',(int(action=='hold'),now,pid))
            else:
                con.execute('UPDATE projects SET status=?,hold=?,cleanup_done=0,updated=? WHERE id=?',('removed' if action=='remove' else 'quarantined',int(action=='quarantine'),now,pid))
                con.execute("UPDATE jobs SET status='cancelled' WHERE project=? AND status='queued'",(pid,))
            if action in ('restore','remove','quarantine'):
                con.execute("UPDATE reports SET status=?,resolved=?,note=? WHERE project=? AND status='open'",('dismissed' if action=='restore' else 'actioned',now,reason,pid))
            audit(con,actor,action,pid,reason)
            kpi_count(con,kpi.bump,'moderation.'+action)
            if action=='remove':
                user=con.execute('SELECT email FROM users WHERE id=?',(p['owner'],)).fetchone()
                if user:notification=(user['email'],p['name'],p['created'])
        if notification:
            email,name,created=notification
            self.mail.notify('remove',pid,email,'A video was removed from your five·six account',f'We removed your video "{name}", uploaded on {datetime.fromtimestamp(created,timezone.utc).date()}.\n\nReason: {reason}\n\nThe video and its share link no longer work. Its files are queued for deletion.\nIf you believe this is a mistake, write to {self.cfg.abuse_email} within 30 days and we will review it.')

    def user_action(self,uid,action,reason,actor):
        now=time.time();notify=None
        with self.db.connect(write=True) as con:
            user=con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not user:raise HTTPException(404)
            expected=(action=='ban' and user['status']=='active' and not is_admin(self.cfg,user)) or (action=='unban' and user['status']=='banned') or (action=='release_hold' and user['upload_hold'])
            if not expected:raise HTTPException(409,CONFLICT)
            if action=='release_hold':con.execute('UPDATE users SET upload_hold=0,hold_reason=NULL WHERE id=?',(uid,))
            else:
                con.execute('UPDATE users SET status=? WHERE id=?',('banned' if action=='ban' else 'active',uid))
                for kind,value in [('email',user['email_key']),('google',user['google_sub'])]:
                    if not value:continue
                    hashed=key_hash(self.cfg,kind,value)
                    if action=='ban':con.execute('INSERT OR REPLACE INTO bans VALUES (?,?,?,?)',(hashed,kind,reason,now))
                    else:con.execute('DELETE FROM bans WHERE key_hash=?',(hashed,))
                if action=='ban':
                    con.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
                    con.execute('DELETE FROM login_tokens WHERE email_key=?',(user['email_key'],))
                    con.execute("UPDATE projects SET status='removed',hold=0,cleanup_done=0,updated=? WHERE owner=? AND status NOT IN ('deleted','expired','removed','quarantined')",(now,uid))
                    con.execute("UPDATE jobs SET status='cancelled' WHERE status='queued' AND project IN (SELECT id FROM projects WHERE owner=?)",(uid,))
                    con.execute("UPDATE reports SET status='actioned',resolved=?,note=? WHERE status='open' AND project IN (SELECT id FROM projects WHERE owner=? AND status='removed')",(now,reason,uid))
                    notify=user['email']
            audit(con,actor,action,uid,reason)
            kpi_count(con,kpi.bump,'moderation.'+action)
        if notify:self.mail.notify('ban',uid,notify,'Your five·six account was closed',f'We closed your five·six account and removed its videos.\n\nReason: {reason}\n\nIf you believe this is a mistake, write to {self.cfg.abuse_email} within 30 days and we will review it.')

    def flags(self,paused,reason,actor):
        if not paused and not self.legal.ready:raise HTTPException(409,'Final legal pages are required before uploads can open.')
        with self.db.connect(write=True) as con:
            con.executemany('INSERT OR REPLACE INTO flags VALUES (?,?,?)',[('uploads_paused','1' if paused else '0',time.time()),('uploads_paused_reason',reason,time.time())])
            audit(con,actor,'pause_uploads' if paused else 'resume_uploads','site',reason)

    def release_quarantine(self,pid,restore,actor='cli'):
        with self.db.connect(write=True) as con:
            p=con.execute("SELECT * FROM projects WHERE id=? AND status='quarantined'",(pid,)).fetchone()
            if not p or (restore and p['expires']<=time.time()):raise HTTPException(409,CONFLICT)
            con.execute('UPDATE projects SET status=?,hold=0,cleanup_done=0,updated=? WHERE id=?',('ready' if restore else 'removed',time.time(),pid))
            audit(con,actor,'release_quarantine',pid,'restore' if restore else 'delete')


def install_routes(app,cfg,db,store,legal,origin,session,ip,rate,verify):
    moderation=Moderation(cfg,db,app.state.mail,legal);app.state.moderation=moderation
    def admin(request,mutation=False):
        try:user=session(request,mutation)
        except HTTPException as exc:
            if exc.status_code==401:raise HTTPException(404)
            raise
        if not is_admin(cfg,user):raise HTTPException(404)
        if user['admin_until']<=time.time():raise HTTPException(403,'Admin unlock required.')
        return user

    @app.post('/api/projects/{pid}/reports')
    async def report(pid:str,body:Report,request:Request):
        origin(request)
        with db.connect(write=True) as con:
            rate(con,'report:ip:'+ip(request),5,3600,time.time())
            rate(con,'report:global',100,86400,time.time())
        contact=body.contact.strip()
        if contact:
            try:contact=normalize_email(contact,aliases=False,disposable=False)[0]
            except ValueError as exc:raise HTTPException(400,str(exc))
        elif body.reason in ('copyright','privacy'):raise HTTPException(400,'Your email address is required for this report.')
        await verify(body.token,'report')
        rid=secrets.token_hex(16)
        with db.connect(write=True) as con:
            p=con.execute("SELECT * FROM projects WHERE id=? AND status='ready' AND expires>?",(pid,time.time())).fetchone()
            if not p:raise HTTPException(404,'Video not found.')
            con.execute('INSERT INTO reports(id,project,reason,details,contact,ip_hash,created) VALUES (?,?,?,?,?,?,?)',(rid,pid,body.reason,body.details,contact or None,ip(request),time.time()))
            if body.reason=='illegal' and not p['hold']:
                con.execute('UPDATE projects SET hold=1 WHERE id=?',(pid,))
                audit(con,'system','hold',pid,'report:'+rid)
            kpi_count(con,kpi.bump,'report.created')
        await run_in_threadpool(app.state.mail.notify,'report',pid,cfg.abuse_email,f'five·six report: {body.reason} on {pid}',f'Report: {rid}\nReason: {body.reason}\nDetails: {body.details}\nContact: {contact}\n{cfg.origin}/admin')
        return {'ok':True}

    @app.get('/admin')
    def page():return FileResponse(Path(__file__).parent/'static/admin.html')

    @app.get('/api/admin/overview')
    def overview(request:Request):
        admin(request)
        with db.connect() as con:
            users={'total':con.execute('SELECT count(*) FROM users').fetchone()[0],'banned':con.execute("SELECT count(*) FROM users WHERE status='banned'").fetchone()[0],'uploadHold':con.execute('SELECT count(*) FROM users WHERE upload_hold=1').fetchone()[0]}
            videos={s:con.execute('SELECT count(*) FROM projects WHERE status=?',(s,)).fetchone()[0] for s in ('ready','quarantined','queued','processing')}
            videos['held']=con.execute("SELECT count(*) FROM projects WHERE status='ready' AND hold=1").fetchone()[0]
            flags=dict(con.execute('SELECT key,value FROM flags'))
            stored=con.execute(STORED_BYTES_SQL).fetchone()[0]
            reports=con.execute("SELECT count(*) FROM reports WHERE status='open'").fetchone()[0]
        status=store.guard.status()
        guard={'period':status.get('period',''),'stopped':status['stopped']}
        for key,label in [('operations','operations'),('upload_bytes','uploadBytes'),('media_bytes','mediaBytes')]:guard[label]={'used':status['used_'+key],'limit':status[key]}
        return {'users':users,'signupCap':cfg.signup_cap,'videos':videos,'openReports':reports,'storedBytes':stored,'storageCapBytes':cfg.storage_cap_bytes,'guard':guard,'uploadsPaused':flags.get('uploads_paused','1')=='1','uploadsPausedReason':flags.get('uploads_paused_reason','')}

    @app.get('/api/admin/reports')
    def reports(request:Request,status:Literal['open','actioned','dismissed']='open'):
        admin(request)
        with db.connect() as con:
            rows=con.execute('SELECT r.id,r.project,r.reason,r.details,r.contact,r.created,r.status,r.note,p.status AS videoStatus,p.hold AS videoHold,u.email AS ownerEmail FROM reports r LEFT JOIN projects p ON p.id=r.project LEFT JOIN users u ON u.id=p.owner WHERE r.status=? ORDER BY r.created DESC LIMIT 100',(status,)).fetchall()
        return {'reports':[dict(row) for row in rows]}

    @app.get('/api/admin/projects')
    def projects(request:Request,filter:Literal['recent','held','quarantined']='recent'):
        admin(request)
        where={'recent':"p.status!='quarantined'",'held':"p.status='ready' AND p.hold=1",'quarantined':"p.status='quarantined'"}[filter]
        with db.connect() as con:rows=con.execute('SELECT p.id,p.name,p.owner AS ownerId,u.email AS ownerEmail,p.status,p.hold,p.created,p.bytes,p.stored_bytes AS storedBytes,p.reject_code AS rejectCode,p.screen,p.expires FROM projects p LEFT JOIN users u ON u.id=p.owner WHERE '+where+' ORDER BY p.created DESC LIMIT 100').fetchall()
        result=[]
        for row in rows:
            item=dict(row);expires=item.pop('expires');item['hold']=bool(item['hold']);item['screen']=json.loads(item['screen']) if item['screen'] else None
            if item['status']=='ready' and expires>time.time():item['posterUrl']=store.url(f"projects/{item['id']}/poster.jpg",min(900,int(expires-time.time())))
            result.append(item)
        return {'projects':result}

    @app.get('/api/admin/users')
    def users(request:Request,filter:Literal['recent','hold','banned']='recent'):
        admin(request)
        where={'recent':'1','hold':'u.upload_hold=1','banned':"u.status='banned'"}[filter]
        with db.connect() as con:
            rows=con.execute("SELECT u.id,u.email,u.status,u.upload_hold AS uploadHold,u.hold_reason AS holdReason,u.created,u.last_login AS lastLogin,(SELECT count(*) FROM projects p WHERE p.owner=u.id AND p.created>?) AS uploads30d,(SELECT count(*) FROM projects p WHERE p.owner=u.id AND p.created>? AND p.reject_code='nsfw') AS nsfwRejections30d FROM users u WHERE "+where+' ORDER BY u.created DESC LIMIT 100',(time.time()-30*86400,time.time()-30*86400)).fetchall()
        return {'users':[dict(row) for row in rows]}

    @app.post('/api/admin/projects/{pid}/action')
    def project_action(pid:str,body:ProjectAction,request:Request):
        user=admin(request,True);moderation.project_action(pid,body.action,body.reason,user['user_id']);return {'ok':True}

    @app.post('/api/admin/users/{uid}/action')
    def user_action(uid:str,body:UserAction,request:Request):
        user=admin(request,True);moderation.user_action(uid,body.action,body.reason,user['user_id']);return {'ok':True}

    @app.post('/api/admin/reports/{rid}/dismiss')
    def dismiss(rid:str,body:Dismiss,request:Request):
        user=admin(request,True)
        with db.connect(write=True) as con:
            if not con.execute("UPDATE reports SET status='dismissed',note=?,resolved=? WHERE id=? AND status='open'",(body.note,time.time(),rid)).rowcount:raise HTTPException(409,CONFLICT)
            audit(con,user['user_id'],'dismiss',rid,body.note)
        return {'ok':True}

    @app.post('/api/admin/flags')
    def flags(body:Flags,request:Request):
        user=admin(request,True);moderation.flags(body.uploadsPaused,body.reason,user['user_id']);return {'ok':True}
