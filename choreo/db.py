import contextlib
import sqlite3
import secrets
import time

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
            if db.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise RuntimeError("Database is newer than this code")
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
            kpi.init(db)

    def migrate(self, cfg):
        from .accounts import normalize_email
        from .config import TERMS_VERSION
        with self.connect(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Database is newer than this code")
            if version == 1:
                return
            email, key = normalize_email(cfg.admin_email)
            db.execute("DROP TABLE sessions")
            for statement in ACCOUNT_SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
            for name, declaration in (
                ('owner', "TEXT NOT NULL DEFAULT ''"), ('hold', 'INTEGER NOT NULL DEFAULT 0'),
                ('stored_bytes', 'INTEGER NOT NULL DEFAULT 0'), ('original_stored', 'INTEGER NOT NULL DEFAULT 1'),
                ('reject_code', 'TEXT'), ('screen', 'TEXT')):
                db.execute(f"ALTER TABLE projects ADD COLUMN {name} {declaration}")
            db.execute("CREATE INDEX projects_owner_created ON projects(owner,created)")
            uid, now = secrets.token_hex(16), time.time()
            db.execute("INSERT INTO users(id,email,email_key,terms_version,created,last_login) VALUES (?,?,?,?,?,?)",
                       (uid,email,key,TERMS_VERSION,now,now))
            db.execute("UPDATE projects SET owner=?", (uid,))
            db.executemany("INSERT OR REPLACE INTO flags VALUES (?,?,?)",
                           [('uploads_paused','1',now),('uploads_paused_reason','Pre-launch',now)])
            db.execute("PRAGMA user_version=1")

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


ACCOUNT_SCHEMA = """
CREATE TABLE users (
 id TEXT PRIMARY KEY, email TEXT NOT NULL, email_key TEXT NOT NULL UNIQUE, google_sub TEXT UNIQUE,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','banned')),
 upload_hold INTEGER NOT NULL DEFAULT 0, hold_reason TEXT,
 terms_version INTEGER NOT NULL, created REAL NOT NULL, last_login REAL NOT NULL
);
CREATE TABLE sessions (
 hash TEXT PRIMARY KEY, csrf TEXT NOT NULL,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 created REAL NOT NULL, expires REAL NOT NULL, admin_until REAL NOT NULL DEFAULT 0
);
CREATE INDEX sessions_user ON sessions(user_id,created);
CREATE TABLE login_tokens (
 hash TEXT PRIMARY KEY, email TEXT NOT NULL, email_key TEXT NOT NULL,
 terms INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, used REAL,
 google_sub TEXT
);
CREATE TABLE oauth_states (
 hash TEXT PRIMARY KEY, nonce TEXT NOT NULL, verifier TEXT NOT NULL, terms INTEGER NOT NULL, created REAL NOT NULL
);
CREATE TABLE bans (
 key_hash TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('email','google')),
 reason TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE reports (
 id TEXT PRIMARY KEY, project TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(reason IN ('illegal','copyright','privacy','other')),
 details TEXT NOT NULL, contact TEXT, ip_hash TEXT NOT NULL, created REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','actioned','dismissed')),
 resolved REAL, note TEXT
);
CREATE INDEX reports_status ON reports(status,created);
CREATE INDEX reports_project ON reports(project);
CREATE TABLE audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT NOT NULL,
 action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE upload_records (
 project TEXT PRIMARY KEY, user_id TEXT NOT NULL, email TEXT NOT NULL,
 ip_hash TEXT NOT NULL, created REAL NOT NULL, source_sha256 TEXT
);
CREATE TABLE egress (
 project TEXT NOT NULL, day TEXT NOT NULL, bytes INTEGER NOT NULL,
 PRIMARY KEY(project,day)
) WITHOUT ROWID;
"""



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
