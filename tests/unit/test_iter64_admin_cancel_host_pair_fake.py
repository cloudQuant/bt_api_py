"""Strict Risk admin composition checks, with synthetic in-process resources only."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bt_api_py.risk_service_config as config_module
import bt_api_py.risk_service_host as risk_host_module
import bt_api_py.service_host as host_module
from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import RiskServiceConfigError, parse_risk_service_config
from bt_api_py.service_config import parse_service_config
from tests.unit.test_iter64_risk_service_config import document
from tests.unit.test_iter64_admin_cancel_admission_local_fake import (
    ARGS, OP, composition, row,
)
from tests.unit.test_service_registry import _next_request, _register


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setattr(config_module, "read_key_file", lambda path, *, secret: path.name.encode().ljust(40, b"0"))
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs: self)
    return parse_risk_service_config(document(), path=Path("/synthetic/risk.yaml"))


def test_config_default_and_explicit_strict(monkeypatch, config):
    assert config.strict_admin_cancel is False
    doc = document()
    doc["service"]["strict_admin_cancel"] = True
    assert parse_risk_service_config(doc, path=Path("/synthetic/risk.yaml")).strict_admin_cancel is True


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_invalid_strict_config_rejected_before_key_read(monkeypatch, value):
    doc = document()
    doc["service"]["strict_admin_cancel"] = value
    monkeypatch.setattr(config_module, "read_key_file", lambda *_args, **_kw: pytest.fail("key read"))
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs: self)
    with pytest.raises(RiskServiceConfigError, match="RISK_SERVICE_CONFIG_STRICT_ADMIN_CANCEL_INVALID"):
        parse_risk_service_config(doc, path=Path("/synthetic/risk.yaml"))


@pytest.mark.parametrize("enabled", [False, True])
def test_risk_host_passes_same_instance_to_both_sides(monkeypatch, config, enabled):
    config = replace(config, strict_admin_cancel=enabled)
    owner = object.__new__(risk_host_module.RiskServiceHost)
    owner.config = config
    owner.engine = object()
    owner.ledger = object()
    owner.order_port = SimpleNamespace(note_run=lambda *_: None)
    owner.control = SimpleNamespace(cancel_scope_status=lambda *_: None)
    owner.instrument_for = lambda *_: None
    owner.ready = lambda: True
    owner._claim_resource_owner = lambda: None
    owner._check_cleanup_ready = lambda: None
    owner._call_lifecycle = lambda function, *args, **kwargs: function(*args, **kwargs)
    monkeypatch.setattr(risk_host_module, "restrict_file", lambda *_: None)
    monkeypatch.setattr(risk_host_module, "service_config_document", lambda *_: {})
    monkeypatch.setattr(risk_host_module, "build_authority", lambda *_: object())
    monkeypatch.setattr("bt_api_py.service_config.parse_service_config", lambda *_: object())
    seen = {}

    class FakeHost:
        def __init__(self, _config, _authority, _path, provider, *, risk_admin_cancel_service):
            actual = provider(None, None, None, None).values["service"]
            seen.update(actual=actual, opted=risk_admin_cancel_service)

        def start(self):
            pass

    monkeypatch.setattr(host_module, "ServiceHost", FakeHost)
    owner.start_service_host()
    assert type(seen["actual"]) is RiskOrderService
    assert seen["actual"]._strict_admin_cancel is enabled
    assert seen["opted"] is (seen["actual"] if enabled else None)


def test_invalid_injected_config_fails_before_resource_claim(config):
    owner = object.__new__(risk_host_module.RiskServiceHost)
    owner.config = replace(config, strict_admin_cancel=1)
    owner._claim_resource_owner = lambda: pytest.fail("resource claimed")
    with pytest.raises(risk_host_module.RiskServiceStartError, match="STRICT_ADMIN_CANCEL_INVALID"):
        owner.start_service_host()


def test_service_host_constructor_rejects_unpaired_or_forged_service(config):
    parsed = parse_service_config(risk_host_module.service_config_document(config))
    authority = risk_host_module.build_authority(config)
    provider = lambda *_: pytest.fail("resources acquired")
    for fake in (object(), SimpleNamespace(_strict_admin_cancel=True),
                 RiskOrderService(object(), object(), account_scope=config.account_scope,
                                  route=config.route, execution_domain_ref=config.execution_domain_ref,
                                  principals=config.principals, instrument_for=lambda *_: None,
                                  ready=lambda: True)):
        with pytest.raises(host_module.ServiceHostError, match="RISK_ADMIN_CANCEL_COMPOSITION_INVALID"):
            host_module.ServiceHost(parsed, authority, ":memory:", provider,
                                    risk_admin_cancel_service=fake)


def test_strict_host_rejects_upstream_before_resource_acquisition(config):
    parsed = parse_service_config(risk_host_module.service_config_document(config))
    object.__setattr__(parsed, "clients", {"synthetic": object()})
    authority = risk_host_module.build_authority(config)
    adapter = RiskOrderService(object(), object(), account_scope=config.account_scope,
                               route=config.route, execution_domain_ref=config.execution_domain_ref,
                               principals=config.principals, instrument_for=lambda *_: None,
                               ready=lambda: True, strict_admin_cancel=True)
    with pytest.raises(host_module.ServiceHostError, match="RISK_ADMIN_CANCEL_COMPOSITION_INVALID"):
        host_module.ServiceHost(parsed, authority, ":memory:", lambda *_: pytest.fail("resources acquired"),
                                risk_admin_cancel_service=adapter)


@pytest.mark.parametrize("enabled", [False, True])
def test_real_service_host_pairs_component_and_registry_before_listener(monkeypatch, config, enabled):
    import bt_api_transport_zmq.service_v2 as transport
    from bt_api_py.service_components import ComponentResources

    parsed = parse_service_config(risk_host_module.service_config_document(config))
    authority = risk_host_module.build_authority(config)
    adapter = RiskOrderService(object(), object(), account_scope=config.account_scope,
                               route=config.route, execution_domain_ref=config.execution_domain_ref,
                               principals=config.principals, instrument_for=lambda *_: None,
                               ready=lambda: True, strict_admin_cancel=enabled)
    seen = []
    class FakeLock:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def acquire(self, *_args, **_kwargs): return True
        def release(self): pass

    monkeypatch.setattr(host_module, "threading", SimpleNamespace(Lock=FakeLock, get_ident=lambda: 1))
    monkeypatch.setattr(transport, "CurveServerCredentials", lambda **_kw: object())
    monkeypatch.setattr(transport, "validate_service_server_configuration", lambda *_a, **_kw: None)
    monkeypatch.setattr(transport, "ZmqServiceServer", lambda *_a, **_kw: pytest.fail("listener created"))
    def registry(*_args, **kwargs):
        seen.append(kwargs["enable_risk_admin_cancel_admission"])
        raise RuntimeError("stop before Registry creation")
    monkeypatch.setattr(host_module, "ServiceRegistry", registry)
    host = host_module.ServiceHost(parsed, authority, ":memory:",
        lambda *_: ComponentResources.borrowed({"service": adapter}, ready=lambda: True),
        risk_admin_cancel_service=adapter if enabled else None)
    with pytest.raises(Exception):
        host.start()
    assert seen == [enabled]


@pytest.mark.parametrize("mismatch", ["different", "missing_opt_in", "component_disabled"])
def test_real_service_host_rejects_different_component_before_registry(monkeypatch, config, mismatch):
    import bt_api_transport_zmq.service_v2 as transport
    from bt_api_py.service_components import ComponentResources

    parsed = parse_service_config(risk_host_module.service_config_document(config))
    authority = risk_host_module.build_authority(config)
    def service():
        return RiskOrderService(object(), object(), account_scope=config.account_scope,
            route=config.route, execution_domain_ref=config.execution_domain_ref,
            principals=config.principals, instrument_for=lambda *_: None,
            ready=lambda: True, strict_admin_cancel=True)
    class FakeLock:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def acquire(self, *_args, **_kwargs): return True
        def release(self): pass
    monkeypatch.setattr(host_module, "threading", SimpleNamespace(Lock=FakeLock, get_ident=lambda: 1))
    monkeypatch.setattr(transport, "CurveServerCredentials", lambda **_kw: object())
    monkeypatch.setattr(transport, "validate_service_server_configuration", lambda *_a, **_kw: None)
    monkeypatch.setattr(host_module, "ServiceRegistry", lambda *_a, **_kw: pytest.fail("registry created"))
    selected, other = service(), service()
    if mismatch == "missing_opt_in":
        selected = None
    elif mismatch == "component_disabled":
        selected = other
    host = host_module.ServiceHost(parsed, authority, ":memory:",
        lambda *_: ComponentResources.borrowed({"service": other}, ready=lambda: True),
        risk_admin_cancel_service=selected)
    if mismatch == "component_disabled":
        other._strict_admin_cancel = False
    with pytest.raises(host_module.ServiceHostError, match="RISK_ADMIN_CANCEL_COMPOSITION_INVALID"):
        host.start()


@pytest.mark.parametrize("scenario", ["authorized", "ungranted", "registry_revoked", "risk_grant_revoked"])
def test_actual_host_registry_admission_to_memory_commit(monkeypatch, config, composition, scenario):
    import bt_api_transport_zmq.service_v2 as transport

    c = composition
    principal = replace(c.service._principals["curve-a"], public_key=b"p" * 40)
    cfg = replace(config, instance_id="risk-a", scope_ref="scope-a", account_scope="account-a",
                  execution_domain_ref="domain-a", principals={"curve-a": principal},
                  strict_admin_cancel=True)
    monkeypatch.setattr(type(cfg), "registry_database", property(lambda _self: Path(":memory:")))
    monkeypatch.setattr(risk_host_module, "restrict_file", lambda *_: None)
    class FakeLock:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def acquire(self, *_args, **_kwargs): return True
        def release(self): pass
    class FakeServer:
        endpoint = "fake-endpoint"
        def close(self): pass
    monkeypatch.setattr(host_module, "threading", SimpleNamespace(Lock=FakeLock, get_ident=lambda: 1))
    monkeypatch.setattr(host_module, "ServiceClientRuntime",
                        lambda *_a, **_kw: pytest.fail("upstream started"))
    monkeypatch.setattr(transport, "CurveServerCredentials", lambda **_kw: object())
    monkeypatch.setattr(transport, "validate_service_server_configuration", lambda *_a, **_kw: None)
    monkeypatch.setattr(transport, "ZmqServiceServer", lambda *_a, **_kw: FakeServer())

    owner = object.__new__(risk_host_module.RiskServiceHost)
    owner.config, owner.engine, owner.ledger = cfg, c.engine, c.ledger
    owner.order_port = SimpleNamespace(note_run=lambda *_: None)
    owner.control = SimpleNamespace(cancel_scope_status=lambda *_: pytest.fail("Gateway called"))
    owner.instrument_for, owner.ready = lambda *_: None, lambda: True
    owner._claim_resource_owner = lambda: None
    owner._check_cleanup_ready = lambda: None
    owner._call_lifecycle = lambda function, *args, **kwargs: function(*args, **kwargs)
    owner.start_service_host()
    host, registry = owner.service_host, owner.service_host._registry
    assert c.db.execute("PRAGMA temp_store").fetchone()[0] == 2
    assert c.db.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    assert [tuple(r) for r in c.db.execute("PRAGMA database_list")] == [(0, "main", "")]
    registry._db.execute("PRAGMA temp_store=MEMORY")
    assert registry._db.execute("PRAGMA temp_store").fetchone()[0] == 2
    assert registry._db.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    assert [tuple(r) for r in registry._db.execute("PRAGMA database_list")] == [(0, "main", "")]
    assert registry._risk_admin_cancel_admission_enabled is True
    channel = SimpleNamespace(handle=host._dispatch_request)
    foreign = _register(channel, principal="foreign-peer", identity=c.identity, scope=c.scope,
                        instance_id="risk-a", port="service.risk_admin", scope_ref="scope-a",
                        operations=(OP,))
    assert foreign["ok"] is False and row(c.db) is None
    registration = _register(channel, principal="curve-a", identity=c.identity, scope=c.scope,
                             instance_id="risk-a", port="service.risk_admin", scope_ref="scope-a",
                             operations=(OP,))
    assert registration["ok"]
    trace = []
    original_grant = registry._risk_admin_cancel_admission_error
    def check_grant(binding):
        trace.append("grant")
        return original_grant(binding)
    registry._risk_admin_cancel_admission_error = check_grant
    original_fence = c.journal._assert_current_in
    fence_calls = []
    def check_fence(connection, fence):
        original_fence(connection, fence)
        fence_calls.append(1)
        if len(fence_calls) == 3:
            if scenario == "registry_revoked":
                registry._risk_admin_cancel_admission_enabled = False
            elif scenario == "risk_grant_revoked":
                owner.component._principals = {}
    c.journal._assert_current_in = check_fence
    c.db.before_commit = lambda: trace.append("commit")
    args = dict(ARGS, scope_value="strategy-b") if scenario == "ungranted" else ARGS
    response = host._dispatch_request("curve-a", _next_request(OP, {
        "registration_id": registration["result"]["registration_id"],
        "service_epoch": registration["result"]["service_epoch"],
        "instance_id": "risk-a", "arguments": args,
    }, identity=c.identity, scope=c.scope))
    assert response["ok"]
    if scenario == "ungranted":
        assert response["result"] == {"status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
        # No cancel state is created, but the refusal itself is appended.
        assert row(c.db) is None
        audits = c.db.execute(
            "SELECT kind, code, detail_json FROM risk_pretrade_audit ORDER BY seq").fetchall()
        assert len(audits) == 1
        assert tuple(audits[0][:2]) == ("admin_refused", "risk_unauthorized")
        assert "strategy-b" in audits[0][2]
    elif scenario == "authorized":
        assert response["result"] == {"status": "OBSERVED", "operation_id": "operation-a", "state": "UNKNOWN"}
        assert tuple(row(c.db)) == ("UNKNOWN", 1, 2)
        assert trace.count("grant") >= 2 and trace[-2:] == ["grant", "commit"]
    else:
        assert response["result"]["state"] == "UNKNOWN"
        assert len(fence_calls) == 3 and row(c.db) is None
        assert "commit" not in trace
    host.close()
