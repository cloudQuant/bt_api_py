#!/usr/bin/env python3
"""Compatibility wrapper for the canonical v2 capability exporter."""

from __future__ import annotations

import importlib as _importlib
import runpy as _runpy
import sys as _sys
from pathlib import Path as _Path
from types import ModuleType as _ModuleType

_CANONICAL_MODULE = "scripts.extract_capabilities_v2"
_canonical: _ModuleType | None = None
_CANONICAL_ATTRIBUTES: set[str] = set()


def _load_canonical() -> _ModuleType:
    global _canonical
    if _canonical is None:
        _canonical = _importlib.import_module(_CANONICAL_MODULE)
    return _canonical


def __getattr__(name: str):
    canonical = _load_canonical()
    if name == "__all__":
        return getattr(
            canonical,
            "__all__",
            [attribute for attribute in vars(canonical) if not attribute.startswith("_")],
        )
    value = getattr(canonical, name)
    _CANONICAL_ATTRIBUTES.add(name)
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_load_canonical())))


class _ForwardingModule(_ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        if name in self.__dict__ or name.startswith("__"):
            super().__setattr__(name, value)
            return
        canonical = _load_canonical()
        if name in _CANONICAL_ATTRIBUTES or hasattr(canonical, name):
            _CANONICAL_ATTRIBUTES.add(name)
            setattr(canonical, name, value)
        else:
            super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in self.__dict__:
            super().__delattr__(name)
            return
        if name.startswith("__"):
            super().__delattr__(name)
            return
        canonical = _load_canonical()
        if name in _CANONICAL_ATTRIBUTES or hasattr(canonical, name):
            _CANONICAL_ATTRIBUTES.add(name)
            delattr(canonical, name)
            return
        super().__delattr__(name)


if __name__ == "__main__":
    _runpy.run_path(
        str(_Path(__file__).resolve().parents[1] / _Path(__file__).name),
        run_name="__main__",
    )
elif __name__ in _sys.modules:
    _sys.modules[__name__].__class__ = _ForwardingModule
