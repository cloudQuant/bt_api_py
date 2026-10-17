"""Risk-owned source-verified old-contract group roll/exit operations.

The adapter is deliberately narrower than ordinary order entry.  It persists
the complete source-bound authorization before a single CLOSE dispatch and
uses a separate SQLite CAS so restart/query paths cannot dispatch twice.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from collections.abc import Mapping
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from bt_api_base.service_protocol import WireRole
from bt_api_execution import (
    ExecutionState,
    OrderIntent,
    OrderType,
    PositionEffect,
    Side,
    TimeInForce,
)
from bt_api_risk.core.admission import ClosablePositionSnapshot

from .service_registry import ActiveRegistrationContext, ServiceCallContext, TrustedGroupWritePolicy
from .service_risk_order_journal import canonical_json, sha256_text

_PREPARE = "orders.prepare_group_roll_exit"
_SUBMIT = "orders.submit_group_roll_exit"
_QUERY = "orders.query_group_roll_exit"
_QUERY_OPS = frozenset({_PREPARE, _QUERY})
_ENTRY_OPS = frozenset({_SUBMIT})
_ACTIVE_PHASES = ("PREPARED", "SUBMIT_STARTED", "STATUS_OBSERVED", "UNKNOWN")
_MAX_ACTIONS_PER_SOURCE = 64
_CLOSE_DEADLINE_NS = 30_000_000_000


class RiskGroupRollExitError(RuntimeError):
    """A finite source/registration/action failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class RiskGroupRollExitActionStore:
    """Durable action CAS sharing the Risk journal file, not its command table."""

    def __init__(self, path: str | Path) -> None:
        if not isinstance(path, (str, Path)):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_INVALID")
        self._path = str(path)
        self._lock = threading.RLock()
        try:
            with self._connection() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS risk_group_roll_exit_actions ("
                    "action_id TEXT PRIMARY KEY, portfolio_ref TEXT NOT NULL, group_id TEXT NOT NULL, "
                    "source_leg_id TEXT NOT NULL, request_fingerprint TEXT NOT NULL, "
                    "authorization_sha256 TEXT NOT NULL, authorization_json TEXT NOT NULL, "
                    "phase TEXT NOT NULL, observation_json TEXT, updated_at_ns INTEGER NOT NULL)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS risk_group_roll_exit_source_idx "
                    "ON risk_group_roll_exit_actions(portfolio_ref,group_id,source_leg_id,updated_at_ns)"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS risk_group_roll_exit_one_active_source "
                    "ON risk_group_roll_exit_actions(portfolio_ref,group_id,source_leg_id) "
                    "WHERE phase IN ('PREPARED','SUBMIT_STARTED','STATUS_OBSERVED','UNKNOWN')"
                )
        except Exception:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def rows_for_source(self, portfolio_ref: str, group_id: str, source_leg_id: str) -> tuple[dict[str, Any], ...]:
        with self._lock:
            try:
                with self._connection() as connection:
                    rows = connection.execute(
                        "SELECT * FROM risk_group_roll_exit_actions "
                        "WHERE portfolio_ref=? AND group_id=? AND source_leg_id=? "
                        "ORDER BY updated_at_ns,action_id",
                        (portfolio_ref, group_id, source_leg_id),
                    ).fetchall()
                return tuple(dict(row) for row in rows)
            except Exception:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None

    def get(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                with self._connection() as connection:
                    row = connection.execute(
                        "SELECT * FROM risk_group_roll_exit_actions WHERE action_id=?",
                        (action_id,),
                    ).fetchone()
                return None if row is None else dict(row)
            except Exception:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None

    def prepare(self, authorization: Any) -> dict[str, Any]:
        from .service_group_roll_exit import GroupRollExitAuthorizationV1, GroupRollExitError

        if type(authorization) is not GroupRollExitAuthorizationV1:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_AUTHORIZATION_INVALID")
        payload = canonical_json(authorization.to_payload())
        request = authorization.request
        with self._lock:
            try:
                with self._transaction() as connection:
                    existing = connection.execute(
                        "SELECT * FROM risk_group_roll_exit_actions WHERE action_id=?",
                        (authorization.action_id,),
                    ).fetchone()
                    if existing is not None:
                        result = dict(existing)
                        if (
                            result["authorization_sha256"] != authorization.authorization_sha256
                            or result["authorization_json"] != payload
                        ):
                            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_ACTION_CONFLICT")
                        return result
                    active = connection.execute(
                        "SELECT action_id FROM risk_group_roll_exit_actions WHERE portfolio_ref=? "
                        "AND group_id=? AND source_leg_id=? AND phase IN (?,?,?,?) LIMIT 1",
                        (request.portfolio_ref, request.group_id, request.source_leg_id, *_ACTIVE_PHASES),
                    ).fetchone()
                    if active is not None:
                        raise RiskGroupRollExitError("GROUP_ROLL_EXIT_RECOVERY_REQUIRED")
                    now_ns = time.time_ns()
                    connection.execute(
                        "INSERT INTO risk_group_roll_exit_actions "
                        "(action_id,portfolio_ref,group_id,source_leg_id,request_fingerprint,"
                        "authorization_sha256,authorization_json,phase,observation_json,updated_at_ns) "
                        "VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                        (
                            authorization.action_id,
                            request.portfolio_ref,
                            request.group_id,
                            request.source_leg_id,
                            request.fingerprint,
                            authorization.authorization_sha256,
                            payload,
                            "PREPARED",
                            now_ns,
                        ),
                    )
                    row = connection.execute(
                        "SELECT * FROM risk_group_roll_exit_actions WHERE action_id=?",
                        (authorization.action_id,),
                    ).fetchone()
                    return dict(row)
            except RiskGroupRollExitError:
                raise
            except Exception:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None

    def start(self, action_id: str, authorization_sha256: str) -> bool:
        with self._lock:
            try:
                with self._transaction() as connection:
                    changed = connection.execute(
                        "UPDATE risk_group_roll_exit_actions SET phase='SUBMIT_STARTED',updated_at_ns=? "
                        "WHERE action_id=? AND authorization_sha256=? AND phase='PREPARED'",
                        (time.time_ns(), action_id, authorization_sha256),
                    ).rowcount
                    return changed == 1
            except Exception:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None

    def observe(self, observation: Any) -> dict[str, Any]:
        from .service_group_roll_exit import GroupRollExitObservationV1

        if type(observation) is not GroupRollExitObservationV1:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_OBSERVATION_INVALID")
        observation_json = canonical_json(observation.to_payload())
        with self._lock:
            try:
                with self._transaction() as connection:
                    row = connection.execute(
                        "SELECT * FROM risk_group_roll_exit_actions WHERE action_id=?",
                        (observation.action_id,),
                    ).fetchone()
                    if row is None:
                        raise RiskGroupRollExitError("GROUP_ROLL_EXIT_ACTION_NOT_FOUND")
                    current = dict(row)
                    if (
                        current["authorization_sha256"] != observation.authorization_sha256
                        or current["phase"] not in _ACTIVE_PHASES
                        or current["phase"] == "PREPARED"
                    ):
                        raise RiskGroupRollExitError("GROUP_ROLL_EXIT_PHASE_CONFLICT")
                    if current["observation_json"] is not None:
                        old = json.loads(current["observation_json"])
                        old_revision = old.get("order_revision")
                        if (
                            old_revision is not None
                            and observation.order_revision is not None
                            and observation.order_revision < old_revision
                        ) or observation.filled_quantity < Decimal(old["filled_quantity"]):
                            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STALE_OBSERVATION")
                    allowed = {
                        "SUBMIT_STARTED": {"SUBMIT_STARTED", "STATUS_OBSERVED", "UNKNOWN", "RESOLVED"},
                        "STATUS_OBSERVED": {"STATUS_OBSERVED", "UNKNOWN", "RESOLVED"},
                        "UNKNOWN": {"UNKNOWN", "STATUS_OBSERVED", "RESOLVED"},
                    }
                    if observation.phase.value not in allowed.get(current["phase"], set()):
                        raise RiskGroupRollExitError("GROUP_ROLL_EXIT_PHASE_CONFLICT")
                    now_ns = time.time_ns()
                    connection.execute(
                        "UPDATE risk_group_roll_exit_actions SET phase=?,observation_json=?,updated_at_ns=? "
                        "WHERE action_id=? AND authorization_sha256=?",
                        (observation.phase.value, observation_json, now_ns, observation.action_id, observation.authorization_sha256),
                    )
                    result = connection.execute(
                        "SELECT * FROM risk_group_roll_exit_actions WHERE action_id=?",
                        (observation.action_id,),
                    ).fetchone()
                    return dict(result)
            except RiskGroupRollExitError:
                raise
            except Exception:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE") from None


class RiskGroupRollExitOwner:
    """Three exact operations over the Risk service's existing owner thread."""

    def __init__(self, owner: Any, source: object, policy: TrustedGroupWritePolicy) -> None:
        from .service_execution_group_roll_exit_source import GroupRollExitAuthorizationSourceV1

        if type(source) is not GroupRollExitAuthorizationSourceV1 or type(policy) is not TrustedGroupWritePolicy:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_COMPOSITION_INVALID")
        self._owner = owner
        self._source = source
        self._policy = policy
        self._lock = threading.RLock()
        self._registrations: dict[tuple[str, str], ActiveRegistrationContext] = {}
        self._store = RiskGroupRollExitActionStore(owner._journal.path)

    @property
    def ready(self) -> bool:
        try:
            return self._source.ready is True
        except Exception:
            return False

    def maintain_registrations(self, contexts: tuple[ActiveRegistrationContext, ...]) -> None:
        if type(contexts) is not tuple:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_SNAPSHOT_INVALID")
        now = time.time()
        retained: dict[tuple[str, str], ActiveRegistrationContext] = {}
        for registration in contexts:
            if type(registration) is not ActiveRegistrationContext:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_SNAPSHOT_INVALID")
            if registration.instance_id != self._policy.instance_id or registration.expires_at <= now:
                continue
            context = registration.context
            if (
                type(context) is not ServiceCallContext
                or context.identity != self._policy.identity
                or context.identity.role is not WireRole.SERVICE
                or context.principal_ref != self._policy.principal_ref
                or context.scope != self._policy.scope
                or registration.strategy_write_run_id != self._policy.run_id
            ):
                continue
            if registration.port == "service.order_query":
                if registration.operations != _QUERY_OPS or registration.write_operations:
                    continue
            elif registration.port == "service.order_entry":
                if registration.operations != _ENTRY_OPS or registration.write_operations != _ENTRY_OPS:
                    continue
                if registration.scope_ref != self._policy.scope_ref:
                    continue
            else:
                continue
            key = (context.registration_id, registration.port)
            prior = retained.get(key)
            if prior is not None and prior != registration:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_CONFLICT")
            retained[key] = registration
        with self._lock:
            self._registrations = retained

    def handle(self, operation: str, context: ServiceCallContext, arguments: Any) -> dict[str, Any]:
        try:
            if operation not in {_PREPARE, _SUBMIT, _QUERY}:
                raise RiskGroupRollExitError("UNKNOWN_OPERATION")
            primary, query_registration, entry_registration = self._registrations_for(operation, context)
            del primary
            if operation == _PREPARE:
                return self._prepare(query_registration, entry_registration, arguments)
            if operation == _SUBMIT:
                return self._submit(query_registration, entry_registration, arguments)
            return self._query(query_registration, entry_registration, arguments)
        except RiskGroupRollExitError as error:
            return _reply(False, error.code, _empty_result())
        except Exception:
            return _reply(False, "GROUP_ROLL_EXIT_UNCERTAIN", _empty_result())

    def _registrations_for(
        self, operation: str, context: ServiceCallContext
    ) -> tuple[ActiveRegistrationContext, ActiveRegistrationContext, ActiveRegistrationContext]:
        if type(context) is not ServiceCallContext:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_DENIED")
        expected_port = "service.order_entry" if operation == _SUBMIT else "service.order_query"
        with self._lock:
            primary = self._registrations.get((context.registration_id, expected_port))
            current = tuple(self._registrations.values())
        if (
            type(primary) is not ActiveRegistrationContext
            or type(primary.context) is not ServiceCallContext
            # ``result_fits`` is a per-request closure created by Registry for
            # reply-size accounting. It is intentionally absent from the
            # durable active-registration snapshot, so compare only the four
            # authenticated registration fields here.
            or primary.context.identity != context.identity
            or primary.context.scope != context.scope
            or primary.context.registration_id != context.registration_id
            or primary.context.principal_ref != context.principal_ref
            or primary.expires_at <= time.time()
            or primary.scope_ref == ""
        ):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_DENIED")
        queries = tuple(
            row for row in current
            if row.port == "service.order_query"
            and row.context.identity == context.identity
            and row.context.principal_ref == context.principal_ref
            and row.context.scope == context.scope
            and row.instance_id == self._policy.instance_id
            and row.expires_at > time.time()
        )
        entries = tuple(
            row for row in current
            if row.port == "service.order_entry"
            and row.context.identity == context.identity
            and row.context.principal_ref == context.principal_ref
            and row.context.scope == context.scope
            and row.scope_ref == self._policy.scope_ref
            and row.instance_id == self._policy.instance_id
            and row.expires_at > time.time()
        )
        if len(queries) != 1 or len(entries) != 1:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_DENIED")
        if (
            context.identity != self._policy.identity
            or context.identity.role is not WireRole.SERVICE
            or context.principal_ref != self._policy.principal_ref
            or context.scope != self._policy.scope
            or queries[0].strategy_write_run_id != self._policy.run_id
            or entries[0].strategy_write_run_id != self._policy.run_id
        ):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_DENIED")
        query_registration = queries[0]
        entry_registration = entries[0]
        if operation == _SUBMIT and primary is not entry_registration:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_DENIED")
        if operation != _SUBMIT and primary is not query_registration:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_REGISTRATION_DENIED")
        return primary, query_registration, entry_registration

    def _prepare(self, query_registration: ActiveRegistrationContext, entry_registration: ActiveRegistrationContext, arguments: Any) -> dict[str, Any]:
        from .service_group_roll_exit import GroupRollExitAuthorizationV1, GroupRollExitPhase, GroupRollExitRequestV1

        request = GroupRollExitRequestV1.from_payload(arguments)
        existing = self._store.rows_for_source(request.portfolio_ref, request.group_id, request.source_leg_id)
        matches = tuple(row for row in existing if row["request_fingerprint"] == request.fingerprint)
        if len(matches) > 1:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_ACTION_CONFLICT")
        if matches:
            row = matches[0]
            authorization = _authorization_from_row(row)
            self._source.assert_authorization(
                query_registration, authorization, operation=_PREPARE, entry_registration=entry_registration
            )
            observation = _observation_from_row(row)
            return _reply(True, "OK", {"phase": row["phase"], "authorization": authorization.to_payload(), "observation": None if observation is None else observation.to_payload()})
        if any(row["phase"] in _ACTIVE_PHASES for row in existing):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_RECOVERY_REQUIRED")
        accepted = self._source.resolve_group_roll_exit(
            query_registration, request, operation=_PREPARE, entry_registration=entry_registration
        )
        authorization = self._build_authorization(accepted, request, entry_registration)
        row = self._store.prepare(authorization)
        return _reply(True, "OK", {"phase": row["phase"], "authorization": authorization.to_payload(), "observation": None})

    def _build_authorization(self, accepted: Any, request: Any, entry_registration: ActiveRegistrationContext) -> Any:
        from .service_group_roll_exit import (
            GroupRollExitAuthorizationV1,
            GroupRollExitSourceFactsV1,
            _MAX_PRIOR_CLOSES,
        )
        from .service_execution_group_roll_exit_source import group_roll_exit_registration_digest

        owner = self._owner
        policy = self._policy
        binding = owner._resolve_scope_binding(policy.scope)
        if binding is None:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_SCOPE_UNAVAILABLE")
        _template, target, _admission, contract = binding
        if (
            contract is None
            or not contract.exposure_ready
            or "CLOSE" not in contract.supported_position_effects
            or target.account_scope != self._source.account_scope
            or target.strategy_id != self._source.execution_scope.strategy_id
            or accepted.request != request
        ):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_SCOPE_UNAVAILABLE")
        plan = accepted.source_plan
        child = accepted.source_progress.submitted_intent
        status = accepted.source_receipt.status
        metadata = target.registry.get(child.instrument)
        now_ns = time.time_ns()
        if (
            metadata is None
            or metadata.instrument != child.instrument
            or metadata.metadata_version == ""
            or metadata.as_of_ns > now_ns
            or metadata.expires_at_ns <= now_ns
            or (child.position_side, child.position_bucket) not in contract.supported_position_dimensions
            or metadata.quantity_unit is None
            or metadata.quantity_step <= 0
            or metadata.tick_size <= 0
            or Fraction(request.limit_price) % Fraction(metadata.tick_size) != 0
        ):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_METADATA_UNAVAILABLE")
        prior = self._prior_resolved_closes(request, target)
        if len(prior) >= _MAX_PRIOR_CLOSES:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_PRIOR_CLOSE_LIMIT")
        remaining = _fraction_to_decimal_exact(
            Fraction(status.filled_quantity) - sum((Fraction(item.filled_quantity) for item in prior), Fraction(0))
        )
        if remaining <= 0:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_CLOSE_BOUND_EMPTY")
        position = self._closable_position(target, child.instrument, child.position_side, child.position_bucket)
        if position.available_quantity is None or position.quantity_unit != metadata.quantity_unit:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_CLOSE_ALLOCATION_UNAVAILABLE")
        if remaining > position.available_quantity:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_CLOSE_BOUND_EMPTY")
        source_reservations = tuple(
            row for row in accepted.group_snapshot.reservations
            if row.account_ref == entry_registration.context.scope.account_ref
        )
        if len(source_reservations) != 1:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_RESERVATION_MISMATCH")
        source_risk_legs = tuple(
            row for row in source_reservations[0].legs
            if row.leg_id == request.source_leg_id
        )
        if len(source_risk_legs) != 1 or source_risk_legs[0].actual_intent_hash is None:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_RISK_LEG_MISMATCH")
        source = GroupRollExitSourceFactsV1(
            portfolio_ref=request.portfolio_ref,
            origin_run_id=accepted.origin_binding.origin_run_id,
            group_id=accepted.group_snapshot.request.group_id,
            group_version=accepted.group_snapshot.request.version,
            group_revision=accepted.group_snapshot.revision,
            group_fingerprint=accepted.group_snapshot.request.fingerprint,
            source_risk_reservation_fingerprint=source_reservations[0].group_fingerprint,
            source_risk_intent_fingerprint=source_risk_legs[0].actual_intent_hash,
            group_request=accepted.group_snapshot.request,
            account_ref=entry_registration.context.scope.account_ref,
            strategy_id=accepted.group_snapshot.request.strategy_id,
            source_leg_id=request.source_leg_id,
            source_intent_id=plan.maximum_intent.intent_id,
            source_request_fingerprint=child.fingerprint,
            source_child_intent=child,
            source_execution_scope=child.scope,
            source_terminal_status=status,
            source_requested_quantity=plan.maximum_intent.quantity,
            source_claim=accepted.source_receipt.claim,
            source_risk_resolution=accepted.source_receipt.resolution,
            source_event_digest=accepted.source_event_digest,
            source_side=child.side,
            instrument=child.instrument,
            source_metadata_version=child.metadata_version,
            close_metadata_version=metadata.metadata_version,
            close_metadata_digest=metadata.digest,
            close_quantity_step=metadata.quantity_step,
            close_tick_size=metadata.tick_size,
            close_metadata_valid_until_ns=metadata.expires_at_ns,
            source_position_side=child.position_side,
            source_position_bucket=child.position_bucket,
            source_filled_quantity=status.filled_quantity,
            prior_resolved_closes=prior,
            remaining_quantity=remaining,
            closable_position_version=_position_version(position),
            service_principal_ref=entry_registration.context.principal_ref,
            service_instance_id=entry_registration.instance_id,
            service_scope_ref=entry_registration.scope_ref,
            service_registration_id=entry_registration.context.registration_id,
            service_operation=_SUBMIT,
            service_port="service.order_entry",
            service_registration_digest=group_roll_exit_registration_digest(entry_registration),
            service_epoch=entry_registration.service_epoch,
            service_policy_version=entry_registration.policy_version,
            service_requirements_version=entry_registration.requirements_version,
            service_write_run_id=entry_registration.strategy_write_run_id,
        )
        deadline = min(metadata.expires_at_ns, now_ns + _CLOSE_DEADLINE_NS)
        if deadline <= now_ns:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_METADATA_STALE")
        closing_side = Side.SELL if child.side is Side.BUY else Side.BUY
        from .service_group_roll_exit import group_roll_exit_action_id

        action_id = group_roll_exit_action_id(source.digest, request.fingerprint)
        intent = OrderIntent(
            intent_id=action_id,
            scope=child.scope,
            signal_id=action_id,
            instrument=child.instrument,
            side=closing_side,
            position_effect=PositionEffect.CLOSE,
            order_type=OrderType.LIMIT,
            quantity=remaining,
            price=request.limit_price,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            metadata_version=metadata.metadata_version,
            deadline_ns=deadline,
            tags={},
            position_side=child.position_side,
            position_bucket=child.position_bucket,
        )
        return GroupRollExitAuthorizationV1.build(
            request=request, source=source, intent=intent, deadline_ns=deadline
        )

    def _submit(self, query_registration: ActiveRegistrationContext, entry_registration: ActiveRegistrationContext, arguments: Any) -> dict[str, Any]:
        from .service_group_roll_exit import GroupRollExitAuthorizationV1, GroupRollExitPhase

        if not isinstance(arguments, Mapping) or set(arguments) != {"authorization"}:
            raise RiskGroupRollExitError("INVALID_REQUEST")
        authorization = GroupRollExitAuthorizationV1.from_payload(arguments["authorization"])
        row = self._store.get(authorization.action_id)
        if row is None or _authorization_from_row(row).to_payload() != authorization.to_payload():
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_AUTHORIZATION_NOT_PREPARED")
        self._source.assert_authorization(
            query_registration, authorization, operation=_SUBMIT, entry_registration=entry_registration
        )
        if row["phase"] != "PREPARED":
            return self._query_record(authorization, row, query_registration, entry_registration)
        self._validate_current_authorization(authorization)
        if not self._store.start(authorization.action_id, authorization.authorization_sha256):
            latest = self._store.get(authorization.action_id)
            if latest is None:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_UNAVAILABLE")
            return self._query_record(authorization, latest, query_registration, entry_registration)
        owner = self._owner
        binding = owner._resolve_scope_binding(self._policy.scope)
        if binding is None:
            observation = _unknown_observation(authorization, "RISK_SCOPE_UNAVAILABLE")
            self._store.observe(observation)
            return _reply(True, "OK", {"phase": "UNKNOWN", "observation": observation.to_payload()})
        _template, target, admission, contract = binding
        args = _order_arguments(authorization)
        try:
            reply = owner._submit(
                principal_ref=self._policy.principal_ref,
                identity=self._policy.identity,
                scope=self._policy.scope,
                target=target,
                admission=admission,
                contract=contract,
                arguments=args,
                trusted_client_order_id=authorization.client_order_id,
                reject_duplicate=True,
                internal_group_roll_exit=True,
            )
        except Exception:
            reply = {"ok": False, "code": "GROUP_ROLL_EXIT_SUBMIT_UNCERTAIN", "result": {}}
        observation = self._observation_from_reply(authorization, target, reply)
        self._store.observe(observation)
        return _reply(bool(reply.get("ok")), str(reply.get("code", "GROUP_ROLL_EXIT_SUBMIT_UNCERTAIN")), {"phase": observation.phase.value, "observation": observation.to_payload()})

    def _query(self, query_registration: ActiveRegistrationContext, entry_registration: ActiveRegistrationContext, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, Mapping) or set(arguments) != {"action_id", "authorization_sha256"}:
            raise RiskGroupRollExitError("INVALID_REQUEST")
        action_id = arguments.get("action_id")
        authorization_sha256 = arguments.get("authorization_sha256")
        row = self._store.get(action_id) if type(action_id) is str else None
        if row is None:
            return _reply(True, "OK", {"phase": "NOT_FOUND", "observation": None})
        if row["authorization_sha256"] != authorization_sha256:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_AUTHORIZATION_MISMATCH")
        authorization = _authorization_from_row(row)
        self._source.assert_authorization(
            query_registration, authorization, operation=_QUERY, entry_registration=entry_registration
        )
        return self._query_record(authorization, row, query_registration, entry_registration)

    def _query_record(self, authorization: Any, row: dict[str, Any], query_registration: ActiveRegistrationContext, entry_registration: ActiveRegistrationContext) -> dict[str, Any]:
        from .service_group_roll_exit import GroupRollExitPhase

        phase = GroupRollExitPhase(row["phase"])
        observation = _observation_from_row(row)
        if phase is GroupRollExitPhase.PREPARED:
            return _reply(True, "OK", {"phase": phase.value, "observation": None})
        if phase is GroupRollExitPhase.RESOLVED:
            binding = self._owner._resolve_scope_binding(self._policy.scope)
            if binding is None:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_SCOPE_UNAVAILABLE")
            receipt = self._close_receipt(authorization, binding[1])
            if receipt is None or observation.close_receipt_sha256 != receipt["receipt_sha256"]:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_TERMINAL_RECEIPT_INVALID")
            return _reply(True, "OK", {"phase": phase.value, "observation": observation.to_payload()})
        owner = self._owner
        binding = owner._resolve_scope_binding(self._policy.scope)
        if binding is None:
            updated = _unknown_observation(authorization, "RISK_SCOPE_UNAVAILABLE", previous=observation)
            self._store.observe(updated)
            return _reply(True, "OK", {"phase": updated.phase.value, "observation": updated.to_payload()})
        _template, target, _admission, contract = binding
        try:
            reply = owner._query(
                principal_ref=self._policy.principal_ref,
                scope=self._policy.scope,
                target=target,
                contract=contract,
                arguments={"intent_id": authorization.intent.intent_id},
            )
        except Exception:
            reply = {"ok": False, "code": "RISK_QUERY_UNCERTAIN", "result": {}}
        updated = self._observation_from_reply(authorization, target, reply, previous=observation)
        self._store.observe(updated)
        return _reply(bool(reply.get("ok")), str(reply.get("code", "GROUP_ROLL_EXIT_QUERY_UNCERTAIN")), {"phase": updated.phase.value, "observation": updated.to_payload()})

    def _validate_current_authorization(self, authorization: Any) -> None:
        owner = self._owner
        source = authorization.source
        binding = owner._resolve_scope_binding(self._policy.scope)
        if binding is None:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_SCOPE_UNAVAILABLE")
        _template, target, _admission, contract = binding
        metadata = target.registry.get(source.instrument)
        if (
            metadata is None
            or metadata.metadata_version != source.close_metadata_version
            or metadata.digest != source.close_metadata_digest
            or metadata.quantity_step != source.close_quantity_step
            or metadata.tick_size != source.close_tick_size
            or metadata.expires_at_ns != source.close_metadata_valid_until_ns
            or metadata.expires_at_ns <= time.time_ns()
            or not contract.exposure_ready
            or "CLOSE" not in contract.supported_position_effects
            or (source.source_position_side, source.source_position_bucket) not in contract.supported_position_dimensions
            or Fraction(authorization.request.limit_price) % Fraction(metadata.tick_size) != 0
            or Fraction(authorization.intent.quantity) % Fraction(metadata.quantity_step) != 0
            or authorization.intent.quantity != source.remaining_quantity
            or authorization.intent.deadline_ns != authorization.deadline_ns
            or authorization.deadline_ns <= time.time_ns()
            or authorization.deadline_ns > metadata.expires_at_ns
        ):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_METADATA_CHANGED")
        position = self._closable_position(target, source.instrument, source.source_position_side, source.source_position_bucket)
        if (
            position.available_quantity is None
            or position.available_quantity < authorization.intent.quantity
            or position.quantity_unit != metadata.quantity_unit
            or _position_version(position) != source.closable_position_version
        ):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_CLOSE_ALLOCATION_CHANGED")
        prior = self._prior_resolved_closes(
            authorization.request, target, exclude_action_id=authorization.action_id
        )
        if prior != source.prior_resolved_closes:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_PRIOR_CLOSES_CHANGED")

    def _closable_position(self, target: Any, instrument: str, position_side: str, position_bucket: str) -> ClosablePositionSnapshot:
        try:
            position = self._owner._gate.get_closable_position(
                target.account_scope,
                instrument,
                target.strategy_id,
                position_side=position_side,
                position_bucket=position_bucket,
            )
        except Exception:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_CLOSE_ALLOCATION_UNAVAILABLE") from None
        if (
            type(position) is not ClosablePositionSnapshot
            or position.scope != target.account_scope
            or position.instrument != instrument
            or position.strategy_id != target.strategy_id
            or position.position_side != position_side
            or position.position_bucket != position_bucket
            or position.completeness != "LOCAL_LEDGER_COMPLETE"
            or type(position.revision) is not str
            or not position.revision
            or type(position.available_quantity) is not Decimal
            or not position.available_quantity.is_finite()
            or position.available_quantity <= 0
            or type(position.quantity_unit) is not str
        ):
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_CLOSE_ALLOCATION_UNAVAILABLE")
        return position

    def _prior_resolved_closes(
        self, request: Any, target: Any, *, exclude_action_id: str | None = None
    ) -> tuple[Any, ...]:
        from .service_group_roll_exit import GroupRollExitPhase, GroupRollExitPriorCloseV1

        rows = self._store.rows_for_source(request.portfolio_ref, request.group_id, request.source_leg_id)
        if len(rows) > _MAX_ACTIONS_PER_SOURCE:
            raise RiskGroupRollExitError("GROUP_ROLL_EXIT_ACTION_LIMIT")
        refs = []
        for row in rows:
            if row["action_id"] == exclude_action_id:
                continue
            phase = GroupRollExitPhase(row["phase"])
            if phase is not GroupRollExitPhase.RESOLVED:
                # A same-request replay is handled before this method; every
                # other live row owns the source until Risk can resolve it.
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_RECOVERY_REQUIRED")
            authorization = _authorization_from_row(row)
            if (
                authorization.source.portfolio_ref != request.portfolio_ref
                or authorization.source.group_id != request.group_id
                or authorization.source.source_leg_id != request.source_leg_id
                or authorization.source.origin_run_id != self._policy.run_id
                or authorization.source.account_ref != self._policy.scope.account_ref
            ):
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_PRIOR_CLOSE_SOURCE_MISMATCH")
            receipt = self._close_receipt(authorization, target)
            if receipt is None:
                raise RiskGroupRollExitError("GROUP_ROLL_EXIT_PRIOR_CLOSE_RECEIPT_INVALID")
            refs.append(
                GroupRollExitPriorCloseV1(
                    action_id=authorization.action_id,
                    intent_id=authorization.intent.intent_id,
                    client_order_id=authorization.client_order_id,
                    source_digest=authorization.source.digest,
                    request_fingerprint=authorization.request.fingerprint,
                    authorization_sha256=authorization.authorization_sha256,
                    receipt_sha256=receipt["receipt_sha256"],
                    resolution_sha256=receipt["exposure_proof_sha256"],
                    closing_side=authorization.intent.side,
                    instrument=authorization.intent.instrument,
                    metadata_version=authorization.intent.metadata_version,
                    requested_quantity=authorization.intent.quantity,
                    filled_quantity=authorization.intent.quantity,
                    terminal_state=ExecutionState.FILLED,
                    order_revision=receipt["order_revision"],
                )
            )
        return tuple(refs)

    def _close_receipt(self, authorization: Any, target: Any) -> dict[str, Any] | None:
        owner = self._owner
        command = owner._journal.get_command(target.account_scope, self._policy.principal_ref, authorization.intent.intent_id)
        if (
            command is None
            or command.client_order_id != authorization.client_order_id
            or command.permit_id is None
            or command.canonical_payload.get("position_effect") != "CLOSE"
            or command.canonical_payload.get("instrument") != authorization.intent.instrument
            or command.canonical_payload.get("side") != authorization.intent.side.value
            or command.canonical_payload.get("metadata_version") != authorization.intent.metadata_version
            or Decimal(str(command.canonical_payload.get("quantity"))) != authorization.intent.quantity
            or Decimal(str(command.canonical_payload.get("price"))) != authorization.request.limit_price
        ):
            return None
        try:
            with owner._journal.writer_fence(target.account_scope) as fence:
                current = owner._journal.get_command(target.account_scope, self._policy.principal_ref, authorization.intent.intent_id)
                if current is None or current.latest_record_sha256 != command.latest_record_sha256:
                    return None
                receipt = owner._gate.dispatch_close_resolution_receipt(command.permit_id)
                from bt_api_risk.core.admission import OrderExposureState

                if (
                    receipt.status.value != "TERMINAL_CLOSE_RESOLVED"
                    or receipt.position_effect != "CLOSE"
                    or receipt.dispatch_latch_active is not False
                    or receipt.has_other_active_freezes is not False
                    or type(receipt.final_state) is not OrderExposureState
                    or receipt.final_state is not OrderExposureState.FILLED
                    or receipt.requested_quantity != authorization.intent.quantity
                    or receipt.filled_quantity != receipt.requested_quantity
                    or type(receipt.trade_count) is not int
                    or receipt.trade_count <= 0
                    or receipt.order_revision is None
                    or receipt.claim.scope != target.account_scope
                    or receipt.claim.permit_id != command.permit_id
                    or receipt.claim.intent_id != command.risk_intent_id
                    or not owner._journal.has_resolved_terminal_exposure_history(
                        fence,
                        principal_ref=self._policy.principal_ref,
                        intent_id=authorization.intent.intent_id,
                        receipt=receipt,
                    )
                ):
                    return None
                payload = _close_receipt_payload(receipt)
                payload["receipt_sha256"] = sha256_text(canonical_json(payload))
                return payload
        except Exception:
            return None

    def _observation_from_reply(self, authorization: Any, target: Any, reply: Any, *, previous: Any = None) -> Any:
        from .service_group_roll_exit import GroupRollExitObservationV1, GroupRollExitPhase

        result = reply.get("result") if isinstance(reply, dict) else None
        record = result.get("record") if isinstance(result, dict) else None
        if result is not None and result.get("found") is True and isinstance(record, dict):
            state_text = record.get("state")
            if state_text == "ACKED_TRACKED":
                state_text = "ACKED"
            try:
                state = ExecutionState(state_text)
                filled = Decimal(str(record.get("filled_quantity", "0")))
            except Exception:
                return _unknown_observation(authorization, "RISK_ORDER_PROJECTION_INVALID", previous=previous)
            command = self._owner._journal.get_command(
                target.account_scope, self._policy.principal_ref, authorization.intent.intent_id
            )
            latest = {} if command is None else command.latest_event
            revision = latest.get("order_revision")
            if type(revision) is not int or revision <= 0:
                revision = None
            if state is ExecutionState.FILLED:
                receipt = self._close_receipt(authorization, target)
                if receipt is not None:
                    return GroupRollExitObservationV1(
                        action_id=authorization.action_id,
                        phase=GroupRollExitPhase.RESOLVED,
                        source_digest=authorization.source.digest,
                        request_fingerprint=authorization.request.fingerprint,
                        authorization_sha256=authorization.authorization_sha256,
                        intent_id=authorization.intent.intent_id,
                        close_state=ExecutionState.FILLED,
                        requested_quantity=authorization.intent.quantity,
                        filled_quantity=authorization.intent.quantity,
                        order_revision=receipt["order_revision"],
                        close_receipt_sha256=receipt["receipt_sha256"],
                        detail_code=None,
                    )
                return _unknown_observation(authorization, "CLOSE_RECEIPT_UNAVAILABLE", previous=previous)
            if state in {ExecutionState.PENDING_ADMISSION, ExecutionState.PENDING_DISPATCH, ExecutionState.DISPATCHING}:
                return GroupRollExitObservationV1(
                    action_id=authorization.action_id,
                    phase=GroupRollExitPhase.SUBMIT_STARTED,
                    source_digest=authorization.source.digest,
                    request_fingerprint=authorization.request.fingerprint,
                    authorization_sha256=authorization.authorization_sha256,
                    intent_id=authorization.intent.intent_id,
                    close_state=state,
                    requested_quantity=authorization.intent.quantity,
                    filled_quantity=Decimal(0),
                    order_revision=revision,
                    close_receipt_sha256=None,
                    detail_code=None,
                )
            if state in {ExecutionState.ACKED, ExecutionState.PARTIALLY_FILLED}:
                return GroupRollExitObservationV1(
                    action_id=authorization.action_id,
                    phase=GroupRollExitPhase.STATUS_OBSERVED,
                    source_digest=authorization.source.digest,
                    request_fingerprint=authorization.request.fingerprint,
                    authorization_sha256=authorization.authorization_sha256,
                    intent_id=authorization.intent.intent_id,
                    close_state=state,
                    requested_quantity=authorization.intent.quantity,
                    filled_quantity=filled if state is ExecutionState.PARTIALLY_FILLED else Decimal(0),
                    order_revision=revision,
                    close_receipt_sha256=None,
                    detail_code=None,
                )
        return _unknown_observation(authorization, "RISK_ORDER_STATUS_UNAVAILABLE", previous=previous)


def _authorization_from_row(row: dict[str, Any]) -> Any:
    from .service_group_roll_exit import GroupRollExitAuthorizationV1

    try:
        payload = json.loads(row["authorization_json"])
        authorization = GroupRollExitAuthorizationV1.from_payload(payload)
        if (
            authorization.action_id != row["action_id"]
            or authorization.authorization_sha256 != row["authorization_sha256"]
            or authorization.request.fingerprint != row["request_fingerprint"]
        ):
            raise ValueError
        return authorization
    except Exception:
        raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_CORRUPT") from None


def _observation_from_row(row: dict[str, Any]) -> Any:
    from .service_group_roll_exit import GroupRollExitObservationV1

    if row["observation_json"] is None:
        return None
    try:
        return GroupRollExitObservationV1.from_payload(json.loads(row["observation_json"]))
    except Exception:
        raise RiskGroupRollExitError("GROUP_ROLL_EXIT_STORE_CORRUPT") from None


def _position_version(position: ClosablePositionSnapshot) -> str:
    payload = {
        "scope_key": position.scope.key,
        "instrument": position.instrument,
        "strategy_id": position.strategy_id,
        "position_side": position.position_side,
        "position_bucket": position.position_bucket,
        "revision": position.revision,
        "declared_quantity": _decimal_text(position.declared_quantity),
        "used_quantity": _decimal_text(position.used_quantity),
        "available_quantity": _decimal_text(position.available_quantity),
        "quantity_unit": position.quantity_unit,
        "completeness": position.completeness,
    }
    return sha256_text(canonical_json(payload))


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _fraction_to_decimal_exact(value: Fraction) -> Decimal:
    numerator, denominator = value.numerator, value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    scale = max(twos, fives)
    if denominator != 1 or scale > 512 or len(str(abs(numerator))) > 1024:
        raise RiskGroupRollExitError("GROUP_ROLL_EXIT_DECIMAL_NOT_EXACT")
    unscaled = numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = "-" if unscaled < 0 else ""
    digits = str(abs(unscaled))
    if scale:
        digits = digits.rjust(scale + 1, "0")
        text = sign + digits[:-scale] + "." + digits[-scale:]
    else:
        text = sign + digits
    result = Decimal(text)
    if Fraction(result) != value:
        raise RiskGroupRollExitError("GROUP_ROLL_EXIT_DECIMAL_NOT_EXACT")
    return result


def _close_receipt_payload(receipt: Any) -> dict[str, Any]:
    return {
        "status": receipt.status.value,
        "claim": {
            "cause_id": receipt.claim.cause_id,
            "claim_digest": receipt.claim.claim_digest,
            "claimed_at": receipt.claim.claimed_at,
            "intent_hash": receipt.claim.intent_hash,
            "intent_id": receipt.claim.intent_id,
            "permit_id": receipt.claim.permit_id,
            "scope_key": receipt.claim.scope.key,
        },
        "proof_sha256": receipt.proof_sha256,
        "exposure_proof_sha256": receipt.exposure_proof_sha256,
        "final_state": receipt.final_state.value,
        "requested_quantity": _decimal_text(receipt.requested_quantity),
        "filled_quantity": _decimal_text(receipt.filled_quantity),
        "trade_count": receipt.trade_count,
        "order_revision": receipt.order_revision,
        "journal_revision": receipt.journal_revision,
        "exposure_reservation_id": receipt.exposure_reservation_id,
        "exposure_reservation_sha256": receipt.exposure_reservation_sha256,
        "dispatch_latch_active": receipt.dispatch_latch_active,
        "has_other_active_freezes": receipt.has_other_active_freezes,
        "position_effect": receipt.position_effect,
    }


def _order_arguments(authorization: Any) -> dict[str, Any]:
    intent = authorization.intent
    arguments: dict[str, Any] = {
        "intent_id": intent.intent_id,
        "signal_id": intent.signal_id,
        "instrument": intent.instrument,
        "side": intent.side.value,
        "position_effect": "CLOSE",
        "order_type": "LIMIT",
        "quantity": _decimal_text(intent.quantity),
        "price": _decimal_text(intent.price),
        "time_in_force": "GTC",
        "reduce_only": True,
        "metadata_version": intent.metadata_version,
        "deadline_ns": authorization.deadline_ns,
        "tags": {},
    }
    if intent.position_side != "NET":
        arguments["position_side"] = intent.position_side
    if intent.position_bucket != "ALL":
        arguments["position_bucket"] = intent.position_bucket
    return arguments


def _unknown_observation(authorization: Any, detail_code: str, *, previous: Any = None) -> Any:
    from .service_group_roll_exit import GroupRollExitObservationV1, GroupRollExitPhase

    prior_fill = Decimal(0) if previous is None else previous.filled_quantity
    revision = None if previous is None else previous.order_revision
    return GroupRollExitObservationV1(
        action_id=authorization.action_id,
        phase=GroupRollExitPhase.UNKNOWN,
        source_digest=authorization.source.digest,
        request_fingerprint=authorization.request.fingerprint,
        authorization_sha256=authorization.authorization_sha256,
        intent_id=authorization.intent.intent_id,
        close_state=ExecutionState.UNKNOWN,
        requested_quantity=authorization.intent.quantity,
        filled_quantity=prior_fill,
        order_revision=revision,
        close_receipt_sha256=None,
        detail_code=detail_code,
    )


def _empty_result() -> dict[str, Any]:
    return {"phase": "UNSTARTED", "authorization": None, "observation": None}


def _reply(ok: bool, code: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": ok, "code": code, "result": result}


__all__ = ["RiskGroupRollExitActionStore", "RiskGroupRollExitError", "RiskGroupRollExitOwner"]
