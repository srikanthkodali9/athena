"""(f) Committed attack fixtures must contain no real secret material."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.security.redaction import scan_secrets

ATTACKS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "attacks"


def _fixture_files():
    return sorted(p for p in ATTACKS_DIR.iterdir() if p.is_file())


def test_fixture_directory_is_populated():
    names = {p.name for p in _fixture_files()}
    assert {"poisoned_runbook.md", "credential_access.json", "unapproved_egress.json"} <= names


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_fixture_contains_no_real_secret(path):
    text = path.read_text(encoding="utf-8")
    findings = scan_secrets(text)
    assert findings == [], f"{path.name} appears to contain secret material: {findings}"


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: p.name)
def test_fixture_uses_obvious_placeholders_only(path):
    text = path.read_text(encoding="utf-8").upper()
    # If a key-like token appears it must be an obvious placeholder.
    if "AKIA" in text:
        assert "PLACEHOLDER" in text or "EXAMPLE" in text
