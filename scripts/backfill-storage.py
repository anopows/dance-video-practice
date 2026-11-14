#!/usr/bin/env python3
"""Reconcile ready-video storage and remove originals. Dry run unless --apply."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from choreo.config import settings
from choreo.db import Database
from choreo.storage import Storage
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');a=p.parse_args()
cfg=settings();db=Database(cfg.state_dir/'room.sqlite3');store=Storage(cfg);source=derived=0
with db.connect() as con:rows=con.execute("SELECT id,bytes FROM projects WHERE status='ready'").fetchall()
for row in rows:
    items=store.inventory(row['id']);total=sum(o['Size'] for o in items if o['Key']!=store.original(row['id']))
    source+=row['bytes'];derived+=total
    print(json.dumps({'project':row['id'],'storedBytes':total,'removeOriginal':any(o['Key']==store.original(row['id']) for o in items),'apply':a.apply}))
    if a.apply:
        with db.connect(write=True) as con:
            live=con.execute("SELECT 1 FROM projects WHERE id=? AND status='ready'",(row['id'],)).fetchone()
            if not live:continue
            store.delete_original(row['id'])
            con.execute('UPDATE projects SET stored_bytes=?,original_stored=0 WHERE id=?',(total,row['id']))
ratio=derived/source if source else None
cap=min(10_000_000_000,int(1.8e12/(90*ratio)//1e9)*1_000_000_000) if ratio else None
print(json.dumps({'ratio':ratio,'recommendedDailyUploadBytes':cap,'videos':len(rows)}))
