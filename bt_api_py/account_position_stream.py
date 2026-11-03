"""Subscription-maintained account positions as reconciliation evidence.

Iteration 60 (acceptance report F5, user decision 2026-11-03): the strategy
ledger reconciles against provider account positions that arrive by
subscription (provider websocket in direct mode, the Gateway account stream
over ZMQ in service mode), not by a provider read on every position query.

:class:`StreamedAccountPositions` is the neutral, thread-safe state both
paths feed. It is evidence only while it is provably complete:

* it starts from one complete snapshot (``apply_snapshot``), for example the
  provider's REST position read or the Gateway's ``sdk.account.view``;
* it then applies absolute position updates per (instrument, side)
  (``apply_position`` / ``remove_position``), as provider position channels
  and Gateway view deltas report them;
* any interruption of the subscription (disconnect, sequence gap, failed
  poll) makes it incomplete until the next complete snapshot
  (``mark_interrupted``);
* freshness is either a liveness proof (``confirm_live``: the source states
  that its data is current until a time) or the time of the last change.

``report()`` returns the ledger's :class:`AccountPositionReport` with no I/O.

:class:`GatewayAccountPositionStream` keeps one such state current from a
``ServiceAccountConsumer`` (cursor + ACK + durable replay over ZMQ) in its own
    thread. Gateway ``state_status`` proves neither a full-account view nor
    provider snapshot coverage; its rows remain diagnostic until a separate
    bound full-account proof is available.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from bt_api_py.strategy_ledger import AccountPositionReport

__all__ = [
    "AccountPositionStreamError",
    "BootstrapPositionReporter",
    "GatewayAccountPositionStream",
    "StreamedAccountPositions",
    "gateway_position_reducers",
]

_SIDES = frozenset({"net", "long", "short"})


class AccountPositionStreamError(RuntimeError):
    """Finite, value-free stream failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _quantity(value: object) -> Decimal:
    if type(value) is Decimal:
        parsed = value
    elif type(value) in (int, str):
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError):
            raise AccountPositionStreamError("POSITION_QUANTITY_INVALID") from None
    elif type(value) is float:
        parsed = Decimal(repr(value))
    else:
        raise AccountPositionStreamError("POSITION_QUANTITY_INVALID")
    if not parsed.is_finite():
        raise AccountPositionStreamError("POSITION_QUANTITY_INVALID")
    return parsed


def _name(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise AccountPositionStreamError("POSITION_INSTRUMENT_INVALID")
    return value


class StreamedAccountPositions:
    """One account's positions, kept current by a subscription.

    ``clock_ns`` is the wall clock used for observation and liveness times
    (nanoseconds since the epoch).
    """

    def __init__(
        self,
        account_ref: str,
        source: str,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.account_ref = _name(account_ref)
        self.source = _name(source)
        if not callable(clock_ns):
            raise AccountPositionStreamError("CLOCK_INVALID")
        self._clock_ns = clock_ns
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], Decimal] = {}
        self._complete = False
        self._interruption: str | None = "NO_SNAPSHOT"
        self._observed_ns = 0
        self._valid_until_ns: int | None = None
        self._sequence: int | None = None
        self._revision = 0

    # -- feeding -------------------------------------------------------------

    def apply_snapshot(
        self,
        rows: Mapping[tuple[str, str], object],
        *,
        observed_ns: int | None = None,
        sequence: int | None = None,
    ) -> None:
        """Replace everything with one complete snapshot: {(instrument, side): qty}."""

        parsed = {}
        for key, value in rows.items():
            if type(key) is not tuple or len(key) != 2 or key[1] not in _SIDES:
                raise AccountPositionStreamError("POSITION_ROW_INVALID")
            quantity = _quantity(value)
            if quantity != 0:
                parsed[(_name(key[0]), key[1])] = quantity
        stamp = self._stamp(observed_ns)
        with self._lock:
            self._rows = parsed
            self._complete = True
            self._interruption = None
            self._observed_ns = stamp
            self._valid_until_ns = None
            self._sequence = sequence
            self._revision += 1

    def snapshot_revision(self) -> int:
        """Capture local mutation state before an unsequenced snapshot read.

        This is only a race detector, not a provider watermark or proof of
        subscription continuity.
        """
        with self._lock:
            return self._revision

    def apply_unverified_snapshot(
        self, rows: Mapping[tuple[str, str], object], *, observed_ns: int, revision: int
    ) -> bool:
        """Keep a diagnostic snapshot without claiming account completeness.

        A callback or disconnect during the read makes even the diagnostic
        snapshot unsafe to replace the locally observed rows.
        """
        parsed = {}
        for key, value in rows.items():
            if type(key) is not tuple or len(key) != 2 or key[1] not in _SIDES:
                raise AccountPositionStreamError("POSITION_ROW_INVALID")
            quantity = _quantity(value)
            if quantity != 0:
                parsed[(_name(key[0]), key[1])] = quantity
        stamp = self._stamp(observed_ns)
        with self._lock:
            changed = revision != self._revision
            if not changed:
                self._rows = parsed
                self._observed_ns = stamp
            self._complete = False
            self._interruption = (
                "BASELINE_STREAM_CHANGED" if changed else "BASELINE_CONTINUITY_UNPROVEN"
            )
            self._valid_until_ns = None
            self._sequence = None
            self._revision += 1
            return not changed

    def apply_position(
        self,
        instrument: str,
        side: str,
        quantity: object,
        *,
        observed_ns: int | None = None,
        sequence: int | None = None,
    ) -> bool:
        """Set the absolute position of (instrument, side); ``False`` if ignored.

        With ``sequence`` the source's numbering must be contiguous: a repeat
        or older number is ignored, a gap interrupts the stream.
        """

        key = (_name(instrument), side)
        if side not in _SIDES:
            raise AccountPositionStreamError("POSITION_ROW_INVALID")
        amount = _quantity(quantity)
        stamp = self._stamp(observed_ns)
        with self._lock:
            if not self._accept_sequence(sequence):
                return False
            if amount == 0:
                self._rows.pop(key, None)
            else:
                self._rows[key] = amount
            self._observed_ns = max(self._observed_ns, stamp)
            self._revision += 1
            return True

    def remove_position(
        self, instrument: str, side: str, *, observed_ns: int | None = None, sequence=None
    ) -> bool:
        return self.apply_position(
            instrument, side, Decimal("0"), observed_ns=observed_ns, sequence=sequence
        )

    def confirm_live(self, *, valid_until_ns: int) -> None:
        """The source proves its state is current until ``valid_until_ns``."""

        if type(valid_until_ns) is not int or valid_until_ns <= 0:
            raise AccountPositionStreamError("LIVENESS_INVALID")
        with self._lock:
            self._valid_until_ns = valid_until_ns

    def mark_interrupted(self, reason: str) -> None:
        """The subscription lost continuity: incomplete until the next snapshot."""

        with self._lock:
            self._complete = False
            self._interruption = _name(reason)
            self._valid_until_ns = None
            self._revision += 1

    # -- reading -------------------------------------------------------------

    @property
    def interruption(self) -> str | None:
        with self._lock:
            return self._interruption

    def report(self) -> AccountPositionReport:
        """The current state as reconciliation evidence; never does I/O."""

        with self._lock:
            positions: dict[str, Decimal] = {}
            for (instrument, side), quantity in self._rows.items():
                signed = -abs(quantity) if side == "short" else (
                    abs(quantity) if side == "long" else quantity
                )
                positions[instrument] = positions.get(instrument, Decimal("0")) + signed
            observed = self._observed_ns or max(int(self._clock_ns()), 1)
            return AccountPositionReport(
                self.account_ref,
                self.source,
                observed,
                self._complete,
                positions,
                valid_until_ns=self._valid_until_ns,
            )

    # -- internals -----------------------------------------------------------

    def _stamp(self, observed_ns: int | None) -> int:
        if observed_ns is None:
            return max(int(self._clock_ns()), 1)
        if type(observed_ns) is not int or observed_ns <= 0:
            raise AccountPositionStreamError("OBSERVED_TIME_INVALID")
        return observed_ns

    def _accept_sequence(self, sequence: int | None) -> bool:
        if sequence is None:
            if self._sequence is not None:
                self._complete = False
                self._interruption = "SEQUENCE_MISSING"
                self._valid_until_ns = None
                self._revision += 1
                return False
            return True
        if type(sequence) is not int or sequence < 0:
            raise AccountPositionStreamError("SEQUENCE_INVALID")
        if self._sequence is not None:
            if sequence <= self._sequence:
                return False
            if sequence != self._sequence + 1:
                self._complete = False
                self._interruption = "SEQUENCE_GAP"
                self._valid_until_ns = None
        self._sequence = sequence
        return True


# ---------------------------------------------------------------------------
# Verified snapshot/stream cut (bt_api_py.position_stream_bootstrap)
# ---------------------------------------------------------------------------


def _position_instrument_id(position: Any) -> str:
    return str(position.instrument.instrument_id)


class BootstrapPositionReporter:
    """Ledger evidence from a :class:`PositionStreamBootstrap`, never from guesses.

    A report is ``complete`` only when the bootstrap returns a projection: a
    snapshot installed at a verifier-attested stream cut, every later event
    contiguous after it, no callback in flight, and the binding lease current.
    Any other state (no snapshot yet, gap, interruption, expired lease,
    verifier refusal) yields an incomplete report with the bootstrap's reason,
    so reconciliation is ``UNVERIFIED`` (fail closed). ``valid_until_ns`` is
    the binding lease. The verifier's ``True`` stays an adapter assertion; no
    built-in CTP/OKX/Binance verifier exists (see the module notes of
    ``bt_api_py.position_stream_bootstrap``).
    """

    def __init__(
        self,
        bootstrap: Any,
        *,
        account_ref: str,
        source: str,
        instrument_name: Callable[[Any], str] = _position_instrument_id,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        from bt_api_py.position_stream_bootstrap import PositionStreamBootstrap

        if type(bootstrap) is not PositionStreamBootstrap:
            raise AccountPositionStreamError("BOOTSTRAP_INVALID")
        if not callable(instrument_name) or not callable(clock_ns):
            raise AccountPositionStreamError("STREAM_CONFIG_INVALID")
        self.account_ref = _name(account_ref)
        self.source = _name(source)
        self._bootstrap = bootstrap
        self._instrument_name = instrument_name
        self._clock_ns = clock_ns
        self.interruption: str | None = "NO_SNAPSHOT"

    def report(self) -> AccountPositionReport:
        from bt_api_py.position_stream_bootstrap import PositionBootstrapError

        try:
            projection = self._bootstrap.projection()
            if projection.binding.account_ref != self.account_ref:
                raise PositionBootstrapError("BINDING_ACCOUNT_MISMATCH")
            positions: dict[str, Decimal] = {}
            for row in projection.positions:
                quantity = _quantity(row.quantity)
                signed = -abs(quantity) if row.side == "short" else (
                    abs(quantity) if row.side == "long" else quantity
                )
                name = _name(self._instrument_name(row))
                positions[name] = positions.get(name, Decimal("0")) + signed
        except (PositionBootstrapError, AccountPositionStreamError) as error:
            self.interruption = str(error) or type(error).__name__
            return AccountPositionReport(
                self.account_ref, self.source, max(int(self._clock_ns()), 1), False, {}
            )
        except Exception:
            self.interruption = "INSTRUMENT_NAME_FAILED"
            return AccountPositionReport(
                self.account_ref, self.source, max(int(self._clock_ns()), 1), False, {}
            )
        self.interruption = None
        return AccountPositionReport(
            self.account_ref,
            self.source,
            projection.observed_ns,
            True,
            positions,
            valid_until_ns=projection.binding.valid_until_ns,
        )

    __call__ = report


# ---------------------------------------------------------------------------
# Gateway account stream (ZMQ, cursor + ACK)
# ---------------------------------------------------------------------------


def _row_key(row: Mapping[str, Any]) -> str:
    identity = {
        "resource": row["resource"],
        "instrument": row["instrument"],
        "side": row["side"],
    }
    return json.dumps(_plain(identity), sort_keys=True, separators=(",", ":"))


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def gateway_position_reducers():
    """``(snapshot_reducer, event_reducer)`` keeping Gateway view positions.

    The projection is ``{"view_scope", "positions": {identity: row}}``; the
    view must publish the ``positions`` fact kind.
    """

    def snapshot_reducer(_initial: Mapping[str, Any], snapshot: Mapping[str, Any]):
        payload = snapshot.get("payload")
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_id") != "sdk.account.view"
            or payload.get("schema_version") != 1
            or "positions" not in payload.get("fact_kinds", ())
        ):
            raise AccountPositionStreamError("GATEWAY_VIEW_WITHOUT_POSITIONS")
        rows = {}
        for row in payload.get("positions", ()):
            identity = _row_key(row)
            if identity in rows:
                raise AccountPositionStreamError("GATEWAY_DUPLICATE_POSITION_ROW")
            rows[identity] = _plain(row)
        return {"view_scope": payload["view_scope"], "positions": rows}

    def event_reducer(current: Mapping[str, Any], event: Mapping[str, Any]):
        if event.get("kind") != "account.view_delta":
            return _plain(current)
        delta = event["payload"]
        if (
            delta.get("schema_id") != "sdk.account.view.delta"
            or delta.get("view_scope") != current.get("view_scope")
        ):
            raise AccountPositionStreamError("GATEWAY_VIEW_DELTA_INVALID")
        result = _plain(current)
        changes = delta.get("changes", {}).get("positions")
        if changes is not None:
            rows = result["positions"]
            for identity in changes["delete"]:
                rows.pop(_row_key(identity), None)
            for row in changes["upsert"]:
                rows[_row_key(row)] = _plain(row)
        return result

    return snapshot_reducer, event_reducer


def _instrument_id(row: Mapping[str, Any]) -> str:
    return str(row["instrument"]["instrument_id"])


class GatewayAccountPositionStream:
    """Keep :class:`StreamedAccountPositions` current from a Gateway account view.

    ``consumer_factory()`` builds the ``ServiceAccountConsumer`` (with the
    reducers from :func:`gateway_position_reducers`) and is called in this
    stream's own thread, which then owns the consumer. Because the service
    runtime is owner-thread bound too, the factory may return
    ``(consumer, runtime)``: the stream then maintains and closes that runtime
    on the same thread. Every poll that reaches
    the stream head with ``state_status.ready`` proves only that an authorized
    view has been received. It does not establish full-account coverage: the
    view may have been filtered by resource and its provider source is not
    bound to a full-position receipt on this path. Reports therefore remain
    incomplete for reconciliation; rows are retained only as diagnostics.
    Failures, non-ready status and non-head polls interrupt the state.
    ``instrument_name(row)`` maps a Gateway position row to the ledger's
    instrument name (default: the instrument id).
    """

    def __init__(
        self,
        consumer_factory: Callable[[], Any],
        *,
        account_ref: str,
        source: str = "gateway:account.read_after",
        instrument_name: Callable[[Mapping[str, Any]], str] = _instrument_id,
        poll_interval_seconds: float = 0.2,
        clock_ns: Callable[[], int] = time.time_ns,
        verified_cut: BootstrapPositionReporter | None = None,
    ) -> None:
        if not callable(consumer_factory) or not callable(instrument_name):
            raise AccountPositionStreamError("STREAM_CONFIG_INVALID")
        if verified_cut is not None and (
            type(verified_cut) is not BootstrapPositionReporter
            or verified_cut.account_ref != account_ref
        ):
            raise AccountPositionStreamError("VERIFIED_CUT_INVALID")
        # Ledger evidence comes only from a verified snapshot/stream cut; the
        # Gateway view rows stay diagnostic (``diagnostic_report``).
        self._verified_cut = verified_cut
        if type(poll_interval_seconds) not in (int, float) or poll_interval_seconds <= 0:
            raise AccountPositionStreamError("STREAM_CONFIG_INVALID")
        self.positions = StreamedAccountPositions(account_ref, source, clock_ns=clock_ns)
        self._factory = consumer_factory
        self._instrument_name = instrument_name
        self._interval = float(poll_interval_seconds)
        self._max_backoff = max(float(poll_interval_seconds), 5.0)
        self._clock_ns = clock_ns
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._first = threading.Event()
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, name="gateway-positions", daemon=True)

    def start(self, *, wait_seconds: float = 10.0) -> GatewayAccountPositionStream:
        self._thread.start()
        self._first.wait(wait_seconds)
        return self

    def report(self) -> AccountPositionReport:
        """Ledger evidence: the verified cut's report, else incomplete.

        Without ``verified_cut`` (no trusted full-account cut verifier is
        bound) the report is never complete, whatever the view rows show.
        """
        if self._verified_cut is not None:
            return self._verified_cut.report()
        diagnostic = self.positions.report()
        return AccountPositionReport(
            diagnostic.account_ref,
            diagnostic.source,
            diagnostic.observed_ns,
            False,
            diagnostic.positions,
        )

    def diagnostic_report(self) -> AccountPositionReport:
        """The Gateway view rows as received; for display, not reconciliation."""
        return self.positions.report()

    __call__ = report

    def close(self, timeout: float = 5.0) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            self.positions.mark_interrupted("GATEWAY_STREAM_CLOSED")
        if self._thread.is_alive():
            self._thread.join(timeout)

    # -- thread --------------------------------------------------------------

    @property
    def error(self) -> str | None:
        """Why the stream thread stopped, if it failed."""

        return self._error

    def _run(self) -> None:
        """Own one consumer at a time; rebuild it with backoff after a failure.

        While no consumer works the state stays interrupted (fail closed);
        ``error`` names the last failure.
        """
        backoff = self._interval
        while not self._stop.is_set():
            consumer = runtime = None
            try:
                built = self._factory()
                consumer, runtime = built if type(built) is tuple else (built, None)
                backoff = self._interval
                while not self._stop.is_set():
                    if runtime is not None:
                        delay = runtime.next_maintenance_delay()
                        if delay is not None and delay <= 0:
                            runtime.maintain()
                    if self._poll_once(consumer) is False:
                        raise AccountPositionStreamError("GATEWAY_POLL_FAILED")
                    self._first.set()
                    self._stop.wait(self._interval)
            except Exception as error:
                self._error = getattr(error, "code", type(error).__name__)
                self.positions.mark_interrupted("GATEWAY_STREAM_FAILED")
                self._first.set()
            finally:
                for owned in (consumer, runtime):
                    if owned is not None:
                        try:
                            owned.close()
                        except Exception:
                            pass
            self._stop.wait(backoff)
            backoff = min(backoff * 2, self._max_backoff)
        self._first.set()

    def _poll_once(self, consumer) -> None:
        try:
            checkpoint = consumer.poll()
            status = consumer.last_state_status
            at_head = consumer.last_page_at_head
        except Exception:
            self.positions.mark_interrupted("GATEWAY_POLL_FAILED")
            return False
        if status is None or status.get("ready") is not True:
            self.positions.mark_interrupted("GATEWAY_NOT_READY")
            return
        if not at_head:
            self.positions.mark_interrupted("GATEWAY_STREAM_BEHIND")
            return
        projection = checkpoint.projection
        rows = projection.get("positions") if isinstance(projection, Mapping) else None
        if not isinstance(rows, Mapping):
            self.positions.mark_interrupted("GATEWAY_VIEW_WITHOUT_POSITIONS")
            return
        # A configured ServiceAccountConsumer binds these scopes on every poll.
        # These checks reject contradictions even if a caller supplies a custom
        # factory; equality alone does not establish account authority.
        bound_account = getattr(consumer, "_account_ref", None)
        if bound_account is not None and bound_account != self.positions.account_ref:
            self.positions.mark_interrupted("GATEWAY_ACCOUNT_SCOPE_MISMATCH")
            return
        binding = getattr(consumer, "_stable_binding", None)
        if isinstance(binding, Mapping):
            expected_view = binding.get("expected_strategy_scope")
            if expected_view is not None and projection.get("view_scope") != expected_view:
                self.positions.mark_interrupted("GATEWAY_VIEW_SCOPE_MISMATCH")
                return
        snapshot = {}
        instrument_identities = {}
        newest = 0
        try:
            for row in rows.values():
                name = _name(self._instrument_name(row))
                identity = json.dumps(
                    _plain(row["instrument"]), sort_keys=True, separators=(",", ":")
                )
                prior_identity = instrument_identities.setdefault(name, identity)
                if prior_identity != identity:
                    raise AccountPositionStreamError("GATEWAY_INSTRUMENT_NAME_COLLISION")
                key = (name, row["side"])
                if key[1] not in _SIDES:
                    raise AccountPositionStreamError("POSITION_ROW_INVALID")
                if key in snapshot:
                    raise AccountPositionStreamError("GATEWAY_POSITION_KEY_COLLISION")
                snapshot[key] = _quantity(row["quantity"])
                stamp = _utc_ns(row.get("observed_at_utc"))
                newest = max(newest, stamp or 0)
            received = int(status["received_ns"])
            if received <= 0:
                raise AccountPositionStreamError("OBSERVED_TIME_INVALID")
        except (
            AccountPositionStreamError, KeyError, TypeError, ValueError, OverflowError
        ) as error:
            self.positions.mark_interrupted(getattr(error, "code", "GATEWAY_POSITION_INVALID"))
            return
        # A close racing with an in-flight poll must not revive a closed stream.
        with self._lifecycle_lock:
            if self._stop.is_set():
                return
            revision = self.positions.snapshot_revision()
            self.positions.apply_unverified_snapshot(
                snapshot, observed_ns=newest or received, revision=revision
            )
            self.positions.mark_interrupted("GATEWAY_FULL_COVERAGE_UNPROVEN")


def _utc_ns(text: object) -> int | None:
    if type(text) is not str:
        return None
    from datetime import datetime

    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return int(moment.timestamp() * 1_000_000_000)



_ACCOUNT_STREAM_OPERATIONS = frozenset({"account.read_after", "account.ack", "account.progress"})


def gateway_position_stream_from_config(
    client_config: Mapping[str, Any],
    *,
    account_ref: str,
    principal_ref: str,
    strategy_ref: str,
    execution_domain_ref: str,
    service_principal_ref: str,
    service_id: str,
    curve_keys: Mapping[str, tuple[bytes, bytes, bytes]],
    projection_path: str,
    expected_account_scope: str,
    expected_view_scope: str,
    mount: str = "store.account",
    instrument_name: Callable[[Mapping[str, Any]], str] = _instrument_id,
    poll_interval_seconds: float = 0.2,
    start: bool = True,
    wait_seconds: float = 10.0,
) -> GatewayAccountPositionStream:
    """Public entry: a Gateway position stream built from the client config.

    ``client_config`` is the public strategy-client configuration mapping
    (``bt_api_py.service_config.parse_client_config``). ``curve_keys`` maps a
    security profile ref to ``(server_public, client_public, client_secret)``.
    The runtime and consumer are created, maintained and closed in the
    stream's own thread (both are owner-thread bound); the returned stream is
    started unless ``start=False``. Pass ``stream.report`` (or the stream
    itself) as the strategy ledger's ``account_positions``.
    """

    from uuid import uuid4

    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

    from bt_api_py.service_account_consumer import ServiceAccountConsumer
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import (
        ClientCurveKeys,
        ServiceClientRuntime,
        ZmqTransportFactory,
    )

    config = parse_client_config(dict(client_config))  # validate before any thread
    keys = {ref: ClientCurveKeys(*material) for ref, material in curve_keys.items()}
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
    peer = WireIdentity(WireRole.SERVICE, service_principal_ref, service_id)
    snapshot_reducer, event_reducer = gateway_position_reducers()

    def factory():
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _scope_ref: WireScope(
                execution_domain_ref, account_ref=account_ref, strategy_ref=strategy_ref
            ),
            peer_resolver=lambda _service_id: peer,
            transport_factory=ZmqTransportFactory(keys),
            run_id=uuid4().hex,
            requested_operations={mount: _ACCOUNT_STREAM_OPERATIONS},
        )
        try:
            runtime.start()
            consumer = ServiceAccountConsumer(
                runtime,
                mount,
                account_ref=account_ref,
                path=projection_path,
                projection_version="sdk.account.view.v1/positions-stream-v1",
                initial_state={"view_scope": expected_view_scope, "positions": {}},
                expected_account_scope=expected_account_scope,
                expected_strategy_scope=expected_view_scope,
                snapshot_reducer=snapshot_reducer,
                event_reducer=event_reducer,
            )
        except BaseException:
            runtime.close()
            raise
        return consumer, runtime

    stream = GatewayAccountPositionStream(
        factory,
        account_ref=account_ref,
        source=f"gateway:{service_id}:{mount}",
        instrument_name=instrument_name,
        poll_interval_seconds=poll_interval_seconds,
    )
    return stream.start(wait_seconds=wait_seconds) if start else stream


__all__.append("gateway_position_stream_from_config")


# ---------------------------------------------------------------------------
# Gateway reconciliation cut (full account, verified source) — acceptance G2
# ---------------------------------------------------------------------------


class GatewayReconciliationPositionSource:
    """Complete reconciliation evidence from ``account.reconciliation_cut``.

    Unlike :class:`GatewayAccountPositionStream` (a possibly trimmed
    per-strategy view, always incomplete), this source asks the Gateway for
    the inspection of a *full-account* position receipt bound to a verified
    source cut and a live, server-owned grant. Each successful poll replaces
    the state with those rows as a complete snapshot and proves it live only
    until the evidence's ``valid_until_ns`` (the shortest of the Gateway's
    receipt, stream-binding, grant and account-binding leases).

    Any refusal (revoked or changed grant, changed source, expired lease,
    unknown principal, transport failure) interrupts the state at once, so a
    revoked principal can never keep reporting ``complete``. Evidence already
    returned before a revocation stays valid only for its own lease and is
    never extended. ``runtime_factory()`` builds a started
    ``ServiceClientRuntime`` in this source's thread (owner-thread bound).
    """

    OPERATION = "account.reconciliation_cut"

    def __init__(
        self,
        runtime_factory: Callable[[], Any],
        *,
        account_ref: str,
        mount: str = "store.account",
        source: str = "gateway:account.reconciliation_cut",
        instrument_name: Callable[[Mapping[str, Any]], str] = _instrument_id,
        poll_interval_seconds: float = 0.2,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if not callable(runtime_factory) or not callable(instrument_name):
            raise AccountPositionStreamError("STREAM_CONFIG_INVALID")
        if type(poll_interval_seconds) not in (int, float) or poll_interval_seconds <= 0:
            raise AccountPositionStreamError("STREAM_CONFIG_INVALID")
        self.positions = StreamedAccountPositions(account_ref, source, clock_ns=clock_ns)
        self._factory = runtime_factory
        self._mount = mount
        self._instrument_name = instrument_name
        self._interval = float(poll_interval_seconds)
        self._max_backoff = max(float(poll_interval_seconds), 5.0)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._first = threading.Event()
        self._error: str | None = None
        self._thread = threading.Thread(
            target=self._run, name="gateway-reconciliation", daemon=True
        )

    def start(self, *, wait_seconds: float = 10.0) -> GatewayReconciliationPositionSource:
        self._thread.start()
        self._first.wait(wait_seconds)
        return self

    def report(self) -> AccountPositionReport:
        return self.positions.report()

    __call__ = report

    @property
    def error(self) -> str | None:
        return self._error

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stop.set()
            self.positions.mark_interrupted("GATEWAY_RECONCILIATION_CLOSED")
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self) -> None:
        backoff = self._interval
        while not self._stop.is_set():
            runtime = None
            try:
                runtime = self._factory()
                backoff = self._interval
                while not self._stop.is_set():
                    delay = runtime.next_maintenance_delay()
                    if delay is not None and delay <= 0:
                        runtime.maintain()
                    self._poll_once(runtime)
                    self._first.set()
                    self._stop.wait(self._interval)
            except Exception as error:
                self._error = getattr(error, "code", type(error).__name__)
                self.positions.mark_interrupted("GATEWAY_RECONCILIATION_FAILED")
                self._first.set()
            finally:
                if runtime is not None:
                    try:
                        runtime.close()
                    except Exception:
                        pass
            self._stop.wait(backoff)
            backoff = min(backoff * 2, self._max_backoff)
        self._first.set()

    def _poll_once(self, runtime) -> None:
        try:
            reply = runtime.call(
                self._mount, self.OPERATION, {}, account_ref=self.positions.account_ref
            )
        except Exception as error:
            self._error = getattr(error, "code", type(error).__name__)
            self.positions.mark_interrupted("GATEWAY_RECONCILIATION_REFUSED")
            return
        try:
            evidence = reply["report"]
            if evidence["account_ref"] != self.positions.account_ref:
                raise AccountPositionStreamError("GATEWAY_ACCOUNT_SCOPE_MISMATCH")
            observed = evidence["observed_ns"]
            valid_until = evidence["valid_until_ns"]
            if type(observed) is not int or type(valid_until) is not int or valid_until <= 0:
                raise AccountPositionStreamError("OBSERVED_TIME_INVALID")
            snapshot: dict[tuple[str, str], Decimal] = {}
            identities: dict[str, str] = {}
            for row in evidence["positions"]:
                name = _name(self._instrument_name(row))
                identity = json.dumps(
                    _plain(row["instrument"]), sort_keys=True, separators=(",", ":")
                )
                if identities.setdefault(name, identity) != identity:
                    raise AccountPositionStreamError("GATEWAY_INSTRUMENT_NAME_COLLISION")
                key = (name, row["side"])
                if key[1] not in _SIDES or key in snapshot:
                    raise AccountPositionStreamError("POSITION_ROW_INVALID")
                snapshot[key] = _quantity(str(row["quantity"]))
        except (AccountPositionStreamError, KeyError, TypeError, ValueError) as error:
            self.positions.mark_interrupted(getattr(error, "code", "GATEWAY_EVIDENCE_INVALID"))
            return
        with self._lock:
            if self._stop.is_set():
                return
            self.positions.apply_snapshot(snapshot, observed_ns=observed)
            self.positions.confirm_live(valid_until_ns=valid_until)
