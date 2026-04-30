"""Cycle 1C smoke: put/head/get/delete roundtrip on atlas-state."""

import uuid

from atlas.storage import BUCKET_ATLAS_STATE, S3Storage


def test_put_head_get_delete_roundtrip() -> None:
    s = S3Storage()
    test_id = uuid.uuid4().hex[:8]
    key = f"_smoke/{test_id}/hello.txt"
    body = b"atlas cycle 1c smoke"

    # put
    s.put_object(BUCKET_ATLAS_STATE, key, body)

    # head
    head = s.head_object(BUCKET_ATLAS_STATE, key)
    assert head["ContentLength"] == len(body)

    # get
    got = s.get_object(BUCKET_ATLAS_STATE, key)
    assert got == body

    # delete
    s.delete_object(BUCKET_ATLAS_STATE, key)

    # verify gone via list under prefix
    remaining = list(s.list_objects(BUCKET_ATLAS_STATE, prefix=f"_smoke/{test_id}/"))
    assert remaining == []
