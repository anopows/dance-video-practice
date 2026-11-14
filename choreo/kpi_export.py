"""Export aggregate counters without initializing object storage."""
import argparse
import os
from pathlib import Path
import shutil
import time
from . import kpi
from .db import Database
from .limits import EXPORT_QUEUE_LIMIT

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('path'); args=parser.parse_args()
    root=Path(os.environ.get('CHOREO_STATE_DIR','/var/lib/choreo'))
    db=Database(root/'room.sqlite3'); now=time.time()
    with db.connect() as con:
        gauges={'sessions_active':con.execute('SELECT count(*) FROM sessions WHERE expires>?',(now,)).fetchone()[0]}
        for status, n in con.execute("SELECT status,count(*) FROM projects WHERE expires>? AND status NOT IN ('deleted','expired') GROUP BY status",(now,)):
            if status in ('creating','uploading','completing','queued','processing','ready','failed'):
                gauges['projects.'+status]=n
        gauges['stored_bytes']=con.execute("SELECT coalesce(sum(bytes),0) FROM projects WHERE status='ready' AND expires>?",(now,)).fetchone()[0]
        gauges['queue_limit']=EXPORT_QUEUE_LIMIT
        gauges['projects_ready']=con.execute("SELECT count(*) FROM projects WHERE status='ready' AND expires>?",(now,)).fetchone()[0]
        gauges['queue']=con.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','processing')").fetchone()[0]
        oldest=con.execute("SELECT min(created) FROM jobs WHERE status='queued'").fetchone()[0]
        gauges['oldest_queued_age_s']=max(0,int(now-oldest)) if oldest is not None else None
        gauges['disk_free_bytes']=shutil.disk_usage(root).free
        payload=kpi.export(con,'fivesix',gauges)
    kpi.write_json(args.path,payload)

if __name__=='__main__': main()
