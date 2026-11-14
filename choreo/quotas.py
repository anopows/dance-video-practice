"""Upload admission uses one SQLite write transaction, including reservations."""
import math
from fastapi import HTTPException
from .accounts import is_admin

STORED_BYTES_SQL = "SELECT coalesce(sum(stored_bytes + CASE WHEN original_stored=1 THEN bytes ELSE 0 END),0) FROM projects WHERE cleanup_done=0"
ACTIVE = ('creating','uploading','completing','queued','processing')


def refuse(code,message,seconds):
    raise HTTPException(code,message,headers={'Retry-After':str(max(1,math.ceil(seconds)))})


def usage(con,uid,now):
    return (con.execute('SELECT count(*) FROM projects WHERE owner=? AND created>?',(uid,now-86400)).fetchone()[0],
            con.execute('SELECT count(*) FROM projects WHERE owner=? AND created>?',(uid,now-30*86400)).fetchone()[0])


def admit(con,cfg,user,iphash,size,now):
    current=con.execute('SELECT * FROM users WHERE id=?',(user['user_id'],)).fetchone()
    if not current or current['status']=='banned':raise HTTPException(401,'Sign in to continue.')
    if current['upload_hold']:raise HTTPException(403,'Uploads are paused for this account while a recent video is reviewed. Check your email.')
    flag=con.execute("SELECT value FROM flags WHERE key='uploads_paused'").fetchone()
    if (not flag or flag[0]=='1') and not is_admin(cfg,user):
        refuse(503,'Uploads are paused right now. Please try again later.',3600)
    if con.execute(STORED_BYTES_SQL).fetchone()[0]+size > cfg.storage_cap_bytes*9//10:
        refuse(503,'five·six is full right now. Please try again in a few days.',86400)
    if con.execute("SELECT 1 FROM projects WHERE owner=? AND status IN ('creating','uploading','completing','queued','processing') AND expires>? AND (status NOT IN ('creating','uploading','completing') OR upload_deadline>?)",(user['user_id'],now,now)).fetchone():
        refuse(429,'Your other video is still uploading or being prepared. Wait for it to finish, then add this one.',30)
    checks=[('SELECT created FROM projects WHERE owner=? AND created>? ORDER BY created',user['user_id'],86400,cfg.user_uploads_day,f"You've added {cfg.user_uploads_day} videos in the last 24 hours. Try again later."),
            ('SELECT created FROM projects WHERE owner=? AND created>? ORDER BY created',user['user_id'],30*86400,cfg.user_uploads_month,f"You've reached the limit of {cfg.user_uploads_month} videos in 30 days."),
            ('SELECT created FROM upload_records WHERE ip_hash=? AND created>? ORDER BY created',iphash,86400,cfg.ip_uploads_day,'five·six has reached its upload limit for now. Try again later.')]
    for query,key,window,count,message in checks:
        rows=con.execute(query,(key,now-window)).fetchall()
        if len(rows)>=count:refuse(429,message,rows[-count][0]+window-now)
    count,total=con.execute('SELECT count(*),coalesce(sum(bytes),0) FROM projects WHERE created>?',(now-86400,)).fetchone()
    if count>=cfg.site_uploads_day or total+size>cfg.site_upload_bytes_day:
        refuse(429,'five·six has reached its upload limit for now. Try again later.',3600)
    if con.execute("SELECT count(*) FROM jobs WHERE kind='analyze' AND status IN ('queued','processing')").fetchone()[0]>=cfg.analyze_queue_limit:
        refuse(429,'five·six is busy. Try again in a few minutes.',300)
