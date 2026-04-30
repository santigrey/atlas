"""Atlas Garage S3 storage layer.

Bucket adoption (NOT creation). Existing buckets pre-allocated by B1 ship Day 73:
- atlas-state: working memory + ephemeral state (Atlas primary)
- backups: cross-cycle backup destination (Atlas writes verified backups)
- artifacts: produced files / report outputs (Atlas writes outputs)

Key prefix conventions (Atlas-owned):
- atlas-state: tasks/<task_id>/..., memory/<kind>/<id>, events/<ts>/..., working/<scope>/<id>
- backups: atlas/<YYYY-MM-DD>/<artifact-name>
- artifacts: atlas/<YYYY-MM-DD>/<task_id>/<artifact-name>

Credentials: env > file precedence; default file /home/jes/garage-beast/.s3-creds.
"""

from atlas.storage.client import (
    BUCKET_ARTIFACTS,
    BUCKET_ATLAS_STATE,
    BUCKET_BACKUPS,
    S3Storage,
    get_storage,
)
from atlas.storage.creds import S3Creds, get_s3_creds

__all__ = [
    "BUCKET_ARTIFACTS",
    "BUCKET_ATLAS_STATE",
    "BUCKET_BACKUPS",
    "S3Creds",
    "S3Storage",
    "get_s3_creds",
    "get_storage",
]
