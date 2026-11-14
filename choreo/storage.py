import boto3
import hashlib
import hmac
import re
import time
from urllib.parse import quote, urlencode
from botocore.config import Config
from .r2_guard import R2Guard, CostLimit


class Storage:
    def __init__(self, cfg):
        self.bucket = cfg.r2_bucket
        self.cfg = cfg
        self.guard = R2Guard(cfg.state_dir / 'r2-guard.sqlite3') if cfg.r2_guard_enabled else None
        if self.guard:
            self.guard.status()
        self.client = boto3.client("s3", endpoint_url=cfg.r2_endpoint,
            aws_access_key_id=cfg.r2_access_key, aws_secret_access_key=cfg.r2_secret_key,
            region_name="auto", config=Config(signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10, read_timeout=60,
                request_checksum_calculation="when_required", response_checksum_validation="when_required"))
        if self.guard:
            self.client.meta.events.register('before-send.s3', self.guard.before_send)

    @staticmethod
    def original(project):
        return f"projects/{project}/original"

    def begin(self, project):
        return self.client.create_multipart_upload(Bucket=self.bucket,
            Key=self.original(project), ContentType="application/octet-stream")["UploadId"]

    def part(self, project, upload, number, body):
        return self.client.upload_part(Bucket=self.bucket, Key=self.original(project),
            UploadId=upload, PartNumber=number, Body=body)["ETag"]

    def parts(self, project, upload):
        result = []
        for page in self.client.get_paginator("list_parts").paginate(Bucket=self.bucket,
                Key=self.original(project), UploadId=upload):
            result.extend(page.get("Parts", []))
        return result

    def complete(self, project, upload, parts):
        self.client.complete_multipart_upload(Bucket=self.bucket, Key=self.original(project),
            UploadId=upload, MultipartUpload={"Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]})

    def abort(self, project, upload):
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=self.original(project), UploadId=upload)
        except self.client.exceptions.ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchUpload":
                raise

    def size(self, key):
        return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]

    def download(self, key, path, maximum):
        r = self.client.get_object(Bucket=self.bucket, Key=key)
        try:
            if r["ContentLength"] > maximum:
                raise ValueError("Stored object exceeds the reserved size")
            total = 0
            with path.open("wb") as f:
                for chunk in r["Body"].iter_chunks(1024 * 1024):
                    total += len(chunk)
                    if total > maximum:
                        raise ValueError("Stored object exceeds the reserved size")
                    f.write(chunk)
            return total
        finally:
            r["Body"].close()

    def upload(self, key, path, content_type):
        self.client.upload_file(str(path), self.bucket, key,
            ExtraArgs={"ContentType": content_type, "CacheControl": "private, max-age=300"})

    def url(self, key, seconds, download=False):
        if getattr(self, 'guard', None):
            self.guard.reserve()
            expires = int(time.time()) + min(900, max(1, int(seconds)))
            sig = self.media_signature(key, expires, int(download))
            return self.cfg.origin+'/media/'+quote(key, safe='/')+'?'+urlencode(
                {'expires': expires, 'download': int(download), 'sig': sig})
        p = {"Bucket": self.bucket, "Key": key}
        if download:
            p["ResponseContentDisposition"] = 'attachment; filename="practice-counts.mp4"'
        return self.client.generate_presigned_url("get_object", Params=p, ExpiresIn=seconds)

    def media_signature(self, key, expires, download):
        message = f'five-six-media-v1\n{key}\n{expires}\n{download}'.encode()
        return hmac.new(self.cfg.r2_secret_key.encode(), message, hashlib.sha256).hexdigest()

    def verify_media(self, key, expires, download, sig):
        return (getattr(self, 'guard', None) is not None
            and re.fullmatch(r'projects/[a-f0-9]{32}/(?:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}|exports/[a-f0-9]{32}\.mp4)', key) is not None
            and download in (0, 1) and time.time() < expires <= time.time()+901
            and hmac.compare_digest(sig, self.media_signature(key, expires, download)))

    def open_media(self, key, range_header=None, head=False):
        self.guard.reserve()
        metadata = self.client.head_object(Bucket=self.bucket, Key=key)
        size = metadata['ContentLength']
        headers = {'Accept-Ranges': 'bytes', 'Content-Type': metadata.get('ContentType', 'application/octet-stream'),
                   'Content-Length': str(size), 'ETag': metadata['ETag']}
        if head:
            return 200, headers, None
        start, end, status = 0, size-1, 200
        if range_header:
            match = re.fullmatch(r'bytes=(\d{0,20})-(\d{0,20})', range_header)
            if not match or not any(match.groups()) or size == 0:
                return 416, {'Content-Range': f'bytes */{size}', 'Content-Length': '0'}, None
            a, b = match.groups()
            if a:
                start, end = int(a), min(int(b), size-1) if b else size-1
            else:
                start, end = max(0, size-int(b)), size-1
            if start > end or start >= size:
                return 416, {'Content-Range': f'bytes */{size}', 'Content-Length': '0'}, None
            status = 206
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            headers['Content-Length'] = str(end-start+1)
        self.guard.reserve(media_bytes=int(headers['Content-Length']))
        params = {'Bucket': self.bucket, 'Key': key, 'IfMatch': metadata['ETag']}
        if status == 206:
            params['Range'] = f'bytes={start}-{end}'
        result = self.client.get_object(**params)
        return status, headers, result['Body']

    def delete_project(self, project):
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=f"projects/{project}/"):
            for item in page.get("Contents", []):
                self.client.delete_object(Bucket=self.bucket, Key=item["Key"])
