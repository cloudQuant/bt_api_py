"""Typed SDK boundary for deployment-owned CTP credential bindings.

This module deliberately imports only the standard library and the SDK error
type.  The deployment package that implements the reviewed adapter is imported
only when ``BtApi.create_ctp_credential_binding_verifier`` is called.

The nominal types below make accidental or compatibility-level substitution
with a lambda or a mapping fail closed.  They are deployment provenance
contracts, not a sandbox against hostile code already running in this process.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ._contracts.errors import NormalizedApiError

_SCOPE_SEAL = object()
_VERIFIER_SEAL = object()
_TEST_VERIFIER_SEAL = object()
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[\w][\w.:-]{0,127}$", re.ASCII)

_ADAPTER_MODULE = "backtrader_runtime._ctp_credential_binding"
_ADAPTER_CLASS = "CtpReviewedCredentialBindingRefreshAdapter"
_RESULT_CLASS = "CtpReviewedCredentialBindingRefreshResult"
_SCOPE_FIELDS = frozenset(
    {
        "account_fingerprint",
        "account_fingerprint_sha256",
        "trading_day",
        "connection_generation",
        "environment_profile",
        "td_front",
        "md_front",
        "td_front_sha256",
        "md_front_sha256",
        "backtrader_sha256",
        "backtrader_runtime_sha256",
        "bt_api_py_sha256",
        "bt_api_ctp_sha256",
        "bt_api_base_sha256",
        "native_sha256",
        "dependency_hashes_sha256",
        "configuration_sha256",
        "strategy_identity_sha256",
        "preflight_sha256",
        "evidence_sha256",
        "md_connection_generation",
        "md_stream_generation",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "scope_sha256",
        "key_id",
        "hmac_sha256",
        "account_fingerprint",
        "account_fingerprint_sha256",
        "td_front",
        "md_front",
        "runtime_config_sha256",
        "registration_sha256",
        "backtrader_runtime_sha256",
    }
)


def _reject(operation: str, code: str) -> None:
    raise NormalizedApiError(operation, code, definite_reject=True)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", "strict")


def _manifest_digest(package_root: Path) -> str:
    """Hash a bounded Python/native source manifest rooted at one package."""

    try:
        paths = []
        for path in package_root.rglob("*"):
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or path.suffix.lower() not in {".py", ".so", ".dylib", ".pyd"}
            ):
                continue
            resolved = path.resolve(strict=True)
            try:
                resolved.relative_to(package_root)
            except ValueError:
                continue
            paths.append(resolved)
        paths.sort(key=lambda path: path.relative_to(package_root).as_posix())
        if not paths:
            raise ValueError
        manifest = [
            {
                "path": path.relative_to(package_root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in paths
        ]
        return hashlib.sha256(_canonical_json(manifest)).hexdigest()
    except Exception:
        raise ValueError("credential_binding_package_identity_unavailable") from None


def _strict_text(value: Any, *, operation: str, code: str, limit: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or "\x00" in value
    ):
        _reject(operation, code)
    return value


def _validate_scope(values: Mapping[str, Any], operation: str) -> dict[str, Any]:
    if not isinstance(values, Mapping) or set(values) != _SCOPE_FIELDS:
        _reject(operation, "ctp_credential_binding_scope_invalid")
    result = dict(values)
    for name in (
        "account_fingerprint",
        "environment_profile",
        "td_front",
        "md_front",
    ):
        _strict_text(result[name], operation=operation, code="ctp_credential_binding_scope_invalid")
    account_short = result["account_fingerprint"]
    if not re.fullmatch(r"acct_[0-9a-f]{16}", account_short):
        _reject(operation, "ctp_credential_binding_scope_invalid")
    if not re.fullmatch(r"[0-9]{8}", str(result["trading_day"])):
        _reject(operation, "ctp_credential_binding_scope_invalid")
    if (
        type(result["connection_generation"]) is not int
        or result["connection_generation"] <= 0
        or type(result["md_connection_generation"]) is not int
        or result["md_connection_generation"] <= 0
        or type(result["md_stream_generation"]) is not int
        or result["md_stream_generation"] <= 0
    ):
        _reject(operation, "ctp_credential_binding_scope_invalid")
    for name in _SCOPE_FIELDS - {
        "account_fingerprint",
        "trading_day",
        "connection_generation",
        "environment_profile",
        "td_front",
        "md_front",
        "md_connection_generation",
        "md_stream_generation",
    }:
        value = result[name]
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            _reject(operation, "ctp_credential_binding_scope_invalid")
    if result["account_fingerprint_sha256"][:16] != account_short.removeprefix("acct_"):
        _reject(operation, "ctp_credential_binding_scope_invalid")
    for name in ("td_front", "md_front"):
        digest_name = f"{name}_sha256"
        if (
            hashlib.sha256(result[name].encode("utf-8", "strict")).hexdigest()
            != result[digest_name]
        ):
            _reject(operation, "ctp_credential_binding_scope_invalid")
    return result


@dataclass(frozen=True, slots=True, init=False)
class CtpCredentialBindingScope:
    """Immutable SDK observation supplied to a reviewed deployment adapter."""

    values: Mapping[str, Any] = field(repr=False)
    _seal: object = field(repr=False, compare=False)

    def __init__(self, *, values: Mapping[str, Any], _seal: object) -> None:
        if _seal is not _SCOPE_SEAL:
            raise TypeError("SDK-created CTP credential binding scope required")
        checked = _validate_scope(values, "build_ctp_execution_approval_context")
        object.__setattr__(self, "values", MappingProxyType(checked))
        object.__setattr__(self, "_seal", _seal)

    @property
    def scope_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(dict(self.values))).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return dict(self.values)

    def __getattr__(self, name: str) -> Any:
        if name in _SCOPE_FIELDS:
            return self.values[name]
        raise AttributeError(name)


class CtpCredentialBindingVerifier:
    """SDK-sealed, owner-bound refresh verifier for one reviewed adapter."""

    __slots__ = (
        "_seal",
        "_owner",
        "_adapter",
        "_package_root",
        "_package_sha256",
        "_refresh_method",
        "_refresh_code",
        "_test_provider",
        "_test_seal",
        "_pinned_runtime_config_sha256",
        "_pinned_registration_sha256",
        "_lock",
    )

    def __init__(
        self,
        *,
        owner: object,
        adapter: object,
        package_root: Path | None,
        package_sha256: str,
        refresh_method: object | None,
        test_provider: object | None = None,
        seal: object,
        test_seal: object | None = None,
    ) -> None:
        if seal is not _VERIFIER_SEAL:
            raise TypeError("SDK-created CTP credential binding verifier required")
        if test_provider is None and (
            package_root is None
            or not isinstance(package_sha256, str)
            or not _HEX64.fullmatch(package_sha256)
            or not callable(refresh_method)
        ):
            raise TypeError("reviewed CTP credential binding adapter required")
        if test_provider is not None and test_seal is not _TEST_VERIFIER_SEAL:
            raise TypeError("controlled CTP binding test seam required")
        self._seal = seal
        self._owner = owner
        self._adapter = adapter
        self._package_root = package_root
        self._package_sha256 = package_sha256
        self._refresh_method = refresh_method
        self._refresh_code = getattr(refresh_method, "__code__", None)
        self._test_provider = test_provider
        self._test_seal = test_seal
        self._pinned_runtime_config_sha256: str | None = None
        self._pinned_registration_sha256: str | None = None
        self._lock = threading.RLock()

    @property
    def package_sha256(self) -> str:
        return self._package_sha256

    @property
    def _is_controlled_test_verifier(self) -> bool:
        return self._test_seal is _TEST_VERIFIER_SEAL

    def refresh(
        self,
        scope: CtpCredentialBindingScope,
        *,
        owner: object,
        operation: str,
    ) -> dict[str, str]:
        if (
            type(self) is not CtpCredentialBindingVerifier
            or self._seal is not _VERIFIER_SEAL
            or self._owner is not owner
            or type(scope) is not CtpCredentialBindingScope
            or scope._seal is not _SCOPE_SEAL
        ):
            _reject(operation, "ctp_credential_binding_trust_required")
        with self._lock:
            if self._test_seal is _TEST_VERIFIER_SEAL:
                return self._refresh_test(scope, operation)
            return self._refresh_reviewed(scope, operation)

    def _refresh_test(self, scope: CtpCredentialBindingScope, operation: str) -> dict[str, str]:
        try:
            provider = self._test_provider
            if not callable(provider):
                _reject(operation, "ctp_credential_binding_invalid")
            binding = provider()
        except NormalizedApiError:
            raise
        except Exception:
            _reject(operation, "ctp_credential_binding_unavailable")
        if not isinstance(binding, Mapping) or set(binding) != {
            "credential_binding_key_id",
            "credential_binding_hmac_sha256",
        }:
            _reject(operation, "ctp_credential_binding_invalid")
        key_id = binding.get("credential_binding_key_id")
        mac = binding.get("credential_binding_hmac_sha256")
        if (
            not isinstance(key_id, str)
            or not _SAFE_ID.fullmatch(key_id)
            or not isinstance(mac, str)
            or not _HEX64.fullmatch(mac)
        ):
            _reject(operation, "ctp_credential_binding_invalid")
        # The controlled seam is only a deterministic contract-test fixture.
        # Still scope its output so replay/config/front drift exercises the
        # same binding behavior as the reviewed adapter contract.
        mac = hashlib.sha256(
            _canonical_json({"scope_sha256": scope.scope_sha256, "test_seed": mac})
        ).hexdigest()
        return {
            "credential_binding_key_id": key_id,
            "credential_binding_hmac_sha256": mac,
        }

    def _refresh_reviewed(self, scope: CtpCredentialBindingScope, operation: str) -> dict[str, str]:
        try:
            if (
                self._adapter_refresh_method() is not self._refresh_method
                or getattr(self._refresh_method, "__code__", None) is not self._refresh_code
            ):
                _reject(operation, "ctp_credential_binding_source_changed")
            result = self._refresh_method(self._adapter, scope)
        except NormalizedApiError:
            raise
        except Exception:
            _reject(operation, "ctp_credential_binding_unavailable")
        if (
            type(result).__module__ != _ADAPTER_MODULE
            or type(result).__name__ != _RESULT_CLASS
            or frozenset(getattr(type(result), "__dataclass_fields__", {})) != _RESULT_FIELDS
            or getattr(getattr(type(result), "__dataclass_params__", None), "frozen", False)
            is not True
        ):
            _reject(operation, "ctp_credential_binding_invalid")
        fields = {name: getattr(result, name, None) for name in _RESULT_FIELDS}
        if any(type(fields[name]) is not str for name in _RESULT_FIELDS):
            _reject(operation, "ctp_credential_binding_invalid")
        if (
            fields["scope_sha256"] != scope.scope_sha256
            or fields["account_fingerprint"] != scope.account_fingerprint
            or fields["account_fingerprint_sha256"] != scope.account_fingerprint_sha256
            or fields["td_front"] != scope.td_front
            or fields["md_front"] != scope.md_front
            or fields["backtrader_runtime_sha256"] != self._package_sha256
            or self._package_sha256 != scope.backtrader_runtime_sha256
        ):
            _reject(operation, "ctp_credential_binding_scope_mismatch")
        for name in ("runtime_config_sha256", "registration_sha256"):
            value = fields[name]
            if not isinstance(value, str) or not _HEX64.fullmatch(value):
                _reject(operation, "ctp_credential_binding_invalid")
        if self._pinned_runtime_config_sha256 is None:
            self._pinned_runtime_config_sha256 = fields["runtime_config_sha256"]
            self._pinned_registration_sha256 = fields["registration_sha256"]
        elif (
            fields["runtime_config_sha256"] != self._pinned_runtime_config_sha256
            or fields["registration_sha256"] != self._pinned_registration_sha256
        ):
            _reject(operation, "ctp_credential_binding_scope_mismatch")
        key_id = fields["key_id"]
        mac = fields["hmac_sha256"]
        if (
            not isinstance(key_id, str)
            or not _SAFE_ID.fullmatch(key_id)
            or not isinstance(mac, str)
            or not _HEX64.fullmatch(mac)
        ):
            _reject(operation, "ctp_credential_binding_invalid")
        return {
            "credential_binding_key_id": key_id,
            "credential_binding_hmac_sha256": mac,
        }

    def _adapter_refresh_method(self) -> object:
        if (
            type(self._adapter).__module__ != _ADAPTER_MODULE
            or type(self._adapter).__name__ != _ADAPTER_CLASS
            or getattr(type(self._adapter), "refresh", None) is not self._refresh_method
        ):
            _reject(
                "create_ctp_credential_binding_verifier", "ctp_credential_binding_trust_required"
            )
        return type(self._adapter).refresh


def _new_scope(values: Mapping[str, Any]) -> CtpCredentialBindingScope:
    return CtpCredentialBindingScope(values=values, _seal=_SCOPE_SEAL)


def _new_test_verifier(owner: object, provider: object) -> CtpCredentialBindingVerifier:
    return CtpCredentialBindingVerifier(
        owner=owner,
        adapter=None,
        package_root=None,
        package_sha256="0" * 64,
        refresh_method=None,
        test_provider=provider,
        seal=_VERIFIER_SEAL,
        test_seal=_TEST_VERIFIER_SEAL,
    )


def _new_reviewed_verifier(owner: object, adapter: object) -> CtpCredentialBindingVerifier:
    """Validate package provenance and seal a reviewed runtime adapter."""

    operation = "create_ctp_credential_binding_verifier"
    try:
        package = importlib.import_module("backtrader_runtime")
        module = importlib.import_module(_ADAPTER_MODULE)
        package_file = Path(str(getattr(package, "__file__", "") or "")).resolve(strict=True)
        module_file = Path(str(getattr(module, "__file__", "") or "")).resolve(strict=True)
        adapter_file = Path(inspect.getfile(type(adapter))).resolve(strict=True)
        refresh_method = getattr(type(adapter), "refresh", None)
        refresh_file = Path(inspect.getfile(refresh_method)).resolve(strict=True)
        package_root = package_file.parent
        module_adapter = getattr(module, _ADAPTER_CLASS, None)
        module_spec = getattr(module, "__spec__", None)
        module_origin = Path(str(getattr(module_spec, "origin", "") or "")).resolve(strict=True)
        if (
            module_adapter is None
            or type(adapter) is not module_adapter
            or type(adapter).__module__ != _ADAPTER_MODULE
            or type(adapter).__name__ != _ADAPTER_CLASS
            or module_origin != module_file
            or adapter_file != module_file
            or refresh_file != module_file
            or package_root not in module_file.parents
            or not callable(refresh_method)
        ):
            _reject(operation, "ctp_credential_binding_trust_required")
        package_sha256 = _manifest_digest(package_root)
    except NormalizedApiError:
        raise
    except Exception:
        _reject(operation, "ctp_credential_binding_trust_required")
    return CtpCredentialBindingVerifier(
        owner=owner,
        adapter=adapter,
        package_root=package_root,
        package_sha256=package_sha256,
        refresh_method=refresh_method,
        seal=_VERIFIER_SEAL,
    )


def _is_verifier(value: object, *, owner: object | None = None) -> bool:
    return bool(
        type(value) is CtpCredentialBindingVerifier
        and value._seal is _VERIFIER_SEAL
        and (owner is None or value._owner is owner)
    )


__all__ = ["CtpCredentialBindingScope", "CtpCredentialBindingVerifier"]
