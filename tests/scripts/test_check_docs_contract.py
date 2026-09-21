"""The public-doc guard rejects stale claims before a site build."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scripts.ci.check_docs_contract import validate_support_matrix

if TYPE_CHECKING:
    from pathlib import Path


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

    assert errors == [
        "venue: certified entry is missing profile",
        "venue: certified entry is missing validated_at",
        "venue: receipt_path does not exist: missing.json",
        "venue: head_sha is not a commit-like SHA",
        "venue: evidence has expired",
    ]


def test_experimental_entry_may_state_narrow_limitations_without_certification_metadata(
    tmp_path: Path,
) -> None:
    data = {
        "policy": {"blocking_python": ["3.11", "3.12", "3.13"], "canary_python": ["3.14"]},
        "entries": [{"name": "bundle", "tier": "experimental", "limitations": "not certified"}],
    }

    assert validate_support_matrix(data, tmp_path) == []
