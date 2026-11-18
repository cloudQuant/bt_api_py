"""Real-CURVE integration tests for future-BEGIN trusted facts and anti-backfill."""

from __future__ import annotations

import importlib
import json
import queue
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.market_recovery_journal_event import parse_recovery_journal_event
from bt_api_base.market_recovery_observation import RecoveryObservationMember
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.ctp.client import CtpNativeStopReceipt, TraderClient
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

import bt_api_py.ctp_gateway_trading as trading
from bt_api_py.ctp_gateway_calendar_config import CtpGatewayCalendarSettings
from bt_api_py.gateway_calendar_service import CalendarMonitorGrant
from bt_api_py.gateway_instrument_product_client import GatewayInstrumentProductClient
from bt_api_py.gateway_instrument_product_codec import OPERATIONS as PRODUCT_OPERATIONS
from bt_api_py.gateway_instrument_product_config import InstrumentProductGrant
from bt_api_py.gateway_monitor_calendar_client import (
    OPERATIONS as CALENDAR_OPERATIONS,
)
from bt_api_py.gateway_monitor_calendar_client import (
    GatewayMonitorCalendarClient,
)
from bt_api_py.monitor_recovery_fact_coordinator import (
    RecoveryFactResolutionCoordinator,
    RecoveryMdMembershipBinding,
)
from bt_api_py.monitor_service_config import parse_monitor_service_config
from bt_api_py.monitor_service_host import MonitorServiceHost
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.trading_calendar_binding import SIMULATION_ONLY
from bt_api_py.trading_calendar_composite import compose_simulated
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy
from bt_api_py.trading_calendar_update_simulation import VenueContent
from tests.calendar_test_store import FixtureCalendarStore
from tests.integration.test_iter63_gateway_direct_service import DOMAIN, SERVICE
from tests.integration.test_monitor_service_process import client as telemetry_client
from tests.test_iter63_ctp_gateway_trading import make
from tests.unit.test_monitor_recovery_duration_host import (
    call_with_host,
    keypair,
    wait_for,
)
from tests.unit.test_monitor_service_v2_config import v2_document

pytestmark = pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_MINUTE = 60 * 1_000_000_000
_EPOCH = "a" * 32
_EVENT_TYPE = "gateway.recovery.journal.v1"

INSTRUMENT_RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")
INSTRUMENT_HC = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "hc2701", "hc2701")
KEY_RB = MarketSubscriptionKey("source", "grant", INSTRUMENT_RB, "trade")
KEY_HC = MarketSubscriptionKey("source", "grant", INSTRUMENT_HC, "trade")
MONITOR_READER_IDENTITY = WireIdentity(WireRole.SERVICE, "monitor-reader", "monitor-reader-service")


def _sorted_members(*pairs: tuple[MarketSubscriptionKey, str]) -> tuple[RecoveryObservationMember, ...]:
    items = [RecoveryObservationMember(key, gen) for key, gen in pairs]
    items.sort(key=lambda item: (item.key.stream_id, item.generation))
    return tuple(items)


def _calendar_content(trading_days: tuple[str, ...]) -> dict:
    source = ["synthetic"]
    return {
        "sources": [{"id": "synthetic", "description": "fictional CURVE test source"}],
        "trading_days": [
            {"venue": "SHFE", "day": day, "status": "open", "source_ids": source}
            for day in trading_days
        ],
        "trading_hours": [
            {
                "venue": "SHFE",
                "kind": "future",
                "product": product,
                "contracts": [contract],
                "valid_from": trading_days[0],
                "valid_to": trading_days[-1],
                "day_windows": [
                    {
                        "id": "morning",
                        "phase": "continuous",
                        "start": "09:00",
                        "end": "09:06",
                        "end_day_offset": 0,
                    },
                    {
                        "id": "break",
                        "phase": "auction",
                        "start": "09:06",
                        "end": "09:36",
                        "end_day_offset": 0,
                    },
                    {
                        "id": "afternoon",
                        "phase": "continuous",
                        "start": "09:36",
                        "end": "10:00",
                        "end_day_offset": 0,
                    },
                ],
                "night_windows": [],
                "night_mappings": [
                    {"trading_day": day, "start_date": None, "source_ids": source}
                    for day in trading_days
                ],
                "windows_complete": True,
                "exceptions_complete": True,
                "source_ids": source,
            }
            for product, contract in (("rb", "rb2701"), ("hc", "hc2701"))
        ],
        "exceptions": [],
    }


def _make_ctp_row(native_row, *, instrument_id: str, product_id: str):
    row = dict(native_row())
    row["InstrumentID"] = instrument_id
    row["ProductID"] = product_id
    return row


def _start_gateway_curve_server(tmp_path: Path, monkeypatch, *, target_day):
    tests_ctp = Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests"
    monkeypatch.syspath_prepend(str(tests_ctp))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    native_row = importlib.import_module("test_iter63_gateway_instrument_query").row
    real_loader = trading._load_ctp_types

    account = AccountKey("ctp", "simnow", "simnow", "account", "9999")
    _, settings, config, authority, direct, _ = make(tmp_path, monkeypatch, account_key=account)
    monkeypatch.setattr(trading, "_load_ctp_types", real_loader)
    settings = replace(settings, close_timeout_seconds=1, connect_timeout_seconds=1)
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    scope = WireScope(DOMAIN, account_ref=settings.account_scope)

    calendar_root = tmp_path / "calendar"
    days = (
        (target_day - timedelta(days=1)).isoformat(),
        target_day.isoformat(),
        (target_day + timedelta(days=1)).isoformat(),
    )
    cal_store = FixtureCalendarStore(calendar_root)
    grouped = VenueContent.from_layers(
        "SHFE",
        target_day - timedelta(days=1),
        target_day + timedelta(days=1),
        _calendar_content(days),
    )
    bundle = compose_simulated(
        mode=SIMULATION_ONLY, venues=(grouped,), snapshot_factory=CalendarSnapshot
    )
    assert bundle.status == "READY_SIMULATED", bundle
    pub = cal_store.publish(
        json.loads(bundle.snapshot_bytes),
        publication_id="pub-v1",
        version_id="v1",
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )
    assert pub.visibility == "COMMITTED"

    policies = frozenset(
        {
            CalendarRangePolicy("policy-rb", "CTP___FUTURE", "SHFE", "future", "rb", "rb2701", 0, 0),
            CalendarRangePolicy("policy-hc", "CTP___FUTURE", "SHFE", "future", "hc", "hc2701", 0, 0),
        }
    )
    cal_grants = frozenset(
        {
            CalendarMonitorGrant(
                MONITOR_READER_IDENTITY.principal_ref,
                MONITOR_READER_IDENTITY.actor_ref,
                DOMAIN,
                settings.account_scope,
                "CTP___FUTURE",
                "SHFE",
                "future",
                product,
                contract,
            )
            for product, contract in (("rb", "rb2701"), ("hc", "hc2701"))
        }
    )
    calendar_settings = CtpGatewayCalendarSettings(
        calendar_root,
        "CTP___FUTURE",
        frozenset(),
        policies,
        target_day,
        monitor_grants=cal_grants,
    )
    resources = (
        ResourceGrant(
            "gateway-a", "service.instrument_product_read", "scope-a", scope, PRODUCT_OPERATIONS
        ),
        ResourceGrant(
            "gateway-a", "service.calendar_read", "scope-a", scope, CALENDAR_OPERATIONS
        ),
    )
    authority = replace(
        authority,
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals={**authority.principals, client_keys[0]: MONITOR_READER_IDENTITY.principal_ref},
        grants={
            **authority.grants,
            MONITOR_READER_IDENTITY.principal_ref: (
                PrincipalGrant(MONITOR_READER_IDENTITY, resources),
            ),
        },
    )
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].extend(
        ["service.instrument_product_read", "service.calendar_read"]
    )
    config = parse_service_config(raw)

    product_map = {"rb2701": "rb", "hc2701": "hc"}

    def start_trader(trader, **_kwargs):
        native = helper._NativeApi()
        native.client = trader

        def request(field, request_id):
            row = _make_ctp_row(
                native_row,
                instrument_id=field.InstrumentID,
                product_id=product_map[field.InstrumentID],
            )
            trader._handle_query_callback("instruments", row, None, request_id, False)
            trader._handle_query_callback("instruments", None, None, request_id, True)
            return 0

        native.ReqQryInstrument = request
        trader._api = native
        helper._login(trader, native, session_id=19, trading_day=target_day.strftime("%Y%m%d"))

    monkeypatch.setattr(TraderClient, "start", start_trader)
    monkeypatch.setattr(
        TraderClient,
        "stop_and_wait",
        lambda trader, **_: CtpNativeStopReceipt(
            trader.connection_generation, False, True, True, False, False
        ),
    )

    def feed():
        return CtpRequestDataFuture(
            None,
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline.invalid:30001",
            md_front="tcp://offline.invalid:30011",
            auto_settlement_confirm=False,
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(settings.trusted_flow_root),
            flow_account_scope=settings.account_scope,
        )

    product_grant = InstrumentProductGrant(
        MONITOR_READER_IDENTITY.principal_ref,
        MONITOR_READER_IDENTITY.actor_ref,
        DOMAIN,
        settings.account_scope,
        frozenset({INSTRUMENT_RB, INSTRUMENT_HC}),
    )
    profile = trading.build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "registry-gateway.sqlite",
        profile_ref="curve-monitor-test",
        settings=settings,
        grants=direct,
        request_feed_factory=feed,
        market_stream_factory=lambda *_: pytest.fail("market session forbidden"),
        assert_admitted=lambda _: False,
        instrument_product_grants=frozenset({product_grant}),
        calendar_settings=calendar_settings,
    )
    ready, stop = queue.Queue(), threading.Event()

    def serve():
        try:
            deployment = profile.deployment_factory()
            deployment.start()
            ready.put(("ready",))
            while not stop.is_set():
                deployment.serve_once(10)
            deployment.close()
        except BaseException as exc:
            ready.put(("error", type(exc).__name__, str(exc)))

    thread = threading.Thread(target=serve)
    thread.start()
    result = ready.get(timeout=10)
    assert result == ("ready",), result
    endpoint = config.communication["profiles"]["server-transport"]["endpoints"]["rpc_bind"]
    gateway_info = {
        "listener": {
            "principal_ref": SERVICE.principal_ref,
            "service_id": SERVICE.actor_ref,
            "instance_id": "gateway-a",
            "scope_ref": "scope-a",
            "rpc_bind": endpoint,
        },
        "server_public_key": server_keys[0],
        "client_keys": client_keys,
        "scope": scope,
        "peer": SERVICE,
    }

    def stop_gateway():
        stop.set()
        thread.join(10)
        assert not thread.is_alive()

    return gateway_info, stop_gateway


def _gateway_client_bundle(gateway_info: dict, *, membership_by_consumer: dict, force_renew: bool = False):
    def factory(_configs):
        template = telemetry_client(
            gateway_info["listener"],
            gateway_info["server_public_key"],
            gateway_info["client_keys"],
            MONITOR_READER_IDENTITY.principal_ref,
            "events.append",
            actor=MONITOR_READER_IDENTITY.actor_ref,
        )
        raw = template._config
        raw["clients"] = {"gateway": raw["clients"].pop("monitor")}
        base_binding = raw["clients"]["gateway"]["settings"]["bindings"][0]
        base_binding.update(
            mount="upstream.instrument_products",
            account_ref=gateway_info["scope"].account_ref,
        )
        raw["clients"]["gateway"]["settings"]["bindings"].append(
            dict(base_binding, mount="upstream.calendar")
        )
        runtime = ServiceClientRuntime(
            parse_service_config(raw),
            identity=MONITOR_READER_IDENTITY,
            scope_resolver=lambda _: gateway_info["scope"],
            peer_resolver=lambda _: gateway_info["peer"],
            transport_factory=ZmqTransportFactory(
                {
                    "client": ClientCurveKeys(
                        gateway_info["server_public_key"], *gateway_info["client_keys"]
                    )
                }
            ),
            requested_operations={
                "upstream.instrument_products": PRODUCT_OPERATIONS,
                "upstream.calendar": CALENDAR_OPERATIONS,
            },
        )
        runtime.start()
        try:
            product_client = GatewayInstrumentProductClient(
                runtime,
                identity=MONITOR_READER_IDENTITY,
                scope=gateway_info["scope"],
                peer=gateway_info["peer"],
                service_id=gateway_info["peer"].actor_ref,
                instance_id="gateway-a",
                scope_ref="scope-a",
            )
            calendar_client = GatewayMonitorCalendarClient(
                runtime,
                identity=MONITOR_READER_IDENTITY,
                scope=gateway_info["scope"],
                peer=gateway_info["peer"],
                service_id=gateway_info["peer"].actor_ref,
                instance_id="gateway-a",
                scope_ref="scope-a",
            )
            coordinator = RecoveryFactResolutionCoordinator(
                product_client=product_client,
                calendar_client=calendar_client,
                route="CTP___FUTURE",
            )
            for consumer_id, (member_key, membership) in membership_by_consumer.items():
                coordinator.establish_binding(
                    consumer_id=consumer_id,
                    member_key=member_key,
                    membership=membership,
                )
            if force_renew:
                runtime._maintain(force=True)
        except Exception:
            runtime.close()
            raise

        class _Bundle:
            def __init__(self):
                self.coordinator = coordinator

            def maintain(self):
                coordinator.maintain()

            def close(self):
                try:
                    coordinator.close()
                finally:
                    try:
                        product_client.close()
                    finally:
                        try:
                            calendar_client.close()
                        finally:
                            runtime.close()
                return runtime.state == "closed"

        return _Bundle()

    return factory


def _monitor_config(tmp_path: Path, *, threshold_rb: int, threshold_hc: int):
    monitor_dir = tmp_path / "monitor"
    monitor_dir.mkdir(exist_ok=True)
    path, doc = v2_document(monitor_dir)
    doc["storage"]["event_types"] = [_EVENT_TYPE]
    doc["producer_bindings"][0].update(
        binding_ref="binding", source_ref="source", event_types=[_EVENT_TYPE]
    )
    doc["recovery_duration"] = [
        {
            "consumer_id": "consumer-rb",
            "binding_ref": "binding",
            "source_ref": "source",
            "scope_ref": "scope",
            "config_version": "v1",
            "member_key": KEY_RB.to_dict(),
            "alert_rule": {
                "rule_id": "rb-recovery-rule",
                "rule_version": 1,
                "threshold_ns": threshold_rb,
                "max_candidates": 20,
                "severity": "warning",
                "notify": False,
            },
        },
        {
            "consumer_id": "consumer-hc",
            "binding_ref": "binding",
            "source_ref": "source",
            "scope_ref": "scope",
            "config_version": "v1",
            "member_key": KEY_HC.to_dict(),
            "alert_rule": {
                "rule_id": "hc-recovery-rule",
                "rule_version": 1,
                "threshold_ns": threshold_hc,
                "max_candidates": 20,
                "severity": "warning",
                "notify": False,
            },
        },
    ]
    return monitor_dir, doc, parse_monitor_service_config(doc, path=path)


def _journal_obs(
    cursor: int,
    sequence: int,
    members: tuple[RecoveryObservationMember, ...],
    *,
    reason: str = "WATERMARK",
    state: str = "RECOVERING",
    start_ns: int | None = None,
    end_ns: int | None = None,
):
    obs = {
        "source_epoch": _EPOCH,
        "owner_pid": 400,
        "sequence": sequence,
        "episode_id": "e" * 32,
        "state": state,
        "reason": reason,
        "observed_monotonic_ns": 100 + cursor,
        "coverage_started_monotonic_ns": 100,
        "coverage_through_monotonic_ns": 100 if reason == "BEGIN" else 100 + cursor,
        "origin_connection_generation": 1,
        "observed_connection_generation": 2,
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
        qual.update(
            status="QUALIFIED",
            reason="OK",
            start_utc_lower_ns=start_ns,
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
            "source_epoch": _EPOCH,
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


def _epoch_marker():
    raw = _journal_obs(1, 1, _sorted_members((KEY_RB, "gen-rb")), reason="BEGIN")
    raw.update(
        journal_cursor=1,
        kind="epoch",
        observation=None,
        qualification=None,
        previous_epoch=None,
        continuity="unknown",
    )
    return raw


def _send_ingress(monitor_dir: Path, doc: dict, events: list[tuple[str, dict]]) -> None:
    producer = telemetry_client(
        doc["ingress"],
        keypair(monitor_dir, "events")[0],
        keypair(monitor_dir, "producer"),
        "producer",
        "events.append",
    )
    producer.start()
    try:
        for event_id, raw in events:
            producer.call(
                "upstream.telemetry",
                "events.append",
                {
                    "schema_version": 2,
                    "event_id": event_id,
                    "event_type": _EVENT_TYPE,
                    "occurred_at": 1800000000.0,
                    "binding_ref": "binding",
                    "source_ref": "source",
                    "data": {
                        "schema": "sdk.gateway_recovery_relay/v1",
                        "envelope_time_basis": "FIRST_RELAY_ENQUEUE",
                        "event": raw,
                    },
                },
            )
    finally:
        producer.close()


def test_real_curve_older_begin_is_not_backfilled_after_restart(tmp_path, monkeypatch):
    now_utc = datetime.now(UTC).replace(microsecond=0)
    target_day = now_utc.astimezone(_SHANGHAI).date() + timedelta(days=1)
    gateway_info, stop_gateway = _start_gateway_curve_server(
        tmp_path, monkeypatch, target_day=target_day
    )
    try:
        monitor_dir, doc, config = _monitor_config(
            tmp_path, threshold_rb=5 * _MINUTE, threshold_hc=5 * _MINUTE
        )
        members = _sorted_members((KEY_RB, "gen-rb"), (KEY_HC, "gen-hc"))
        membership = RecoveryMdMembershipBinding("source", _EPOCH, 1, 0, members)

        # Older BEGIN occurred 30 minutes BEFORE the coordinator queries ProductID!
        old_start = now_utc - timedelta(minutes=30)
        old_end = now_utc - timedelta(minutes=20)
        old_start_ns = int(old_start.timestamp()) * 1_000_000_000
        old_end_ns = int(old_end.timestamp()) * 1_000_000_000

        host = MonitorServiceHost(config)
        host.start()
        try:
            call_with_host(
                host,
                lambda: _send_ingress(
                    monitor_dir,
                    doc,
                    [
                        ("epoch", _epoch_marker()),
                        ("old-begin", _journal_obs(2, 1, members, reason="BEGIN")),
                        (
                            "old-mark",
                            _journal_obs(3, 2, members, start_ns=old_start_ns, end_ns=old_end_ns),
                        ),
                    ],
                ),
            )
            wait_for(lambda: host.recovery_duration_snapshot("consumer-rb")["status"] == "PENDING_FACTS")
        finally:
            host.close()

        # Now restart Monitor with real CURVE fact coordinator establishing a fresh binding
        # AFTER the older BEGIN occurred: must REFUSE to backfill!
        factory = _gateway_client_bundle(
            gateway_info,
            membership_by_consumer={
                "consumer-rb": (KEY_RB, membership),
                "consumer-hc": (KEY_HC, membership),
            },
        )
        reopened = MonitorServiceHost(config, recovery_fact_coordinator_factory=factory)
        reopened.start()
        try:
            wait_for(
                lambda: reopened.recovery_duration_snapshot("consumer-rb").get("fact_resolution")
                is not None
            )
            rb_view = reopened.recovery_duration_snapshot("consumer-rb")
            hc_view = reopened.recovery_duration_snapshot("consumer-hc")
            assert rb_view["status"] == "PENDING_FACTS"
            assert rb_view["current_state"] == "UNKNOWN"
            assert rb_view["fact_resolution"]["status"] == "REFUSED"
            assert rb_view["fact_resolution"]["reason"] == "HISTORICAL_BEGIN_PREDATES_BINDING"
            assert rb_view["historical_snapshot"]["checkpoint"] == 2
            assert rb_view["historical_snapshot"]["episodes"][0]["members"][0]["lower_bound_ns"] == 0
            assert hc_view["fact_resolution"]["status"] == "REFUSED"
            assert hc_view["fact_resolution"]["reason"] == "HISTORICAL_BEGIN_PREDATES_BINDING"

            with sqlite3.connect(monitor_dir / "data/events.sqlite") as db:
                assert db.execute("SELECT COUNT(*) FROM recovery_duration_alert_candidate").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM monitor_alert_events").fetchone()[0] == 0
        finally:
            reopened.close()
    finally:
        stop_gateway()


def test_real_curve_future_begin_resolves_windows_and_per_contract_alerts(tmp_path, monkeypatch):
    now_utc = datetime.now(UTC).replace(microsecond=0)
    target_day = now_utc.astimezone(_SHANGHAI).date() + timedelta(days=1)
    gateway_info, stop_gateway = _start_gateway_curve_server(
        tmp_path, monkeypatch, target_day=target_day
    )
    try:
        monitor_dir, doc, config = _monitor_config(
            tmp_path, threshold_rb=10 * _MINUTE, threshold_hc=15 * _MINUTE
        )
        members = _sorted_members((KEY_RB, "gen-rb"), (KEY_HC, "gen-hc"))
        membership = RecoveryMdMembershipBinding("source", _EPOCH, 1, 0, members)
        factory = _gateway_client_bundle(
            gateway_info,
            membership_by_consumer={
                "consumer-rb": (KEY_RB, membership),
                "consumer-hc": (KEY_HC, membership),
            },
            force_renew=True,
        )
        host = MonitorServiceHost(config, recovery_fact_coordinator_factory=factory)
        host.start()
        try:
            # Future BEGIN is in target_day (09:00..10:00 CST), strictly after binding establishment!
            session_start = datetime(
                target_day.year, target_day.month, target_day.day, 9, 0, tzinfo=_SHANGHAI
            ).astimezone(UTC)
            start_ns = int(session_start.timestamp()) * 1_000_000_000
            # Morning continuous: [0..6m], Break auction: [6..36m], Afternoon continuous: [36..60m].
            # Mark 1 covers 0..38m => 6m + 2m = 8m (< 10m rb threshold, < 15m hc threshold).
            mark1_end_ns = start_ns + 38 * _MINUTE
            # Mark 2 covers 0..40m => 6m + 4m = 10m (== rb threshold -> TRIGGERED; hc stays < 15m).
            mark2_end_ns = start_ns + 40 * _MINUTE
            # Terminal RECOVERED at 41m => rb emits RECOVERED.
            term_end_ns = start_ns + 41 * _MINUTE

            call_with_host(
                host,
                lambda: _send_ingress(
                    monitor_dir,
                    doc,
                    [
                        ("epoch", _epoch_marker()),
                        ("begin", _journal_obs(2, 1, members, reason="BEGIN")),
                        ("mark-8m", _journal_obs(3, 2, members, start_ns=start_ns, end_ns=mark1_end_ns)),
                    ],
                ),
            )
            wait_for(
                lambda: host.recovery_duration_snapshot("consumer-rb")["historical_snapshot"][
                    "checkpoint"
                ]
                == 3
            )
            wait_for(
                lambda: host.recovery_duration_snapshot("consumer-hc")["historical_snapshot"][
                    "checkpoint"
                ]
                == 3
            )
            rb_after_mark1 = host.recovery_duration_snapshot("consumer-rb")
            assert rb_after_mark1["historical_snapshot"]["episodes"][0]["members"][0]["lower_bound_ns"] == 8 * _MINUTE
            assert rb_after_mark1["fact_resolution"]["status"] == "RESOLVED"

            call_with_host(
                host,
                lambda: _send_ingress(
                    monitor_dir,
                    doc,
                    [
                        ("mark-10m", _journal_obs(4, 3, members, start_ns=start_ns, end_ns=mark2_end_ns)),
                    ],
                ),
            )
            wait_for(
                lambda: host.recovery_duration_snapshot("consumer-rb")["historical_snapshot"][
                    "checkpoint"
                ]
                == 4
            )
            wait_for(
                lambda: host.recovery_duration_snapshot("consumer-hc")["historical_snapshot"][
                    "checkpoint"
                ]
                == 4
            )
            with sqlite3.connect(monitor_dir / "data/events.sqlite") as db:
                wait_for(
                    lambda: db.execute("SELECT COUNT(*) FROM monitor_alert_events").fetchone()[0]
                    == 1
                )

            call_with_host(
                host,
                lambda: _send_ingress(
                    monitor_dir,
                    doc,
                    [
                        (
                            "recovered",
                            _journal_obs(
                                5,
                                4,
                                members,
                                state="RECOVERED",
                                reason="COMPLETED",
                                start_ns=start_ns,
                                end_ns=term_end_ns,
                            ),
                        ),
                    ],
                ),
            )
            wait_for(
                lambda: host.recovery_duration_snapshot("consumer-rb")["historical_snapshot"][
                    "checkpoint"
                ]
                == 5
            )
            wait_for(
                lambda: host.recovery_duration_snapshot("consumer-hc")["historical_snapshot"][
                    "checkpoint"
                ]
                == 5
            )

            def _both_delivered():
                with sqlite3.connect(monitor_dir / "data/events.sqlite") as db:
                    return db.execute("SELECT COUNT(*) FROM monitor_alert_events").fetchone()[0] == 2

            wait_for(_both_delivered)

            rb_final = host.recovery_duration_snapshot("consumer-rb")
            hc_final = host.recovery_duration_snapshot("consumer-hc")
            assert rb_final["historical_snapshot"]["episodes"][0]["historical_state"] == "RECOVERED_OBSERVED"
            assert rb_final["historical_snapshot"]["episodes"][0]["members"][0]["lower_bound_ns"] == 11 * _MINUTE
            assert rb_final["historical_snapshot"]["episodes"][0]["members"][0]["fact"]["product"] == "rb"
            assert hc_final["historical_snapshot"]["episodes"][0]["members"][0]["lower_bound_ns"] == 11 * _MINUTE
            assert hc_final["historical_snapshot"]["episodes"][0]["members"][0]["fact"]["product"] == "hc"

            with sqlite3.connect(monitor_dir / "data/events.sqlite") as db:
                events = db.execute(
                    "SELECT rule_id, state, state_version, dimensions_json FROM monitor_alert_events ORDER BY state_version"
                ).fetchall()
                lifecycles = db.execute(
                    "SELECT rule_id, last_state FROM monitor_alert_lifecycles"
                ).fetchall()
            assert [(e[0], e[1], e[2]) for e in events] == [
                ("rb-recovery-rule", "TRIGGERED", 1),
                ("rb-recovery-rule", "RECOVERED", 2),
            ]
            assert lifecycles == [("rb-recovery-rule", "RECOVERED")]
            dims = json.loads(events[0][3])
            assert dims["contract"] == "rb2701"
            evidence = json.loads(dims["evidence"])
            assert evidence["mode"] == "SIMULATION_ONLY"
            assert evidence["policy_declaration"] == "DEPLOYMENT_DECLARED_UNVERIFIED"
            assert evidence["source_authentication"] == "NOT_PROVEN"
            assert evidence["business_eligibility"] == "UNKNOWN"
            assert evidence["lower_bound_ns"] == 10 * _MINUTE
        finally:
            host.close()
        assert host.state == "closed"
    finally:
        stop_gateway()
