import errno
import json

import boto3
from botocore.exceptions import ClientError

from movierec.storage.base import ArtifactStore

# Error codes S3 uses for a key that is not there. get_object reports
# NoSuchKey; head_object has no body, so it reports the bare status, 404.
_MISSING_CODES = {"NoSuchKey", "404", "NotFound"}


def _is_missing(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") in _MISSING_CODES


class S3Store(ArtifactStore):
    """ArtifactStore over one S3 bucket, with the same semantics as LocalStore.

    Paths are used as keys verbatim (§3: identical for local and S3), so a
    path written here reads back from LocalStore's layout and vice versa.
    """

    def __init__(self, bucket: str, client=None):
        self.bucket = bucket
        self.client = client if client is not None else boto3.client("s3")

    def _not_found(self, path: str) -> FileNotFoundError:
        return FileNotFoundError(errno.ENOENT, "No such key", f"s3://{self.bucket}/{path}")

    def put_bytes(self, path: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=path, Body=data)

    def get_bytes(self, path: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=path)
        except ClientError as err:
            if _is_missing(err):
                raise self._not_found(path) from err
            raise
        return response["Body"].read()

    def put_json(self, path: str, obj: dict) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=path,
            Body=json.dumps(obj).encode("utf-8"),
            ContentType="application/json",
        )

    def get_json(self, path: str) -> dict:
        return json.loads(self.get_bytes(path).decode("utf-8"))

    def exists(self, path: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=path)
        except ClientError as err:
            # Only a genuine 404 means absent. A 403 must propagate: turning
            # a permissions error into False would hide a broken IAM policy.
            if _is_missing(err):
                return False
            raise
        return True

    def list(self, prefix: str) -> list[str]:
        # LocalStore treats the prefix as a path, not a string prefix:
        # list("tfidf/2026-09-1") does not match "tfidf/2026-09-15/...".
        # Match that by keeping only the exact key or keys under prefix + "/".
        base = prefix.rstrip("/")
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=base):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        if not base:
            return sorted(keys)
        if base in keys:
            return [prefix]
        return sorted(k for k in keys if k.startswith(base + "/"))
