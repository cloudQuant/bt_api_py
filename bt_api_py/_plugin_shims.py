from __future__ import annotations

from importlib import import_module
import warnings
from typing import Any


def reexport_plugin_module(
    legacy_module: str,
    plugin_module: str,
    namespace: dict[str, Any],
) -> tuple[list[str], Any]:
    """Load a plugin module and expose its public symbols via a legacy path."""

    warnings.warn(
        (
            f"{legacy_module} is deprecated. "
            f"Install the corresponding plugin package and import from {plugin_module} instead."
        ),
        DeprecationWarning,
        stacklevel=2,
    )
    module = import_module(plugin_module)
    exports = list(
        getattr(module, "__all__", [name for name in dir(module) if not name.startswith("_")])
    )
    namespace.update({name: getattr(module, name) for name in exports if hasattr(module, name)})
    return exports, module
