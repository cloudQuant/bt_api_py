"""Authenticated account-side producer for durable group Risk reservations.

The service binds one Registry-authorized coordinator request to the existing
Risk gate and the host-local :class:`AcceptedGroupBindingStore`. It has no
provider path and never accepts Risk hashes, account scopes, or run authority
from the request body.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.contracts import OrderIntent, order_intent_from_payload
from bt_api_execution.group_contracts import group_leg_intent_id, risk_account_scope_fingerprint
from bt_api_execution.group_order_contracts import (
    GroupAccountReservationSnapshot,
    GroupReservationPhase,
    GroupRiskLegSnapshot,
)
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    RiskAdmissionDeniedError,
    RiskGroupReservationReceipt,
    RiskIntent,
)

from .service_execution_group_client import (
    AcceptedGroupBinding,
    AcceptedGroupBindingError,
    AcceptedGroupBindingStore,
    AcceptedGroupConflictError,
)
from .service_registry import (
    ActiveRegistrationContext,
    ServiceCallContext,
    TrustedGroupReservationPolicy,
)

SUPPORTED_OPERATIONS = frozenset(
    {"reservations.reserve", "reservations.query", "reservations.release"}
)
PORT = "service.group_reservations"
_WRITE_OPERATIONS = frozenset({"reservations.reserve", "reservations.release"})
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807
_TERMINAL_RESERVATION_STATES = frozenset({"RELEASED", "EXPIRED", "NO_FILL_TERMINAL"})


class GroupReservationsServiceError(RuntimeError):
    """Finite request or current-authority error with no provider details."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or _REFERENCE.fullmatch(code) is None:
            raise ValueError("invalid group reservation service error")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GroupReservationTarget:
    """Trusted Risk mapping for one exact account-owner wire scope."""

    account_scope: AccountScope
    admission: DurableRiskAdmissionAdapter

    def __post_init__(self) -> None:
        if type(self.account_scope) is not AccountScope:
            raise TypeError("group target requires a configured Risk AccountScope")
        if type(self.admission) is not DurableRiskAdmissionAdapter:
            raise TypeError("group target requires the durable Risk admission adapter")


def validate_service_options(
    options: Mapping[str, object], bind_ports: tuple[str, ...]
) -> Mapping[str, str]:
    """Validate the fixed group-reservations role without loading Risk state."""

    if (
        not isinstance(options, Mapping)
        or set(options) != {"role"}
        or options.get("role") != "group_reservations"
        or type(bind_ports) is not tuple
        or bind_ports != (PORT,)
    ):
        raise GroupReservationsServiceError("INVALID_OPTIONS")
    return MappingProxyType({"role": "group_reservations"})


class GroupReservationsService:
    """Serve the exact same-account reserve/query/release account port."""

    SUPPORTED_OPERATIONS = SUPPORTED_OPERATIONS

    def __init__(
        self,
        targets: Mapping[WireScope, GroupReservationTarget],
        accepted_group_store: AcceptedGroupBindingStore,
        trusted_policies: tuple[TrustedGroupReservationPolicy, ...],
        *,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if not isinstance(targets, Mapping) or not targets:
            raise GroupReservationsServiceError("INVALID_TARGETS")
        target_copy: dict[WireScope, GroupReservationTarget] = {}
        for wire_scope, target in targets.items():
            if type(wire_scope) is not WireScope or type(target) is not GroupReservationTarget:
                raise GroupReservationsServiceError("INVALID_TARGETS")
            if not self._stable_scope(wire_scope):
                raise GroupReservationsServiceError("INVALID_TARGET_SCOPE")
            target_copy[wire_scope] = target
        if type(accepted_group_store) is not AcceptedGroupBindingStore:
            raise GroupReservationsServiceError("INVALID_ACCEPTED_GROUP_STORE")
        if type(trusted_policies) is not tuple or not trusted_policies or any(
            type(policy) is not TrustedGroupReservationPolicy for policy in trusted_policies
        ):
            raise GroupReservationsServiceError("INVALID_TRUSTED_POLICIES")
        instance_ids = {policy.instance_id for policy in trusted_policies}
        if len(instance_ids) != 1:
            raise GroupReservationsServiceError("INVALID_TRUSTED_POLICIES")
        if not callable(clock_ns):
            raise GroupReservationsServiceError("INVALID_CLOCK")
        seen: set[tuple[object, ...]] = set()
        for policy in trusted_policies:
            key = (
                policy.principal_ref,
                policy.identity,
                policy.scope_ref,
                policy.scope,
                policy.run_id,
            )
            if key in seen or policy.port != PORT or policy.scope not in target_copy:
                raise GroupReservationsServiceError("INVALID_TRUSTED_POLICIES")
            if policy.account_ref != policy.scope.account_ref:
                raise GroupReservationsServiceError("INVALID_TRUSTED_POLICIES")
            seen.add(key)

        self._targets = MappingProxyType(target_copy)
        self._store = accepted_group_store
        self._policies = trusted_policies
        self._instance_id = next(iter(instance_ids))
        self._clock_ns = clock_ns
        self._lock = threading.RLock()
        self._registrations: Mapping[str, ActiveRegistrationContext] = MappingProxyType({})
        self._closed = False

    @property
    def supported_operations(self) -> frozenset[str]:
        return self.SUPPORTED_OPERATIONS

    @property
    def ready(self) -> bool:
        with self._lock:
            return not self._closed and bool(self._targets) and bool(self._policies)

    def maintain_registrations(
        self, contexts: tuple[ActiveRegistrationContext, ...]
    ) -> None:
        """Replace the current registration view; it grants no authority alone."""

        if type(contexts) is not tuple:
            raise GroupReservationsServiceError("INVALID_REGISTRATION_SNAPSHOT")
        retained: dict[str, ActiveRegistrationContext] = {}
        for registration in contexts:
            if type(registration) is not ActiveRegistrationContext:
                raise GroupReservationsServiceError("INVALID_REGISTRATION_SNAPSHOT")
            if registration.instance_id != self._instance_id:
                continue
            context = registration.context
            if (
                registration.port != PORT
                or registration.operations != self.SUPPORTED_OPERATIONS
                or registration.write_operations != _WRITE_OPERATIONS
                or context.scope not in self._targets
                or self._find_policy(context, registration) is None
            ):
                continue
            previous = retained.get(context.registration_id)
            if previous is not None and previous != registration:
                raise GroupReservationsServiceError("REGISTRATION_SNAPSHOT_CONFLICT")
            retained[context.registration_id] = registration
        with self._lock:
            if self._closed:
                raise GroupReservationsServiceError("CLOSED")
            self._registrations = MappingProxyType(retained)

    def handle(
        self,
        operation: str,
        *,
        context: ServiceCallContext,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Handle a Registry-authenticated account reservation operation."""

        try:
            registration, _policy, target = self._authorize(operation, context)
            if not isinstance(arguments, Mapping):
                raise GroupReservationsServiceError("INVALID_REQUEST")
            if operation == "reservations.reserve":
                result = self._reserve(registration, target, arguments)
            elif operation == "reservations.query":
                result = self._query(registration, target, arguments)
            elif operation == "reservations.release":
                result = self._release(registration, target, arguments)
            else:  # _authorize has already rejected unsupported operations.
                raise GroupReservationsServiceError("UNKNOWN_OPERATION")
            return {"ok": True, "code": "OK", "result": result.to_payload()}
        except GroupReservationsServiceError as error:
            return self._failure(error.code)
        except AcceptedGroupConflictError:
            return self._failure("GROUP_BINDING_CONFLICT")
        except AcceptedGroupBindingError:
            return self._failure("GROUP_BINDING_UNAVAILABLE")
        except RiskAdmissionDeniedError as error:
            # The Risk adapter projects only exact, finite gate denials here.
            return self._failure(error.code)

    def close(self) -> None:
        """Forget the registration view; resource ownership closes the store."""

        with self._lock:
            self._registrations = MappingProxyType({})
            self._closed = True

    def _authorize(
        self, operation: str, context: ServiceCallContext
    ) -> tuple[ActiveRegistrationContext, TrustedGroupReservationPolicy, GroupReservationTarget]:
        if type(operation) is not str or operation not in self.SUPPORTED_OPERATIONS:
            raise GroupReservationsServiceError("UNKNOWN_OPERATION")
        if type(context) is not ServiceCallContext:
            raise GroupReservationsServiceError("SERVICE_CONTEXT_REQUIRED")
        if (
            type(context.identity) is not WireIdentity
            or context.identity.role is not WireRole.SERVICE
            or context.identity.principal_ref != context.principal_ref
            or type(context.scope) is not WireScope
            or not self._stable_scope(context.scope)
            or type(context.registration_id) is not str
        ):
            raise GroupReservationsServiceError("SERVICE_AUTHORITY_INVALID")
        with self._lock:
            if self._closed:
                raise GroupReservationsServiceError("CLOSED")
            registration = self._registrations.get(context.registration_id)
        if registration is None or not self._same_call(registration.context, context):
            raise GroupReservationsServiceError("REGISTRATION_NOT_ACTIVE")
        if registration.expires_at <= self._clock_ns() / 1_000_000_000:
            raise GroupReservationsServiceError("REGISTRATION_EXPIRED")
        if (
            registration.instance_id != self._instance_id
            or registration.port != PORT
            or registration.operations != self.SUPPORTED_OPERATIONS
            or registration.write_operations != _WRITE_OPERATIONS
            or operation not in registration.operations
            or (operation in _WRITE_OPERATIONS and operation not in registration.write_operations)
        ):
            raise GroupReservationsServiceError("REGISTRATION_SCOPE_MISMATCH")
        target = self._targets.get(context.scope)
        if target is None:
            raise GroupReservationsServiceError("SCOPE_DENIED")
        policy = self._find_policy(context, registration)
        if policy is None or operation not in policy.operations:
            raise GroupReservationsServiceError("POLICY_DENIED")
        if (
            policy.instance_id != self._instance_id
            or policy.port != PORT
            or policy.scope_ref != registration.scope_ref
            or policy.account_ref != context.scope.account_ref
            or policy.run_id != registration.strategy_write_run_id
        ):
            raise GroupReservationsServiceError("POLICY_STALE")
        return registration, policy, target

    def _find_policy(
        self,
        context: ServiceCallContext,
        registration: ActiveRegistrationContext,
    ) -> TrustedGroupReservationPolicy | None:
        for policy in self._policies:
            if (
                policy.instance_id == registration.instance_id
                and policy.principal_ref == context.principal_ref
                and policy.identity == context.identity
                and policy.scope_ref == registration.scope_ref
                and policy.scope == context.scope
                and policy.account_ref == context.scope.account_ref
                and policy.run_id == registration.strategy_write_run_id
            ):
                return policy
        return None

    @staticmethod
    def _same_call(expected: ServiceCallContext, actual: ServiceCallContext) -> bool:
        return (
            expected.identity == actual.identity
            and expected.scope == actual.scope
            and expected.registration_id == actual.registration_id
            and expected.principal_ref == actual.principal_ref
        )

    @staticmethod
    def _stable_scope(scope: WireScope) -> bool:
        return (
            type(scope) is WireScope
            and type(scope.account_ref) is str
            and bool(scope.account_ref)
            and scope.account_refs == ()
            and type(scope.execution_domain_ref) is str
            and bool(scope.execution_domain_ref)
            and type(scope.portfolio_ref) is str
            and bool(scope.portfolio_ref)
            and type(scope.strategy_ref) is str
            and bool(scope.strategy_ref)
            and scope.run_id is None
            and scope.manual_book_ref is None
            and scope.group_id is None
            and scope.leg_id is None
        )

    def _reserve(
        self,
        registration: ActiveRegistrationContext,
        target: GroupReservationTarget,
        arguments: Mapping[str, Any],
    ) -> GroupAccountReservationSnapshot:
        if set(arguments) != {"origin_run_id", "group_id", "deadline_ns", "members"}:
            raise GroupReservationsServiceError("INVALID_REQUEST")
        origin_run_id = self._reference(arguments["origin_run_id"], "origin_run_id")
        group_id = self._reference(arguments["group_id"], "group_id")
        deadline_ns = arguments["deadline_ns"]
        if (
            type(deadline_ns) is not int
            or not 0 < deadline_ns <= _MAX_SQLITE_INTEGER
            or deadline_ns <= self._clock_ns()
        ):
            raise GroupReservationsServiceError("GROUP_DEADLINE_INVALID")
        members = self._parse_members(
            arguments["members"],
            registration.context.scope,
            group_id=group_id,
            deadline_ns=deadline_ns,
        )
        binding = self._store.get_binding(
            registration,
            origin_run_id=origin_run_id,
            group_id=group_id,
            operation="reservations.reserve",
        )
        if binding is not None:
            # begin_reservation is the store's exact-request idempotency check.
            binding = self._store.begin_reservation(
                registration,
                origin_run_id=origin_run_id,
                group_id=group_id,
                deadline_ns=deadline_ns,
                members=self._member_payloads(members),
            )
            if binding.reservation is not None:
                risk_receipt = target.admission.read_group_vector_snapshot(
                    target.account_scope, group_id
                )
                snapshot = self._snapshot_from_receipt(
                    risk_receipt,
                    binding,
                    target,
                    expected_risk_legs=None,
                )
                self._store.record_reservation_snapshot(
                    registration,
                    origin_run_id=origin_run_id,
                    group_id=group_id,
                    reservation=snapshot,
                    operation="reservations.reserve",
                )
                return snapshot
            mapped = self._map_members(target, binding.members)
            try:
                risk_receipt = target.admission.read_group_vector_snapshot(
                    target.account_scope, group_id
                )
            except RiskAdmissionDeniedError as error:
                if error.code != "GROUP_NOT_FOUND":
                    raise
                risk_receipt = None
            if risk_receipt is not None:
                snapshot = self._snapshot_from_receipt(
                    risk_receipt,
                    binding,
                    target,
                    expected_risk_legs=mapped,
                )
                return self._store.complete_reservation(binding, snapshot).reservation  # type: ignore[return-value]
            risk_receipt = target.admission.reserve_group_vector(
                target.account_scope,
                group_id,
                tuple(members),
                deadline_ns=deadline_ns,
            )
            snapshot = self._snapshot_from_receipt(
                risk_receipt,
                binding,
                target,
                expected_risk_legs=mapped,
            )
            completed = self._store.complete_reservation(binding, snapshot)
            if completed.reservation is None:
                raise RuntimeError("accepted group completion returned no receipt")
            return completed.reservation

        mapped = self._map_members(target, members)
        try:
            preexisting = target.admission.read_group_vector_snapshot(
                target.account_scope, group_id
            )
        except RiskAdmissionDeniedError as error:
            if error.code != "GROUP_NOT_FOUND":
                raise
            preexisting = None
        if preexisting is not None:
            # Do not bind an older Risk row merely because a new caller reused
            # its group ID. A concurrent exact reserve has its local PENDING
            # row committed before its Risk effect; re-read that store fact.
            raced = self._store.get_binding(
                registration,
                origin_run_id=origin_run_id,
                group_id=group_id,
                operation="reservations.reserve",
            )
            if raced is None:
                raise GroupReservationsServiceError("UNBOUND_RISK_GROUP")
            binding = self._store.begin_reservation(
                registration,
                origin_run_id=origin_run_id,
                group_id=group_id,
                deadline_ns=deadline_ns,
                members=self._member_payloads(members),
            )
            snapshot = self._snapshot_from_receipt(
                preexisting,
                binding,
                target,
                expected_risk_legs=mapped,
            )
            if binding.reservation is None:
                return self._store.complete_reservation(binding, snapshot).reservation  # type: ignore[return-value]
            self._store.record_reservation_snapshot(
                registration,
                origin_run_id=origin_run_id,
                group_id=group_id,
                reservation=snapshot,
                operation="reservations.reserve",
            )
            return snapshot

        binding = self._store.begin_reservation(
            registration,
            origin_run_id=origin_run_id,
            group_id=group_id,
            deadline_ns=deadline_ns,
            members=self._member_payloads(members),
        )
        if binding.reservation is not None:
            # The store row appeared after the initial read. Its exact durable
            # receipt makes this a replay, so query instead of replacing it.
            risk_receipt = target.admission.read_group_vector_snapshot(
                target.account_scope, group_id
            )
            snapshot = self._snapshot_from_receipt(
                risk_receipt, binding, target, expected_risk_legs=None
            )
            self._store.record_reservation_snapshot(
                registration,
                origin_run_id=origin_run_id,
                group_id=group_id,
                reservation=snapshot,
                operation="reservations.reserve",
            )
            return snapshot
        risk_receipt = target.admission.reserve_group_vector(
            target.account_scope,
            group_id,
            tuple(members),
            deadline_ns=deadline_ns,
        )
        snapshot = self._snapshot_from_receipt(
            risk_receipt,
            binding,
            target,
            expected_risk_legs=mapped,
        )
        completed = self._store.complete_reservation(binding, snapshot)
        if completed.reservation is None:
            raise RuntimeError("accepted group completion returned no receipt")
        return completed.reservation

    def _query(
        self,
        registration: ActiveRegistrationContext,
        target: GroupReservationTarget,
        arguments: Mapping[str, Any],
    ) -> GroupAccountReservationSnapshot:
        if set(arguments) != {"origin_run_id", "group_id"}:
            raise GroupReservationsServiceError("INVALID_REQUEST")
        origin_run_id = self._reference(arguments["origin_run_id"], "origin_run_id")
        group_id = self._reference(arguments["group_id"], "group_id")
        binding = self._store.get_binding(
            registration,
            origin_run_id=origin_run_id,
            group_id=group_id,
            operation="reservations.query",
        )
        if binding is None:
            raise GroupReservationsServiceError("GROUP_NOT_FOUND")
        risk_receipt = target.admission.read_group_vector_snapshot(
            target.account_scope, group_id
        )
        expected = (
            self._map_members(target, binding.members)
            if binding.reservation is None
            else None
        )
        return self._snapshot_from_receipt(
            risk_receipt,
            binding,
            target,
            expected_risk_legs=expected,
        )

    def _release(
        self,
        registration: ActiveRegistrationContext,
        target: GroupReservationTarget,
        arguments: Mapping[str, Any],
    ) -> GroupAccountReservationSnapshot:
        if set(arguments) != {"origin_run_id", "group_id", "reason"}:
            raise GroupReservationsServiceError("INVALID_REQUEST")
        origin_run_id = self._reference(arguments["origin_run_id"], "origin_run_id")
        group_id = self._reference(arguments["group_id"], "group_id")
        reason = arguments["reason"]
        if type(reason) is not str or _REASON.fullmatch(reason) is None:
            raise GroupReservationsServiceError("INVALID_RELEASE_REASON")
        binding = self._store.get_binding(
            registration,
            origin_run_id=origin_run_id,
            group_id=group_id,
            operation="reservations.release",
        )
        if binding is None:
            raise GroupReservationsServiceError("GROUP_NOT_FOUND")
        if binding.reservation is None:
            # A lost reserve reply is reconciled only by the exact reserve
            # replay. Release must not adopt or complete a PENDING binding.
            raise GroupReservationsServiceError("GROUP_BINDING_INCOMPLETE")
        risk_receipt = target.admission.read_group_vector_snapshot(
            target.account_scope, group_id
        )
        snapshot = self._snapshot_from_receipt(
            risk_receipt,
            binding,
            target,
            expected_risk_legs=None,
        )
        released = target.admission.release_group_unclaimed(
            target.account_scope, group_id, reason
        )
        snapshot = self._snapshot_from_receipt(
            released,
            binding,
            target,
            expected_risk_legs=None,
        )
        snapshot = self._released_operation_projection(snapshot, binding, target)
        return self._store.record_reservation_snapshot(
            registration,
            origin_run_id=origin_run_id,
            group_id=group_id,
            reservation=snapshot,
            operation="reservations.release",
        ).reservation  # type: ignore[return-value]

    @staticmethod
    def _released_operation_projection(
        snapshot: GroupAccountReservationSnapshot,
        binding: AcceptedGroupBinding,
        target: GroupReservationTarget,
    ) -> GroupAccountReservationSnapshot:
        """Project a completed release effect without rewriting Risk facts.

        Risk may expire an unclaimed vector while processing the explicit
        release request. The account-service envelope can report completion
        when every returned leg is terminal and none was claimed or resolved;
        its per-leg state remains the exact durable Risk state. This is used
        only on the actual release response, never on a read-only query.
        """

        if (
            type(snapshot) is not GroupAccountReservationSnapshot
            or type(binding) is not AcceptedGroupBinding
            or type(target) is not GroupReservationTarget
        ):
            raise GroupReservationsServiceError("INVALID_RELEASE_RESULT")
        accepted = binding.reservation
        expected_leg_ids = {leg_id for leg_id, _intent in binding.members}
        actual_legs = {leg.leg_id: leg for leg in snapshot.legs}
        accepted_legs = {} if accepted is None else {leg.leg_id: leg for leg in accepted.legs}
        if (
            accepted is None
            or snapshot.group_id != binding.group_id
            or snapshot.account_ref != binding.account_ref
            or snapshot.scope_fingerprint != risk_account_scope_fingerprint(target.account_scope.key)
            or snapshot.group_fingerprint != accepted.group_fingerprint
            or snapshot.policy_fingerprint != accepted.policy_fingerprint
            or snapshot.deadline_ns != binding.deadline_ns
            or set(actual_legs) != expected_leg_ids
            or set(accepted_legs) != expected_leg_ids
        ):
            raise GroupReservationsServiceError("INVALID_RELEASE_RESULT")
        for leg_id, actual in actual_legs.items():
            prior = accepted_legs[leg_id]
            if (
                actual.permit_id != prior.permit_id
                or actual.intent_id != prior.intent_id
                or actual.reserved_intent_hash != prior.reserved_intent_hash
                or actual.exposure_profile_fingerprint != prior.exposure_profile_fingerprint
            ):
                raise GroupReservationsServiceError("INVALID_RELEASE_RESULT")
        if all(
            leg.state in {"RELEASED", "EXPIRED"}
            and not leg.claimed
            and not leg.resolved
            for leg in actual_legs.values()
        ):
            return replace(snapshot, phase=GroupReservationPhase.RELEASED)
        return snapshot

    @staticmethod
    def _reference(value: object, name: str) -> str:
        if type(value) is not str or _REFERENCE.fullmatch(value) is None:
            raise GroupReservationsServiceError("INVALID_" + name.upper())
        return value

    @staticmethod
    def _parse_members(
        value: object,
        scope: WireScope,
        *,
        group_id: str,
        deadline_ns: int,
    ) -> tuple[tuple[str, OrderIntent], ...]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise GroupReservationsServiceError("INVALID_MEMBERS")
        if not 1 <= len(value) <= 3:
            raise GroupReservationsServiceError("INVALID_MEMBERS")
        members: list[tuple[str, OrderIntent]] = []
        for item in value:
            if not isinstance(item, Mapping) or set(item) != {"leg_id", "intent"}:
                raise GroupReservationsServiceError("INVALID_MEMBERS")
            leg_id = item["leg_id"]
            payload = item["intent"]
            if type(leg_id) is not str or _REFERENCE.fullmatch(leg_id) is None:
                raise GroupReservationsServiceError("INVALID_MEMBERS")
            if not isinstance(payload, Mapping):
                raise GroupReservationsServiceError("INVALID_MEMBERS")
            try:
                intent = order_intent_from_payload(payload)
            except Exception:
                raise GroupReservationsServiceError("INVALID_ORDER_INTENT") from None
            try:
                expected_intent_id = group_leg_intent_id(group_id, leg_id)
            except (TypeError, ValueError):
                raise GroupReservationsServiceError("INVALID_MEMBERS") from None
            if (
                intent.intent_id != expected_intent_id
                or intent.scope.account_ref != scope.account_ref
                or intent.scope.strategy_id != scope.strategy_ref
                or intent.deadline_ns != deadline_ns
            ):
                raise GroupReservationsServiceError("GROUP_MEMBER_SCOPE_MISMATCH")
            members.append((leg_id, intent))
        if len({leg for leg, _ in members}) != len(members):
            raise GroupReservationsServiceError("INVALID_MEMBERS")
        return tuple(sorted(members, key=lambda item: item[0]))

    @staticmethod
    def _map_members(
        target: GroupReservationTarget,
        members: tuple[tuple[str, OrderIntent], ...],
    ) -> Mapping[str, RiskIntent]:
        mapped: dict[str, RiskIntent] = {}
        for leg_id, intent in members:
            # Pass the exact received OrderIntent through Risk's trusted mapper.
            # The owner-selected AccountScope is a separate explicit argument.
            risk_intent = target.admission.map_group_intent(
                intent, scope=target.account_scope
            )
            if (
                type(risk_intent) is not RiskIntent
                or risk_intent.scope != target.account_scope
                or risk_intent.intent_id != intent.intent_id
                or risk_intent.exposure_profile_fingerprint is None
            ):
                raise GroupReservationsServiceError("RISK_MAPPING_INVALID")
            mapped[leg_id] = risk_intent
        return MappingProxyType(mapped)

    @staticmethod
    def _snapshot_from_receipt(
        receipt: RiskGroupReservationReceipt,
        binding: AcceptedGroupBinding,
        target: GroupReservationTarget,
        *,
        expected_risk_legs: Mapping[str, RiskIntent] | None,
    ) -> GroupAccountReservationSnapshot:
        if type(receipt) is not RiskGroupReservationReceipt:
            raise RuntimeError("Risk returned an invalid group snapshot type")
        expected_members = dict(binding.members)
        risk_legs = {leg.leg_id: leg for leg in receipt.legs}
        if (
            receipt.scope != target.account_scope
            or receipt.group_id != binding.group_id
            or receipt.deadline_ns != binding.deadline_ns
            or set(risk_legs) != set(expected_members)
        ):
            raise RuntimeError("Risk group snapshot differs from accepted account binding")
        bound_receipt = binding.reservation
        if bound_receipt is not None and (
            receipt.group_fingerprint != bound_receipt.group_fingerprint
            or receipt.policy_fingerprint != bound_receipt.policy_fingerprint
        ):
            raise RuntimeError("Risk group fingerprint changed after acceptance")
        output_legs: list[GroupRiskLegSnapshot] = []
        for leg_id, intent in binding.members:
            risk_leg = risk_legs[leg_id]
            if risk_leg.intent_id != intent.intent_id:
                raise RuntimeError("Risk group intent identity differs from accepted binding")
            if expected_risk_legs is not None:
                mapped = expected_risk_legs.get(leg_id)
                if mapped is None or (
                    risk_leg.reserved_intent_hash != mapped.fingerprint
                    or risk_leg.exposure_profile_fingerprint
                    != mapped.exposure_profile_fingerprint
                ):
                    raise RuntimeError("Risk group receipt does not match trusted mapping")
            if bound_receipt is not None:
                prior = next(
                    (item for item in bound_receipt.legs if item.leg_id == leg_id), None
                )
                if prior is None or (
                    risk_leg.permit_id != prior.permit_id
                    or risk_leg.intent_id != prior.intent_id
                    or risk_leg.reserved_intent_hash != prior.reserved_intent_hash
                    or risk_leg.exposure_profile_fingerprint
                    != prior.exposure_profile_fingerprint
                ):
                    raise RuntimeError("Risk group leg identity changed after acceptance")
            output_legs.append(
                GroupRiskLegSnapshot(
                    leg_id=risk_leg.leg_id,
                    permit_id=risk_leg.permit_id,
                    intent_id=risk_leg.intent_id,
                    reserved_intent_hash=risk_leg.reserved_intent_hash,
                    exposure_profile_fingerprint=risk_leg.exposure_profile_fingerprint,
                    actual_intent_hash=risk_leg.actual_intent_hash,
                    state=risk_leg.state.name,
                    claimed=risk_leg.claimed,
                    resolved=risk_leg.resolved,
                    expires_at_ns=risk_leg.expires_at_ns,
                )
            )
        states = {leg.state for leg in output_legs}
        if states.issubset(_TERMINAL_RESERVATION_STATES):
            phase = (
                GroupReservationPhase.RELEASED
                if "RELEASED" in states
                else GroupReservationPhase.DENIED
            )
        else:
            phase = GroupReservationPhase.ACTIVE
        return GroupAccountReservationSnapshot(
            group_id=receipt.group_id,
            account_ref=binding.account_ref,
            scope_fingerprint=risk_account_scope_fingerprint(receipt.scope.key),
            phase=phase,
            group_fingerprint=receipt.group_fingerprint,
            policy_fingerprint=receipt.policy_fingerprint,
            deadline_ns=receipt.deadline_ns,
            legs=tuple(output_legs),
        )

    @staticmethod
    def _failure(code: str) -> Mapping[str, Any]:
        return {"ok": False, "code": code, "result": {}}

    @staticmethod
    def _member_payloads(
        members: tuple[tuple[str, OrderIntent], ...],
    ) -> tuple[Mapping[str, object], ...]:
        return tuple(
            {"leg_id": leg_id, "intent": intent.to_payload()}
            for leg_id, intent in members
        )


__all__ = [
    "GroupReservationTarget",
    "GroupReservationsService",
    "GroupReservationsServiceError",
    "PORT",
    "SUPPORTED_OPERATIONS",
    "validate_service_options",
]
