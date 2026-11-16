"""Synthetic CTP owner and scoped direct status reads; no live accounts."""

from dataclasses import replace
from pathlib import Path

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope

from bt_api_py.gateway_instrument_status_service import GatewayInstrumentStatusReadSettings
from bt_api_py.service_registry import PrincipalGrant, ServiceCallContext
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, DOMAIN, _resources, make

KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")


def test_settings_are_frozen_bounded_and_exact():
    source = {"principal": (KEY,)}
    settings = GatewayInstrumentStatusReadSettings(source, 0.1)
    source.clear()
    assert settings.principal_instruments["principal"] == (KEY,)
    with pytest.raises(TypeError):
        settings.principal_instruments["other"] = (KEY,)
    for value in (True, 0, 6, float("nan")):
        with pytest.raises(ValueError, match="INVALID_INSTRUMENT_STATUS_SETTINGS"):
            GatewayInstrumentStatusReadSettings({"principal": (KEY,)}, value)
    for value in ({}, {"principal": [KEY]}, {"principal": ()}, {"principal": (KEY, KEY)}):
        with pytest.raises(ValueError, match="INVALID_INSTRUMENT_STATUS_SETTINGS"):
            GatewayInstrumentStatusReadSettings(value, 1)


@pytest.fixture
def actual_owner(tmp_path, monkeypatch):
    from bt_api_ctp.ctp.callback_ingress import CtpTraderCallbackSourceTagsV2
    from bt_api_ctp.ctp.client import TraderClient
    from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
    from bt_api_ctp.gateway_session import CtpGatewaySession

    import bt_api_py.ctp_gateway_trading as trading

    ctp_tests = Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
    monkeypatch.syspath_prepend(str(ctp_tests))
    from test_iter63_gateway_session import KEY as ACCOUNT
    from test_iter63_gateway_session import prepared

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch, account_key=ACCOUNT)
    subdir = tmp_path / "synthetic-native"
    subdir.mkdir()
    native = prepared.__wrapped__(subdir, monkeypatch)
    _, feed, stream, feeds, natives, _, _, _ = native
    start = TraderClient.start

    def start_with_tags(trader, **kwargs):
        start(trader, **kwargs)
        with trader._query_state_lock:
            trader._spi._callback_source_tags = CtpTraderCallbackSourceTagsV2(
                *trader._query_source_tags_locked()
            )

    monkeypatch.setattr(TraderClient, "start", start_with_tags)
    monkeypatch.setattr(
        trading, "_load_ctp_types", lambda: (CtpGatewaySession, CtpDirectWriteProvider)
    )
    settings = replace(
        settings,
        trusted_flow_root=subdir / "flow",
        close_timeout_seconds=3,
        session_stop_timeout_seconds=1,
        connect_timeout_seconds=2,
    )
    entry = authority.grants[CLIENT_A.principal_ref][0]
    direct = entry.resources[0]
    direct = replace(direct, operations=direct.operations | {"direct.instrument_status"})
    authority = replace(
        authority, grants={CLIENT_A.principal_ref: (PrincipalGrant(entry.identity, (direct,)),)}
    )
    options = GatewayInstrumentStatusReadSettings({CLIENT_A.principal_ref: (KEY,)}, 0.2)
    profile = trading.build_ctp_gateway_trading_profile(
        config,
        authority,
        tmp_path / "status-registry.sqlite",
        profile_ref="status-test",
        settings=settings,
        grants=grants,
        request_feed_factory=feed,
        market_stream_factory=stream,
        assert_admitted=lambda _: True,
        instrument_status_read=options,
    )
    resources = _resources(profile, authority)
    try:
        yield resources, feeds, natives, config, authority
    finally:
        resources.close()


def test_real_owner_status_snapshot_and_six_field_allowlist(actual_owner):
    from test_iter67_instrument_status_ingress import native

    resources, feeds, natives, _, _ = actual_owner
    binding = resources.values["binding"]
    scope = WireScope(
        DOMAIN, account_ref=binding.account_scope, strategy_ref=CLIENT_A.actor_ref, run_id="run"
    )
    context = ServiceCallContext(
        CLIENT_A, scope, "registration", CLIENT_A.principal_ref, service_epoch="epoch"
    )
    assert "direct.instrument_status" in binding.supported_operations

    def query(key):
        return binding.handle(
            "direct.instrument_status", context=context, arguments={"instrument": key.to_dict()}
        )

    # The first bridge acquisition intentionally asks for a subsequent explicit read.
    assert query(KEY)["code"] == "INSTRUMENT_STATUS_UNAVAILABLE"
    binding._owner.submit_service_call(
        lambda: feeds[0].trader_client._spi.OnRtnInstrumentStatus(
            native("2", ExchangeInstID="rb", InstrumentID="rb")
        )
    ).result(timeout_seconds=1)
    reply = query(KEY)
    assert reply["status"] == "OBSERVED"
    assert reply["registration_id"] == "registration" and reply["service_epoch"] == "epoch"
    assert reply["projection"]["status_category"] == "continuous"
    assert (
        reply["decision_eligible"]
        is reply["continuity_proven"]
        is reply["authorizes_trading"]
        is False
    )
    assert reply["product_mapping"] == "NOT_PROVEN"
    for other in (
        replace(KEY, venue="DCE"),
        replace(KEY, provider="other"),
        replace(KEY, environment="live"),
        replace(KEY, product_type="spot"),
        replace(KEY, instrument_id="other"),
    ):
        assert query(other)["code"] == "SCOPE_DENIED"
    assert set(natives[0].calls) <= {"query_settlement"}


def test_explicit_status_client_uses_existing_gateway_api_binding(monkeypatch):
    from bt_api_py.gateway_api import GatewayBtApi
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from tests.unit.test_iter63_gateway_strategy_client import parts

    options = parts()
    options["requested_operations"] = frozenset({"direct.instrument_status"})
    session = GatewayStrategyClientSession(**options)
    assert session.instrument_status is not None
    assert session.instrument_status._route is not session._route
    assert session.instrument_status._route == session._route
    api = GatewayBtApi(session, exchange_name="CTP___FUTURE")
    result = object()
    monkeypatch.setattr(session.instrument_status, "query", lambda instrument, **kwargs: result)
    assert api.query_instrument_status("CTP___FUTURE", KEY) is result
    original = session.instrument_status._route
    session.instrument_status._route = replace(original)
    with pytest.raises(RuntimeError, match="GATEWAY_BINDING_CHANGED"):
        api.query_instrument_status("CTP___FUTURE", KEY)
    session.instrument_status._route = original
    assert session.close().state == "closed"
    old = GatewayStrategyClientSession(**parts())
    assert old.instrument_status is None
    assert old.close().state == "closed"


def test_snapshot_change_during_owner_cut_is_unavailable(actual_owner, monkeypatch):
    resources, _, _, _, _ = actual_owner
    binding = resources.values["binding"]
    context = ServiceCallContext(
        CLIENT_A,
        WireScope(
            DOMAIN, account_ref=binding.account_scope, strategy_ref=CLIENT_A.actor_ref, run_id="run"
        ),
        "registration",
        CLIENT_A.principal_ref,
        service_epoch="epoch",
    )
    args = {"instrument": KEY.to_dict()}
    binding.handle("direct.instrument_status", context=context, arguments=args)  # initialize bridge
    session = binding._instrument_status_service._session
    monkeypatch.setattr(session, "snapshot_is_current", lambda _snapshot: False)
    result = binding.handle("direct.instrument_status", context=context, arguments=args)
    assert result == {"status": "REQUEST_REJECTED", "code": "INSTRUMENT_STATUS_UNAVAILABLE"}


def test_registry_reply_rechecks_ready_and_original_epoch(actual_owner, monkeypatch):
    from bt_api_py.service_registry import (
        PrincipalGrant,
        ResourceGrant,
        ServiceCapability,
        ServiceRegistry,
    )
    from tests.unit.test_service_registry import _next_request, _register

    resources, _, _, _, _ = actual_owner
    binding = resources.values["binding"]
    identity = CLIENT_A
    scope = WireScope(
        DOMAIN, account_ref=binding.account_scope, strategy_ref=identity.actor_ref, run_id="run"
    )
    template = replace(scope, run_id=None)
    operation = "direct.instrument_status"
    capability = ServiceCapability(
        "gateway-a", "service.direct_write", frozenset({operation}), frozenset()
    )
    grant = ResourceGrant(
        "gateway-a", "service.direct_write", "scope-a", template, frozenset({operation})
    )
    registry = ServiceRegistry(
        ":memory:",
        service_id="gateway-service",
        capabilities=(capability,),
        grants={identity.principal_ref: (PrincipalGrant(identity, (grant,)),)},
        policy_version="p",
        requirements_version="r",
    )
    seen = []

    def handler(context, arguments):
        seen.append(context.service_epoch)
        return binding.handle(operation, context=context, arguments=arguments)

    registry.register_handler("gateway-a", operation, handler)
    try:
        registered = _register(
            registry,
            principal=identity.principal_ref,
            identity=identity,
            scope=scope,
            instance_id="gateway-a",
            port="service.direct_write",
            scope_ref="scope-a",
            operations=(operation,),
        )
        assert registered["ok"]
        rec = registered["result"]

        def invoke():
            return registry.handle(
                identity.principal_ref,
                _next_request(
                    operation,
                    {
                        "registration_id": rec["registration_id"],
                        "service_epoch": rec["service_epoch"],
                        "instance_id": "gateway-a",
                        "arguments": {"instrument": KEY.to_dict()},
                    },
                    identity=identity,
                    scope=scope,
                ),
            )

        assert invoke()["ok"]  # first read initializes the exact provider bridge
        assert seen[-1] == rec["service_epoch"]
        session = binding._instrument_status_service._session
        original = session.snapshot_is_current

        def changed_epoch(snapshot):
            with registry._lock:
                registry._epoch = "replacement-epoch"
                registry._db.execute(
                    "UPDATE sessions SET service_epoch=? WHERE registration_id=?",
                    (registry._epoch, rec["registration_id"]),
                )
            return original(snapshot)

        monkeypatch.setattr(session, "snapshot_is_current", changed_epoch)
        result = invoke()
        assert result["ok"] is False
        assert seen[-1] == rec["service_epoch"]
    finally:
        registry.close()


def test_protected_synthetic_operator_bundle_explicit_status_section(tmp_path, monkeypatch):
    import json

    from bt_api_py.ctp_gateway_local_profile import (
        CtpLocalProfileError,
        load_simnow_trading_resources,
    )
    from tests.unit.test_iter63_ctp_gateway_local_profile import _fixture, _returns

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["returns"] = _returns()
    principal = doc["clients"][0]["identity"]["principal_ref"]
    doc["clients"][0]["resources"][0]["operations"].append("direct.instrument_status")
    doc["instrument_status_read"] = {
        "principal_instruments": {principal: [KEY.to_dict()]},
        "owner_wait_seconds": 0.2,
    }
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    assert resources.instrument_status_read.principal_instruments[principal] == (KEY,)
    for invalid in (
        None,
        {"principal_instruments": {principal: [KEY.to_dict()]}, "owner_wait_seconds": True},
        {
            "principal_instruments": {principal: [KEY.to_dict(), KEY.to_dict()]},
            "owner_wait_seconds": 0.2,
        },
    ):
        doc["instrument_status_read"] = invalid
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="CTP_LOCAL_RESOURCES_INVALID"):
            load_simnow_trading_resources(config)


@pytest.mark.parametrize("change", ["ready", "lease", "revoke"])
def test_registry_rechecks_scope_at_status_reply(actual_owner, monkeypatch, change):
    from bt_api_py.service_registry import (
        PrincipalGrant,
        ResourceGrant,
        ServiceCapability,
        ServiceRegistry,
    )
    from tests.unit.test_service_registry import _next_request, _register

    resources, _, _, _, _ = actual_owner
    binding = resources.values["binding"]
    identity = CLIENT_A
    scope = WireScope(
        DOMAIN, account_ref=binding.account_scope, strategy_ref=identity.actor_ref, run_id="run"
    )
    operation = "direct.instrument_status"
    grant = ResourceGrant(
        "gateway-a",
        "service.direct_write",
        "scope-a",
        replace(scope, run_id=None),
        frozenset({operation}),
    )
    now = [100.0]
    registry = ServiceRegistry(
        ":memory:",
        service_id="gateway-service",
        capabilities=(
            ServiceCapability(
                "gateway-a", "service.direct_write", frozenset({operation}), frozenset()
            ),
        ),
        grants={identity.principal_ref: (PrincipalGrant(identity, (grant,)),)},
        policy_version="p",
        requirements_version="r",
        lease_seconds=10,
        clock=lambda: now[0],
    )
    seen = []

    def handler(context, arguments):
        seen.append(context.service_epoch)
        return binding.handle(operation, context=context, arguments=arguments)

    registry.register_handler("gateway-a", operation, handler)
    try:
        registered = _register(
            registry,
            principal=identity.principal_ref,
            identity=identity,
            scope=scope,
            instance_id="gateway-a",
            port="service.direct_write",
            scope_ref="scope-a",
            operations=(operation,),
        )
        assert registered["ok"]
        rec = registered["result"]

        def invoke():
            return registry.handle(
                identity.principal_ref,
                _next_request(
                    operation,
                    {
                        "registration_id": rec["registration_id"],
                        "service_epoch": rec["service_epoch"],
                        "instance_id": "gateway-a",
                        "arguments": {"instrument": KEY.to_dict()},
                    },
                    identity=identity,
                    scope=scope,
                ),
            )

        assert invoke()["ok"]
        registry.set_ready(False)
        assert not invoke()["ok"] and len(seen) == 1
        registry.set_ready(True)
        session = binding._instrument_status_service._session
        original = session.snapshot_is_current

        def change_before_reply(snapshot):
            if change == "ready":
                registry.set_ready(False)
            elif change == "lease":
                now[0] += 11
            else:
                assert registry.revoke_principal(identity.principal_ref)
            return original(snapshot)

        monkeypatch.setattr(session, "snapshot_is_current", change_before_reply)
        assert not invoke()["ok"]
        assert seen[-1] == rec["service_epoch"]
    finally:
        registry.close()


def test_default_runtime_registration_excludes_status_and_old_host_refuses_explicit_request():
    from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError

    runtime = object.__new__(ServiceClientRuntime)
    runtime._requested_operations = {}
    runtime.client_session_id = "synthetic-session"
    binding = {
        "mount": "broker.direct_write",
        "service_id": "gateway",
        "instance_id": "gateway-a",
        "scope_ref": "scope-a",
        "transport_ref": "transport",
    }
    capability = {
        "instance_id": "gateway-a",
        "port": "service.direct_write",
        "operations": ["direct.command_status", "direct.instrument_status"],
    }
    sent = []

    class RecordedError(Exception):
        pass

    def rpc(*args, **kwargs):
        sent.append(args[4])
        raise RecordedError

    runtime._rpc = rpc
    with pytest.raises(RecordedError):
        runtime._register_port_impl(
            binding,
            None,
            "service.direct_write",
            [capability],
            timeout=1,
            required=True,
            heartbeat_interval_seconds=1,
        )
    assert sent[0]["operations"] == ["direct.command_status"]
    runtime._requested_operations = {"broker.direct_write": frozenset({"direct.instrument_status"})}
    capability["operations"] = ["direct.command_status"]
    with pytest.raises(ServiceRuntimeError, match="CAPABILITY_UNAVAILABLE"):
        runtime._register_port_impl(
            binding,
            None,
            "service.direct_write",
            [capability],
            timeout=1,
            required=True,
            heartbeat_interval_seconds=1,
        )


def test_selected_status_requires_exact_grant_and_original_ctp_owner(tmp_path, monkeypatch):
    import bt_api_py.ctp_gateway_trading as trading
    from bt_api_py.service_components import ServiceResourceConstructionError

    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    options = GatewayInstrumentStatusReadSettings({CLIENT_A.principal_ref: (KEY,)}, 0.1)

    def build(selected, auth=authority):
        return trading.build_ctp_gateway_trading_profile(
            config,
            auth,
            tmp_path / "profile-registry.sqlite",
            profile_ref="status-missing",
            settings=settings,
            grants=grants,
            request_feed_factory=lambda: None,
            market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True,
            instrument_status_read=selected,
        )

    # An allowlist does not silently grant the Registry operation.
    with pytest.raises(trading.CtpGatewayTradingError, match="INVALID_INSTRUMENT_STATUS_SETTINGS"):
        build(options)
    entry = authority.grants[CLIENT_A.principal_ref][0]
    direct = replace(
        entry.resources[0], operations=entry.resources[0].operations | {"direct.instrument_status"}
    )
    granted = replace(
        authority, grants={CLIENT_A.principal_ref: (PrincipalGrant(entry.identity, (direct,)),)}
    )
    for invalid in (
        replace(KEY, provider="other"),
        replace(KEY, environment="live"),
        replace(KEY, native_symbol="not-in-grant"),
    ):
        with pytest.raises(
            trading.CtpGatewayTradingError, match="INVALID_INSTRUMENT_STATUS_SETTINGS"
        ):
            build(
                GatewayInstrumentStatusReadSettings({CLIENT_A.principal_ref: (invalid,)}, 0.1),
                granted,
            )
    # The legacy fake session cannot become a selected status source merely by
    # accepting a setting; partial owner resources are cleaned up on failure.
    with pytest.raises(ServiceResourceConstructionError):
        _resources(build(options, granted), granted)


def test_unrelated_extra_direct_operation_does_not_pass_host_capability_check(
    actual_owner, tmp_path
):
    from bt_api_py.service_host import ServiceHost, ServiceHostError

    resources, _, _, config, authority = actual_owner
    binding = resources.values["binding"]
    binding.supported_operations = binding.supported_operations | {"direct.unrelated_extra"}
    host = ServiceHost(
        config, authority, tmp_path / "extra-op-registry.sqlite", lambda *_: resources
    )
    with pytest.raises(ServiceHostError):
        host.start()


def test_status_respects_existing_scope_and_reply_budget(actual_owner):
    from bt_api_py.gateway_direct_service import WRITE_OPERATIONS
    from bt_api_py.service_registry import ServiceResultTooLargeError

    resources, _, _, _, _ = actual_owner
    binding = resources.values["binding"]
    op = "direct.instrument_status"
    assert op not in WRITE_OPERATIONS
    scope = WireScope(
        DOMAIN, account_ref=binding.account_scope, strategy_ref=CLIENT_A.actor_ref, run_id="run"
    )
    context = ServiceCallContext(
        CLIENT_A, scope, "registration", CLIENT_A.principal_ref, service_epoch="epoch"
    )
    args = {"instrument": KEY.to_dict()}
    bad = replace(context, scope=replace(scope, account_ref="another-account"))
    assert binding.handle(op, context=bad, arguments=args)["code"] == "SCOPE_DENIED"
    bad = replace(context, identity=replace(CLIENT_A, delegate_ref="delegate"))
    assert binding.handle(op, context=bad, arguments=args)["code"] == "SCOPE_DENIED"
    assert (
        binding.handle(op, context=replace(context, service_epoch=None), arguments=args)["code"]
        == "INSTRUMENT_STATUS_UNAVAILABLE"
    )
    assert (
        binding.handle(
            op, context=context, arguments={"instrument": KEY.to_dict(), "epoch": "client"}
        )["code"]
        == "INVALID_REQUEST"
    )
    with pytest.raises(ServiceResultTooLargeError):
        binding.handle(op, context=replace(context, result_fits=lambda _: False), arguments=args)


def test_disabled_status_imports_are_lazy_without_gateway_package():
    import subprocess
    import sys

    code = """
import importlib.abc, sys
class BlockGateway(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "bt_api_gateway" or fullname.startswith("bt_api_gateway."):
            raise ModuleNotFoundError("synthetic optional gateway missing")
sys.meta_path.insert(0, BlockGateway())
import bt_api_py
import bt_api_py.market_stream_contracts
assert "bt_api_py.gateway_instrument_status_query" not in sys.modules
assert "bt_api_py.gateway_instrument_status_service" not in sys.modules
assert "bt_api_py.gateway_strategy_instrument_status" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
