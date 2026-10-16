"""Read narrowly typed account facts from an explicitly registered service."""

from __future__ import annotations

import threading
from collections.abc import Mapping

from bt_api_base.account_certification_protocol import (
    AccountConfigurationObservation,
    ExchangeServerTimeObservation,
)
from bt_api_base.account_protocol import AccountKey

from bt_api_py.service_deployment import MOUNT_PORTS
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError

_ACCOUNT_READ_PORT = "service.account_read"
_OPERATIONS = frozenset({"account.configuration", "account.server_time_probe"})


class ServiceAccountCertificationError(RuntimeError):
    """Finite local diagnosis for a failed certification fact read."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ServiceAccountCertificationClient:
    """Issue explicit, read-only provider-fact RPCs on an existing runtime.

    This client never starts or closes the shared runtime, retries a failed
    request, or substitutes the journal snapshot for a missing provider fact.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        mount: str,
        *,
        account_ref: str,
        expected_account: AccountKey,
        expected_scope_ref: str,
    ) -> None:
        if not isinstance(runtime, ServiceClientRuntime):
            raise ServiceAccountCertificationError("INVALID_RUNTIME")
        if (
            type(mount) is not str
            or mount not in MOUNT_PORTS
            or _ACCOUNT_READ_PORT not in MOUNT_PORTS[mount]
        ):
            raise ServiceAccountCertificationError("INVALID_ACCOUNT_MOUNT")
        if type(account_ref) is not str or not account_ref or account_ref != account_ref.strip():
            raise ServiceAccountCertificationError("INVALID_ACCOUNT_REF")
        if type(expected_account) is not AccountKey:
            raise ServiceAccountCertificationError("INVALID_EXPECTED_ACCOUNT")
        if (
            type(expected_scope_ref) is not str
            or not expected_scope_ref
            or expected_scope_ref != expected_scope_ref.strip()
        ):
            raise ServiceAccountCertificationError("INVALID_EXPECTED_SCOPE_REF")
        self._runtime = runtime
        self._mount = mount
        self._account_ref = account_ref
        self._expected_account = expected_account
        self._expected_scope_ref = expected_scope_ref
        self._owner_thread_id = threading.get_ident()
        self._closed = False

    def read_account_configuration(
        self, *, expected_generation: str | None = None
    ) -> AccountConfigurationObservation:
        """Return the current allowlisted configuration observation."""

        raw = self._read("account.configuration")
        try:
            observation = AccountConfigurationObservation.from_dict(raw)
        except Exception:
            raise ServiceAccountCertificationError("INVALID_ACCOUNT_CONFIGURATION_REPLY") from None
        self._validate_identity(observation.account_key_id, observation.session_generation)
        self._validate_expected_generation(observation.session_generation, expected_generation)
        return observation

    def read_exchange_server_time(
        self, *, expected_generation: str | None = None
    ) -> ExchangeServerTimeObservation:
        """Return the provider's native server-time fact."""

        raw = self._read("account.server_time_probe")
        try:
            observation = ExchangeServerTimeObservation.from_dict(raw)
        except Exception:
            raise ServiceAccountCertificationError("INVALID_SERVER_TIME_REPLY") from None
        self._validate_identity(observation.account_key_id, observation.session_generation)
        self._validate_expected_generation(observation.session_generation, expected_generation)
        return observation

    def close(self) -> None:
        """Disable this local facade without closing the shared runtime."""

        self._assert_owner()
        self._closed = True

    def _read(self, operation: str) -> Mapping[str, object]:
        self._assert_owner()
        if self._closed:
            raise ServiceAccountCertificationError("CLIENT_CLOSED")
        if operation not in _OPERATIONS:
            raise ServiceAccountCertificationError("UNSUPPORTED_OPERATION")
        try:
            self._runtime.maintain()
            before = self._binding(operation)
            reply = self._runtime.call(
                self._mount,
                operation,
                {},
                account_ref=self._account_ref,
                scope_ref=self._expected_scope_ref,
            )
            after = self._binding(operation)
        except ServiceAccountCertificationError:
            raise
        except ServiceRuntimeError:
            raise ServiceAccountCertificationError("ACCOUNT_FACT_READ_FAILED") from None
        except Exception:
            raise ServiceAccountCertificationError("ACCOUNT_FACT_READ_FAILED") from None
        if _binding_signature(before) != _binding_signature(after):
            raise ServiceAccountCertificationError("ACCOUNT_BINDING_CHANGED")
        if not isinstance(reply, Mapping) or set(reply) != {"observation"}:
            raise ServiceAccountCertificationError("INVALID_ACCOUNT_FACT_REPLY")
        observation = reply.get("observation")
        if not isinstance(observation, Mapping):
            raise ServiceAccountCertificationError("INVALID_ACCOUNT_FACT_REPLY")
        return observation

    def _binding(self, operation: str):
        try:
            binding = self._runtime.account_binding_view(
                self._mount,
                self._account_ref,
                required_operations=frozenset({operation}),
            )
        except ServiceRuntimeError:
            raise ServiceAccountCertificationError("ACCOUNT_BINDING_UNAVAILABLE") from None
        if binding.scope_ref != self._expected_scope_ref:
            raise ServiceAccountCertificationError("SCOPE_DENIED")
        return binding

    def _validate_identity(self, account_key_id: str, generation: str) -> None:
        if account_key_id != self._expected_account.account_key_id:
            raise ServiceAccountCertificationError("ACCOUNT_IDENTITY_MISMATCH")
        if type(generation) is not str or not generation:
            raise ServiceAccountCertificationError("INVALID_SESSION_GENERATION")

    @staticmethod
    def _validate_expected_generation(actual: str, expected: str | None) -> None:
        if expected is not None and (type(expected) is not str or actual != expected):
            raise ServiceAccountCertificationError("SESSION_GENERATION_CHANGED")

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceAccountCertificationError("WRONG_OWNER_THREAD")


def _binding_signature(binding: object) -> tuple[object, ...]:
    return (
        getattr(binding, "mount", None),
        getattr(binding, "service_id", None),
        getattr(binding, "instance_id", None),
        getattr(binding, "scope_ref", None),
        getattr(binding, "account_ref", None),
        getattr(binding, "registration_id", None),
        getattr(binding, "service_epoch", None),
        getattr(binding, "rpc_scope", None),
        getattr(binding, "transport_ref", None),
        getattr(binding, "operations", None),
    )


__all__ = ["ServiceAccountCertificationClient", "ServiceAccountCertificationError"]
