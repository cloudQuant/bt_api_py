"""Synthetic-backed CTP Gateway process for the 007 acceptance chain.

Standalone harness (never product code).  It runs the *real* Gateway profile
(``bt_api_py.ctp_gateway_trading.build_ctp_gateway_trading_profile``) with the
*synthetic* CTP TD/MD native clients proven in
``test_iter63_ctp_gateway_joint_service.py``, on fixed loopback endpoints.
Only native CTP is synthetic; no venue, account or network is contacted.

Usage::

    python -m tests.integration._gateway_007_live_gateway --config <json>

The JSON contains only synthetic key file paths, identities and instrument
descriptors.  Nothing is printed that contains key bytes.

This module is a harness file (``_gateway_007_live_`` prefix), not an edited
test.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ENDPOINT_RPC = "tcp://127.0.0.1:12345"
ENDPOINT_MARKET = "tcp://127.0.0.1:12346"

_CTP_TZ = ZoneInfo("Asia/Shanghai")
SYNTHETIC_TRADING_DAY = "20261119"


def _now_fields() -> dict:
    now = datetime.now(_CTP_TZ)
    return {
        "ActionDay": now.strftime("%Y%m%d"),
        "UpdateTime": now.strftime("%H:%M:%S"),
        "UpdateMillisec": now.microsecond // 1000,
    }


def _read_key(path: str) -> bytes:
    value = Path(path).read_bytes().strip()
    if len(value) != 40:
        raise RuntimeError("INVALID_SYNTHETIC_KEY_LENGTH")
    return value


def _tick_price(counter: int) -> float:
    """Synthetic price path: a clean sine so an MA crossover forms quickly.

    Tick timeframe means each tick is one bar; a 40-tick period gives several
    5/20 SMA crossings inside the bounded run.
    """
    return float(round(1000.0 + 12.0 * math.sin(2.0 * math.pi * counter / 40.0)))


def run_gateway(config_path: str) -> int:
    root = Path(config_path).resolve().parent
    with open(config_path, "r", encoding="utf-8") as stream:
        cfg = json.load(stream)

    state: dict = {"ready": False}
    fake_md_holder: list = []
    native_holder: list = []
    ticks = {"emitted": 0}
    stop_requested = threading_event_factory()
    try:
        # Make the CTP test fixtures importable exactly like the proven test.
        ctp_tests = Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
        sys.path.insert(0, str(ctp_tests))
        from test_iter62_settlement_readback_on_write import _NativeApi, _login
        from test_market_provider import _SyntheticMdClient
        from test_iter63_gateway_qualified_order_process import _TraderSpi

        from bt_api_ctp.ctp.client import TraderClient, CtpNativeStopReceipt
        from bt_api_ctp.feeds import live_ctp_feed
        from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream, CtpRequestDataFuture
        from bt_api_ctp.trusted_flow import flow_binding, trusted_flow

        from bt_api_base.account_protocol import AccountKey, AccountResourceKey
        from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
        from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

        from bt_api_py.ctp_gateway_trading import (
            CtpGatewayAccountReadSettings,
            CtpGatewayMarketSettings,
            CtpGatewayReturnSettings,
            CtpGatewayTradingSettings,
            build_ctp_gateway_trading_profile,
        )
        from bt_api_py.gateway_delegation_service import (
            GatewayDelegateGrant,
            GatewayDelegationService,
            GatewayDelegationSettings,
        )
        from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant
        from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
        from bt_api_py.service_config import parse_service_config
        from bt_api_py.service_host import TrustedServiceAuthority
        from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

        from tests.integration.test_iter63_ctp_gateway_joint_service import MARKET_OPS as _BASE_MARKET_OPS
        from tests.integration.test_iter63_gateway_direct_service import _service_config

        # ---------------------------------------------------------- identities
        actor = cfg["strategy_actor"]
        principal = cfg["strategy_principal"]
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal, actor)
        domain = cfg["execution_domain"]
        account_key = AccountKey(**cfg["account_key"])
        scope = account_key.account_key_id
        service_principal, service_actor = cfg["service_principal"], cfg["service_actor"]
        service = WireIdentity(WireRole.SERVICE, service_principal, service_actor)
        instance_id, scope_ref = cfg["instance_id"], cfg["scope_ref"]
        exchange = cfg["exchange_name"]
        source_ref, entitlement_ref = cfg["source_ref"], cfg["entitlement_ref"]

        instrument_dicts = cfg["instruments"]
        instruments = tuple(MarketInstrumentKey.from_dict(item) for item in instrument_dicts)
        keys = tuple(
            MarketSubscriptionKey(
                source_ref,
                entitlement_ref,
                item,
                "tick",
            )
            for item in instruments
        )

        server_public = _read_key(cfg["server_public_key_file"])
        server_secret = _read_key(cfg["server_secret_key_file"])
        client_public = _read_key(cfg["client_public_key_file"])

        # ---------------------------------------------------------- market ops
        market_ops = frozenset(_BASE_MARKET_OPS) | {"market.clock_observation"}
        direct_ops = frozenset(cfg["direct_operations"])
        missing = direct_ops - frozenset(
            {
                "direct.reserve", "direct.release", "direct.dispatch", "direct.command_status",
                "direct.cancel", "direct.cancel_status", "direct.strategy_position",
                "direct.strategy_close_capacity", "direct.delegated_command_status",
                "direct.delegated_cancel", "direct.delegated_cancel_status",
                "direct.delegated_reserve", "direct.delegated_dispatch",
                "direct.events.read_after", "direct.events.progress", "direct.events.ack",
                "direct.delegate_actor_status", "direct.delegate_actor_cancel",
                "direct.delegate_actor_cancel_status", "direct.delegate_actor_release",
                "direct.delegate_actor_release_status", "direct.delegate_events.read_after",
                "direct.delegate_events.progress", "direct.delegate_events.ack",
                "direct.parent.register", "direct.parent.query", "direct.parent.cancel",
                "direct.parent.operation", "direct.parent.source", "direct.parent.register_v2",
                "direct.parent.operation_v2", "direct.parent.cancel_v2",
                "direct.parent.fault", "direct.parent.recover",
            }
        )
        if missing:
            raise RuntimeError("UNSUPPORTED_REQUESTED_OPERATIONS " + ",".join(sorted(missing)))

        # ---------------------------------------------------------- synthetic native
        natives, feeds, streams, stops = [], [], [], []

        class Native(_NativeApi):
            def ReqOrderInsert(self, field, request_id):
                from types import SimpleNamespace

                result = super().ReqOrderInsert(field, request_id)
                suffix = str(field.OrderRef)
                common = dict(
                    BrokerID="9999", InvestorID="account", TradingDay=SYNTHETIC_TRADING_DAY,
                    ExchangeID=instruments[0].venue, InstrumentID=field.InstrumentID,
                    OrderRef=field.OrderRef, OrderSysID="sys-" + suffix, Direction="0",
                )
                order = SimpleNamespace(
                    **common, FrontID=7, SessionID=19, CombOffsetFlag="0", OrderStatus="1",
                    OrderSubmitStatus="3", OrderPriceType="2", LimitPrice=10.0,
                    VolumeTotalOriginal=2, VolumeTraded=1, VolumeTotal=1,
                )
                trade = SimpleNamespace(
                    **common, TradeID="trade-" + suffix, OffsetFlag="0", Volume=1,
                    Price=10.0, TradeTime=datetime.now(_CTP_TZ).strftime("%H:%M:%S"),
                )
                self.client._spi.OnRtnOrder(order)
                self.client._spi.OnRtnTrade(trade)
                return result

            def _reply(self, kind, rows, request_id):
                for row in rows:
                    self.client._handle_query_callback(kind, row, None, request_id, False)
                self.client._handle_query_callback(kind, None, None, request_id, True)
                return 0

            def ReqQryTradingAccount(self, field, request_id):
                return self._reply(
                    "account",
                    [dict(AccountID="account", BrokerID="9999", CurrencyID="CNY",
                          Balance=1000, Available=800)],
                    request_id,
                )

            def ReqQryInvestorPosition(self, field, request_id):
                return self._reply("positions", [], request_id)

            def ReqQryOrder(self, field, request_id):
                return self._reply("orders", [], request_id)

            def ReqQryTrade(self, field, request_id):
                self.calls.append("return_query")
                return self._reply("trades", [], request_id)

        def start(trader, **_kwargs):
            native = Native()
            native.client = trader
            trader._api = native
            _login(trader, native, session_id=19, trading_day=SYNTHETIC_TRADING_DAY)
            natives.append(native)

        def stop(trader, *, timeout):
            stops.append(1)
            return CtpNativeStopReceipt(
                trader.connection_generation, False, True, True, False, False
            )

        class FakeMd(_SyntheticMdClient):
            instances = []
            _gateway_md_dispatch_coordinator = None

            def _bind_gateway_md_dispatch_coordinator(
                self, coordinator, *, account_scope, service_generation
            ):
                if (
                    not coordinator.matches_owner(account_scope, service_generation)
                    or self._gateway_md_dispatch_coordinator is not None
                ):
                    raise ValueError("ctp_gateway_dispatch_owner_mismatch")
                self._gateway_md_dispatch_coordinator = coordinator
                parent = getattr(super(), "_bind_gateway_md_dispatch_coordinator", None)
                if callable(parent):
                    parent(
                        coordinator,
                        account_scope=account_scope,
                        service_generation=service_generation,
                    )

            def __init__(self, *args, **kwargs):
                self.trusted_flow_binding = flow_binding(
                    trusted_flow(kwargs["trusted_flow_root"], kwargs["flow_account_scope"])
                )
                self.last_stop_receipt = None
                self.trading_day = SYNTHETIC_TRADING_DAY
                super().__init__(*args)

            def stop_and_wait(self, *, timeout):
                self.last_stop_receipt = super().stop_and_wait(timeout=timeout)
                return self.last_stop_receipt

            def emit_tick(self, **fields: object) -> None:
                # Force one coherent synthetic venue/clock domain: the base
                # fixture's readiness tick hardcodes CFFEX and a stale day.
                now = datetime.now(_CTP_TZ)
                fields["ExchangeID"] = instruments[0].venue
                fields["ActionDay"] = now.strftime("%Y%m%d")
                fields["UpdateTime"] = now.strftime("%H:%M:%S")
                fields.setdefault("UpdateMillisec", now.microsecond // 1000)
                fields["TradingDay"] = SYNTHETIC_TRADING_DAY
                if not self.subscribed:
                    return
                fields.setdefault("InstrumentID", self.subscribed[0])
                super().emit_tick(**fields)

        def _emit(md, counter: int) -> None:
            if not callable(getattr(md, "on_tick", None)):
                return
            if not getattr(md, "subscribed", None):
                return
            symbol = md.subscribed[0]
            price = _tick_price(counter)
            md.emit_tick(
                InstrumentID=symbol,
                LastPrice=price,
                Volume=2,
                BidPrice1=price - 1.0,
                AskPrice1=price + 1.0,
                BidVolume1=3,
                AskVolume1=4,
                LowerLimitPrice=price - 100.0,
                UpperLimitPrice=price + 100.0,
                OpenInterest=1000.0,
                Turnover=10000.0,
            )
            ticks["emitted"] += 1

        TraderClient.start, TraderClient.stop_and_wait = start, stop
        live_ctp_feed.ctp_client.MdClient = FakeMd
        fake_md_holder.append(FakeMd)

        flow_root = root / "flow"
        flow_root.mkdir(parents=True, exist_ok=True)
        guard_root = root / "guard"
        guard_root.mkdir(parents=True, exist_ok=True)
        params = dict(
            broker_id="9999",
            user_id="account",
            password="synthetic",
            td_front="tcp://offline-neutral-td.invalid:30001",
            md_front="tcp://offline-neutral-md.invalid:30011",
            ctp_env_profile="config_front_pair",
            ctp_environment="simnow",
            trusted_flow_root=str(flow_root),
            flow_account_scope=scope,
        )

        def feed():
            result = CtpRequestDataFuture(None, auto_settlement_confirm=False, **params)
            result._connect_timeout = 0.05
            feeds.append(result)
            return result

        def stream(key, q):
            del key
            result = CtpMarketStream(q, **params)
            streams.append(result)
            return result

        # ---------------------------------------------------------- service config
        raw = _service_config(ENDPOINT_RPC).to_public_dict()
        gateway = raw["components"]["gateway"]["settings"]
        gateway["instance_id"] = instance_id
        gateway["bind_ports"] = [
            "service.direct_write", "service.market_source", "service.account_read",
        ]
        # Delegation requires account extensions, which require the strategy
        # ledger, which requires the control plane.  This is the full consistent
        # feature set; the profile builder refuses any partial combination.
        gateway["options"].update(
            dispatch_enabled=True, cancel_enabled=True, control_enabled=True,
            strategy_ledger_enabled=True, account_extensions_enabled=True,
            delegation_enabled=True,
        )
        raw["service_id"] = service_actor
        raw["scope_ref"] = scope_ref
        raw["communication"]["profiles"]["server-transport"]["endpoints"]["market_bind"] = ENDPOINT_MARKET
        raw["communication"]["profiles"]["server-transport"].setdefault("options", {})[
            "market_wire_version"
        ] = 2

        wire = WireScope(domain, account_ref=scope, strategy_ref=actor)
        account_ops = frozenset({"account.snapshot.typed"})
        resources = [
            ResourceGrant(instance_id, "service.direct_write", scope_ref, wire, direct_ops),
            ResourceGrant(instance_id, "service.market_source", scope_ref, wire, market_ops),
            ResourceGrant(instance_id, "service.account_read", scope_ref, wire, account_ops),
        ]
        principals = {client_public: principal}
        grants = {principal: (PrincipalGrant(identity, tuple(resources)),)}

        control_cfg = cfg["control"]
        control = WireIdentity(
            WireRole.APPLICATION_CLIENT, control_cfg["principal_ref"], control_cfg["actor_ref"]
        )
        principals[_read_key(control_cfg["public_key_file"])] = control.principal_ref
        control_ops = frozenset(
            {
                "direct.control.apply", "direct.control.status", "direct.control.list",
                "direct.control.cancel_scope", "direct.control.cancel_scope_status",
            }
        )
        control_grants = {
            control.principal_ref: GatewayControlGrant(
                control.actor_ref, True, frozenset(), control_ops, kind="operator",
                modes=frozenset({"STOP_NEW", "FREEZE", "DRAIN"}), instrument_scope=True,
            )
        }
        grants[control.principal_ref] = (
            PrincipalGrant(
                control,
                (
                    ResourceGrant(
                        instance_id, "service.direct_write", scope_ref,
                        WireScope(domain, account_ref=scope), control_ops,
                    ),
                ),
            ),
        )

        delegation = None
        delegate_cfg = cfg.get("delegation")
        if delegate_cfg:
            from bt_api_py.gateway_delegate_actor_service import OPERATIONS as ACTOR_OPERATIONS

            risk = WireIdentity(
                WireRole.SERVICE,
                delegate_cfg["principal_ref"],
                delegate_cfg["actor_ref"],
            )
            delegate_public = _read_key(delegate_cfg["public_key_file"])
            principals[delegate_public] = risk.principal_ref
            delegate_ops = frozenset(
                {"direct.delegated_reserve", "direct.delegated_dispatch"}
            ) | frozenset(ACTOR_OPERATIONS)
            grants[risk.principal_ref] = (
                PrincipalGrant(
                    risk,
                    (
                        ResourceGrant(
                            instance_id, "service.direct_write", scope_ref, wire, delegate_ops
                        ),
                    ),
                ),
            )
            delegation = GatewayDelegationSettings(
                (
                    GatewayDelegateGrant(
                        risk,
                        scope,
                        actor,
                        domain,
                        "risk",
                        delegate_cfg["grant_revision"],
                        frozenset(instruments),
                        frozenset(delegate_cfg["logical_operations"]),
                    ),
                ),
                lambda value: value in instruments,
            )

        authority = TrustedServiceAuthority(
            identity=service,
            scope=WireScope(domain),
            scope_ref=scope_ref,
            scope_version="scope-v1",
            security_ref="security-a",
            security_version="security-v1",
            registration_policy_ref="direct-policy-v1",
            registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1",
            service_requirements_version="direct-requirements-v1",
            curve_public_key=server_public,
            curve_secret_key=server_secret,
            principals=principals,
            grants=grants,
        )

        journal_path = root / "orders.sqlite"
        issuer = DurableOrderCorrelationIssuer(root / "identity.sqlite")
        issuer_id = issuer.issuer_id
        issuer.close()

        settings = CtpGatewayTradingSettings(
            account_key,
            scope,
            guard_root,
            "acceptance-owner",
            journal_path,
            cfg["journal_id"],
            0,
            flow_root,
            source_ref,
            entitlement_ref,
            write_enabled=True,
            maintenance_interval_seconds=0.1,
            connect_timeout_seconds=5,
            close_timeout_seconds=5,
        )
        return_instruments = instruments
        read = CtpGatewayAccountReadSettings(
            root / "account.sqlite",
            "account-state",
            (AccountResourceKey("future", "futures"),),
            root / "identity.sqlite",
            issuer_id,
            return_instruments,
            refresh_interval_seconds=0.2,
            max_staleness_seconds=3,
            request_timeout_seconds=2,
        )
        market_settings = CtpGatewayMarketSettings(
            frozenset(keys), {principal: frozenset(keys)}
        )
        profile = build_ctp_gateway_trading_profile(
            parse_service_config(raw),
            authority,
            root / "registry.sqlite",
            profile_ref="offline-007-acceptance",
            settings=settings,
            grants={principal: GatewayDirectGrant(actor, frozenset(i.native_symbol for i in instruments))},
            request_feed_factory=feed,
            market_stream_factory=stream,
            assert_admitted=lambda _record: True,
            return_settings=CtpGatewayReturnSettings(
                return_instruments, query_interval_seconds=1
            ),
            account_read_settings=read,
            account_read_principals=frozenset({principal}),
            market_settings=market_settings,
            control_grants=control_grants,
            delegation_settings=delegation,
            upgrade_controls_from_v9=True,
            upgrade_strategy_ledger_from_v10=True,
            upgrade_account_extensions_from_v11=True,
        )

        deployment = profile.deployment_factory()
        deployment.start()
        state["ready"] = True
        print(json.dumps({"event": "ready", "pid": os.getpid()}), flush=True)

        tick_interval = float(cfg.get("tick_interval_seconds", 0.25))
        next_tick = time.monotonic()
        counter = 0
        stop_file = cfg.get("stop_file")
        while not stop_requested():
            if stop_file and Path(stop_file).exists():
                break
            now = time.monotonic()
            md = FakeMd.instances[0] if FakeMd.instances else None
            if md is not None and now >= next_tick:
                _emit(md, counter)
                counter += 1
                next_tick = max(next_tick + tick_interval, now)
            deployment.serve_once(5)
            # report one bounded stats line per second
            if now - state.setdefault("last_stats", 0.0) >= 1.0:
                state["last_stats"] = now
                md = FakeMd.instances[0] if FakeMd.instances else None
                print(
                    json.dumps(
                        {
                            "event": "stats",
                            "ticks": ticks["emitted"],
                            "natives": len(natives),
                            "feeds": len(feeds),
                            "streams": len(streams),
                            "md": len(FakeMd.instances),
                            "subscribed": list(getattr(md, "subscribed", ()) or ()),
                            "inserts": 0 if not natives else natives[0].calls.count("insert"),
                            "settled": False,
                        }
                    ),
                    flush=True,
                )
        return 0
    except BaseException:
        print(json.dumps({"event": "error", "traceback": traceback.format_exc()}), flush=True)
        return 1
    finally:
        try:
            if state.get("ready") and "deployment" in locals():
                deployment.close()
        except BaseException:
            print(json.dumps({"event": "cleanup_error", "traceback": traceback.format_exc()}), flush=True)
        print(json.dumps({"event": "closed", "pid": os.getpid()}), flush=True)


def threading_event_factory():
    """A stop probe that never fires by itself (bounded by the parent's kill)."""
    return lambda: False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    return run_gateway(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
