"""Fail-closed, lifetime R2 allowances. No clock-based or restart resets.

Every actual S3 attempt (including SDK retries) reserves one operation before
network I/O. Charging every operation at Standard Class A pricing is deliberately
conservative. Byte reservations are never refunded after retries or deletions.
"""
import contextlib
import sqlite3
import time
from pathlib import Path


class CostLimit(RuntimeError):
    pass


class R2Guard:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def initialize(cls, path, operations=20_000_000, upload_bytes=100_000_000_000,
                   media_bytes=1_000_000_000_000):
        path = Path(path)
        # Exclusive creation: an installation rerun must never reset allowances.
        with path.open('xb'):
            pass
        path.chmod(0o600)
        with sqlite3.connect(path) as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE limits (id INTEGER PRIMARY KEY CHECK(id=1),
                    operations INTEGER NOT NULL, upload_bytes INTEGER NOT NULL,
                    media_bytes INTEGER NOT NULL, used_operations INTEGER NOT NULL DEFAULT 0,
                    used_upload_bytes INTEGER NOT NULL DEFAULT 0,
                    used_media_bytes INTEGER NOT NULL DEFAULT 0,
                    stopped TEXT NOT NULL DEFAULT '', stopped_at REAL);
            ''')
            db.execute('INSERT INTO limits(id,operations,upload_bytes,media_bytes) VALUES(1,?,?,?)',
                       (operations, upload_bytes, media_bytes))
        return cls(path)

    @contextlib.contextmanager
    def connect(self):
        try:
            # mode=rw refuses to silently recreate missing safety state.
            db = sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise CostLimit('Storage safety state is unavailable; access paused.') from exc

    def reserve(self, operations=0, upload_bytes=0, media_bytes=0):
        increments = (operations, upload_bytes, media_bytes)
        if any(type(n) is not int or n < 0 for n in increments):
            raise CostLimit('Invalid storage reservation; access paused.')
        reason = ''
        with self.connect() as db:
            row = db.execute('SELECT * FROM limits WHERE id=1').fetchone()
            if row is None:
                raise CostLimit('Storage safety state is missing; access paused.')
            reason = row['stopped']
            if not reason:
                for key, n in zip(('operations', 'upload_bytes', 'media_bytes'), increments):
                    if row['used_'+key]+n > row[key]:
                        reason = 'R2 safety allowance exhausted: '+key
                        db.execute('UPDATE limits SET stopped=?, stopped_at=? WHERE id=1', (reason, time.time()))
                        break
            if not reason:
                db.execute('''UPDATE limits SET used_operations=used_operations+?,
                    used_upload_bytes=used_upload_bytes+?, used_media_bytes=used_media_bytes+? WHERE id=1''', increments)
        # Raise after commit so the stop latch survives process restarts.
        if reason:
            raise CostLimit('Media access is paused by the storage cost safeguard.')

    def stop(self, reason):
        with self.connect() as db:
            db.execute("UPDATE limits SET stopped=?, stopped_at=? WHERE id=1 AND stopped=''",
                       (reason[:200], time.time()))

    def status(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM limits WHERE id=1').fetchone()
            if row is None:
                raise CostLimit('Storage safety state is missing.')
            return dict(row)

    def before_send(self, request, event_name, **kwargs):
        operation = event_name.rsplit('.', 1)[-1]
        allowed = {'CreateMultipartUpload', 'UploadPart', 'CompleteMultipartUpload',
                   'AbortMultipartUpload', 'ListParts', 'HeadObject', 'GetObject',
                   'PutObject', 'ListObjectsV2', 'DeleteObject'}
        if operation not in allowed:
            raise CostLimit('Unapproved storage operation; access paused.')
        uploaded = 0
        if operation in {'UploadPart', 'PutObject'}:
            # Reject copy and non-Standard storage even if called outside Storage.
            if request.headers.get('x-amz-copy-source'):
                raise CostLimit('Server-side copies are disabled.')
            storage_class = request.headers.get('x-amz-storage-class', b'STANDARD')
            if storage_class not in ('STANDARD', b'STANDARD'):
                raise CostLimit('Only Standard storage is allowed.')
            length = request.headers.get('Content-Length')
            if length is None:
                raise CostLimit('Uploads require a known length.')
            uploaded = int(length)
        if operation == 'CreateMultipartUpload':
            if request.headers.get('x-amz-storage-class', b'STANDARD') not in ('STANDARD', b'STANDARD'):
                raise CostLimit('Only Standard storage is allowed.')
        self.reserve(operations=1, upload_bytes=uploaded)
