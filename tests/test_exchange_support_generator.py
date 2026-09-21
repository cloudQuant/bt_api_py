"""Tests for exchange support documentation generation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "generate_exchange_support_docs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_exchange_support_docs", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_renderer_uses_evidence_tiers_without_certified_exchange_counts() -> None:
    module = _load_module()
    data = module.load_data()

    rendered = module.render(data)

    assert "evidence tiers, not a count of production-ready exchanges" in rendered
    for entry in data["entries"]:
        assert entry["name"] in rendered
        assert f"`{entry['tier']}`" in rendered


def test_renderer_preserves_entry_rows_order_and_policy_text() -> None:
    module = _load_module()
    data = {
        "policy": {
            "blocking_python": ["3.11", "3.12"],
            "canary_python": ["3.13"],
        },
        "entries": [
            {
                "name": "First Venue",
                "tier": "certified",
                "scope": "spot only",
                "limitations": "read-only metadata",
            },
            {
                "name": "Second Venue",
                "tier": "experimental",
                "scope": "linear futures",
                "limitations": "no write eligibility",
            },
        ],
    }

    rendered = module.render(data)

    assert rendered == "\n".join(
        [
            "## Support status",
            "",
            "The entries below are evidence tiers, not a count of production-ready exchanges.",
            "",
            "| Scope | Tier | Evidence boundary | Current limitation |",
            "| --- | --- | --- | --- |",
            "| First Venue | `certified` | spot only | read-only metadata |",
            "| Second Venue | `experimental` | linear futures | no write eligibility |",
            "",
            "Blocking CI supports Python `3.11`, `3.12`; Python `3.13` is canary-only.",
            "",
            "See `docs/operations/support-status-policy.md` for the evidence and expiry rules.",
        ]
    )


def test_replace_marker_block_updates_only_target_section() -> None:
    module = _load_module()
    original = "\n".join(
        [
            "before",
            "<!-- BEGIN GENERATED:EXAMPLE -->",
            "old",
            "<!-- END GENERATED:EXAMPLE -->",
            "after",
        ]
    )

    updated = module.replace_marker_block(original, "EXAMPLE", "new")

    assert updated == "\n".join(
        [
            "before",
            "<!-- BEGIN GENERATED:EXAMPLE -->",
            "new",
            "<!-- END GENERATED:EXAMPLE -->",
            "after",
        ]
    )
