"""Neutral Gateway five-component readiness proof client and validator (T-R2-02).

Consumes ``bt_api_base.gateway_readiness_protocol.GatewayReadinessProof`` (and
optional ``GatewayReadinessAttestation`` envelopes) over an injected read-only
fetcher without opening any Monitor or Runtime RPC port in the strategy process.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from bt_api_base.gateway_readiness_protocol import (
    GATEWAY_READINESS_ATTESTATION_SCHEMA,
    GatewayReadinessAttestation,
    GatewayReadinessProof,
    GatewayReadinessProofError,
    parse_gateway_readiness_proof,
    validate_gateway_readiness_proof,
)


class GatewayReadinessClient:
    """Read-only client that fetches and validates Gateway readiness proofs.

    Enforces:
    - Finite error classification distinguishing unreachable, auth failure,
      confirmed protocol incompatibility, unsafe strategy principal grants,
      and component not-ready states.
    - Monotonic ``proof_generation`` tracking (rejecting proof rollback).
    - Optional HMAC-SHA256 attestation verification when ``shared_key`` is
      configured or an attestation envelope is returned.
    """

    def __init__(
        self,
        fetch_proof: Callable[[], Any],
        *,
        expected_account_scope: str | None = None,
        expected_strategy_scope: str | None = None,
        expected_route: str | None = None,
        expected_run_id: str | None = None,
        shared_key: bytes | None = None,
        require_attestation: bool = False,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if not callable(fetch_proof):
            raise GatewayReadinessProofError(
                "GATEWAY_READINESS_COMPONENT_NOT_READY",
                detail="fetch_proof_not_callable",
            )
        if not callable(clock_ns):
            raise GatewayReadinessProofError(
                "GATEWAY_READINESS_COMPONENT_NOT_READY",
                detail="clock_not_callable",
            )
        if require_attestation and (type(shared_key) is not bytes or len(shared_key) < 16):
            raise GatewayReadinessProofError(
                "GATEWAY_READINESS_AUTH_FAILED",
                detail="attestation_key_required",
            )
        self._fetch_proof = fetch_proof
        self._expected_account_scope = expected_account_scope
        self._expected_strategy_scope = expected_strategy_scope
        self._expected_route = expected_route
        self._expected_run_id = expected_run_id
        self._shared_key = shared_key
        self._require_attestation = require_attestation
        self._clock_ns = clock_ns
        self._lock = threading.Lock()
        self._last_generation = 0
        self._last_proof: GatewayReadinessProof | None = None

    @property
    def last_verified_proof(self) -> GatewayReadinessProof | None:
        with self._lock:
            return self._last_proof

    def _unwrap_response(self, raw: Any) -> GatewayReadinessProof:
        is_attestation = isinstance(raw, GatewayReadinessAttestation) or (
            isinstance(raw, Mapping)
            and raw.get("schema") == GATEWAY_READINESS_ATTESTATION_SCHEMA
        )
        if is_attestation:
            att = (
                raw
                if isinstance(raw, GatewayReadinessAttestation)
                else GatewayReadinessAttestation.from_mapping(raw)
            )
            if self._shared_key is not None and not att.verify_mac(self._shared_key):
                raise GatewayReadinessProofError(
                    "GATEWAY_READINESS_AUTH_FAILED",
                    detail="attestation_mac_invalid",
                )
            return att.proof
        if self._require_attestation:
            raise GatewayReadinessProofError(
                "GATEWAY_READINESS_AUTH_FAILED",
                detail="attestation_missing",
            )
        return parse_gateway_readiness_proof(raw)

    def verify_ready(
        self,
        *,
        require_ready_for_write: bool = True,
        allow_cancel_during_outage: bool = False,
    ) -> GatewayReadinessProof:
        """Fetch and validate a fresh ``GatewayReadinessProof`` (fail-closed)."""
        try:
            raw = self._fetch_proof()
        except GatewayReadinessProofError:
            raise
        except PermissionError:
            raise GatewayReadinessProofError(
                "GATEWAY_READINESS_AUTH_FAILED",
                detail="transport_permission_denied",
            ) from None
        except Exception:
            raise GatewayReadinessProofError(
                "GATEWAY_READINESS_UNREACHABLE",
                detail="fetch_failed",
            ) from None

        proof = self._unwrap_response(raw)
        now = self._clock_ns()
        validated = validate_gateway_readiness_proof(
            proof,
            expected_account_scope=self._expected_account_scope,
            expected_strategy_scope=self._expected_strategy_scope,
            expected_route=self._expected_route,
            expected_run_id=self._expected_run_id,
            now_ns=now,
            require_ready_for_write=require_ready_for_write,
            allow_cancel_during_outage=allow_cancel_during_outage,
        )

        with self._lock:
            if validated.proof_generation < self._last_generation:
                raise GatewayReadinessProofError(
                    "GATEWAY_READINESS_COMPONENT_NOT_READY",
                    detail="proof_generation_rollback",
                )
            self._last_generation = validated.proof_generation
            self._last_proof = validated
        return validated


__all__ = [
    "GatewayReadinessClient",
]
