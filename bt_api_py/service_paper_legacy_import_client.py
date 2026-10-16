"""Typed application client for a Host-owned legacy Paper import target.

The facade borrows an already-started ``ServiceClientRuntime`` and never owns
its registration, transport, store, target scope, or shutdown lifecycle.  It
routes only through ``application.paper_legacy_import`` and never retries a
write whose transport outcome is uncertain.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from bt_api_execution.paper_legacy_import import PaperLegacyImportV1

from .service_deployment import MOUNT_PORTS
from .service_runtime import ServiceClientRuntime, ServiceRuntimeError

_MOUNT = "application.paper_legacy_import"
_PORT = "service.paper_legacy_import"
_STATUS = "paper.import.status"
_INSPECT_SOURCE = "paper.import.inspect_source"
_PREPARE = "paper.import.prepare"
_ACTIVATE = "paper.import.activate"
_ABORT = "paper.import.abort"
_OPERATIONS = frozenset({_STATUS, _INSPECT_SOURCE, _PREPARE, _ACTIVATE, _ABORT})
_REPLY_CODES = frozenset(
    {
        "IMPORT_INVALID",
        "IMPORT_OWNER_BUSY",
        "IMPORT_RESIDUAL_UNREPRESENTABLE",
        "IMPORT_STATE_CONFLICT",
        "IMPORT_STORE_UNAVAILABLE",
        "IMPORT_WRITE_UNCERTAIN",
        "INVALID_ARGUMENTS",
        "OK",
        "SOURCE_OWNER_MISMATCH",
        "SCOPE_DENIED",
        "UNSUPPORTED_OPERATION",
    }
)


class PaperLegacyImportClientError(RuntimeError):
    """Finite local diagnostics for an invalid import route or reply."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PaperLegacyImportReply:
    """Detached bounded result returned by the Host import adapter."""

    ok: bool
    code: str
    result: Mapping[str, Any]


class PaperLegacyImportClient:
    """Call one pre-registered trusted legacy-import account binding.

    ``account_ref`` and ``scope_ref`` select an already authorized runtime
    binding.  The client cannot select or transmit an Execution store, target
    mapping, or target scope; those remain owned by the ServiceHost.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        *,
        account_ref: str,
        scope_ref: str,
    ) -> None:
        if not isinstance(runtime, ServiceClientRuntime):
            raise PaperLegacyImportClientError("INVALID_RUNTIME")
        if type(account_ref) is not str or not account_ref or account_ref != account_ref.strip():
            raise PaperLegacyImportClientError("INVALID_ACCOUNT_REF")
        if type(scope_ref) is not str or not scope_ref or scope_ref != scope_ref.strip():
            raise PaperLegacyImportClientError("INVALID_SCOPE_REF")
        if _PORT not in MOUNT_PORTS.get(_MOUNT, ()):
            raise PaperLegacyImportClientError("INVALID_IMPORT_MOUNT")
        self._runtime = runtime
        self._account_ref = account_ref
        self._scope_ref = scope_ref
        self._owner_thread_id = threading.get_ident()

    def status(self) -> PaperLegacyImportReply:
        """Read the Host's current import receipt without starting migration."""

        return self._call(_STATUS, {})

    def inspect_source(self, source_account_id: str) -> PaperLegacyImportReply:
        """Read the target-side status for one legacy account identifier."""

        if (
            type(source_account_id) is not str
            or not source_account_id
            or source_account_id != source_account_id.strip()
            or len(source_account_id) > 256
            or not source_account_id.isprintable()
        ):
            raise PaperLegacyImportClientError("INVALID_SOURCE_ACCOUNT_ID")
        return self._call(_INSPECT_SOURCE, {"source_account_id": source_account_id})

    def prepare(self, imported: PaperLegacyImportV1) -> PaperLegacyImportReply:
        """Persist a validated source snapshot and dormant target receipt."""

        if type(imported) is not PaperLegacyImportV1:
            raise PaperLegacyImportClientError("INVALID_IMPORT_SNAPSHOT")
        return self._call(_PREPARE, {"imported": imported.to_payload()})

    def activate(self) -> PaperLegacyImportReply:
        """Activate a previously prepared compatible target import."""

        return self._call(_ACTIVATE, {})

    def abort(self, source_sha256: str) -> PaperLegacyImportReply:
        """Abort the exact prepared source snapshot by its frozen digest."""

        if (
            type(source_sha256) is not str
            or len(source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_sha256)
        ):
            raise PaperLegacyImportClientError("INVALID_SOURCE_DIGEST")
        return self._call(_ABORT, {"source_sha256": source_sha256})

    def _call(self, operation: str, arguments: Mapping[str, Any]) -> PaperLegacyImportReply:
        if threading.get_ident() != self._owner_thread_id:
            raise PaperLegacyImportClientError("WRONG_OWNER_THREAD")
        if operation not in _OPERATIONS:
            raise PaperLegacyImportClientError("INVALID_OPERATION")
        try:
            body = self._runtime.call(
                _MOUNT,
                operation,
                arguments,
                account_ref=self._account_ref,
                scope_ref=self._scope_ref,
            )
        except ServiceRuntimeError:
            raise
        if (
            not isinstance(body, Mapping)
            or set(body) != {"ok", "code", "result"}
            or type(body.get("ok")) is not bool
            or type(body.get("code")) is not str
            or body.get("code") not in _REPLY_CODES
            or not isinstance(body.get("result"), Mapping)
        ):
            raise PaperLegacyImportClientError("INVALID_REPLY")
        return PaperLegacyImportReply(
            body["ok"],
            body["code"],
            MappingProxyType(dict(body["result"])),
        )


__all__ = [
    "PaperLegacyImportClient",
    "PaperLegacyImportClientError",
    "PaperLegacyImportReply",
]
