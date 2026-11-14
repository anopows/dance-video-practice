import contextlib
import sqlite3

from . import kpi

SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS requests (bucket TEXT NOT NULL, stamp REAL NOT NULL);
CREATE INDEX IF NOT EXISTS requests_bucket_stamp ON requests(bucket, stamp);
CREATE TABLE IF NOT EXISTS proofs (hash TEXT PRIMARY KEY, stamp REAL NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, bytes INTEGER NOT NULL, created REAL NOT NULL,
 expires REAL NOT NULL, upload_deadline REAL NOT NULL, status TEXT NOT NULL,
 upload_id TEXT, error TEXT, stage TEXT, analysis TEXT, alignment TEXT NOT NULL DEFAULT '{}',
 revision INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 0,
 updated REAL NOT NULL, cleanup_done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS parts (
 project TEXT NOT NULL, number INTEGER NOT NULL, etag TEXT NOT NULL, bytes INTEGER NOT NULL,
 PRIMARY KEY(project,number)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
 created REAL NOT NULL, updated REAL NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
 payload TEXT NOT NULL DEFAULT '{}', error TEXT, object_key TEXT,
 UNIQUE(project,kind,payload)
);
"""


class Database:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript(SCHEMA)
            kpi.init(db)

    @contextlib.contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()



def kpi_count(con, fn, *args, **kwargs):
    try:
        con.execute('SAVEPOINT kpi_count')
        fn(con, *args, **kwargs)
        con.execute('RELEASE kpi_count')
    except Exception:
        try:
            con.execute('ROLLBACK TO kpi_count'); con.execute('RELEASE kpi_count')
        except Exception: pass

def best_count(db, fn, *args):
    try:
        con = sqlite3.connect(db.path, timeout=0.05)
        try:
            fn(con, *args); con.commit()
        finally: con.close()
    except Exception: pass
