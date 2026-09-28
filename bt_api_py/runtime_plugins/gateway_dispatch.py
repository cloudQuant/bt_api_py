"""Gateway-only managed submission composition for Iteration 41.

This module connects the sealed ``managed_live_gateway`` capability shape to
the existing provider-neutral execution, risk, gateway, and ZMQ contracts.  A
Backtrader-side client owns only a durable *transport* journal.  The gateway
server owns the sole provider dispatch, durable execution authority, and risk
gate.  The client never receives a provider port and never invokes the legacy
Store dispatch callback supplied by the framework bridge.

The implementation is deliberately local-process friendly for acceptance
tests.  It does not provision an endpoint, authenticate a real account, or
claim production provider readiness.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import RLock
from typing import Any

from .catalog import LoadedCapabilities, RuntimePluginError
from .contracts import (
    CAPABILITY_EXECUTION,
    CAPABILITY_GATEWAY,
    CAPABILITY_MONITOR,
    CAPABILITY_RISK,
    CAPABILITY_TRANSPORT_ZMQ,
)
from .instrument_risk import (
    SealedNormalizedInstrumentMetadataSnapshot,
    compose_instrument_risk_admission,
)
from .managed import ManagedExecutionRuntime
from .managed_recovery import DurableManagedRecoveryCoordinator

_COMMAND_PAYLOAD_SCHEMA = "bt_api_py.iteration41.gateway-managed-command.v1"
_OUTCOME_SCHEMA = "bt_api_py.iteration41.gateway-managed-outcome.v1"
_OUTCOME_FIELDS = frozenset(
    {
        "average_price",
        "command_fingerprint",
        "command_id",
        "filled_quantity",
        "intent_fingerprint",
        "intent_id",
        "provider_order_id",
        "reason_code",
        "schema",
        "scope_key",
        "state",
    }
)
_COMMAND_PAYLOAD_FIELDS = frozenset(
    {
        "contract_fingerprint",
        "intent",
        "intent_fingerprint",
        "intent_id",
        "schema",
        "scope_key",
    }
)


class GatewayManagedDispatchError(RuntimeError):
    """A gateway-only managed command cannot be safely submitted or decoded."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GatewayManagedOutcomeUnknownError(GatewayManagedDispatchError):
    """The gateway may have received a command; client execution becomes UNKNOWN."""


# The concise protocol name remains useful to callers.  Keep the actual class
# name conventional for exception discovery and tooling.
GatewayManagedOutcomeUnknown = GatewayManagedOutcomeUnknownError


@dataclass(frozen=True)
class _GatewayTransportPermit:
    """A local journal permit, explicitly not a risk decision.

    ``ManagedExecutionFacade`` requires a typed admission port to advance its
    durable client journal.  This permit binds one immutable intent to the
    gateway transport only.  It has no limits, no account authority, and no
    provider dispatch path; the server-owned risk gate remains authoritative.
    """

    permit_id: str


class _GatewayTransportAdmission:
    """Structural local admission for a gateway transport journal only."""

    def reserve(self, intent: Any) -> _GatewayTransportPermit:
        return _GatewayTransportPermit(_transport_permit_id(intent))

    def validate(self, permit_reference: str, intent: Any) -> _GatewayTransportPermit:
        expected = _transport_permit_id(intent)
        if permit_reference != expected:
            raise GatewayManagedDispatchError(
                "GATEWAY_TRANSPORT_PERMIT_MISMATCH",
                "gateway transport journal permit does not match intent",
            )
        return _GatewayTransportPermit(expected)

    def settle(self, permit_reference: str) -> _GatewayTransportPermit:
        return _GatewayTransportPermit(permit_reference)

    def release(self, permit_reference: str, reason: str) -> None:
        del permit_reference, reason


class _GatewayClientCommandJournal:
    """Persist exact command timestamps so restart/reconciliation keeps its fingerprint."""

    def __init__(self, database_path: Path, clock: Callable[[], float]) -> None:
        self._database_path = Path(database_path)
        self._clock = clock
        self._lock = RLock()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def command_for(
        self,
        intent: Any,
        *,
        contract: Any,
        gateway: Any,
        ttl_seconds: float,
    ) -> Any:
        """Return the one persisted gateway command for an immutable intent."""

        _validate_gateway_intent(intent)
        scope_key = intent.scope.key
        intent_fingerprint = intent.fingerprint
        command_id = gateway_command_id(intent)
        contract_fingerprint = contract.fingerprint()
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT intent_fingerprint, contract_fingerprint, command_payload_json,
                       command_id, command_fingerprint
                FROM gateway_client_commands WHERE scope_key = ? AND intent_id = ?
                """,
                (scope_key, intent.intent_id),
            ).fetchone()
            if row is not None:
                if (
                    str(row["intent_fingerprint"]) != intent_fingerprint
                    or str(row["contract_fingerprint"]) != contract_fingerprint
                ):
                    raise GatewayManagedDispatchError(
                        "GATEWAY_COMMAND_ID_REUSED",
                        "gateway client journal saw a changed intent or effective contract",
                    )
                try:
                    payload = json.loads(str(row["command_payload_json"]))
                    command = gateway.gateway_command_from_wire_payload(payload)
                except Exception as error:
                    raise GatewayManagedDispatchError(
                        "GATEWAY_COMMAND_JOURNAL_CORRUPT",
                        "gateway client command journal cannot prove command identity",
                    ) from error
                if (
                    command.command_id != command_id
                    or command.command_id != str(row["command_id"])
                    or command.fingerprint != payload["command_fingerprint"]
                    or command.fingerprint != str(row["command_fingerprint"])
                ):
                    raise GatewayManagedDispatchError(
                        "GATEWAY_COMMAND_JOURNAL_CORRUPT",
                        "gateway client command identity does not match the intent",
                    )
                return command

            issued_at = _finite_clock(self._clock)
            command = gateway.GatewayCommand(
                command_id=command_id,
                account_scope=intent.scope.account_key,
                strategy_scope=scope_key,
                kind=gateway.GatewayCommandKind.SUBMIT,
                payload=_command_payload(intent, contract_fingerprint),
                receipt_digest=contract.effective_digest,
                issued_at=issued_at,
                expires_at=issued_at + ttl_seconds,
            )
            payload = gateway.gateway_command_to_wire_payload(command)
            connection.execute(
                """
                INSERT INTO gateway_client_commands (
                    scope_key, intent_id, intent_fingerprint, contract_fingerprint,
                    command_id, command_fingerprint, command_payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_key,
                    intent.intent_id,
                    intent_fingerprint,
                    contract_fingerprint,
                    command.command_id,
                    command.fingerprint,
                    _canonical_json(payload),
                    issued_at,
                ),
            )
            return command

    def _initialize_schema(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gateway_client_commands (
                    scope_key TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    intent_fingerprint TEXT NOT NULL,
                    contract_fingerprint TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    command_fingerprint TEXT NOT NULL,
                    command_payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(scope_key, intent_id),
                    UNIQUE(command_id)
                );
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(
                str(self._database_path), timeout=5.0, isolation_level=None
            )
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")


@dataclass
class GatewayManagedDispatcher:
    """Client dispatch port that can only send one strict gateway wire command."""

    execution: Any
    gateway: Any
    transport: Any
    contract: Any
    scope: Any
    client: Any
    command_journal: _GatewayClientCommandJournal
    timeout_ms: int
    command_ttl_seconds: float
    instrument_metadata_snapshot: SealedNormalizedInstrumentMetadataSnapshot
    instrument_clock_ns: Callable[[], int]
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        _validate_gateway_contract(self.contract)
        _validate_scope(self.scope, self.contract)
        if not isinstance(
            self.instrument_metadata_snapshot, SealedNormalizedInstrumentMetadataSnapshot
        ):
            raise GatewayManagedDispatchError(
                "INSTRUMENT_SNAPSHOT_INVALID",
                "gateway dispatcher needs sealed instrument metadata",
            )
        self.instrument_metadata_snapshot.require_execution_scope(self.scope)
        self.instrument_metadata_snapshot.require_live_metadata()
        if not callable(getattr(self.client, "request", None)):
            raise GatewayManagedDispatchError(
                "GATEWAY_CLIENT_REQUIRED", "gateway dispatcher needs a typed command client"
            )
        if type(self.timeout_ms) is not int or self.timeout_ms <= 0:
            raise GatewayManagedDispatchError(
                "GATEWAY_TIMEOUT_INVALID", "gateway timeout must be positive"
            )
        if (
            isinstance(self.command_ttl_seconds, bool)
            or not isinstance(self.command_ttl_seconds, (int, float))
            or not math.isfinite(float(self.command_ttl_seconds))
            or self.command_ttl_seconds <= 0
        ):
            raise GatewayManagedDispatchError(
                "GATEWAY_COMMAND_TTL_INVALID", "gateway command TTL must be positive and finite"
            )
        if not callable(self.instrument_clock_ns):
            raise GatewayManagedDispatchError(
                "INSTRUMENT_SNAPSHOT_CLOCK_INVALID",
                "gateway metadata clock must be callable",
            )

    def command_for_intent(self, intent: Any) -> Any:
        """Return the persisted command used for this intent without sending it."""

        self._require_scope(intent)
        self._require_bound_metadata(intent)
        return self.command_journal.command_for(
            intent,
            contract=self.contract,
            gateway=self.gateway,
            ttl_seconds=float(self.command_ttl_seconds),
        )

    def submit(self, intent: Any) -> Any:
        """Send one durable command and return only typed provider evidence.

        Any client timeout, malformed response, or transport exception raises.
        ``ManagedExecutionFacade`` then records the already-claimed client
        intent as ``UNKNOWN``.  There is intentionally no direct SDK/Broker
        fallback and no retry loop here.
        """

        command = self.command_for_intent(intent)
        message = self.transport.WireMessage(
            message_id=command.command_id,
            channel=self.transport.WireChannel.COMMAND,
            scope=command.strategy_scope,
            sequence=_wire_sequence(command),
            sent_at=_finite_clock(self.clock),
            payload=self.gateway.gateway_command_to_wire_payload(command),
        )
        try:
            response = self.client.request(message, timeout_ms=self.timeout_ms)
        except Exception as error:
            raise GatewayManagedOutcomeUnknown(
                "GATEWAY_TRANSPORT_OUTCOME_UNKNOWN",
                "gateway command result requires reconciliation",
            ) from error
        return _provider_observation_from_wire_response(
            execution=self.execution,
            gateway=self.gateway,
            transport=self.transport,
            command=command,
            intent=intent,
            response=response,
        )

    def _require_scope(self, intent: Any) -> None:
        _validate_gateway_intent(intent)
        if intent.scope != self.scope:
            raise GatewayManagedDispatchError(
                "GATEWAY_INTENT_SCOPE_MISMATCH", "gateway intent scope differs from runtime scope"
            )

    def _require_bound_metadata(self, intent: Any) -> None:
        """Verify client-side metadata before a command can leave this process."""

        effect = getattr(getattr(intent, "position_effect", None), "value", None)
        self.instrument_metadata_snapshot.require_bound_intent(
            intent,
            now_ns=self.instrument_clock_ns(),
            require_fresh=effect == "OPEN",
        )


@dataclass
class GatewayManagedExecutionRuntime:
    """Backtrader-bindable client runtime with a local durable UNKNOWN journal.

    ``submit`` accepts the framework bridge's legacy callback only to retain
    that bridge's small protocol.  It deliberately never invokes the callback.
    The only dispatch port is :attr:`dispatcher`, which sends an authenticated
    gateway command.  Provider execution and risk admission belong exclusively
    to :class:`GatewayExecutionAuthority` on the server side.
    """

    contract: Any
    execution: Any
    scope: Any
    dispatcher: GatewayManagedDispatcher
    execution_store: Any
    _facade: Any
    instrument_metadata_snapshot: SealedNormalizedInstrumentMetadataSnapshot

    gateway_dispatch: str = "zmq_gateway_v1"

    def __post_init__(self) -> None:
        _validate_gateway_contract(self.contract)
        _validate_scope(self.scope, self.contract)
        if (
            self.dispatcher.contract != self.contract
            or self.dispatcher.scope != self.scope
            or self.dispatcher.execution is not self.execution
            or self.dispatcher.instrument_metadata_snapshot is not self.instrument_metadata_snapshot
            or not callable(getattr(self._facade, "submit", None))
        ):
            raise GatewayManagedDispatchError(
                "GATEWAY_CLIENT_RUNTIME_INVALID",
                "gateway client runtime components do not share one sealed scope",
            )
        self.instrument_metadata_snapshot.require_execution_scope(self.scope)
        self.instrument_metadata_snapshot.require_live_metadata()

    def submit(self, intent: Any, legacy_dispatch: Callable[[Any], Any]) -> Any:
        """Record/send through the gateway while intentionally discarding direct dispatch."""

        if not callable(legacy_dispatch):
            raise GatewayManagedDispatchError(
                "GATEWAY_LEGACY_PORT_INVALID", "framework bridge did not supply a dispatch callback"
            )
        del legacy_dispatch
        if getattr(intent, "scope", None) != self.scope:
            raise GatewayManagedDispatchError(
                "GATEWAY_INTENT_SCOPE_MISMATCH", "gateway intent scope differs from runtime scope"
            )
        self.recover()
        return self._facade.submit(intent, self.dispatcher)

    def recover(self) -> tuple[str, ...]:
        """Turn a client-side pre-authority dispatch gap into ``UNKNOWN``.

        The gateway client does not own provider risk or a provider port, so it
        cannot resolve an uncertain command.  It can only fence its local
        writer, record ``UNKNOWN``, and leave the server authority to reconcile
        any command that may already have crossed the transport boundary.
        This method deliberately performs no gateway or provider I/O.
        """

        try:
            writer_lease = self._facade.acquire_writer_lease()
            dispatching = self.execution_store.list_dispatching(self.scope)
            recovered: list[str] = []
            for record in dispatching:
                unknown = self.execution_store.mark_unknown(
                    record.intent_id,
                    self.scope,
                    "gateway_client_dispatch_reconciliation_required",
                    writer_lease=writer_lease,
                )
                if unknown.state.value != "UNKNOWN":
                    raise GatewayManagedDispatchError(
                        "GATEWAY_CLIENT_RECOVERY_INVALID",
                        "gateway client recovery did not retain an unknown command",
                    )
                recovered.append(record.intent_id)
            return tuple(recovered)
        except GatewayManagedDispatchError:
            raise
        except Exception as error:
            raise GatewayManagedDispatchError(
                "GATEWAY_CLIENT_RECOVERY_FAILED",
                "gateway client could not durably retain an uncertain command",
            ) from error

    def close(self) -> None:
        """Release the local writer lease and close only the client journal."""

        first_error: Exception | None = None
        for component in (self._facade, self.execution_store):
            try:
                component.close()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


class GatewayExecutionAuthority:
    """Server-side gateway authority for one scope and one provider dispatch port."""

    def __init__(
        self,
        *,
        server_runtime: ManagedExecutionRuntime,
        gateway: Any,
        transport: Any,
        router_database: Path,
        provider_dispatch: Any,
        server_admission: Callable[[Any, Any], bool] | None = None,
        writer_authority: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        _validate_gateway_contract(server_runtime.contract)
        _validate_scope(server_runtime.scope, server_runtime.contract)
        if not (
            callable(provider_dispatch) or callable(getattr(provider_dispatch, "submit", None))
        ):
            raise GatewayManagedDispatchError(
                "GATEWAY_PROVIDER_PORT_INVALID",
                "gateway authority needs a server provider dispatch port",
            )
        if server_admission is not None and not callable(server_admission):
            raise GatewayManagedDispatchError(
                "GATEWAY_SERVER_ADMISSION_INVALID",
                "gateway authority server admission must be callable",
            )
        if writer_authority is not None and not isinstance(
            writer_authority, gateway.GatewayAccountWriterAuthority
        ):
            raise GatewayManagedDispatchError(
                "GATEWAY_WRITER_AUTHORITY_INVALID",
                "gateway writer authority must be an explicit server-owned authority",
            )
        self.contract = server_runtime.contract
        self.execution = server_runtime.execution
        self.scope = server_runtime.scope
        self.server_runtime = server_runtime
        self.gateway = gateway
        self.transport = transport
        self._provider_dispatch = provider_dispatch
        self._clock = clock
        self.router = gateway.GatewayCommandRouter(
            router_database,
            self._execute_command,
            clock=clock,
            admission=server_admission,
            writer_authority=writer_authority,
        )

    def handle(self, principal: Any, message: Any) -> Mapping[str, Any]:
        """Handle one authenticated wire message through the durable router only."""

        if not isinstance(principal, self.gateway.GatewayPrincipal):
            raise GatewayManagedDispatchError(
                "GATEWAY_PRINCIPAL_REQUIRED", "gateway principal must be derived by the server"
            )
        if not isinstance(message, self.transport.WireMessage):
            raise GatewayManagedDispatchError("GATEWAY_WIRE_INVALID", "gateway message is invalid")
        if message.channel is not self.transport.WireChannel.COMMAND:
            raise GatewayManagedDispatchError(
                "GATEWAY_COMMAND_CHANNEL_REQUIRED",
                "gateway authority accepts command messages only",
            )
        try:
            command = self.gateway.gateway_command_from_wire_payload(message.payload)
        except Exception as error:
            raise GatewayManagedDispatchError(
                "GATEWAY_COMMAND_WIRE_INVALID", "gateway command mapping is invalid"
            ) from error
        intent = self._intent_from_command(command, message)
        try:
            result = self.router.dispatch(principal, command)
        except Exception:
            # The command may already be journaled/issued to the provider.  The
            # transport server converts this to a bounded rejection; the client
            # treats that response as UNKNOWN rather than a safe retry signal.
            raise
        if result.status is self.gateway.GatewayCommandStatus.SUCCEEDED:
            return _validated_gateway_outcome(
                result.outcome,
                command=command,
                intent=intent,
                contract=self.contract,
            )
        return _unknown_gateway_outcome(command, intent)

    def create_zmq_server(
        self,
        endpoint: str,
        authenticator: Callable[[bytes, Any], Any],
        **kwargs: Any,
    ) -> Any:
        """Create a transport server wired to this authority's sole handler."""

        return self.transport.ZmqCommandServer(endpoint, authenticator, self.handle, **kwargs)

    def close(self) -> None:
        """Close the server execution/risk journals; transport lifetime is caller-owned."""

        self.server_runtime.close()

    def _intent_from_command(self, command: Any, message: Any) -> Any:
        if command.kind is not self.gateway.GatewayCommandKind.SUBMIT:
            raise GatewayManagedDispatchError(
                "GATEWAY_COMMAND_KIND_INVALID", "managed authority accepts submit commands only"
            )
        if command.manual_resume_authorized:
            raise GatewayManagedDispatchError(
                "GATEWAY_MANUAL_RESUME_INVALID",
                "managed submission cannot carry resume authorization",
            )
        if message.scope != command.strategy_scope or message.sequence != _wire_sequence(command):
            raise GatewayManagedDispatchError(
                "GATEWAY_WIRE_IDENTITY_MISMATCH",
                "gateway wire stream does not match command identity",
            )
        if (
            command.receipt_digest != self.contract.effective_digest
            or command.account_scope != self.scope.account_key
            or command.strategy_scope != self.scope.key
        ):
            raise GatewayManagedDispatchError(
                "GATEWAY_CONTRACT_SCOPE_MISMATCH",
                "gateway command does not match the server effective contract and scope",
            )
        payload = command.payload
        _require_exact_mapping(payload, _COMMAND_PAYLOAD_FIELDS, "gateway command payload")
        if (
            payload["schema"] != _COMMAND_PAYLOAD_SCHEMA
            or payload["contract_fingerprint"] != self.contract.fingerprint()
            or payload["scope_key"] != self.scope.key
            or not isinstance(payload["intent"], Mapping)
            or not isinstance(payload["intent_id"], str)
            or not isinstance(payload["intent_fingerprint"], str)
        ):
            raise GatewayManagedDispatchError(
                "GATEWAY_COMMAND_PAYLOAD_INVALID",
                "gateway command payload is not an exact managed intent",
            )
        try:
            intent = self.execution.order_intent_from_payload(payload["intent"])
        except Exception as error:
            raise GatewayManagedDispatchError(
                "GATEWAY_INTENT_PAYLOAD_INVALID", "gateway command intent is invalid"
            ) from error
        if (
            intent.scope != self.scope
            or intent.intent_id != payload["intent_id"]
            or intent.fingerprint != payload["intent_fingerprint"]
            or command.command_id != gateway_command_id(intent)
        ):
            raise GatewayManagedDispatchError(
                "GATEWAY_INTENT_IDENTITY_MISMATCH",
                "gateway command changed intent identity or scope",
            )
        return intent

    def _execute_command(self, command: Any) -> Mapping[str, Any]:
        """The router's only server-side provider/risk execution callback."""

        # The router has already authenticated/authorized scope and durably
        # persisted ``command``.  Revalidate the payload independent of the
        # transport before using the server-owned execution runtime.
        intent = self._intent_from_command(
            command,
            _SyntheticWireIdentity(command.strategy_scope, _wire_sequence(command)),
        )
        record = self.server_runtime.submit(intent, self._provider_dispatch)
        return _gateway_outcome_from_record(command, intent, record)


@dataclass(frozen=True)
class _SyntheticWireIdentity:
    """Minimal internal identity used after the router has accepted a command."""

    scope: str
    sequence: int


def compose_gateway_managed_client(
    capabilities: LoadedCapabilities,
    *,
    state_directory: Path,
    provider: str,
    environment: str,
    account_ref: str,
    strategy_id: str,
    writer_id: str,
    client: Any,
    timeout_ms: int = 5_000,
    command_ttl_seconds: float = 30.0,
    clock: Callable[[], float] = time.time,
    trading_day: str | None = None,
    instrument_metadata_snapshot: Any | None = None,
    instrument_clock_ns: Callable[[], int] | None = None,
) -> GatewayManagedExecutionRuntime:
    """Create the client-only durable gateway submission runtime.

    The returned object has the same ``contract``, ``execution``, ``scope``,
    ``submit`` and ``close`` shape that Backtrader's managed bridge requires.
    Its ``submit`` method cannot reach a legacy Store provider callback.
    """

    contract = capabilities.contract
    _validate_gateway_contract(contract)
    if strategy_id != contract.strategy_id:
        raise RuntimePluginError(
            "STRATEGY_SCOPE_MISMATCH", "gateway client strategy does not match effective contract"
        )
    if environment != contract.environment:
        raise RuntimePluginError(
            "ENVIRONMENT_SCOPE_MISMATCH",
            "gateway client environment does not match effective contract",
        )
    snapshot = _require_gateway_snapshot(instrument_metadata_snapshot)
    _validate_gateway_capabilities(capabilities)
    execution = capabilities.require(CAPABILITY_EXECUTION)
    gateway = capabilities.require(CAPABILITY_GATEWAY)
    transport = capabilities.require(CAPABILITY_TRANSPORT_ZMQ)
    state_directory = Path(state_directory).resolve(strict=False)
    scope = execution.ExecutionScope(
        provider=provider,
        environment=environment,
        account_ref=account_ref,
        strategy_id=strategy_id,
        trading_day=trading_day,
    )
    snapshot.require_execution_scope(scope)
    snapshot.require_live_metadata()
    store = execution.SqliteExecutionStore(state_directory / "gateway_client_execution.sqlite3")
    try:
        journal = _GatewayClientCommandJournal(
            state_directory / "gateway_client_commands.sqlite3", clock
        )
        dispatcher = GatewayManagedDispatcher(
            execution=execution,
            gateway=gateway,
            transport=transport,
            contract=contract,
            scope=scope,
            client=client,
            command_journal=journal,
            timeout_ms=timeout_ms,
            command_ttl_seconds=command_ttl_seconds,
            instrument_metadata_snapshot=snapshot,
            instrument_clock_ns=instrument_clock_ns or time.time_ns,
            clock=clock,
        )
        facade = execution.ManagedExecutionFacade(
            store,
            scope,
            writer_id=writer_id,
            admission_gate=_GatewayTransportAdmission(),
        )
    except Exception:
        store.close()
        raise
    return GatewayManagedExecutionRuntime(
        contract=contract,
        execution=execution,
        scope=scope,
        dispatcher=dispatcher,
        execution_store=store,
        _facade=facade,
        instrument_metadata_snapshot=snapshot,
    )


def compose_gateway_execution_authority(
    capabilities: LoadedCapabilities,
    *,
    state_directory: Path,
    provider: str,
    environment: str,
    account_ref: str,
    strategy_id: str,
    writer_id: str,
    policy_id: str,
    max_increase_notional: Decimal,
    max_increase_count: int,
    provider_dispatch: Any,
    server_admission: Callable[[Any, Any], bool] | None = None,
    writer_authority: Any | None = None,
    permit_ttl_seconds: float = 30.0,
    clock: Callable[[], float] = time.time,
    trading_day: str | None = None,
    instrument_metadata_snapshot: Any | None = None,
    instrument_clock_ns: Callable[[], int] | None = None,
) -> GatewayExecutionAuthority:
    """Create the sole server-side execution/risk authority for a gateway route."""

    contract = capabilities.contract
    _validate_gateway_contract(contract)
    if strategy_id != contract.strategy_id:
        raise RuntimePluginError(
            "STRATEGY_SCOPE_MISMATCH",
            "gateway authority strategy does not match effective contract",
        )
    if environment != contract.environment:
        raise RuntimePluginError(
            "ENVIRONMENT_SCOPE_MISMATCH",
            "gateway authority environment does not match effective contract",
        )
    snapshot = _require_gateway_snapshot(instrument_metadata_snapshot)
    _validate_gateway_capabilities(capabilities)
    execution = capabilities.require(CAPABILITY_EXECUTION)
    risk = capabilities.require(CAPABILITY_RISK)
    monitor = capabilities.require(CAPABILITY_MONITOR)
    gateway = capabilities.require(CAPABILITY_GATEWAY)
    transport = capabilities.require(CAPABILITY_TRANSPORT_ZMQ)
    state_directory = Path(state_directory).resolve(strict=False)
    scope = execution.ExecutionScope(
        provider=provider,
        environment=environment,
        account_ref=account_ref,
        strategy_id=strategy_id,
        trading_day=trading_day,
    )
    snapshot.require_execution_scope(scope)
    snapshot.require_live_metadata()
    risk_scope = risk.AccountScope(
        provider=provider,
        environment=environment,
        account_id=account_ref,
    )
    risk_policy = risk.RiskPolicy(
        policy_id=policy_id,
        max_increase_notional=max_increase_notional,
        max_increase_count=max_increase_count,
        permit_ttl_seconds=permit_ttl_seconds,
    )
    risk_gate = risk.DurableRiskGate(state_directory / "risk.sqlite3", risk_policy, clock=clock)

    execution_store = execution.SqliteExecutionStore(state_directory / "execution.sqlite3")
    try:
        # The facade invokes this adapter's ``claim_for_dispatch`` immediately
        # before the sole provider port.  The shared risk gate performs the
        # validation plus ``dispatch-inflight`` freeze in one transaction; do
        # not install a second check-then-freeze guard in this composition.
        instrument_admission = compose_instrument_risk_admission(
            capabilities,
            risk_gate=risk_gate,
            risk_scope=risk_scope,
            normalized_snapshot=snapshot,
            execution_scope=scope,
            clock_ns=instrument_clock_ns,
        )

        facade = execution.ManagedExecutionFacade(
            execution_store,
            scope,
            writer_id=writer_id,
            admission_gate=instrument_admission.admission_gate,
        )
        outbox = monitor.DurableOutbox(state_directory / "monitor.sqlite3")
        recovery_coordinator = DurableManagedRecoveryCoordinator(
            state_directory / "managed_recovery.sqlite3"
        )
        runtime = ManagedExecutionRuntime(
            contract=contract,
            facade=facade,
            execution_store=execution_store,
            outbox=outbox,
            risk_gate=risk_gate,
            risk_scope=risk_scope,
            scope=scope,
            execution=execution,
            outbox_event_type=monitor.OutboxEvent,
            state_directory=state_directory,
            instrument_admission=instrument_admission,
            instrument_metadata_snapshot=snapshot,
            recovery_coordinator=recovery_coordinator,
        )
        return GatewayExecutionAuthority(
            server_runtime=runtime,
            gateway=gateway,
            transport=transport,
            router_database=state_directory / "gateway_router.sqlite3",
            provider_dispatch=provider_dispatch,
            server_admission=server_admission,
            writer_authority=writer_authority,
            clock=clock,
        )
    except Exception:
        execution_store.close()
        risk_gate.close()
        raise


def gateway_command_id(intent: Any) -> str:
    """Return a scope-qualified stable command ID for one immutable intent."""

    _validate_gateway_intent(intent)
    digest = hashlib.sha256(
        _canonical_json(
            {
                "intent_fingerprint": intent.fingerprint,
                "intent_id": intent.intent_id,
                "scope_key": intent.scope.key,
            }
        ).encode("utf-8")
    ).hexdigest()
    return "iteration41.gateway." + digest


def _validate_gateway_capabilities(capabilities: LoadedCapabilities) -> None:
    _validate_gateway_contract(capabilities.contract)
    for capability in (
        CAPABILITY_EXECUTION,
        CAPABILITY_RISK,
        CAPABILITY_MONITOR,
        CAPABILITY_GATEWAY,
        CAPABILITY_TRANSPORT_ZMQ,
    ):
        capabilities.require(capability)


def _validate_gateway_contract(contract: Any) -> None:
    if (
        getattr(contract, "preset", None) != "managed_live_gateway"
        or getattr(contract, "mode", None) != "live"
        or getattr(contract, "environment", None) != "production"
        or getattr(contract, "order_route", None) != "managed_execution"
        or tuple(getattr(contract, "required_capabilities", ()))
        != (
            CAPABILITY_EXECUTION,
            CAPABILITY_RISK,
            CAPABILITY_MONITOR,
            CAPABILITY_GATEWAY,
            CAPABILITY_TRANSPORT_ZMQ,
        )
        or not callable(getattr(contract, "fingerprint", None))
    ):
        raise RuntimePluginError(
            "GATEWAY_CONTRACT_REQUIRED", "gateway composition requires the sealed gateway contract"
        )


def _require_gateway_snapshot(
    snapshot: Any | None,
) -> SealedNormalizedInstrumentMetadataSnapshot:
    """Require the same sealed metadata authority as direct managed execution."""

    if snapshot is None:
        raise RuntimePluginError(
            "INSTRUMENT_SNAPSHOT_REQUIRED",
            "managed live gateway composition requires sealed instrument metadata",
        )
    if not isinstance(snapshot, SealedNormalizedInstrumentMetadataSnapshot):
        raise RuntimePluginError(
            "INSTRUMENT_SNAPSHOT_INVALID",
            "managed live gateway composition needs a sealed normalized metadata snapshot",
        )
    return snapshot


def _validate_scope(scope: Any, contract: Any) -> None:
    if (
        scope is None
        or getattr(scope, "strategy_id", None) != contract.strategy_id
        or getattr(scope, "environment", None) != contract.environment
        or not isinstance(getattr(scope, "key", None), str)
        or not isinstance(getattr(scope, "account_key", None), str)
    ):
        raise GatewayManagedDispatchError(
            "GATEWAY_SCOPE_INVALID", "gateway execution scope does not match the effective contract"
        )


def _validate_gateway_intent(intent: Any) -> None:
    if (
        not isinstance(getattr(intent, "intent_id", None), str)
        or not isinstance(getattr(intent, "fingerprint", None), str)
        or getattr(intent, "scope", None) is None
        or not callable(getattr(intent, "to_payload", None))
    ):
        raise GatewayManagedDispatchError(
            "GATEWAY_INTENT_INVALID", "gateway dispatch needs an immutable typed execution intent"
        )


def _command_payload(intent: Any, contract_fingerprint: str) -> dict[str, Any]:
    return {
        "contract_fingerprint": contract_fingerprint,
        "intent": intent.to_payload(),
        "intent_fingerprint": intent.fingerprint,
        "intent_id": intent.intent_id,
        "schema": _COMMAND_PAYLOAD_SCHEMA,
        "scope_key": intent.scope.key,
    }


def _transport_permit_id(intent: Any) -> str:
    return (
        "gateway-transport."
        + hashlib.sha256(
            _canonical_json(
                {
                    "intent_fingerprint": intent.fingerprint,
                    "intent_id": intent.intent_id,
                    "scope_key": intent.scope.key,
                }
            ).encode("utf-8")
        ).hexdigest()
    )


def _wire_sequence(command: Any) -> int:
    return int(command.fingerprint[:16], 16)


def _gateway_outcome_from_record(command: Any, intent: Any, record: Any) -> dict[str, Any]:
    return {
        "average_price": _decimal_text_or_none(getattr(record, "average_price", None)),
        "command_fingerprint": command.fingerprint,
        "command_id": command.command_id,
        "filled_quantity": _decimal_text_or_zero(getattr(record, "filled_quantity", None)),
        "intent_fingerprint": intent.fingerprint,
        "intent_id": intent.intent_id,
        "provider_order_id": _text_or_none(getattr(record, "provider_order_id", None)),
        "reason_code": _text_or_none(getattr(record, "unknown_reason", None)),
        "schema": _OUTCOME_SCHEMA,
        "scope_key": intent.scope.key,
        "state": str(getattr(getattr(record, "state", None), "value", "UNKNOWN")),
    }


def _unknown_gateway_outcome(command: Any, intent: Any) -> dict[str, Any]:
    return {
        "average_price": None,
        "command_fingerprint": command.fingerprint,
        "command_id": command.command_id,
        "filled_quantity": "0",
        "intent_fingerprint": intent.fingerprint,
        "intent_id": intent.intent_id,
        "provider_order_id": None,
        "reason_code": "gateway_reconciliation_required",
        "schema": _OUTCOME_SCHEMA,
        "scope_key": intent.scope.key,
        "state": "UNKNOWN",
    }


def _provider_observation_from_wire_response(
    *,
    execution: Any,
    gateway: Any,
    transport: Any,
    command: Any,
    intent: Any,
    response: Any,
) -> Any:
    if not isinstance(response, transport.WireMessage):
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_RESPONSE_INVALID", "gateway response is not a typed wire message"
        )
    if (
        response.channel is not transport.WireChannel.RESPONSE
        or response.message_id != command.command_id
        or response.scope != command.strategy_scope
        or response.sequence != _wire_sequence(command)
        or not isinstance(response.payload, Mapping)
        or set(response.payload) != {"accepted", "outcome", "status"}
    ):
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_RESPONSE_IDENTITY_MISMATCH", "gateway response cannot prove command identity"
        )
    if response.payload["accepted"] is not True or response.payload["status"] != "handled":
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_RESPONSE_UNCONFIRMED", "gateway did not return a confirmed command outcome"
        )
    return _provider_observation_from_outcome(
        execution=execution,
        command=command,
        intent=intent,
        outcome=response.payload["outcome"],
    )


def _provider_observation_from_outcome(
    *, execution: Any, command: Any, intent: Any, outcome: Any
) -> Any:
    _validated_gateway_outcome(outcome, command=command, intent=intent, contract=None)
    state = outcome["state"]
    if state == "UNKNOWN":
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_EXECUTION_UNKNOWN", "gateway execution needs reconciliation"
        )
    if state in {"REJECTED", "BLOCKED"}:
        reason = "gateway_rejected" if state == "REJECTED" else "gateway_blocked"
        return execution.ProviderObservation.rejected(intent.intent_id, reason)
    if state not in {"ACKED", "PARTIALLY_FILLED", "FILLED", "CANCELLED"}:
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_EXECUTION_STATE_UNPROVEN", "gateway outcome does not prove provider state"
        )
    provider_order_id = outcome["provider_order_id"]
    if not isinstance(provider_order_id, str) or not provider_order_id.strip():
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_PROVIDER_ID_UNPROVEN", "gateway outcome lacks provider order identity"
        )
    if state == "ACKED":
        if outcome["filled_quantity"] != "0" or outcome["average_price"] is not None:
            raise GatewayManagedOutcomeUnknown(
                "GATEWAY_ACK_EVIDENCE_INVALID",
                "gateway acknowledgement includes unsupported fill evidence",
            )
        return execution.ProviderObservation.accepted(intent.intent_id, provider_order_id)
    filled_quantity = _parse_nonnegative_decimal(outcome["filled_quantity"], "filled_quantity")
    average_price = _parse_positive_decimal(outcome["average_price"], "average_price")
    try:
        return execution.ProviderObservation(
            intent.intent_id,
            execution.ExecutionState(state),
            provider_order_id=provider_order_id,
            filled_quantity=filled_quantity,
            average_price=average_price,
        )
    except Exception as error:
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_PROVIDER_EVIDENCE_INVALID", "gateway outcome is not valid provider evidence"
        ) from error


def _validated_gateway_outcome(
    outcome: Any,
    *,
    command: Any,
    intent: Any,
    contract: Any | None,
) -> dict[str, Any]:
    if not isinstance(outcome, Mapping):
        raise GatewayManagedDispatchError(
            "GATEWAY_OUTCOME_INVALID", "gateway outcome must be an object"
        )
    _require_exact_mapping(outcome, _OUTCOME_FIELDS, "gateway outcome")
    if (
        outcome["schema"] != _OUTCOME_SCHEMA
        or outcome["command_id"] != command.command_id
        or outcome["command_fingerprint"] != command.fingerprint
        or outcome["intent_id"] != intent.intent_id
        or outcome["intent_fingerprint"] != intent.fingerprint
        or outcome["scope_key"] != intent.scope.key
        or not isinstance(outcome["state"], str)
        or not isinstance(outcome["filled_quantity"], str)
        or outcome["provider_order_id"] is not None
        and not isinstance(outcome["provider_order_id"], str)
        or outcome["average_price"] is not None
        and not isinstance(outcome["average_price"], str)
        or outcome["reason_code"] is not None
        and not isinstance(outcome["reason_code"], str)
    ):
        raise GatewayManagedDispatchError(
            "GATEWAY_OUTCOME_IDENTITY_MISMATCH", "gateway outcome cannot prove command identity"
        )
    del contract
    return dict(outcome)


def _require_exact_mapping(value: Mapping[str, Any], fields: frozenset[str], name: str) -> None:
    if set(value) != fields:
        raise GatewayManagedDispatchError(
            "GATEWAY_MAPPING_FIELDS_INVALID", name + " contains missing or unknown fields"
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _finite_clock(clock: Callable[[], float]) -> float:
    value = clock()
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise GatewayManagedDispatchError(
            "GATEWAY_CLOCK_INVALID", "gateway clock must return a finite number"
        )
    return float(value)


def _decimal_text_or_zero(value: Any) -> str:
    if value is None:
        return "0"
    return _decimal_text(value)


def _decimal_text_or_none(value: Any) -> str | None:
    return None if value is None else _decimal_text(value)


def _decimal_text(value: Any) -> str:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise GatewayManagedDispatchError(
            "GATEWAY_DECIMAL_INVALID", "gateway outcome has an invalid decimal"
        ) from error
    if not decimal_value.is_finite():
        raise GatewayManagedDispatchError(
            "GATEWAY_DECIMAL_INVALID", "gateway outcome has a non-finite decimal"
        )
    return format(decimal_value, "f")


def _parse_nonnegative_decimal(value: Any, name: str) -> Decimal:
    parsed = _parse_decimal(value, name)
    if parsed < 0:
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_DECIMAL_INVALID", "gateway " + name + " must be non-negative"
        )
    return parsed


def _parse_positive_decimal(value: Any, name: str) -> Decimal:
    parsed = _parse_decimal(value, name)
    if parsed <= 0:
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_DECIMAL_INVALID", "gateway " + name + " must be positive"
        )
    return parsed


def _parse_decimal(value: Any, name: str) -> Decimal:
    if not isinstance(value, str):
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_DECIMAL_INVALID", "gateway " + name + " must be a decimal string"
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_DECIMAL_INVALID", "gateway " + name + " is invalid"
        ) from error
    if not parsed.is_finite():
        raise GatewayManagedOutcomeUnknown(
            "GATEWAY_DECIMAL_INVALID", "gateway " + name + " must be finite"
        )
    return parsed


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


__all__ = [
    "GatewayExecutionAuthority",
    "GatewayManagedDispatchError",
    "GatewayManagedDispatcher",
    "GatewayManagedExecutionRuntime",
    "GatewayManagedOutcomeUnknown",
    "GatewayManagedOutcomeUnknownError",
    "compose_gateway_execution_authority",
    "compose_gateway_managed_client",
    "gateway_command_id",
]
