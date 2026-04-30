"""Smoke test for Cycle 1A scaffold."""

from atlas import __version__


def test_version_string() -> None:
    assert __version__ == "0.1.0"
