import boto3
from botocore.config import Config


class Storage:
    def __init__(self, cfg):
        self.bucket = cfg.r2_bucket
        self.client = boto3.client("s3", endpoint_url=cfg.r2_endpoint,
            aws_access_key_id=cfg.r2_access_key, aws_secret_access_key=cfg.r2_secret_key,
            region_name="auto", config=Config(signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10, read_timeout=60,
                request_checksum_calculation="when_required", response_checksum_validation="when_required"))

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
        p = {"Bucket": self.bucket, "Key": key}
        if download:
            p["ResponseContentDisposition"] = 'attachment; filename="practice-counts.mp4"'
        return self.client.generate_presigned_url("get_object", Params=p, ExpiresIn=seconds)

    def delete_project(self, project):
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=f"projects/{project}/"):
            for item in page.get("Contents", []):
                self.client.delete_object(Bucket=self.bucket, Key=item["Key"])
