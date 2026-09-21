"""Offline contracts for the exchange capability table renderers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATHS = (
    ROOT / "scripts" / "extract_capabilities_v2.py",
    ROOT / "scripts" / "analysis" / "extract_capabilities_v2.py",
)


def _load_module(script_path: Path):
    module_name = f"extract_capabilities_v2_{script_path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=("root-copy", "analysis-copy"))
def test_renderers_preserve_sorted_capability_rows_and_unknown_asset_type(
    script_path: Path,
) -> None:
    module = _load_module(script_path)
    exchange_caps = {
        "zeta___SWAP": ["trades", "depth"],
        "alpha": ["orders"],
        "beta___SPOT": ["depth", "orders"],
    }

    assert module.generate_markdown_table(exchange_caps) == "\n".join(
        [
            "| 交易所 | 资产类型 | depth | orders | trades |",
            "|---|---|---|---|---|",
            "|alpha|UNKNOWN|✗|✓|✗|",
            "|beta|SPOT|✓|✓|✗|",
            "|zeta|SWAP|✓|✗|✓|",
        ]
    )
    assert module.generate_csv(exchange_caps) == "\n".join(
        [
            "交易所,资产类型,depth,orders,trades",
            "alpha,UNKNOWN,0,1,0",
            "beta,SPOT,1,1,0",
            "zeta,SWAP,1,0,1",
        ]
    )
