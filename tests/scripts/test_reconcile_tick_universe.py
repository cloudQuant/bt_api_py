"""Pure in-memory contracts for missing-universe classification."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Never

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "reconcile_tick_universe.py"


class _PyarrowParquetModule(types.ModuleType):
    ParquetFile: Callable[..., Never]


class _PyarrowModule(types.ModuleType):
    parquet: _PyarrowParquetModule


def _load_reconciler(monkeypatch):
    pyarrow_module = _PyarrowModule("pyarrow")
    pyarrow_module.__path__ = []
    parquet_module = _PyarrowParquetModule("pyarrow.parquet")

    def reject_parquet_read(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("classification must not read Parquet files")

    parquet_module.ParquetFile = reject_parquet_read
    pyarrow_module.parquet = parquet_module
    monkeypatch.setitem(sys.modules, "pyarrow", pyarrow_module)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", parquet_module)

    module_name = "reconcile_tick_universe_classification_test"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def test_classify_missing_partitions_categories_counts_totals_and_orders_extras(
    monkeypatch,
) -> None:
    module = _load_reconciler(monkeypatch)
    universe = {
        "SHFE": {
            "rb2609",
            "rb2610",
            "cu2609",
            "cu2610",
            "au2609-C-4000",
            "au2610-C-4000",
        },
        "DCE": {"m2701", "m2705", "i2701", "a2701-C-3000"},
        "CFFEX": {"IF2609", "IF2612"},
    }
    persisted = module.Persisted(
        rows={
            "SHFE": {
                "rb2609": 4,
                "cu9999": 2,
                "au2609-C-4000": 1,
                "au9999-C-4000": 3,
                "zz_extra": 5,
            },
            "DCE": {"m2701": 2, "m9999": 1, "zz_extra": 6},
            "CFFEX": {"zz_extra": 4, "aa_extra": 2},
        }
    )

    whole, individual, extras, totals = module.classify_missing(universe, persisted)

    assert whole == {
        "CFFEX/IF/future": ["IF2609", "IF2612"],
        "DCE/A/option": ["a2701-C-3000"],
        "DCE/I/future": ["i2701"],
    }
    assert individual == {
        "DCE/M/future": ["m2705"],
        "SHFE/AU/option": ["au2610-C-4000"],
        "SHFE/CU/future": ["cu2609", "cu2610"],
        "SHFE/RB/future": ["rb2610"],
    }
    assert totals == {
        "CFFEX/IF/future": 2,
        "DCE/M/future": 2,
        "DCE/I/future": 1,
        "DCE/A/option": 1,
        "SHFE/RB/future": 2,
        "SHFE/CU/future": 2,
        "SHFE/AU/option": 2,
    }
    assert extras == [
        ("CFFEX", "aa_extra"),
        ("CFFEX", "zz_extra"),
        ("DCE", "m9999"),
        ("DCE", "zz_extra"),
        ("SHFE", "au9999-C-4000"),
        ("SHFE", "cu9999"),
        ("SHFE", "zz_extra"),
    ]
