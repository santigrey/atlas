"""Cycle 1C smoke: connect to Garage, list buckets including expected 3."""

from atlas.storage import (
    BUCKET_ARTIFACTS,
    BUCKET_ATLAS_STATE,
    BUCKET_BACKUPS,
    S3Storage,
)


def test_list_buckets_includes_expected() -> None:
    s = S3Storage()
    buckets = set(s.list_buckets())
    assert BUCKET_ATLAS_STATE in buckets
    assert BUCKET_BACKUPS in buckets
    assert BUCKET_ARTIFACTS in buckets
