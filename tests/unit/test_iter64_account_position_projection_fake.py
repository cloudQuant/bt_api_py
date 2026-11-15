"""Bounded Risk account-position projection; no account or service I/O."""

from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_UP, Decimal, Inexact, localcontext
from types import SimpleNamespace

import pytest
from bt_api_base.account_protocol import (
    AccountKey,
    AccountObservation,
    AccountPosition,
    AccountResourceKey,
)
from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py.risk_service_config import RiskInstrumentBinding
from bt_api_py.risk_service_gateway import (
    RiskAccountSnapshotSource,
    RiskFactsCache,
    account_snapshot_from_observation,
)

KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
ACCOUNT = AccountKey("ctp", "simnow", "simnow", "synthetic")
R1 = AccountResourceKey("future", "pool-a")
R2 = AccountResourceKey("future", "pool-b")
TIME = "2030-01-01T01:00:00Z"


def position(resource=R1, *, instrument=KEY, side="long", quantity="1.23",
             today="1.23", yesterday="0", unit="contract"):
    return AccountPosition(resource, instrument, side, quantity, unit, None, None,
                           TIME, today, yesterday)


def observation(*rows):
    resources = tuple(dict.fromkeys(row.resource for row in rows)) or (R1,)
    return AccountObservation(ACCOUNT, "synthetic-observation", resources, (), tuple(rows),
                              None, TIME, TIME, "multi_query", False, False)


def project(value, keys=None):
    return account_snapshot_from_observation(
        value, instrument_ids={KEY: "rb"} if keys is None else keys,
        dated_venues=frozenset({"SHFE"}),
    )


def source(value):
    binding = RiskInstrumentBinding("rb", KEY, Decimal("10"), Decimal("1"))
    facts = RiskFactsCache({"rb": binding}, now_ns=lambda: 1)
    client = SimpleNamespace(refresh=lambda **_: SimpleNamespace(observation=value))
    result = RiskAccountSnapshotSource(client, facts, instrument_ids={KEY: "rb"},
                                       timeout_seconds=1)
    return result, facts, client


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP])
def test_same_complete_key_and_unit_sum_exactly_independent_of_context(rounding):
    value = observation(position(R1), position(R2, quantity="4.56", today="4.56"))
    with localcontext() as context:
        context.prec = 2
        context.rounding = rounding
        context.traps[Inexact] = True
        snapshot = project(value)
    assert snapshot.positions[("rb", "LONG")] == (Decimal("5.79"), Decimal(0))
    assert snapshot.complete is False and snapshot.valid_until_ns is None


def test_actual_source_uses_full_config_key_and_revokes_failed_refresh():
    good = observation(position())
    reader, facts, client = source(good)
    assert reader.refresh() is True
    assert facts.account_snapshot().positions[("rb", "LONG")] == (Decimal("1.23"), Decimal(0))
    other_resource = AccountResourceKey("option", "pool-c")
    other_key = replace(KEY, product_type="option")
    bad = observation(position(), position(other_resource, instrument=other_key))
    client.refresh = lambda **_: SimpleNamespace(observation=bad)
    assert reader.refresh() is False and reader.failures == 1
    assert facts.account_snapshot() is None


@pytest.mark.parametrize("change", [
    {"instrument_id": "another"},
    {"native_symbol": "another"},
    {"venue": "DCE"},
])
def test_unconfigured_complete_key_never_falls_back_to_native_alias(change):
    other = replace(KEY, **change)
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
        project(observation(position(instrument=other)))


@pytest.mark.parametrize("side", ["long", "short"])
def test_same_key_different_observed_units_are_not_merged_even_across_sides(side):
    value = observation(position(R1), position(R2, side=side, unit="lot"))
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
        project(value)


def test_duplicate_projection_id_or_legacy_partial_key_is_unavailable():
    other = replace(KEY, venue="DCE")
    for keys in ({KEY: "rb", other: "rb"}, {("SHFE", "rb"): "rb"}):
        with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
            project(observation(position()), keys)


def test_source_rejects_mismatched_host_full_key_map():
    binding = RiskInstrumentBinding("rb", KEY, None, None)
    facts = RiskFactsCache({"rb": binding}, now_ns=lambda: 1)
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_CONFIG_INVALID$"):
        RiskAccountSnapshotSource(object(), facts, instrument_ids={KEY: "other"},
                                  timeout_seconds=1)


@pytest.mark.parametrize("field,bad", [
    ("quantity", "1" * 129),
    ("quantity", "NaN"),
    ("quantity", "1e99999"),
    ("today_quantity", "2"),
    ("yesterday_quantity", None),
    ("qty_unit", ""),
])
def test_invalid_or_unbounded_mutated_rows_fail_whole_projection(field, bad):
    row = position()
    value = observation(row)
    object.__setattr__(row, field, bad)
    with pytest.raises(ValueError, match="^RISK_ACCOUNT_POSITION_UNAVAILABLE$"):
        project(value)


def test_source_failure_does_not_keep_earlier_cache_entry():
    reader, facts, client = source(observation(position()))
    assert reader.refresh()
    def fail(**_):
        raise RuntimeError("synthetic failure")
    client.refresh = fail
    assert reader.refresh() is False and facts.account_snapshot() is None


def test_source_clock_failure_during_install_clears_earlier_snapshot():
    reader, facts, client = source(observation(position()))
    assert reader.refresh()
    client.refresh = lambda **_: SimpleNamespace(
        observation=observation(position(quantity="2", today="2"))
    )

    def fail_clock():
        raise RuntimeError("synthetic clock failure")

    facts._now_ns = fail_clock
    assert reader.refresh() is False
    assert reader.failures == 1 and facts.account_snapshot() is None
    assert facts._account_received_ns is None


def test_direct_cache_install_clock_failure_preserves_previous_entry_atomically():
    reader, facts, _ = source(observation(position()))
    assert reader.refresh()
    previous = facts._account
    received = facts._account_received_ns

    def fail_clock():
        raise RuntimeError("synthetic clock failure")

    facts._now_ns = fail_clock
    with pytest.raises(RuntimeError, match="synthetic clock failure"):
        facts.set_account(object())
    assert facts._account is previous and facts._account_received_ns == received


def test_non_dated_venue_without_split_remains_incomplete():
    key = replace(KEY, venue="DCE")
    row = position(instrument=key, quantity="2", today=None, yesterday=None)
    result = account_snapshot_from_observation(observation(row), instrument_ids={key: "rb"},
                                               dated_venues=frozenset({"SHFE"}))
    assert result.positions[("rb", "LONG")] == (Decimal(2), Decimal(0))
    assert result.complete is False


def test_host_wires_complete_keys_and_refuses_ambiguous_control(monkeypatch):
    from pathlib import Path
    from tests.unit.test_iter64_risk_service_config import synthetic_refresh_document
    import bt_api_risk.pretrade as pretrade
    import bt_api_py.gateway_account_observer_client as observer_module
    import bt_api_py.gateway_strategy_account as account_module
    import bt_api_py.risk_service_config as config_module
    import bt_api_py.risk_service_gateway as gateway_module
    import bt_api_py.risk_service_host as host_module

    doc = synthetic_refresh_document(monkeypatch)
    doc["account"]["instruments"] = {
        "rb": {"venue": "SHFE", "native_symbol": "rb", "product_type": "future"},
        "rb-option": {"venue": "SHFE", "native_symbol": "rb", "product_type": "option"},
    }
    # Market and financial sources are unrelated to this account-only fake.
    del doc["gateway"]["market"]
    for name in ("market_poll_seconds", "strategy_financial_seconds", "strategy_financial_max_age_seconds"):
        del doc["refresh"][name]
    config = config_module.parse_risk_service_config(doc, path=Path("/synthetic/risk_service.yaml"))
    option = config.instruments["rb-option"].key
    value = observation(position(), position(AccountResourceKey("option", "pool-c"),
                                               instrument=option, quantity="2", today="2"))

    class Runtime:
        calls = []
        def start(self, **_):
            return object()
        def call(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            pytest.fail("ambiguous scope must never be sent")

    class AccountClient:
        def __init__(self, runtime):
            pass
        def refresh(self, **_):
            return SimpleNamespace(observation=value)

    runtime = Runtime()
    monkeypatch.setattr(host_module, "_require_supported_r04", lambda _: None)
    monkeypatch.setattr(host_module, "_granted", lambda *_: True)
    monkeypatch.setattr(host_module, "WireIdentity", lambda *a, **k: object())
    monkeypatch.setattr(host_module, "WireScope", lambda *a, **k: object())
    monkeypatch.setattr(pretrade, "PretradeEngine", lambda *a, **k: object())
    monkeypatch.setattr(gateway_module, "DelegateOrderPort", lambda **k: object())
    monkeypatch.setattr(gateway_module, "ObservationPump", lambda *a, **k: object())
    monkeypatch.setattr(gateway_module, "payload_reader", lambda *_: object())
    monkeypatch.setattr(observer_module, "GatewayAccountObserverRoute", lambda *a, **k: object())
    monkeypatch.setattr(observer_module, "GatewayAccountObserverClient", lambda *a, **k: object())
    monkeypatch.setattr(account_module, "GatewayStrategyAccountClient", AccountClient)
    host = host_module.RiskServiceHost.__new__(host_module.RiskServiceHost)
    host.config, host._now_ns = config, lambda: 1
    host._claim_resource_owner = lambda: None
    host._check_cleanup_ready = lambda: None
    host._call_lifecycle = lambda fn, *a, **k: fn(*a, **k)
    host._control_runtime = lambda: runtime
    host._service_runtime = lambda *a: runtime
    host._connect_delegate = host._record_cleanup_uncertain = host._port_cleanup_uncertain = lambda *a: None
    host._monotonic = lambda: 0
    host._fault_point = None
    host.ledger = host.policy_store = host.rules = host.delegate_storage = object()
    host._runtimes = []
    host.build_engine()
    host.start_gateway()
    assert host.instrument_for("SHFE", "rb") is None
    assert host.account.refresh() is True
    snap = host.facts.account_snapshot()
    assert snap.positions == {("rb", "LONG"): (Decimal("1.23"), Decimal(0)),
                              ("rb-option", "LONG"): (Decimal(2), Decimal(0))}
    assert snap.complete is False and snap.valid_until_ns is None
    for reply in (
        host.control.apply(cause_id="cause", mode="STOP_NEW", scope_kind="instrument", scope_value="SHFE:rb"),
        host.control.cancel_scope(operation_id="op", scope_kind="instrument", scope_value="SHFE:rb"),
    ):
        assert (reply.outcome, reply.code) == ("REQUEST_DENIED", "RISK_CONTROL_SCOPE_UNSUPPORTED")
    assert not runtime.calls

    value = observation(position(), position(R2, unit="lot"))
    assert host.account.refresh() is False
    assert host.facts.account_snapshot() is None


@pytest.mark.parametrize("configured_symbol", ["rb", "hc"])
def test_gateway_other_full_keys_block_instrument_control_without_rpc(configured_symbol):
    from bt_api_py.risk_service_config import _instruments
    from bt_api_py.risk_service_gateway import GatewayControlAdapter

    # The Risk configuration contains one alias match or none. Gateway has
    # two other fully distinct identities with that same native alias; there
    # is no trusted complete directory supplied to the adapter.
    instruments = _instruments({
        configured_symbol: {"venue": "SHFE", "native_symbol": configured_symbol,
                            "product_type": "future"},
    }, "ctp", "simnow")
    gateway_keys = (KEY, replace(KEY, instrument_id="rb-option", product_type="option"))
    assert len(set(gateway_keys)) == 2
    assert sum(b.key.venue == "SHFE" and b.key.native_symbol == "rb"
               for b in instruments.values()) == (configured_symbol == "rb")

    class FakeGateway:
        def __init__(self):
            self.active_keys = gateway_keys
            self.status_calls = self.rpc_calls = 0
        def status(self):
            self.status_calls += 1
            pytest.fail("instrument scope must be refused before runtime access")
        def call(self, *args, **kwargs):
            self.rpc_calls += 1
            pytest.fail("instrument scope must never reach Gateway")

    gateway = FakeGateway()
    control = GatewayControlAdapter(gateway, mount="direct", service_id="gateway",
                                    instance_id="instance", scope_ref="scope",
                                    account_ref="account", timeout_seconds=1)
    for reply in (
        control.apply(cause_id="cause", mode="STOP_NEW", scope_kind="instrument", scope_value="SHFE:rb"),
        control.cancel_scope(operation_id="op", scope_kind="instrument", scope_value="SHFE:rb"),
    ):
        assert (reply.outcome, reply.code) == ("REQUEST_DENIED", "RISK_CONTROL_SCOPE_UNSUPPORTED")
    assert gateway.status_calls == gateway.rpc_calls == 0
