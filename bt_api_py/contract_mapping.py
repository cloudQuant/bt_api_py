"""Copy normalized public mappings into ordinary Python containers."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from typing import Any, Type


def contract_mapping(
    value: Any,
    contract_name: str,
    *,
    error_type: Type[Exception] = ValueError,
) -> dict[str, Any]:
    """Copy a public Mapping/dataclass contract into an ordinary dictionary.

    ``error_type`` lets the owning adapter retain its public error category
    without making this stateless converter depend on a Store implementation.
    """
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise error_type(f"{contract_name} must be a mapping or dataclass")


def copy_mapping_tree(value: Any) -> Any:
    """Recursively copy Mapping and collection nodes while preserving leaf values."""
    if isinstance(value, Mapping):
        return {deepcopy(key): copy_mapping_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [copy_mapping_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(copy_mapping_tree(item) for item in value)
    if isinstance(value, set):
        return {copy_mapping_tree(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(copy_mapping_tree(item) for item in value)
    return deepcopy(value)


__all__ = ("contract_mapping", "copy_mapping_tree")
