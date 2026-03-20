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


def test_renderers_use_same_exchange_counts() -> None:
    module = _load_module()
    data = module.load_data()

    readme = module.render_readme(data)
    status = module.render_status(data)

    assert f"{len(data['fully_supported'])} 个完整支持" in readme
    assert f"| ✅ 完整支持 | {len(data['fully_supported'])} |" in status


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
