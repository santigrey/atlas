"""Atlas Garage S3 credential resolution.

Precedence: os.environ takes priority over file. Falls back to file.
File path: /home/jes/garage-beast/.s3-creds (canonical Beast pattern, B1 ship Day 73).
File format: shell-export style (lines like `export VAR=value`).
Never logs secret values.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TypedDict


DEFAULT_CREDS_PATH = Path("/home/jes/garage-beast/.s3-creds")

REQUIRED_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
    "AWS_ENDPOINT_URL",
)

_EXPORT_RE = re.compile(r'^export\s+([A-Z_][A-Z0-9_]*)="?(.*?)"?\s*$')


class S3Creds(TypedDict):
    aws_access_key_id: str
    aws_secret_access_key: str
    region_name: str
    endpoint_url: str


def _parse_creds_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        m = _EXPORT_RE.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def get_s3_creds(path: Path | None = None) -> S3Creds:
    """Resolve Garage S3 creds with env -> file precedence.

    Args:
        path: Optional override of default creds file path.

    Returns:
        Resolved S3Creds dict suitable for boto3 client construction.

    Raises:
        ValueError: If any required var missing in both env and file.
    """
    file_path = path or DEFAULT_CREDS_PATH
    file_creds = _parse_creds_file(file_path)

    resolved: dict[str, str] = {}
    missing: list[str] = []
    for var in REQUIRED_VARS:
        # env wins if present
        val = os.environ.get(var) or file_creds.get(var)
        if not val:
            missing.append(var)
        else:
            resolved[var] = val

    if missing:
        raise ValueError(
            f"missing AWS S3 creds: {missing}; checked env then {file_path}"
        )

    return S3Creds(
        aws_access_key_id=resolved["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=resolved["AWS_SECRET_ACCESS_KEY"],
        region_name=resolved["AWS_DEFAULT_REGION"],
        endpoint_url=resolved["AWS_ENDPOINT_URL"],
    )
