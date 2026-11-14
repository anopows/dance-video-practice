import json
import sqlite3
import datetime
from choreo import kpi

def test_counters_and_export(tmp_path):
    c = sqlite3.connect(':memory:'); kpi.init(c)
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    kpi.bump(c, 'count', 2, now); kpi.bump(c, 'count', -1, now)
    kpi.seen(c, 'unique', 'visitor', 'secret', now - 86400)
    kpi.seen(c, 'unique', 'visitor', 'secret', now)
    kpi.seen(c, 'unique', 'visitor', 'secret', now)
    for v in (.5, 1800, 1801): kpi.observe(c, 'time', v, now)
    payload = kpi.export(c, 'songfinder', {'queue': 0})
    today = payload['days'][kpi.day_at(now)]
    assert today == {'count': 1, 'time.le.1': 1, 'time.le.1800': 1, 'time.le.inf': 1, 'unique': 1}
    assert c.execute('SELECT DISTINCT day FROM kpi_seen').fetchall() == [(kpi.day_at(now),)]
    assert payload['schema'] == 1 and payload['site'] == 'songfinder' and payload['histogram_bounds'] == list(kpi.BOUNDS)
    target = tmp_path / 'stats.json'; kpi.write_json(target, payload)
    assert json.loads(target.read_text()) == payload
    assert list(tmp_path.iterdir()) == [target]
    assert target.stat().st_mode & 0o777 == 0o644


def test_prune_without_visitor():
    c = sqlite3.connect(':memory:'); kpi.init(c)
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    c.execute('INSERT INTO kpi_seen VALUES(?,?,?)', (kpi.day_at(now-86400), 'unique', 'abc'))
    c.execute('INSERT INTO kpi_seen VALUES(?,?,?)', (kpi.day_at(now), 'unique', 'def'))
    kpi.prune(c, now)
    assert c.execute('SELECT hash FROM kpi_seen').fetchall() == [('def',)]
