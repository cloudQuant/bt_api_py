"""Explicit distribution pin verification without entry-point discovery."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .contracts import RuntimeCapabilityContract


class RuntimePluginError(RuntimeError):
    """Stable fail-closed error for a missing, mismatched, or unsafe capability."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CapabilityPin:
    """Code-owned package identity; never populate this from ``config.yaml``."""

    capability: str
    distribution: str
    module: str
    version: str
    module_sha256: str | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.capability, self.distribution, self.module, self.version)
        ):
            raise ValueError("capability pin fields are required")
        if self.module_sha256 is not None and (
            len(self.module_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.module_sha256)
        ):
            raise ValueError("module_sha256 must be a lower-case SHA-256 digest")


@dataclass(frozen=True)
class LoadedCapabilities:
    """The only result of catalog loading; access does not trigger imports."""

    contract: RuntimeCapabilityContract
    modules: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "modules", MappingProxyType(dict(self.modules)))

    def require(self, capability: str) -> Any:
        try:
            return self.modules[capability]
        except KeyError as error:
            raise RuntimePluginError(
                "CAPABILITY_NOT_LOADED", "capability was not loaded"
            ) from error


class CapabilityCatalog:
    """A sealed map from allowed capability name to distribution pin."""

    def __init__(
        self,
        pins: tuple[CapabilityPin, ...],
        *,
        importer: Callable[[str], Any] = importlib.import_module,
        version_getter: Callable[[str], str] = importlib.metadata.version,
    ) -> None:
        entries = tuple(pins)
        by_capability: dict[str, CapabilityPin] = {}
        for pin in entries:
            if not isinstance(pin, CapabilityPin):
                raise TypeError("catalog entries must be CapabilityPin")
            if pin.capability in by_capability:
                raise ValueError("catalog has duplicate capability pins")
            by_capability[pin.capability] = pin
        self._pins = MappingProxyType(by_capability)
        self._importer = importer
        self._version_getter = version_getter

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self._pins)

    def load(self, contract: RuntimeCapabilityContract) -> LoadedCapabilities:
        """Verify exact pins then import only the sealed required capabilities."""
        if not isinstance(contract, RuntimeCapabilityContract):
            raise TypeError("RuntimeCapabilityContract is required")
        modules: dict[str, Any] = {}
        for capability in contract.required_capabilities:
            pin = self._pins.get(capability)
            if pin is None:
                raise RuntimePluginError("CAPABILITY_PIN_MISSING", "required capability has no pin")
            try:
                observed_version = self._version_getter(pin.distribution)
            except importlib.metadata.PackageNotFoundError as error:
                raise RuntimePluginError(
                    "CAPABILITY_NOT_INSTALLED", "required capability is not installed"
                ) from error
            if observed_version != pin.version:
                raise RuntimePluginError(
                    "CAPABILITY_VERSION_MISMATCH", "required capability version differs from pin"
                )
            try:
                module = self._importer(pin.module)
            except Exception as error:
                raise RuntimePluginError(
                    "CAPABILITY_IMPORT_FAILED", "required capability could not be imported"
                ) from error
            self._verify_module_hash(module, pin)
            modules[capability] = module
        return LoadedCapabilities(contract=contract, modules=modules)

    @staticmethod
    def _verify_module_hash(module: Any, pin: CapabilityPin) -> None:
        if pin.module_sha256 is None:
            return
        filename = getattr(module, "__file__", None)
        if not isinstance(filename, str):
            raise RuntimePluginError(
                "CAPABILITY_HASH_UNAVAILABLE", "module location is unavailable"
            )
        try:
            actual = hashlib.sha256(Path(filename).read_bytes()).hexdigest()
        except OSError as error:
            raise RuntimePluginError(
                "CAPABILITY_HASH_UNAVAILABLE", "module bytes are unavailable"
            ) from error
        if actual != pin.module_sha256:
            raise RuntimePluginError("CAPABILITY_HASH_MISMATCH", "module hash differs from pin")
