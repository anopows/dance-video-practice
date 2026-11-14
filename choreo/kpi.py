"""Aggregate daily counters. Copied from the kpi repo; keep identical."""
import datetime
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path

KPI_VERSION = 1
BOUNDS = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1800)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS kpi_daily(day TEXT NOT NULL, metric TEXT NOT NULL,
 value INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day, metric)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kpi_seen(day TEXT NOT NULL, kind TEXT NOT NULL, hash TEXT NOT NULL,
 PRIMARY KEY(day, kind, hash)) WITHOUT ROWID;
'''

def day_at(now=None):
    return datetime.datetime.fromtimestamp(time.time() if now is None else now, datetime.timezone.utc).date().isoformat()

def init(con):
    # execute individual statements: executescript would commit the caller's transaction.
    for statement in SCHEMA.split(';'):
        if statement.strip(): con.execute(statement)

def bump(con, metric, n=1, now=None):
    con.execute('INSERT INTO kpi_daily VALUES(?,?,?) ON CONFLICT(day,metric) DO UPDATE SET value=value+excluded.value', (day_at(now), metric, n))

def seen(con, kind, key, secret, now=None):
    day = day_at(now)
    digest = hmac.new(secret.encode() if isinstance(secret, str) else secret, f'{day}|{kind}|{key}'.encode(), hashlib.sha256).hexdigest()[:16]
    inserted = con.execute('INSERT OR IGNORE INTO kpi_seen VALUES(?,?,?)', (day, kind, digest)).rowcount
    if inserted: bump(con, kind, now=now)
    prune(con, now)

def prune(con, now=None):
    con.execute('DELETE FROM kpi_seen WHERE day<?', (day_at(now),))

def observe(con, name, seconds, now=None):
    bound = next((str(b) for b in BOUNDS if seconds <= b), 'inf')
    bump(con, f'{name}.le.{bound}', now=now)

def export(con, site, gauges, days=400):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    since = (today - datetime.timedelta(days=days-1)).isoformat()
    data = {}
    for day, metric, value in con.execute('SELECT day,metric,value FROM kpi_daily WHERE day>=? AND day<=? AND value!=0 ORDER BY day,metric', (since, today.isoformat())):
        data.setdefault(day, {})[metric] = value
    return {'schema': KPI_VERSION, 'site': site, 'generated': int(time.time()), 'histogram_bounds': list(BOUNDS), 'days': data, 'gauges': gauges}

def write_json(path, payload):
    path = Path(path)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.kpi-', delete=False) as f:
            name = f.name
            os.fchmod(f.fileno(), 0o644)
            json.dump(payload, f, separators=(',', ':'), allow_nan=False)
            f.write('\n')
        os.replace(name, path)
    finally:
        if name and os.path.exists(name): os.unlink(name)
