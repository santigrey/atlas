"""Cycle 1C smoke: credential resolution precedence (env > file)."""

import pytest

from atlas.storage.creds import get_s3_creds


pytestmark = pytest.mark.homelab


def test_file_resolution_default_path() -> None:
    """Default file path resolves successfully (canonical .s3-creds reachable)."""
    creds = get_s3_creds()
    assert creds["endpoint_url"]
    assert creds["region_name"]
    assert creds["aws_access_key_id"].startswith("GK")  # Garage key prefix
    assert len(creds["aws_secret_access_key"]) > 0


def test_env_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """When env var is set, env wins over file value."""
    fake_creds = tmp_path / ".s3-creds-fake"
    fake_creds.write_text(
        'export AWS_ACCESS_KEY_ID=GKfilevalue\n'
        'export AWS_SECRET_ACCESS_KEY=filesecret\n'
        'export AWS_DEFAULT_REGION=garage\n'
        'export AWS_ENDPOINT_URL=http://from-file:3900\n'
    )
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "GKenvvalue")
    creds = get_s3_creds(path=fake_creds)
    assert creds["aws_access_key_id"] == "GKenvvalue"  # env wins
    assert creds["aws_secret_access_key"] == "filesecret"  # file fills in
    assert creds["endpoint_url"] == "http://from-file:3900"  # file fills in
