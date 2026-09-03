"""Legacy generator entry points must fail clearly instead of being syntactically broken."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("relative_path", "replacement"),
    [
        ("scripts/generate_enhanced_docs.py", "scripts/generate_exchange_support_docs.py"),
        ("scripts/docs/generate_enhanced_docs.py", "scripts/generate_exchange_support_docs.py"),
        ("scripts/generate_modern_docs.py", "scripts/generate_exchange_support_docs.py"),
        ("scripts/docs/generate_modern_docs.py", "scripts/generate_exchange_support_docs.py"),
        ("scripts/generate_plugins/generate_exchange_plugin.py", "_templates/README.md"),
        ("scripts/docs/generate_plugins/generate_exchange_plugin.py", "_templates/README.md"),
    ],
)
def test_retired_legacy_generator_fails_with_a_migration_hint(
    relative_path: str, replacement: str
) -> None:
    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / relative_path)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "retired" in result.stderr.lower()
    assert replacement in result.stderr
