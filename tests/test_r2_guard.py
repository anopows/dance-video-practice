import concurrent.futures
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import boto3
import pytest
from botocore.config import Config
from fastapi.testclient import TestClient
from moto import mock_aws

from choreo.api import create_app
from choreo.config import Settings
from choreo.r2_guard import CostLimit, R2Guard
from choreo.storage import Storage


def test_atomic_limits_latch_restart_and_no_reset(tmp_path):
    path = tmp_path/'guard.sqlite3'
    guard = R2Guard.initialize(path, operations=10)
    def attempt(_):
        try:
            R2Guard(path).reserve(operations=1)
            return True
        except CostLimit:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(30))) == 10
    assert guard.status()['used_operations'] == 10
    with pytest.raises(CostLimit):
        R2Guard(path).reserve()
    with pytest.raises(FileExistsError):
        R2Guard.initialize(path)


def test_missing_and_corrupt_safety_state_fail_closed(tmp_path):
    path = tmp_path/'missing.sqlite3'
    with pytest.raises(CostLimit):
        R2Guard(path).reserve(operations=1)
    assert not path.exists()
    path.write_text('not a database')
    with pytest.raises(CostLimit):
        R2Guard(path).reserve(operations=1)


def test_write_bytes_retries_unknown_operations_and_storage_class(tmp_path):
    guard = R2Guard.initialize(tmp_path/'guard.sqlite3', upload_bytes=10)
    req = SimpleNamespace(headers={'Content-Length': b'6'})
    guard.before_send(req, 'before-send.s3.UploadPart')
    with pytest.raises(CostLimit):
        guard.before_send(req, 'before-send.s3.UploadPart')
    assert guard.status()['used_upload_bytes'] == 6
    assert guard.status()['stopped']
    for op, headers in [('CopyObject', {}), ('PutObject', {}),
                        ('PutObject', {'Content-Length': b'1', 'x-amz-storage-class': b'STANDARD_IA'}),
                        ('CreateMultipartUpload', {'x-amz-storage-class': b'STANDARD_IA'})]:
        with pytest.raises(CostLimit):
            guard.before_send(SimpleNamespace(headers=headers), 'before-send.s3.'+op)


def test_actual_sdk_retries_are_counted_before_network(tmp_path):
    attempts = []
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            attempts.append(1)
            self.send_response(503 if len(attempts) == 1 else 200)
            self.send_header('Content-Length', '0')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        guard = R2Guard.initialize(tmp_path/'guard.sqlite3', operations=2)
        client = boto3.client('s3', endpoint_url=f'http://127.0.0.1:{server.server_port}',
            aws_access_key_id='test', aws_secret_access_key='test', region_name='auto',
            config=Config(retries={'max_attempts': 2, 'mode': 'standard'}))
        client.meta.events.register('before-send.s3', guard.before_send)
        client.head_object(Bucket='test-bucket', Key='test')
        assert len(attempts) == guard.status()['used_operations'] == 2
        with pytest.raises(CostLimit):
            client.head_object(Bucket='test-bucket', Key='test')
        assert len(attempts) == 2
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def guarded_app(tmp_path):
    with mock_aws():
        R2Guard.initialize(tmp_path/'r2-guard.sqlite3', media_bytes=100)
        cfg = Settings(origin='https://practice.test', state_dir=tmp_path,admin_email='admin@example.com',secret_key='s'*32,
            r2_guard_enabled=True, r2_secret_key='test-secret', r2_access_key='test',
            r2_bucket='test-bucket', r2_endpoint='https://s3.amazonaws.com',
            r2_webhook_secret='webhook-secret', r2_budget_policy_id='policy', r2_account_id='account')
        raw = boto3.client('s3', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
        raw.create_bucket(Bucket=cfg.r2_bucket)
        key = 'projects/'+'a'*32+'/practice.mp4'
        raw.put_object(Bucket=cfg.r2_bucket, Key=key, Body=b'0123456789', ContentType='video/mp4')
        store = Storage(cfg)
        # moto supplies responses before before-send; network hook is tested above.
        app = create_app(cfg, store, testing=True)
        with app.state.db.connect(write=True) as db:
            db.execute('''INSERT INTO projects(id,name,bytes,created,expires,upload_deadline,status,updated)
                VALUES(?,?,10,?,?,?,'ready',?)''', ('a'*32, 'test', time.time(), time.time()+1000, time.time()+10, time.time()))
        yield TestClient(app, base_url=cfg.origin), store, key


def test_media_is_local_signed_range_capable_and_revocable(guarded_app):
    client, store, key = guarded_app
    url = store.url(key, 900)
    assert url.startswith('https://practice.test/media/')
    assert 'X-Amz' not in url
    assert client.get(url).content == b'0123456789'
    r = client.get(url, headers={'Range': 'bytes=2-5'})
    assert r.status_code == 206 and r.content == b'2345'
    assert r.headers['content-range'] == 'bytes 2-5/10'
    assert client.get(url, headers={'Range': 'bytes=-3'}).content == b'789'
    assert client.get(url, headers={'Range': 'bytes=10-'}).status_code == 416
    assert client.get(url, headers={'Range': 'bytes=0-1,3-4'}).status_code == 416
    assert client.head(url).headers['content-length'] == '10'
    assert client.get(url.replace('download=0', 'download=1')).status_code == 403
    assert client.get(url.replace('practice.mp4', 'original')).status_code == 403
    expired = int(time.time())-1
    assert not store.verify_media(key, expired, 0, store.media_signature(key, expired, 0))
    export_key = 'projects/'+'a'*32+'/exports/'+'b'*32+'.mp4'
    exp = int(time.time())+900
    assert store.verify_media(export_key, exp, 1, store.media_signature(export_key, exp, 1))
    store.guard.stop('test')
    # moto bypasses SDK hooks; url issuance and ledger still enforce the latch.
    assert client.get(url).status_code == 503
    assert client.head(url).status_code == 503
    with pytest.raises(CostLimit):
        store.url(key, 900)


def test_webhook_auth_scope_test_and_duplicate_delivery(guarded_app):
    client, store, key = guarded_app
    payload = {'policy_id': 'policy', 'account_id': 'account', 'alert_type': 'billing_budget_alert'}
    path = '/hooks/cloudflare-budget'
    assert client.post(path, json=payload).status_code == 403
    headers = {'cf-webhook-auth': 'webhook-secret'}
    assert client.post(path, json={**payload, 'policy_id': 'other'}, headers=headers).status_code == 400
    message = 'Hello World! This is a test message sent from https://cloudflare.com. If you can see this, your webhook is configured properly.'
    assert client.post(path, json={'text': message}, headers=headers).json()['test']
    assert client.post(path, json={'text': message.replace('properly.', 'correctly.')}, headers=headers).json()['test']
    assert not store.guard.status()['stopped']
    assert client.post(path, json={**payload, 'alert_event': 'ALERT_STATE_EVENT_END'}, headers=headers).json()['ignored']
    assert not store.guard.status()['stopped']
    # An attack saturating playback's global limiter must not block the alarm.
    with client.app.state.db.connect(write=True) as db:
        db.executemany('INSERT INTO requests VALUES (?,?)', [('api:global', time.time())]*1200)
    assert client.post(path, json=payload, headers=headers).json()['stopped']
    stamp = store.guard.status()['stopped_at']
    assert client.post(path, json=payload, headers=headers).status_code == 200
    assert store.guard.status()['stopped_at'] == stamp


def test_media_allowance_latches_before_download(guarded_app):
    client, store, key = guarded_app
    store.guard.reserve(media_bytes=95)
    url = store.url(key, 900)
    assert client.get(url).status_code == 503
    assert store.guard.status()['used_media_bytes'] == 95
