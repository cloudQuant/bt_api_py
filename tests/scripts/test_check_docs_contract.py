"""The public-doc guard rejects stale claims before a site build."""

from __future__ import annotations

from pathlib import Path

from scripts.ci.check_docs_contract import validate_support_matrix


def test_certification_tier_requires_current_complete_evidence(tmp_path: Path) -> None:
    data = {
        "policy": {"blocking_python": ["3.11", "3.12", "3.13"], "canary_python": ["3.14"]},
        "entries": [
            {
                "name": "venue",
                "tier": "certified",
                "receipt_path": "missing.json",
                "head_sha": "not-a-sha",
                "profile": "",
                "validated_at": "",
                "expires_at": "2000-01-01T00:00:00+00:00",
            }
        ],
    }

    errors = validate_support_matrix(data, tmp_path)

    assert any("missing profile" in error for error in errors)
    assert any("receipt_path does not exist" in error for error in errors)
    assert any("expired" in error for error in errors)


def test_experimental_entry_may_state_narrow_limitations_without_certification_metadata(
    tmp_path: Path,
) -> None:
    data = {
        "policy": {"blocking_python": ["3.11", "3.12", "3.13"], "canary_python": ["3.14"]},
        "entries": [{"name": "bundle", "tier": "experimental", "limitations": "not certified"}],
    }

    assert validate_support_matrix(data, tmp_path) == []
