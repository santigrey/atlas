"""Atlas Garage S3 client wrapper.

bot3 wrapper against Beast Garage S3 LAN endpoint http://192.168.1.152:3900.
Path-style addressing required for Garage compat (virtual-hosted-style needs DNS).
Bucket adoption (NOT creation): Atlas reads/writes existing pre-allocated buckets.
"""

from __future__ import annotations

from typing import Any, Iterator

import boto3
import structlog
from botocore.client import Config as BotoConfig

from atlas.storage.creds import S3Creds, get_s3_creds

log = structlog.get_logger(__name__)

# Bucket name constants (adopted from B1 ship Day 73; Atlas does not create in v0.1)
BUCKET_ATLAS_STATE = "atlas-state"
BUCKET_BACKUPS = "backups"
BUCKET_ARTIFACTS = "artifacts"


class S3Storage:
    """boto3 wrapper for Atlas Garage S3 access.

    Bucket adoption (NOT creation). Atlas reads/writes to existing buckets only.
    Uses path-style addressing per Garage compat requirement.
    """

    def __init__(self, creds: S3Creds | None = None) -> None:
        self._creds = creds or get_s3_creds()
        self._client = boto3.client(
            "s3",
            aws_access_key_id=self._creds["aws_access_key_id"],
            aws_secret_access_key=self._creds["aws_secret_access_key"],
            endpoint_url=self._creds["endpoint_url"],
            region_name=self._creds["region_name"],
            config=BotoConfig(s3={"addressing_style": "path"}),
        )

    def list_buckets(self) -> list[str]:
        """Return list of bucket names visible to current creds."""
        resp = self._client.list_buckets()
        return [b["Name"] for b in resp.get("Buckets", [])]

    def put_object(
        self, bucket: str, key: str, body: bytes | str, **kwargs: Any
    ) -> dict[str, Any]:
        """Put object (str body auto-encoded as utf-8)."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        return self._client.put_object(Bucket=bucket, Key=key, Body=body, **kwargs)

    def get_object(self, bucket: str, key: str) -> bytes:
        """Get object body as bytes."""
        resp = self._client.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()

    def head_object(self, bucket: str, key: str) -> dict[str, Any]:
        """Head object (returns metadata dict including ContentLength)."""
        return self._client.head_object(Bucket=bucket, Key=key)

    def delete_object(self, bucket: str, key: str) -> dict[str, Any]:
        """Delete object."""
        return self._client.delete_object(Bucket=bucket, Key=key)

    def list_objects(
        self, bucket: str, prefix: str = "", page_size: int = 1000
    ) -> Iterator[dict[str, Any]]:
        """Paginated list of objects under prefix."""
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=bucket,
            Prefix=prefix,
            PaginationConfig={"PageSize": page_size},
        ):
            for obj in page.get("Contents", []):
                yield obj


def get_storage() -> S3Storage:
    """Convenience constructor with default creds resolution."""
    return S3Storage()
