"""Account identity, one-use sign-in links and server-side Google OIDC."""
import base64
import hashlib
import hmac
import re
import secrets
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import config, kpi
from .db import kpi_count, best_count
from .mail import Mailer

COOKIE = "__Host-choreo"
OAUTH_COOKIE = "__Host-choreo-oauth"
TOKEN_SECONDS = 900
OAUTH_SECONDS = 600
ADMIN_SECONDS = 3600
MAX_SESSIONS = 10
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]{1,64}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@lru_cache
def disposable_domains():
    text = (Path(__file__).parent / "data/disposable_domains.txt").read_text()
    return frozenset(line.strip().lower() for line in text.splitlines() if line.strip() and not line.startswith('#'))


def normalize_email(value, *, aliases=True, disposable=True):
    if not isinstance(value, str):
        raise ValueError("Enter a valid email address.")
    email = value.strip(" \t\r\n\v\f").lower()
    if len(email) > 254 or not email.isascii() or not EMAIL_RE.fullmatch(email):
        raise ValueError("Enter a valid email address.")
    local, domain = email.split('@')
    if disposable:
        pieces = domain.split('.')
        if any('.'.join(pieces[i:]) in disposable_domains() for i in range(len(pieces)-1)):
            raise ValueError("Use a permanent email address.")
    # Only Google documents these aliases as the same mailbox. Other domains
    # may deliver a+b and a to different people; merging them would grant access.
    if aliases and domain in ('gmail.com', 'googlemail.com'):
        local, domain = local.split('+', 1)[0].replace('.', ''), 'gmail.com'
    if not local:
        raise ValueError("Enter a valid email address.")
    return email, local + '@' + domain


def key_hash(cfg, kind, value):
    return hmac.new(cfg.secret_key.encode(), (kind + ':' + value).encode(), 'sha256').hexdigest()


def is_admin(cfg, user):
    return bool(user and user['email_key'] == normalize_email(cfg.admin_email)[1])


def resolve_account(con, cfg, legal, email, email_key, google_sub, terms):
    now = time.time()
    for kind, value in [('email', email_key), ('google', google_sub)]:
        if value and con.execute('SELECT 1 FROM bans WHERE key_hash=?', (key_hash(cfg,kind,value),)).fetchone():
            return None, 'banned'
    user = con.execute('SELECT * FROM users WHERE google_sub=?', (google_sub,)).fetchone() if google_sub else None
    if user is None:
        user = con.execute('SELECT * FROM users WHERE email_key=?', (email_key,)).fetchone()
        if user and google_sub:
            if user['google_sub'] and user['google_sub'] != google_sub:
                return None, 'conflict'
            con.execute('UPDATE users SET google_sub=? WHERE id=?', (google_sub,user['id']))
    if user is None:
        if not legal.ready or con.execute('SELECT count(*) FROM users').fetchone()[0] >= cfg.signup_cap:
            kpi_count(con,kpi.bump,'signup.closed')
            return None, 'closed'
        if terms != config.TERMS_VERSION:
            return None, 'terms'
        uid = secrets.token_hex(16)
        con.execute('INSERT INTO users(id,email,email_key,google_sub,terms_version,created,last_login) VALUES (?,?,?,?,?,?,?)',
                    (uid,email,email_key,google_sub,terms,now,now))
        user = con.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        kpi_count(con,kpi.bump,'signup.new')
    if user['status'] == 'banned':
        return None, 'banned'
    con.execute('UPDATE users SET last_login=? WHERE id=?', (now,user['id']))
    return user, None


def create_session(con, cfg, uid):
    now = time.time()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    con.execute('DELETE FROM sessions WHERE expires<=?', (now,))
    con.execute('INSERT INTO sessions(hash,csrf,user_id,created,expires) VALUES (?,?,?,?,?)',
                (digest(token),csrf,uid,now,now+cfg.session_seconds))
    con.execute('DELETE FROM sessions WHERE hash IN (SELECT hash FROM sessions WHERE user_id=? ORDER BY created DESC,rowid DESC LIMIT -1 OFFSET ?)', (uid,MAX_SESSIONS))
    return token, csrf


def set_session(response, cfg, token):
    response.set_cookie(COOKIE,token,max_age=cfg.session_seconds,secure=cfg.cookie_secure,httponly=True,samesite='strict',path='/')


class Google:
    def __init__(self,cfg):
        import jwt
        self.cfg = cfg
        self.keys = jwt.PyJWKClient('https://www.googleapis.com/oauth2/v3/certs',lifespan=3600,timeout=10)

    async def exchange(self,code,verifier):
        async with httpx.AsyncClient(timeout=10) as client:
            result = await client.post('https://oauth2.googleapis.com/token',data={
                'grant_type':'authorization_code','code':code,'client_id':self.cfg.google_client_id,
                'client_secret':self.cfg.google_client_secret,'redirect_uri':self.cfg.origin+'/auth/google/callback',
                'code_verifier':verifier})
            result.raise_for_status()
            token = result.json()['id_token']
        def decode():
            import jwt
            key=self.keys.get_signing_key_from_jwt(token).key
            return jwt.decode(token,key,algorithms=['RS256'],audience=self.cfg.google_client_id,
                              issuer=['https://accounts.google.com','accounts.google.com'],leeway=60,
                              options={'require':['exp','iat','sub','nonce','email','email_verified']})
        return await run_in_threadpool(decode)


def valid_claims(claims,cfg,nonce):
    now=time.time()
    aud=claims.get('aud')
    audiences=aud if isinstance(aud,list) else [aud]
    return (cfg.google_client_id in audiences and
            (len(audiences)==1 or claims.get('azp')==cfg.google_client_id) and
            (not claims.get('azp') or claims['azp']==cfg.google_client_id) and
            claims.get('iss') in ('https://accounts.google.com','accounts.google.com') and
            claims.get('email_verified') is True and
            isinstance(claims.get('sub'),str) and 0<len(claims['sub'])<=255 and claims['sub'].isascii() and
            isinstance(claims.get('nonce'),str) and hmac.compare_digest(claims['nonce'].encode(),nonce.encode()) and
            type(claims.get('exp')) in (int,float) and now-60<claims['exp'] and
            type(claims.get('iat')) in (int,float) and claims['iat']<=now+60)


class EmailRequest(BaseModel):
    email: str = Field(max_length=300)
    token: str = Field(min_length=1,max_length=2048)
    terms: int


class Confirmation(BaseModel):
    token: str = Field(min_length=1,max_length=128)


class Terms(BaseModel):
    terms: int


class DeleteAccount(BaseModel):
    confirm: str = Field(max_length=254)


class AdminUnlock(BaseModel):
    password: str = Field(max_length=128)
    token: str = Field(min_length=1,max_length=2048)


def install_routes(app,cfg,db,legal,origin,session,ip,rate,verify,*,mailer=None,google=None):
    mail = Mailer(cfg,db,mailer)
    app.state.mail = mail
    provider = google or (Google(cfg) if cfg.google_client_id else None)

    def email_limits(address_key,request):
        now=time.time()
        message='Too many sign-in emails. Wait a while and try again.'
        with db.connect(write=True) as con:
            rate(con,'email:ip:'+ip(request),5,3600,now,message)
            hashed=key_hash(cfg,'email',address_key)
            rate(con,'email:key:h:'+hashed,3,3600,now,message)
            rate(con,'email:key:d:'+hashed,6,86400,now,message)
            rate(con,'email:global',cfg.email_daily_cap,86400,now,message)

    async def send_link(email,email_key,terms,google_sub=None):
        now=time.time()
        token=secrets.token_urlsafe(32)
        with db.connect(write=True) as con:
            banned=con.execute('SELECT 1 FROM bans WHERE key_hash=?',(key_hash(cfg,'email',email_key),)).fetchone()
            existing=con.execute('SELECT 1 FROM users WHERE email_key=?',(email_key,)).fetchone()
            if banned or (not existing and (not legal.ready or con.execute('SELECT count(*) FROM users').fetchone()[0]>=cfg.signup_cap)):
                return
            con.execute('INSERT INTO login_tokens(hash,email,email_key,terms,created,expires,google_sub) VALUES (?,?,?,?,?,?,?)',
                        (digest(token),email,email_key,terms,now,now+TOKEN_SECONDS,google_sub))
            kpi_count(con,kpi.bump,'signin.email.requested')
        try:
            await run_in_threadpool(mail.signin,email,token)
        except Exception:
            with db.connect(write=True) as con:
                con.execute('DELETE FROM login_tokens WHERE hash=?',(digest(token),))
            raise HTTPException(503,"We couldn't send the email. Try again in a minute.")

    @app.post('/api/auth/email')
    async def email_signin(body:EmailRequest,request:Request):
        origin(request)
        if not cfg.smtp_host:raise HTTPException(404)
        # Record invalid-address attempts in the IP bucket as well.
        try: email,key=normalize_email(body.email)
        except ValueError as exc:
            with db.connect(write=True) as con:rate(con,'email:ip:'+ip(request),5,3600,time.time())
            raise HTTPException(400,str(exc))
        email_limits(key,request)
        await verify(body.token,'signin')
        await send_link(email,key,body.terms)
        return {'ok':True}

    @app.get('/auth/email')
    def email_page():
        return FileResponse(Path(__file__).parent/'static/signin-confirm.html')

    @app.post('/api/auth/email/confirm')
    def confirm(body:Confirmation,request:Request,response:Response):
        origin(request)
        with db.connect(write=True) as con:rate(con,'confirm:'+ip(request),20,3600,time.time())
        with db.connect(write=True) as con:
            row=con.execute('SELECT * FROM login_tokens WHERE hash=? AND used IS NULL AND expires>?',(digest(body.token),time.time())).fetchone()
            if row:
                con.execute('UPDATE login_tokens SET used=? WHERE hash=?',(time.time(),row['hash']))
                user,refusal=resolve_account(con,cfg,legal,row['email'],row['email_key'],row['google_sub'],row['terms'])
                if not refusal:
                    token,csrf=create_session(con,cfg,user['id'])
                    kpi_count(con,kpi.bump,'signin.email.ok')
                    kpi_count(con,kpi.bump,'login.ok')
        # Commit token consumption even when resolving an account is refused.
        if not row:raise HTTPException(400,'This sign-in link has expired or was already used. Ask for a new one.')
        if refusal:
            best_count(db,kpi.bump,'signin.failed')
            if refusal=='terms':raise HTTPException(409,'The terms changed. Ask for a new sign-in link.')
            raise HTTPException(403,"five·six isn't taking new sign-ups right now.")
        set_session(response,cfg,token)
        return {'csrf':csrf}

    @app.get('/auth/google/start')
    def google_start(request:Request,terms:int=0):
        if not provider:raise HTTPException(404)
        now=time.time()
        state,nonce,verifier=secrets.token_urlsafe(32),secrets.token_urlsafe(32),secrets.token_urlsafe(64)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        with db.connect(write=True) as con:
            rate(con,'gstart:'+ip(request),20,3600,now)
            con.execute('DELETE FROM oauth_states WHERE created<?',(now-OAUTH_SECONDS,))
            con.execute('INSERT INTO oauth_states VALUES (?,?,?,?,?)',(digest(state),nonce,verifier,terms,now))
        url='https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({
            'client_id':cfg.google_client_id,'redirect_uri':cfg.origin+'/auth/google/callback',
            'response_type':'code','scope':'openid email','state':state,'nonce':nonce,
            'code_challenge':challenge,'code_challenge_method':'S256','prompt':'select_account'})
        response=RedirectResponse(url,status_code=302)
        response.set_cookie(OAUTH_COOKIE,state,max_age=OAUTH_SECONDS,httponly=True,secure=cfg.cookie_secure,samesite='lax',path='/')
        return response

    def google_return(code='',token=None):
        response=RedirectResponse('/?signin='+code if code else '/',status_code=303)
        response.delete_cookie(OAUTH_COOKIE,path='/',secure=cfg.cookie_secure,httponly=True,samesite='lax')
        if token:set_session(response,cfg,token)
        if code:best_count(db,kpi.bump,'signin.failed')
        return response

    @app.get('/auth/google/callback')
    async def google_callback(request:Request):
        query=request.query_params
        if not provider:return google_return('unavailable')
        if query.get('error') or not query.get('code') or not query.get('state'):return google_return('cancelled')
        state=query['state']
        cookie=request.cookies.get(OAUTH_COOKIE,'')
        if len(state)>128 or not cookie or not hmac.compare_digest(state.encode(),cookie.encode()):return google_return('expired')
        with db.connect(write=True) as con:
            row=con.execute('SELECT * FROM oauth_states WHERE hash=? AND created>?',(digest(state),time.time()-OAUTH_SECONDS)).fetchone()
            con.execute('DELETE FROM oauth_states WHERE hash=?',(digest(state),))
        if not row:return google_return('expired')
        try:claims=await provider.exchange(query['code'],row['verifier'])
        except Exception:return google_return('unavailable')
        try:
            if not valid_claims(claims,cfg,row['nonce']):return google_return('failed')
            email,key=normalize_email(claims['email'])
        except (ValueError,KeyError,TypeError):return google_return('failed')
        # A previously verified third-party Google address can have changed hands.
        # Confirm the current mailbox before first linking such a Google subject.
        authoritative=email.endswith('@gmail.com') or bool(claims.get('hd'))
        with db.connect() as con:
            linked=con.execute('SELECT 1 FROM users WHERE google_sub=?',(claims['sub'],)).fetchone()
        if not authoritative and not linked:
            if not cfg.smtp_host:return google_return('failed')
            try:
                email_limits(key,request)
                await send_link(email,key,row['terms'],claims['sub'])
            except HTTPException:return google_return('unavailable')
            return google_return('verify_email')
        with db.connect(write=True) as con:
            user,refusal=resolve_account(con,cfg,legal,email,key,claims['sub'],row['terms'])
            if not refusal:
                token,_=create_session(con,cfg,user['id'])
                kpi_count(con,kpi.bump,'signin.google.ok')
                kpi_count(con,kpi.bump,'login.ok')
        return google_return({'banned':'failed'}.get(refusal,refusal)) if refusal else google_return(token=token)

    @app.post('/api/account/terms')
    def accept_terms(body:Terms,request:Request):
        s=session(request,True)
        if body.terms!=config.TERMS_VERSION or not legal.ready:raise HTTPException(409,'The terms changed. Reload the page.')
        with db.connect(write=True) as con:con.execute('UPDATE users SET terms_version=? WHERE id=?',(body.terms,s['user_id']))
        return {'ok':True}

    @app.delete('/api/account')
    def delete_account(body:DeleteAccount,request:Request,response:Response):
        s=session(request,True)
        if is_admin(cfg,s):raise HTTPException(400,'The admin account cannot be deleted here.')
        if body.confirm!=s['email']:raise HTTPException(400,'Type your email address to confirm.')
        with db.connect(write=True) as con:
            # Preservation holds survive account deletion.
            con.execute("UPDATE projects SET status='deleted',cleanup_done=0,updated=? WHERE owner=? AND status NOT IN ('deleted','expired','removed','quarantined')",(time.time(),s['user_id']))
            con.execute("UPDATE jobs SET status='cancelled' WHERE status='queued' AND project IN (SELECT id FROM projects WHERE owner=?)",(s['user_id'],))
            con.execute('DELETE FROM login_tokens WHERE email_key=?',(s['email_key'],))
            con.execute('DELETE FROM users WHERE id=?',(s['user_id'],))
        response.delete_cookie(COOKIE,path='/',secure=cfg.cookie_secure,httponly=True,samesite='strict')
        return {'ok':True}

    @app.post('/api/admin/unlock')
    async def admin_unlock(body:AdminUnlock,request:Request):
        try:s=session(request,True)
        except HTTPException as exc:
            if exc.status_code==401:raise HTTPException(404)
            raise
        if not is_admin(cfg,s):raise HTTPException(404)
        with db.connect(write=True) as con:
            rate(con,'login:'+ip(request),5,900,time.time(),'Too many tries.')
            rate(con,'login:global',30,900,time.time(),'Too many tries.')
        await verify(body.token,'admin')
        try:await run_in_threadpool(PasswordHasher().verify,cfg.password_hash,body.password)
        except VerificationError:raise HTTPException(401,"That password didn't work. Check it and try again.")
        until=time.time()+ADMIN_SECONDS
        with db.connect(write=True) as con:con.execute('UPDATE sessions SET admin_until=? WHERE hash=?',(until,s['hash']))
        return {'adminUntil':until}
    return mail
