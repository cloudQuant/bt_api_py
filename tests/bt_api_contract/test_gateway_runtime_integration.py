"""The retired Base gateway runtime is absent from active SDK imports."""

from __future__ import annotations

from importlib.util import find_spec

import pytest


_RETIRED_GATEWAY_MODULES = (
    "bt_api_base.gateway.config",
    "bt_api_base.gateway.registrar",
    "bt_api_base.gateway.runtime",
)


@pytest.mark.parametrize("module_name", _RETIRED_GATEWAY_MODULES)
def test_legacy_gateway_runtime_modules_are_not_importable(module_name: str) -> None:
    try:
        module_spec = find_spec(module_name)
    except ModuleNotFoundError:
        module_spec = None

    assert module_spec is None
