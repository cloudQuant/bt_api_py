"""Unit tests for pre-BEGIN TD catalog, MD membership, and calendar fact coordination."""

from __future__ import annotations

import json
import sqlite3
import threading
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.market_recovery_journal_event import parse_recovery_journal_event
from bt_api_base.market_recovery_observation import RecoveryObservationMember
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_monitor.alert_delivery import AlertDeliveryService
from bt_api_monitor.durable import DurableOutbox, OutboxEvent
from bt_api_monitor.recovery_duration import RecoveryDurationBinding, RecoveryDurationConsumer
from bt_api_monitor.recovery_duration_alerts import (
    RecoveryDurationAlertReconciler,
    RecoveryDurationAlertRule,
)
from bt_api_monitor.service_ingress import MonitorIngressProducerBinding

from bt_api_py.gateway_instrument_product_client import GatewayInstrumentProductClient
from bt_api_py.gateway_instrument_product_codec import (
    BEGIN as PRODUCT_BEGIN,
)
from bt_api_py.gateway_instrument_product_codec import (
    OPERATIONS as PRODUCT_OPERATIONS,
)
from bt_api_py.gateway_instrument_product_codec import (
    SCHEMA as PRODUCT_SCHEMA,
)
from bt_api_py.gateway_monitor_calendar_client import (
    OPERATIONS as CALENDAR_OPERATIONS,
)
from bt_api_py.gateway_monitor_calendar_client import (
    GatewayMonitorCalendarClient,
)
from bt_api_py.monitor_recovery_fact_coordinator import (
    RecoveryFactResolutionCoordinator,
    RecoveryFactResolutionError,
    RecoveryMdMembershipBinding,
)
from bt_api_py.service_runtime import RegisteredBinding, ServiceClientRuntime

KEY_RB = MarketSubscriptionKey(
    "source",
    "grant",
    MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701"),
    "trade",
)
KEY_HC = MarketSubscriptionKey(
    "source",
    "grant",
    MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "hc2701", "hc2701"),
    "trade",
)
IDENTITY = WireIdentity(WireRole.SERVICE, "monitor-p", "monitor-a")
PEER = WireIdentity(WireRole.SERVICE, "gateway", "gateway-service")
SCOPE = WireScope("domain", account_ref="account")
MINUTE = 60 * 1_000_000_000
EPOCH = "a" * 32
DAY = "2026-11-17"
# 2026-11-17T02:00:00Z = 10:00:00 CST (Asia/Shanghai) on trading_day 2026-11-17.
BASE_UTC = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
BASE_NS = int(BASE_UTC.timestamp()) * 1_000_000_000


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sorted_members(*pairs: tuple[MarketSubscriptionKey, str]) -> tuple[RecoveryObservationMember, ...]:
    items = [RecoveryObservationMember(key, gen) for key, gen in pairs]
    items.sort(key=lambda item: (item.key.stream_id, item.generation))
    return tuple(items)


def _make_clients(monkeypatch, *, clock_box):
    runtime = object.__new__(ServiceClientRuntime)
    product_binding = RegisteredBinding(
        "upstream.instrument_products",
        "gateway-service",
        "instance",
        "scope",
        SCOPE,
        "reg-prod",
        "epoch-1",
        PRODUCT_OPERATIONS,
        300,
        "policy",
        "requirements",
        "transport",
        1,
        lease_deadline=250,
        renewal_due_at=200,
    )
    calendar_binding = RegisteredBinding(
        "upstream.calendar",
        "gateway-service",
        "instance",
        "scope",
        SCOPE,
        "reg-cal",
        "epoch-1",
        CALENDAR_OPERATIONS,
        300,
        "policy",
        "requirements",
        "transport",
        1,
        lease_deadline=250,
        renewal_due_at=200,
    )
    runtime._state = "registered"
    runtime._bindings = [product_binding, calendar_binding]
    runtime.identity = IDENTITY
    runtime.run_id = "run"
    runtime._cleanup_unknown = None
    runtime._on_cleanup_sticky = None
    runtime._monotonic_now = lambda: 100
    lineage_prod = tuple(object() for _ in range(7))
    lineage_cal = tuple(object() for _ in range(7))
    runtime._registration_lineage = {
        id(product_binding): (product_binding, None, *lineage_prod),
        id(calendar_binding): (calendar_binding, None, *lineage_cal),
    }
    runtime._has_current_registration_observation = lambda b: (
        id(b) in runtime._registration_lineage and runtime._registration_lineage[id(b)][0] is b
    )
    runtime.authenticated_peer_for_binding = lambda _: (PEER, "f" * 64)
    runtime._transports = {("gateway-service", "transport"): object()}

    state = {
        "products": {
            "rb2701": "rb",
            "hc2701": "hc",
        },
        "session_generation": "sess-1",
        "connection_generation": 1,
        "trading_day": DAY,
        "calendar_ref": "v1",
        "version_sha256": "1" * 64,
        "load_status": "CURRENT",
        "cal_status": "COMPLETE",
        "jobs": {},
    }

    def call(mount, operation, arguments, **_kwargs):
        if mount == "upstream.instrument_products":
            op_id = arguments["operation_id"]
            if operation == PRODUCT_BEGIN:
                instrument = MarketInstrumentKey.from_dict(arguments["instrument"])
                state["jobs"][op_id] = instrument
                return {"operation_id": op_id, "status": "pending", "code": None, "snapshot": None}
            instrument = state["jobs"][op_id]
            prod_id = state["products"][instrument.instrument_id]
            completed_dt = clock_box["utc"]
            return {
                "operation_id": op_id,
                "status": "ready",
                "code": None,
                "snapshot": {
                    "schema": PRODUCT_SCHEMA,
                    "instrument": instrument.to_dict(),
                    "product_id": prod_id,
                    "session_generation": state["session_generation"],
                    "connection_generation": state["connection_generation"],
                    "trading_day": state["trading_day"],
                    "completed_at_utc": _iso(completed_dt),
                    "remaining_seconds": 30.0,
                },
            }
        assert mount == "upstream.calendar"
        scope_arg = deepcopy(dict(arguments))
        cal_day = date.fromisoformat(state["trading_day"])
        win_morning_start = BASE_UTC
        win_morning_end = BASE_UTC + timedelta(minutes=6)
        win_auction_start = BASE_UTC + timedelta(minutes=6)
        win_auction_end = BASE_UTC + timedelta(minutes=36)
        win_afternoon_start = BASE_UTC + timedelta(minutes=36)
        win_afternoon_end = BASE_UTC + timedelta(minutes=60)
        q_start = datetime.fromisoformat(scope_arg["start_utc"][:-1] + "+00:00")
        q_end = datetime.fromisoformat(scope_arg["end_utc"][:-1] + "+00:00")
        all_windows = [
            {
                "window_id": "w1",
                "phase": "continuous",
                "trading_day": state["trading_day"],
                "start_utc": _iso(win_morning_start),
                "end_utc": _iso(win_morning_end),
            },
            {
                "window_id": "w2",
                "phase": "auction",
                "trading_day": state["trading_day"],
                "start_utc": _iso(win_auction_start),
                "end_utc": _iso(win_auction_end),
            },
            {
                "window_id": "w3",
                "phase": "continuous",
                "trading_day": state["trading_day"],
                "start_utc": _iso(win_afternoon_start),
                "end_utc": _iso(win_afternoon_end),
            },
        ]
        overlapping = [
            w
            for w in all_windows
            if datetime.fromisoformat(w["start_utc"][:-1] + "+00:00") < q_end
            and datetime.fromisoformat(w["end_utc"][:-1] + "+00:00") > q_start
        ]
        if state["cal_status"] != "COMPLETE":
            return {
                "schema": "sdk.gateway.calendar.windows/1",
                "scope": scope_arg,
                "registration_id": "reg-cal",
                "service_epoch": "epoch-1",
                "status": "UNKNOWN",
                "reason": "SOURCE_UNAVAILABLE",
                "binding": None,
                "policy": None,
                "windows": [],
                "gaps": [],
                "source_authentication": "NOT_PROVEN",
                "trade_authorization": "NONE",
            }
        return {
            "schema": "sdk.gateway.calendar.windows/1",
            "scope": scope_arg,
            "registration_id": "reg-cal",
            "service_epoch": "epoch-1",
            "status": "COMPLETE",
            "reason": "OK",
            "binding": {
                "route": "CTP___FUTURE",
                "source_generation": "cal-gen-1",
                "bound_trading_day": state["trading_day"],
                "calendar_ref": state["calendar_ref"],
                "publication_id": "pub-1",
                "content_sha256": "2" * 64,
                "coverage_manifest_sha256": "3" * 64,
                "binding_sha256": "4" * 64,
                "version_sha256": state["version_sha256"],
                "coverage_status": "DECLARED_UNVERIFIED",
                "coverage_start": (cal_day - timedelta(days=5)).isoformat(),
                "coverage_end": (cal_day + timedelta(days=5)).isoformat(),
                "load_status": state["load_status"],
                "data_origin": "SYNTHETIC_MOCK",
            },
            "policy": {
                "policy_id": "policy",
                "declaration": "DEPLOYMENT_DECLARED_UNVERIFIED",
                "max_advance_days": 0,
                "max_spill_days": 0,
                "first_owner_day": cal_day.isoformat(),
                "last_owner_day": cal_day.isoformat(),
            },
            "windows": overlapping,
            "gaps": [],
            "source_authentication": "NOT_PROVEN",
            "trade_authorization": "NONE",
        }

    runtime.call = call
    product_client = GatewayInstrumentProductClient(
        runtime,
        identity=IDENTITY,
        scope=SCOPE,
        peer=PEER,
        service_id="gateway-service",
        instance_id="instance",
        scope_ref="scope",
    )
    calendar_client = GatewayMonitorCalendarClient(
        runtime,
        identity=IDENTITY,
        scope=SCOPE,
        peer=PEER,
        service_id="gateway-service",
        instance_id="instance",
        scope_ref="scope",
    )
    return product_client, calendar_client, state


def _consumer(tmp_path: Path, key: MarketSubscriptionKey, *, threshold_ns: int = 10 * MINUTE):
    outbox = DurableOutbox(tmp_path / "outbox.sqlite", clock=lambda: 2000.0)
    ingress = MonitorIngressProducerBinding(
        "binding", "source", IDENTITY, SCOPE, "outbox-scope", ("gateway.recovery.journal.v1",)
    )
    binding = RecoveryDurationBinding("consumer-rb", ingress, "scope", "cfg-v1", key)
    rule = RecoveryDurationAlertRule("recovery-rule", 1, threshold_ns, 100, "warning", False)
    consumer = RecoveryDurationConsumer(
        outbox, binding, scope_bindings=(ingress,), alert_rule=rule
    )
    delivery = AlertDeliveryService(outbox, (), clock=lambda: 2000.0)
    return outbox, binding, consumer, delivery


def _append(outbox: DurableOutbox, bind: RecoveryDurationBinding, raw: dict, event_id: str) -> None:
    producer = asdict(bind.ingress.identity)
    producer["role"] = bind.ingress.identity.role.value
    data = {
        "schema_version": 2,
        "producer": producer,
        "scope": asdict(bind.ingress.scope),
        "binding_ref": "binding",
        "source_ref": "source",
        "source_event_id": event_id,
        "payload": {
            "schema": "sdk.gateway_recovery_relay/v1",
            "envelope_time_basis": "FIRST_RELAY_ENQUEUE",
            "event": raw,
        },
    }
    outbox.append(
        OutboxEvent(event_id, bind.ingress.outbox_scope, "gateway.recovery.journal.v1", data, 123.0)
    )


def _epoch_event(cursor=1, epoch=EPOCH):
    return {
        "schema": "bt_api.market_recovery_journal_event/v1",
        "scope_ref": "scope",
        "source_ref": "source",
        "source_epoch": epoch,
        "journal_cursor": cursor,
        "kind": "epoch",
        "observation": None,
        "qualification": None,
        "gap_start": None,
        "gap_end": None,
        "previous_epoch": None,
        "continuity": "unknown",
        "through_sequence": None,
    }


def _obs_event(
    cursor: int,
    sequence: int,
    members: tuple[RecoveryObservationMember, ...],
    *,
    epoch: str = EPOCH,
    episode_id: str = "d" * 32,
    state: str = "RECOVERING",
    reason: str = "WATERMARK",
    origin_gen: int = 1,
    start_ns: int | None = None,
    start_lower_ns: int | None = None,
    end_ns: int | None = None,
):
    obs = {
        "source_epoch": epoch,
        "owner_pid": 111,
        "sequence": sequence,
        "episode_id": episode_id,
        "state": state,
        "reason": reason,
        "observed_monotonic_ns": 100 + cursor,
        "coverage_started_monotonic_ns": 100,
        "coverage_through_monotonic_ns": 100 if reason == "BEGIN" else 100 + cursor,
        "origin_connection_generation": origin_gen,
        "observed_connection_generation": origin_gen + 1,
        "members": [{"key": m.key.to_dict(), "generation": m.generation} for m in members],
    }
    qual = {
        "status": "UNKNOWN",
        "reason": "NO_CLOCK_HISTORY",
        "start_utc_lower_ns": None,
        "start_utc_upper_ns": None,
        "end_utc_lower_ns": None,
        "end_utc_upper_ns": None,
        "source_epoch": None,
        "first_sample_sequence": None,
        "last_sample_sequence": None,
    }
    if reason != "BEGIN":
        assert start_ns is not None and end_ns is not None
        low = start_ns if start_lower_ns is None else start_lower_ns
        qual.update(
            status="QUALIFIED",
            reason="OK",
            start_utc_lower_ns=low,
            start_utc_upper_ns=start_ns,
            end_utc_lower_ns=end_ns,
            end_utc_upper_ns=end_ns,
            source_epoch="c" * 32,
            first_sample_sequence=1,
            last_sample_sequence=2,
        )
    return parse_recovery_journal_event(
        {
            "schema": "bt_api.market_recovery_journal_event/v1",
            "scope_ref": "scope",
            "source_ref": "source",
            "source_epoch": epoch,
            "journal_cursor": cursor,
            "kind": "observation",
            "observation": obs,
            "qualification": qual,
            "gap_start": None,
            "gap_end": None,
            "previous_epoch": None,
            "continuity": None,
            "through_sequence": None,
        }
    ).to_dict()


def test_membership_binding_rejects_invalid_and_ambiguous_inputs():
    m1 = RecoveryObservationMember(KEY_RB, "gen-1")
    m2 = RecoveryObservationMember(KEY_RB, "gen-2")
    with pytest.raises(RecoveryFactResolutionError, match="INVALID_MD_MEMBERSHIP_BINDING"):
        RecoveryMdMembershipBinding("source", "bad", 1, 0, (m1,))
    with pytest.raises(RecoveryFactResolutionError, match="INVALID_MD_MEMBERSHIP_BINDING"):
        RecoveryMdMembershipBinding("source", EPOCH, 0, 0, (m1,))
    with pytest.raises(RecoveryFactResolutionError, match="INVALID_MD_MEMBERSHIP_BINDING"):
        RecoveryMdMembershipBinding("source", EPOCH, 1, 0, ())
    with pytest.raises(RecoveryFactResolutionError, match="INVALID_MD_MEMBERSHIP_BINDING"):
        RecoveryMdMembershipBinding("source", EPOCH, 1, 0, (m2, m1))
    binding = RecoveryMdMembershipBinding("source", EPOCH, 1, 0, (m1, m2))
    with pytest.raises(RecoveryFactResolutionError, match="MEMBERSHIP_MEMBER_AMBIGUOUS"):
        binding.member_for(KEY_RB)
    with pytest.raises(RecoveryFactResolutionError, match="MEMBERSHIP_MEMBER_MISSING"):
        binding.member_for(KEY_HC)


def test_refuses_when_no_prebound_binding_exists(tmp_path, monkeypatch):
    clock_box = {"utc": BASE_UTC}
    product_client, calendar_client, _ = _make_clients(monkeypatch, clock_box=clock_box)
    coordinator = RecoveryFactResolutionCoordinator(
        product_client=product_client,
        calendar_client=calendar_client,
        route="CTP___FUTURE",
        clock_utc_ns=lambda: int(clock_box["utc"].timestamp()) * 1_000_000_000,
    )
    outbox, bind, consumer, _ = _consumer(tmp_path, KEY_RB)
    members = _sorted_members((KEY_RB, "gen-rb"))
    _append(outbox, bind, _epoch_event(), "epoch")
    _append(outbox, bind, _obs_event(2, 1, members, reason="BEGIN"), "begin")
    _append(
        outbox,
        bind,
        _obs_event(3, 2, members, start_ns=BASE_NS, end_ns=BASE_NS + 40 * MINUTE),
        "mark",
    )
    assert consumer.process_next() == "SOURCE_CONTINUITY_UNKNOWN"
    assert consumer.process_next() == "BEGIN_CANDIDATE"
    assert consumer.process_next() == "PENDING_MAPPING_OR_CALENDAR"

    result = coordinator.resolve_pending_once("consumer-rb", consumer)
    assert result.status == "REFUSED"
    assert result.reason == "NO_PREBOUND_FACT_BINDING"
    assert result.permanent_refusal is True
    assert consumer.snapshot()["checkpoint"] == 2
    assert consumer.snapshot()["episodes"][0]["members"][0]["lower_bound_ns"] == 0


def test_older_begin_is_not_backfilled_by_later_product_query(tmp_path, monkeypatch):
    clock_box = {"utc": BASE_UTC + timedelta(minutes=15)}
    product_client, calendar_client, _ = _make_clients(monkeypatch, clock_box=clock_box)
    coordinator = RecoveryFactResolutionCoordinator(
        product_client=product_client,
        calendar_client=calendar_client,
        route="CTP___FUTURE",
        clock_utc_ns=lambda: int(clock_box["utc"].timestamp()) * 1_000_000_000,
    )
    outbox, bind, consumer, _ = _consumer(tmp_path, KEY_RB)
    members = _sorted_members((KEY_RB, "gen-rb"))
    # Older BEGIN occurred at BASE_UTC (15 minutes BEFORE ProductID query at BASE_UTC + 15m).
    _append(outbox, bind, _epoch_event(), "epoch")
    _append(outbox, bind, _obs_event(2, 1, members, reason="BEGIN"), "old-begin")
    _append(
        outbox,
        bind,
        _obs_event(
            3,
            2,
            members,
            start_lower_ns=BASE_NS,
            start_ns=BASE_NS,
            end_ns=BASE_NS + 40 * MINUTE,
        ),
        "old-mark",
    )
    assert consumer.process_next() == "SOURCE_CONTINUITY_UNKNOWN"
    assert consumer.process_next() == "BEGIN_CANDIDATE"
    assert consumer.process_next() == "PENDING_MAPPING_OR_CALENDAR"

    # Establish binding AFTER the historical BEGIN.
    membership = RecoveryMdMembershipBinding("source", EPOCH, 1, 0, members)
    coordinator.establish_binding(consumer_id="consumer-rb", member_key=KEY_RB, membership=membership)

    result = coordinator.resolve_pending_once("consumer-rb", consumer)
    assert result.status == "REFUSED"
    assert result.reason == "HISTORICAL_BEGIN_PREDATES_BINDING"
    assert result.permanent_refusal is True
    assert consumer.snapshot()["checkpoint"] == 2
    assert consumer.snapshot()["episodes"][0]["members"][0]["lower_bound_ns"] == 0
    with sqlite3.connect(outbox.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM recovery_duration_alert_candidate").fetchone()[0] == 0


def test_lower_bound_before_binding_refused_even_when_upper_bound_after(tmp_path, monkeypatch):
    # Clock error window spans across binding establishment time -> must fail closed!
    clock_box = {"utc": BASE_UTC + timedelta(seconds=10)}
    product_client, calendar_client, _ = _make_clients(monkeypatch, clock_box=clock_box)
    coordinator = RecoveryFactResolutionCoordinator(
        product_client=product_client,
        calendar_client=calendar_client,
        route="CTP___FUTURE",
        clock_utc_ns=lambda: int(clock_box["utc"].timestamp()) * 1_000_000_000,
    )
    outbox, bind, consumer, _ = _consumer(tmp_path, KEY_RB)
    members = _sorted_members((KEY_RB, "gen-rb"))
    _append(outbox, bind, _epoch_event(), "epoch")
    _append(outbox, bind, _obs_event(2, 1, members, reason="BEGIN"), "begin")
    _append(
        outbox,
        bind,
        _obs_event(
            3,
            2,
            members,
            start_lower_ns=BASE_NS + 9_000_000_000,
            start_ns=BASE_NS + 11_000_000_000,
            end_ns=BASE_NS + 40 * MINUTE,
        ),
        "mark",
    )
    assert consumer.process_next() == "SOURCE_CONTINUITY_UNKNOWN"
    assert consumer.process_next() == "BEGIN_CANDIDATE"
    assert consumer.process_next() == "PENDING_MAPPING_OR_CALENDAR"

    membership = RecoveryMdMembershipBinding("source", EPOCH, 1, 0, members)
    coordinator.establish_binding(consumer_id="consumer-rb", member_key=KEY_RB, membership=membership)
    result = coordinator.resolve_pending_once("consumer-rb", consumer)
    assert result.status == "REFUSED"
    assert result.reason == "HISTORICAL_BEGIN_PREDATES_BINDING"


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("sequence", "HISTORICAL_BEGIN_SEQUENCE_PREDATES_BINDING"),
        ("epoch", "SOURCE_EPOCH_MISMATCH"),
        ("md_gen", "MD_GENERATION_MISMATCH"),
        ("member_gen", "MEMBER_GENERATION_MISMATCH"),
        ("membership", "MEMBERSHIP_MISMATCH"),
        ("session_gen", "SESSION_GENERATION_MISMATCH"),
        ("trading_day", "TRADING_DAY_MISMATCH"),
        ("product_id", "PRODUCT_MISMATCH"),
        ("calendar_version", "CALENDAR_VERSION_MISMATCH"),
        ("calendar_stale", "CALENDAR_UNAVAILABLE"),
    ],
)
def test_mismatched_binding_dimensions_are_refused(tmp_path, monkeypatch, mutation, expected_reason):
    clock_box = {"utc": BASE_UTC}
    product_client, calendar_client, state = _make_clients(monkeypatch, clock_box=clock_box)
    coordinator = RecoveryFactResolutionCoordinator(
        product_client=product_client,
        calendar_client=calendar_client,
        route="CTP___FUTURE",
        clock_utc_ns=lambda: int(clock_box["utc"].timestamp()) * 1_000_000_000,
    )
    outbox, bind, consumer, _ = _consumer(tmp_path, KEY_RB)
    members = _sorted_members((KEY_RB, "gen-rb"))
    snap_members = members
    after_seq = 0
    obs_epoch = EPOCH
    origin_gen = 1

    if mutation == "sequence":
        after_seq = 1
    elif mutation == "epoch":
        obs_epoch = "b" * 32
    elif mutation == "md_gen":
        origin_gen = 2
    elif mutation == "member_gen":
        members = _sorted_members((KEY_RB, "gen-other"))
    elif mutation == "membership":
        members = _sorted_members((KEY_RB, "gen-rb"), (KEY_HC, "gen-hc"))

    membership = RecoveryMdMembershipBinding("source", EPOCH, 1, after_seq, snap_members)
    coordinator.establish_binding(consumer_id="consumer-rb", member_key=KEY_RB, membership=membership)

    _append(outbox, bind, _epoch_event(epoch=obs_epoch), "epoch")
    _append(
        outbox,
        bind,
        _obs_event(2, 1, members, epoch=obs_epoch, reason="BEGIN", origin_gen=origin_gen),
        "begin",
    )
    _append(
        outbox,
        bind,
        _obs_event(
            3,
            2,
            members,
            epoch=obs_epoch,
            origin_gen=origin_gen,
            start_ns=BASE_NS,
            end_ns=BASE_NS + 40 * MINUTE,
        ),
        "mark",
    )
    assert consumer.process_next() == "SOURCE_CONTINUITY_UNKNOWN"
    assert consumer.process_next() == "BEGIN_CANDIDATE"
    assert consumer.process_next() == "PENDING_MAPPING_OR_CALENDAR"

    if mutation == "session_gen":
        state["session_generation"] = "sess-2"
    elif mutation == "trading_day":
        state["trading_day"] = "2026-11-18"
    elif mutation == "product_id":
        state["products"]["rb2701"] = "hc"
    elif mutation == "calendar_version":
        state["version_sha256"] = "9" * 64
    elif mutation == "calendar_stale":
        state["load_status"] = "STALE"

    result = coordinator.resolve_pending_once("consumer-rb", consumer)
    assert result.status == "REFUSED"
    assert result.reason == expected_reason
    assert consumer.snapshot()["checkpoint"] == 2


def test_future_begin_resolves_across_lunch_break_and_triggers_recovers_alert(tmp_path, monkeypatch):
    clock_box = {"utc": BASE_UTC}
    product_client, calendar_client, _ = _make_clients(monkeypatch, clock_box=clock_box)
    coordinator = RecoveryFactResolutionCoordinator(
        product_client=product_client,
        calendar_client=calendar_client,
        route="CTP___FUTURE",
        clock_utc_ns=lambda: int(clock_box["utc"].timestamp()) * 1_000_000_000,
    )
    outbox, bind, consumer, delivery = _consumer(tmp_path, KEY_RB, threshold_ns=10 * MINUTE)
    members = _sorted_members((KEY_RB, "gen-rb"), (KEY_HC, "gen-hc"))
    membership = RecoveryMdMembershipBinding("source", EPOCH, 1, 0, members)
    bound = coordinator.establish_binding(
        consumer_id="consumer-rb", member_key=KEY_RB, membership=membership
    )
    assert bound.product_id == "rb" and bound.trading_day == DAY

    # Future BEGIN occurs after established_at_utc_ns (BASE_NS).
    # Morning continuous: [0..6m] (6m), Auction/break: [6..36m] (0m), Afternoon continuous: [36..60m].
    _append(outbox, bind, _epoch_event(), "epoch")
    _append(outbox, bind, _obs_event(2, 1, members, reason="BEGIN"), "begin")
    _append(
        outbox,
        bind,
        _obs_event(3, 2, members, start_ns=BASE_NS, end_ns=BASE_NS + 38 * MINUTE),
        "mark-8m",
    )
    assert consumer.process_next() == "SOURCE_CONTINUITY_UNKNOWN"
    assert consumer.process_next() == "BEGIN_CANDIDATE"
    assert consumer.process_next() == "PENDING_MAPPING_OR_CALENDAR"

    first = coordinator.resolve_pending_once("consumer-rb", consumer)
    assert first.status == "RESOLVED" and first.reason == "WATERMARK_RECORDED"
    assert consumer.snapshot()["episodes"][0]["members"][0]["lower_bound_ns"] == 8 * MINUTE
    assert RecoveryDurationAlertReconciler(consumer, delivery).reconcile(limit=10) == 0

    # Advance into afternoon continuous to reach exact 10m threshold (6m + 4m).
    _append(
        outbox,
        bind,
        _obs_event(4, 3, members, start_ns=BASE_NS, end_ns=BASE_NS + 40 * MINUTE),
        "mark-10m",
    )
    assert consumer.process_next() == "PENDING_MAPPING_OR_CALENDAR"
    second = coordinator.resolve_pending_once("consumer-rb", consumer)
    assert second.status == "RESOLVED" and second.reason == "WATERMARK_RECORDED"
    assert consumer.snapshot()["episodes"][0]["members"][0]["lower_bound_ns"] == 10 * MINUTE
    assert RecoveryDurationAlertReconciler(consumer, delivery).reconcile(limit=10) == 1

    # Terminal RECOVERED emits RECOVERED alert.
    _append(
        outbox,
        bind,
        _obs_event(
            5,
            4,
            members,
            state="RECOVERED",
            reason="COMPLETED",
            start_ns=BASE_NS,
            end_ns=BASE_NS + 41 * MINUTE,
        ),
        "recovered",
    )
    assert consumer.process_next() == "PENDING_MAPPING_OR_CALENDAR"
    third = coordinator.resolve_pending_once("consumer-rb", consumer)
    assert third.status == "RESOLVED" and third.reason == "RECOVERED_OBSERVED"
    assert RecoveryDurationAlertReconciler(consumer, delivery).reconcile(limit=10) == 1

    with sqlite3.connect(outbox.database_path) as db:
        rows = db.execute(
            "SELECT state, state_version, dimensions_json FROM monitor_alert_events ORDER BY state_version"
        ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("TRIGGERED", 1), ("RECOVERED", 2)]
    evidence = json.loads(json.loads(rows[0][2])["evidence"])
    assert evidence["mode"] == "SIMULATION_ONLY"
    assert evidence["source_authentication"] == "NOT_PROVEN"
    assert evidence["business_eligibility"] == "UNKNOWN"


def test_wrong_owner_thread_refused(monkeypatch):
    clock_box = {"utc": BASE_UTC}
    product_client, calendar_client, _ = _make_clients(monkeypatch, clock_box=clock_box)
    coordinator = RecoveryFactResolutionCoordinator(
        product_client=product_client,
        calendar_client=calendar_client,
        route="CTP___FUTURE",
    )
    errors = []

    def worker():
        try:
            coordinator.binding_for("consumer-rb")
        except RecoveryFactResolutionError as exc:
            errors.append(exc.code)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert errors == ["WRONG_OWNER"]
