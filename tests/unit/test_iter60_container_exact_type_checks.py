"""Iteration 60: no exact ``type(x) is <container class>`` checks remain.

A published container is frozen by moving it to a read-only subclass of its
class (Base ``AutoInitMixin.freeze``), so ``type(x) is CtpTickerData`` stops
holding exactly when the data is published. Plain ``isinstance`` would accept
any subclass, which can override getters and pass as native data. The
supported exact check is Base ``container_class(x) is Cls``: the same before
and after ``freeze()``, and a hand-made subclass is still refused.

This scan fails when an exact type comparison against any class defined in a
``containers`` package of Base or the direct-scope providers reappears in the
SDK or provider sources.
"""

from __future__ import annotations

import re
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_SOURCES = (
    "bt_api/bt_api_base/src",
    "bt_api/bt_api_ctp/src",
    "bt_api/bt_api_okx/src",
    "bt_api/bt_api_binance/src",
)
SCANNED_SOURCES = (
    "bt_api_py",
    *CONTAINER_SOURCES,
    "bt_api/bt_api_gateway/src",
    "bt_api/bt_api_transport_zmq/src",
    "bt_api/bt_api_execution/src",
)
_CLASS = re.compile(r"^class (\w+)\(", re.M)
_EXACT = re.compile(r"type\(\s*[\w.\[\]\"']+\s*\)\s+(?:is not|is|==|!=)\s+(\w+)")


def _container_classes() -> set[str]:
    names: set[str] = set()
    for source in CONTAINER_SOURCES:
        for path in (SDK_ROOT / source).rglob("*.py"):
            if "containers" in path.parts:
                names.update(_CLASS.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return names


def test_container_classes_are_found():
    names = _container_classes()
    assert {"CtpTickerData", "OkxL2OrderBookData", "BinanceWssTickerData"} <= names


def test_no_exact_type_checks_against_container_classes():
    names = _container_classes()
    offenders = []
    for source in SCANNED_SOURCES:
        root = SDK_ROOT / source
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for number, line in enumerate(text.splitlines(), 1):
                offenders.extend(
                    f"{path.relative_to(SDK_ROOT)}:{number}: {line.strip()}"
                    for match in _EXACT.finditer(line)
                    if match.group(1) in names
                )
    assert offenders == [], "use container_class(x) is Cls instead:\n" + "\n".join(offenders)
