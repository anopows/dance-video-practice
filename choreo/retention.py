"""Extend available projects without reviving expired or deleted uploads."""
import time


def extend_available(db, seconds, *, apply=False, now=None):
    if seconds < 86400:
        raise ValueError('Retention must be at least one day')
    now = time.time() if now is None else now
    where = "status IN ('creating','uploading','completing','queued','processing','ready') AND expires>? AND created+?>expires"
    with db.connect(write=apply) as con:
        count = con.execute('SELECT COUNT(*) FROM projects WHERE '+where, (now,seconds)).fetchone()[0]
        if apply:
            con.execute('UPDATE projects SET expires=created+? WHERE '+where, (seconds,now,seconds))
    return count
