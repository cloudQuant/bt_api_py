"""Risk service host composition and CLI (64.09). UNIT evidence: temp storage, no Gateway contact."""

from __future__ import annotations

import json
import os
import re
import stat
from types import SimpleNamespace

import pytest

from bt_api_py.risk_service_config import load_risk_service_config
from bt_api_py.risk_service_host import (
    RiskServiceHost,
    RiskServiceStartError,
    _r05_cut_matches_current_gateway,
    bootstrap_policy,
    build_authority,
    main,
    probe_writer_fence,
)
from tests.unit.test_iter64_risk_service_component import V2_POLICY_BLOCKED
from tests.unit.test_iter64_risk_service_config import document, write_keys

POLICY = (
    # Only the v2 wire schema is a current policy format; v1 is refused.
    "schema: bt_api_risk.policy.v2\n"
    "policy_id: unit-{name}\n"
    "account: {{route: CTP___FUTURE, account_scope: account-a}}\n"
    "per_order:\n  max_quantity: {{strategy-a: {{rb2701: \"{cap}\"}}}}\n"
)


def setup(tmp_path, cap="5"):
    write_keys(tmp_path)
    (tmp_path / "risk_policy.yaml").write_text(POLICY.format(name="a", cap=cap), encoding="utf-8")
    path = tmp_path / "risk_service.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolate_process_cleanup_state():
    """Give every test a fresh-process view of the module-global UNKNOWN state.

    Production deliberately never clears these containers, and an unresolved
    fence entry quarantines its account scope for the rest of the process.
    These tests create independent synthetic owners in temporary directories,
    so they start from a clean process view; nothing here relaxes production.
    """
    import bt_api_risk.risk_order_journal as risk_journal

    def reset():
        from bt_api_py import risk_service_host as host_module

        host_module._RETAINED_HOSTS.clear()
        host_module._PROBE_STATE["active"] = False
        host_module._PROBE_STATE["uncertain"].clear()
        host_module._STORAGE_INIT_ROOTS.clear()
        host_module._STORAGE_ACTIVE_ROOTS.clear()
        risk_journal._FENCE_ENTRY_QUARANTINED_KEYS.clear()
        risk_journal._FENCE_ENTRY_QUARANTINED_ALL = False
        risk_journal._FENCE_PROVISIONAL_OWNERS.clear()

    reset()
    yield
    reset()


def opened(tmp_path):
    host = RiskServiceHost(load_risk_service_config(tmp_path / "risk_service.json"))
    host.open_storage()
    host.build_engine()
    return host


def _r05_current_page_fixture():
    progress = {
        "pending": None,
        "cursor": 4,
        "ack_observed": 4,
        "epoch": "facts-epoch-a",
        "gateway_journal_id": "journal-a",
    }
    cut = {
        "seq": 3,
        "cut_ref": "cut-ref-a",
        "query_manifest_digest": "query-manifest-a",
        "receipt_manifest_digest": "receipt-manifest-a",
        "gateway_account": "account-a",
        "journal_id": "journal-a",
        "owner_generation": "gateway-generation-a",
        "fact_epoch": "facts-epoch-a",
        "fact_watermark": 4,
        "source_binding_ref": "source-binding-a",
        "current_session_trading_day": "20261126",
        "base_sequence": 0,
        "begin_sequence": 1,
        "end_sequence": 2,
        "source_prefix": "source-prefix-a",
        "query_digest": "query-digest-a",
        "query_scope": "ctp.futures.account_orders.v1",
        "query_row_count": 1,
        "cut_record_digest": "cut-record-a",
        "query_fact_digests_digest": "query-facts-a",
        "source_history_has_gap": False,
        "observed_at_ns": 1_000,
        "max_age_ns": 100,
        "query_next_ordinal": 1,
        "query_complete": True,
        "receipt_next_sequence": 2,
        "receipt_complete": True,
    }
    header_fields = (
        "journal_id", "owner_generation", "fact_epoch", "fact_watermark",
        "current_session_trading_day", "base_sequence", "begin_sequence",
        "end_sequence", "source_prefix", "query_digest", "query_scope",
        "query_row_count", "cut_record_digest", "query_fact_digests_digest",
        "source_binding_ref", "source_history_has_gap",
    )
    page = {
        "schema": "sdk.gateway.order_cut.current/1",
        "registration_id": "gateway-registration-a",
        "service_epoch": "gateway-service-epoch-a",
        "cut_ref": cut["cut_ref"],
        "query_manifest_digest": cut["query_manifest_digest"],
        "header": {name: cut[name] for name in header_fields},
    }
    kwargs = {
        "registration_id": "gateway-registration-a",
        "service_epoch": "gateway-service-epoch-a",
        "observer_service_epoch": "gateway-service-epoch-a",
        "verified_stream_epoch": "account-events-epoch-a",
    }
    return progress, cut, page, kwargs


def test_r05_current_gateway_binding_accepts_same_generation_complete_cut():
    progress, cut, page, kwargs = _r05_current_page_fixture()
    assert _r05_cut_matches_current_gateway(progress, cut, page, True, **kwargs)


@pytest.mark.parametrize("field,value", [
    ("current_session_trading_day", "20261125"),
    ("owner_generation", "gateway-generation-old"),
    ("query_scope", "ctp.futures.account_orders.other"),
])
def test_r05_current_gateway_binding_rejects_day_generation_and_scope_mismatch(field, value):
    progress, cut, page, kwargs = _r05_current_page_fixture()
    cut[field] = value
    assert not _r05_cut_matches_current_gateway(progress, cut, page, True, **kwargs)


@pytest.mark.parametrize("field,value", [
    ("query_complete", False),
    ("receipt_complete", False),
    ("source_history_has_gap", True),
    ("seq", 0),
])
def test_r05_current_gateway_binding_rejects_incomplete_gapped_or_noncurrent_cut(field, value):
    progress, cut, page, kwargs = _r05_current_page_fixture()
    cut[field] = value
    if field == "source_history_has_gap":
        page["header"][field] = value
    assert not _r05_cut_matches_current_gateway(progress, cut, page, True, **kwargs)


def test_r05_current_gateway_binding_rejects_stale_canonical_coverage():
    progress, cut, page, kwargs = _r05_current_page_fixture()
    # The canonical Risk ledger verifier supplied False for an expired cut.
    assert not _r05_cut_matches_current_gateway(progress, cut, page, False, **kwargs)


def test_r05_current_gateway_binding_rejects_authenticated_page_mismatch():
    progress, cut, page, kwargs = _r05_current_page_fixture()
    page["header"]["current_session_trading_day"] = "20261125"
    assert not _r05_cut_matches_current_gateway(progress, cut, page, True, **kwargs)
    progress, cut, page, kwargs = _r05_current_page_fixture()
    page["query_manifest_digest"] = "other-query-manifest"
    assert not _r05_cut_matches_current_gateway(progress, cut, page, True, **kwargs)


def test_r05_status_observation_preserves_host_state_and_write_check_degrades():
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_py.risk_service_component import RiskOrderService
    from bt_api_py.risk_service_config import RiskPrincipal

    host = object.__new__(RiskServiceHost)
    host.config = SimpleNamespace(
        account_facts_observation=SimpleNamespace(order_cut_enabled=True),
    )
    host.order_cut_status = "CUT_APPLIED"
    host.ready = lambda: False

    class Engine:
        def status_summary(self):
            return {"available": True}

    principal = RiskPrincipal(
        "operator", "operator", "desk", frozenset(),
        frozenset({"risk.admin.status"}), b"x" * 40,
    )
    service = RiskOrderService(
        Engine(), object(), account_scope="account-a", route="CTP___FUTURE",
        execution_domain_ref="paper:demo", principals={"operator": principal},
        instrument_for=lambda *_args: None, ready=lambda: True,
        order_cut_required=True,
        order_cut_admission_ready=host._order_cut_admission_ready,
        order_cut_admission_observe=host._observe_order_cut_admission,
    )
    context = SimpleNamespace(
        identity=WireIdentity(WireRole.APPLICATION_CLIENT, "operator", "desk"),
        scope=WireScope("paper:demo", account_ref="account-a"),
        principal_ref="operator",
    )

    before = host.order_cut_status
    reply = service.handle(
        "risk.admin.status", context=context,
        arguments={"order_cut_admission": True},
    )
    assert reply["summary"]["order_cut_admission"] == {"checked": True, "ready": False}
    assert host.order_cut_status == before == "CUT_APPLIED"

    assert service._order_cut_allows_write() is False
    assert host.order_cut_status == "UNKNOWN"


@pytest.mark.parametrize("remaining,expected_ready", [(8, False), (9, True)])
def test_r05_admission_headroom_counts_only_wire_current(monkeypatch, remaining, expected_ready):
    import bt_api_py.gateway_order_cut_client as cut_module
    import bt_api_py.risk_account_facts_coordinator as coordinator_module

    class Result:
        def __init__(self, outcome, code=None):
            self.outcome, self.code = outcome, code

    class CopyResult:
        def __init__(self, outcome, page=None, code=None):
            self.outcome, self.page, self.code = outcome, page, code

    class CutClient:
        def __init__(self, page):
            self.page = page
            self.wire_current_calls = 0
            self.local_copy_checks = 0

        def current(self, **_kwargs):
            self.wire_current_calls += 1
            return Result("OBSERVED")

        def validate_copy_issued(self, _result, **_kwargs):
            self.local_copy_checks += 1
            return CopyResult("OBSERVED", page=self.page)

    class Coordinator:
        def __init__(self, client):
            self._mode = "READY"
            self._cut_client = client

        def _check(self):
            return None

    monkeypatch.setattr(cut_module, "GatewayOrderCutClient", CutClient)
    monkeypatch.setattr(cut_module, "GatewayOrderCutResult", Result)
    monkeypatch.setattr(cut_module, "GatewayOrderCutCopyResult", CopyResult)
    monkeypatch.setattr(coordinator_module, "RiskAccountFactsCoordinator", Coordinator)

    progress, cut, page, _kwargs = _r05_current_page_fixture()
    client = CutClient(page)
    binding = SimpleNamespace(
        registration_id="gateway-registration-a",
        service_epoch="gateway-service-epoch-a",
    )
    host = object.__new__(RiskServiceHost)
    host.config = SimpleNamespace(account_facts_observation=SimpleNamespace(
        order_cut_enabled=True, order_cut_timeout_seconds=None, timeout_seconds=0.1,
    ))
    host.order_cut_status = "CUT_APPLIED"
    host.account_facts_status = "APPLIED"
    host._account_facts_runtime = SimpleNamespace(
        status=lambda: SimpleNamespace(min_socket_message_ids_remaining=remaining),
    )
    host._account_facts_coordinator = Coordinator(client)
    host._observer_runtime = object()
    host.ledger = SimpleNamespace(_r05_uncertain=False)
    host.ready = lambda: True
    host._now_ns = lambda: 1_050
    host._call_lifecycle = lambda callback, *args, **kwargs: callback(*args, **kwargs)
    host._verified_observer_stream_epoch = lambda: "account-events-epoch-a"
    host._active_account_binding = lambda _runtime, _operations: binding
    host._read_r05_order_cut_state = lambda _now_ns: (progress, cut, True)
    host._check_cleanup_ready = lambda: None

    assert host._order_cut_admission_ready() is expected_ready
    assert client.wire_current_calls == (1 if expected_ready else 0)
    assert client.local_copy_checks == (1 if expected_ready else 0)


def test_policy_bootstrap_first_unchanged_tightened_and_loosening_refused(tmp_path):
    setup(tmp_path)
    host = opened(tmp_path)
    try:
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "bootstrapped"
        assert host.engine.status_summary()["policy_version"] == 1
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "unchanged"
        (tmp_path / "risk_policy.yaml").write_text(POLICY.format(name="a", cap="3"))
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "tightened"
        assert host.engine.status_summary()["policy_version"] == 2
        (tmp_path / "risk_policy.yaml").write_text(POLICY.format(name="a", cap="9"))
        with pytest.raises(RiskServiceStartError) as raised:
            bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
        assert raised.value.code == "RISK_SERVICE_POLICY_FILE_LOOSENS"
        assert host.engine.status_summary()["policy_version"] == 2
        (tmp_path / "risk_policy.yaml").write_text(
            POLICY.format(name="a", cap="3").replace("account-a", "account-b"))
        with pytest.raises(RiskServiceStartError) as raised:
            bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
        assert raised.value.code == "RISK_SERVICE_POLICY_ACCOUNT_MISMATCH"
    finally:
        host.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file locks and modes")
def test_storage_is_private_and_a_held_fence_refuses_at_once(tmp_path):
    setup(tmp_path)
    host = opened(tmp_path)
    try:
        config = host.config
        assert stat.S_IMODE(config.storage_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(config.risk_database.stat().st_mode) == 0o600
        # Another opener of the same account's journal must not wait for the fence.
        from bt_api_risk.core.admission import AccountScope
        from bt_api_risk.risk_order_journal import RiskOrderJournal

        other = RiskOrderJournal(config.risk_database, writer_id="second")
        with pytest.raises(RiskServiceStartError) as raised:
            probe_writer_fence(other, AccountScope("ctp", "account-a", "simnow"))
        # Same process: the live provisional owner refuses the entry before the
        # OS lock is consulted, so the code is STORAGE_UNAVAILABLE.
        assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    finally:
        host.close()
    # After close the fence is free again.
    probe_writer_fence(other, AccountScope("ctp", "account-a", "simnow"))


def test_authority_installs_the_principal_table_only(tmp_path):
    setup(tmp_path)
    config = load_risk_service_config(tmp_path / "risk_service.json")
    authority = build_authority(config)
    assert set(authority.principals.values()) == {"principal-a", "operator-1"}
    (strategy_grant,) = authority.grants["principal-a"]
    assert strategy_grant.identity.role.value == "strategy_client"
    (resource,) = strategy_grant.resources
    assert resource.port == "service.risk_order" and resource.scope.strategy_ref == "strategy-a"
    assert resource.scope.run_id is None and "risk.policy.publish" not in resource.operations
    (operator_grant,) = authority.grants["operator-1"]
    (resource,) = operator_grant.resources
    assert resource.port == "service.risk_admin" and resource.scope.strategy_ref is None
    assert authority.identity.actor_ref == "risk-service"


def test_risk_gateway_runtimes_require_explicit_unregister_ack(tmp_path):
    from bt_api_base.service_protocol import WireScope

    from bt_api_py.risk_service_gateway import CONTROL_OPERATIONS
    from bt_api_py.risk_service_host import OBSERVER_OPERATIONS

    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    scope = WireScope(host.config.execution_domain_ref, account_ref=host.config.account_scope)
    observer = host._service_runtime("upstream.account", scope, OBSERVER_OPERATIONS)._factory()
    control = host._application_runtime({"application.direct_write": CONTROL_OPERATIONS})._factory()
    try:
        assert observer._require_unregister_ack is True
        assert control._require_unregister_ack is True
    finally:
        observer.close()
        control.close()
        host.close()


def test_engine_control_adapter_pins_the_configured_bound_route(tmp_path):
    import hashlib

    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

    from bt_api_py.service_runtime_thread import BoundCallRoute

    setup(tmp_path)
    host = opened(tmp_path)
    try:
        gateway = host.config.gateway
        route = host.control._route
        assert type(route) is BoundCallRoute
        assert route.identity == WireIdentity(
            WireRole.APPLICATION_CLIENT,
            gateway.control.principal_ref,
            gateway.control.actor_ref,
        )
        assert route.scope == WireScope(
            host.config.execution_domain_ref,
            account_ref=host.config.account_scope,
        )
        assert (
            route.scope_ref,
            route.service_id,
            route.instance_id,
            route.peer,
        ) == (
            gateway.scope_ref,
            gateway.service_id,
            gateway.instance_id,
            host._peer(),
        )
        assert host.control._server_key_fingerprint == hashlib.sha256(
            gateway.server_public_key
        ).hexdigest()
        assert host.control._runtime is host.control_runtime
    finally:
        host.close()


def test_cli_validate_policy_offline(tmp_path, capsys):
    setup(tmp_path)
    assert main(["validate-policy", str(tmp_path / "risk_policy.yaml")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is True and out["policy_id"] == "unit-a" and len(out["digest"]) == 64
    (tmp_path / "bad.yaml").write_text("schema: bt_api_risk.policy.v1\npolicy_id: x\n")
    assert main(["validate-policy", str(tmp_path / "bad.yaml")]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is False and out["code"]


@V2_POLICY_BLOCKED
def test_cli_status_reads_the_local_ledger_without_the_gateway(tmp_path, capsys):
    path = setup(tmp_path)
    assert main(["status", "--config", str(path)]) == 2
    assert json.loads(capsys.readouterr().out) == {"code": "RISK_SERVICE_STORAGE_MISSING"}
    host = opened(tmp_path)
    try:
        # Inside the try: a refused bootstrap (today RULE_R04_V2_UNAVAILABLE via
        # RISK_SERVICE_POLICY_PUBLISH_REFUSED) must still close the host fence.
        bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
        # While the service holds the fence, status answers "running" at once.
        assert main(["status", "--config", str(path)]) == 3
        assert json.loads(capsys.readouterr().out) == {"running": True,
                                                       "code": "RISK_SERVICE_FENCE_HELD"}
    finally:
        host.close()
    assert main(["status", "--config", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["running"] is False and out["summary"]["policy_version"] == 1
    text = json.dumps(out)
    assert str(tmp_path) not in text and "account-a" not in json.dumps(out["summary"]["counts"])


def test_cli_status_busy_storage_does_not_claim_service_stopped(tmp_path, capsys, monkeypatch):
    path = setup(tmp_path)
    config = load_risk_service_config(path)
    config.storage_directory.mkdir()
    config.risk_database.touch()

    def busy(_self):
        raise RiskServiceStartError("RISK_SERVICE_STORAGE_BUSY")

    monkeypatch.setattr(RiskServiceHost, "open_storage", busy)
    assert main(["status", "--config", str(path)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "running": None, "code": "RISK_SERVICE_STORAGE_BUSY"}


@pytest.mark.parametrize("cleanup", ["exception", "alive", "owner_uncertain", "unregister_uncertain", "registration_unknown", "factory_unproven"])
def test_observer_reconnect_never_replaces_an_unresolved_owner(tmp_path, monkeypatch, cleanup):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)), monotonic=lambda: 1.0)
    gaps = []
    host.pump = SimpleNamespace(_gap=gaps.append)
    host._observer_route = object()
    class Old:
        def status(self):
            return SimpleNamespace(state="failed")
        def close(self, **_kwargs):
            if cleanup == "exception":
                raise RuntimeError("cleanup failed")
            return SimpleNamespace(state="failed", thread_alive=cleanup == "alive",
                                   error_code="FACTORY_FAILED_CLEANUP_UNPROVEN" if cleanup == "factory_unproven" else "MAINTENANCE_FAILED",
                                   owner_cleanup_uncertainties=int(cleanup == "owner_uncertain"),
                                   unregister_uncertainties=int(cleanup == "unregister_uncertain"),
                                   unknown_registration_count=int(cleanup == "registration_unknown"))
    old = Old()
    host._observer_runtime = old
    host._runtimes = [old]
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: pytest.fail("replacement"))
    host._reconnect_observer()
    assert gaps == ["observation_reconnect"]
    assert host._observer_runtime is old and host._runtimes == [old]


def test_delegate_failed_start_with_unresolved_cleanup_retains_original_runtime(tmp_path, monkeypatch):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    class Unresolved:
        def start(self, **_kwargs):
            raise RuntimeError("startup failed")
        def close(self, **_kwargs):
            raise RuntimeError("cleanup unconfirmed")
    runtime = Unresolved()
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: runtime)
    route = SimpleNamespace(scope=SimpleNamespace(run_id="original-run"))
    with pytest.raises(RuntimeError, match="startup failed"):
        host._connect_delegate(route)
    assert host._delegate_cleanup_unresolved == [runtime]
    host._delegate_cleanup_unresolved[:] = [runtime] * 16
    with pytest.raises(RiskServiceStartError) as raised:
        host._connect_delegate(route)
    assert raised.value.code == "RISK_SERVICE_HOST_CLEANUP_FINALIZED"


@pytest.mark.parametrize("problem", ["unregister", "registration"])
def test_delegate_unknown_remote_unregister_blocks_next_session(tmp_path, monkeypatch, problem):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    class UnregisteredUnknown:
        def start(self, **_kwargs):
            raise RuntimeError("startup failed")
        def close(self, **_kwargs):
            return SimpleNamespace(state="failed", thread_alive=False,
                                   error_code="MAINTENANCE_FAILED",
                                   unregister_uncertainties=int(problem == "unregister"),
                                   owner_cleanup_uncertainties=0,
                                   unknown_registration_count=int(problem == "registration"))
    old = UnregisteredUnknown()
    attempts = []
    def create(*_args, **_kwargs):
        attempts.append(1)
        return old
    monkeypatch.setattr(host, "_service_runtime", create)
    route = SimpleNamespace(scope=SimpleNamespace(run_id="original-run"))
    with pytest.raises(RuntimeError):
        host._connect_delegate(route)
    with pytest.raises(RiskServiceStartError) as raised:
        host._connect_delegate(route)
    assert raised.value.code == "RISK_SERVICE_HOST_CLEANUP_FINALIZED"
    assert attempts == [1] and host._delegate_cleanup_unresolved == [old]


@pytest.mark.parametrize("problem", ["unregister", "registration_unknown", "factory", "alive", "owner", "exception"])
def test_incomplete_host_close_retains_fence_without_retry(tmp_path, problem):
    import gc
    import weakref

    from bt_api_py.risk_service_host import _RETAINED_HOSTS

    config = load_risk_service_config(setup(tmp_path))
    class Runtime:
        resolved = False
        def close(self, **_kwargs):
            if not self.resolved and problem == "exception":
                raise RuntimeError("cleanup unavailable")
            return SimpleNamespace(
                state="failed", thread_alive=not self.resolved and problem == "alive",
                error_code=("FACTORY_FAILED_CLEANUP_UNPROVEN"
                            if not self.resolved and problem == "factory" else "MAINTENANCE_FAILED"),
                unregister_uncertainties=int(not self.resolved and problem == "unregister"),
                owner_cleanup_uncertainties=int(not self.resolved and problem == "owner"),
                unknown_registration_count=int(not self.resolved and problem == "registration_unknown"))
    resource = Runtime()
    host = RiskServiceHost(config)
    host.open_storage()
    host._delegate_cleanup_unresolved.append(resource)
    ref = weakref.ref(host)
    contender = RiskServiceHost(config)
    try:
        assert host.close() is False and host in _RETAINED_HOSTS
        with pytest.raises(RiskServiceStartError) as raised:
            contender.open_storage()
        assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
        del host
        gc.collect()
        host = ref()
        assert host is not None and host in _RETAINED_HOSTS
        resource.resolved = True
        assert host.close() is False and host in _RETAINED_HOSTS
        with pytest.raises(RiskServiceStartError) as raised:
            contender.open_storage()
        assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    finally:
        contender.close()


def test_incomplete_delegate_port_close_retains_fence(tmp_path):
    config = load_risk_service_config(setup(tmp_path))
    class Port:
        resolved = False
        def close(self):
            return self.resolved
    port = Port()
    host = RiskServiceHost(config)
    host.open_storage()
    host.order_port = port
    contender = RiskServiceHost(config)
    try:
        assert host.close() is False
        with pytest.raises(RiskServiceStartError) as raised:
            contender.open_storage()
        assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
        port.resolved = True
        assert host.close() is False
        with pytest.raises(RiskServiceStartError) as raised:
            contender.open_storage()
        assert raised.value.code == "RISK_SERVICE_STORAGE_UNAVAILABLE"
    finally:
        contender.close()


def test_run_entry_waits_without_retrying_incomplete_cleanup(monkeypatch):
    from bt_api_py import risk_service_host as module

    events = []
    class Host:
        def __init__(self, _config):
            self.stop_event = SimpleNamespace(set=lambda: None)
        def start(self):
            events.append("start")
        def run(self):
            events.append("run")
        def close(self):
            events.append("close")
            return events.count("close") == 2
    monkeypatch.setattr(module, "load_risk_service_config", lambda _path: object())
    monkeypatch.setattr(module, "RiskServiceHost", Host)
    class StopWait(BaseException):
        pass

    original_wait = module._wait_for_cleanup
    def stop_wait():
        events.append("wait")
        raise StopWait
    monkeypatch.setattr(module, "_wait_for_cleanup", lambda host: original_wait(host, _wait=stop_wait))
    monkeypatch.setattr(module.signal, "signal", lambda *_args: None)
    with pytest.raises(StopWait):
        module.main(["run", "--config", "synthetic"])
    assert events == ["start", "run", "close", "wait"]


def test_run_owner_stop_file_is_opt_in_and_cleanup_precedes_stopped_output(
    tmp_path, monkeypatch
):
    from bt_api_py import risk_service_host as module
    from bt_api_py._owner_stop_file import marker_requested
    from bt_api_py.risk_service_config import restrict_file

    def protect_marker(path):
        restrict_file(path)
        if os.name == "nt":
            from bt_api_py._win32_security import _set_owner_only_acl

            _set_owner_only_acl(path)

    config_path = setup(tmp_path)
    config = load_risk_service_config(config_path)
    real_host_type = RiskServiceHost
    events = []
    printed = []
    owners = []

    class CliOwner:
        def __init__(self, _config):
            self.stop_path = None
            owners.append(self)

        def start(self):
            events.append("start")

        def run(self, **kwargs):
            real_host = real_host_type(config)
            if not kwargs:
                events.append("run_default_without_marker")

                def one_tick():
                    events.append("default_tick")
                    real_host.stop_event.set()

                real_host.run_once = one_tick
                real_host.run()
                return

            assert set(kwargs) == {"stop_file"}
            stop_file = kwargs["stop_file"]
            assert stop_file.parent == config_path.parent
            assert re.fullmatch(r"risk-[0-9a-f]{32}\.stop", stop_file.name)
            assert printed[-1][0] == "RISK_SERVICE_STOP_FILE " + stop_file.name
            self.stop_path = stop_file
            events.append("run_with_marker")
            ticks = 0

            def one_tick():
                nonlocal ticks
                ticks += 1
                if ticks == 1:
                    with stop_file.open("xb") as marker:
                        marker.write(b"not-empty")
                    protect_marker(stop_file)
                    events.append("nonempty_created")
                elif ticks == 2:
                    assert stop_file.is_file() and not stop_file.is_symlink()
                    assert stop_file.read_bytes() == b"not-empty"
                    assert marker_requested(stop_file) is False
                    events.append("nonempty_ignored_and_retained")
                    stop_file.unlink()
                    with stop_file.open("xb"):
                        pass
                    protect_marker(stop_file)
                    events.append("empty_created")
                else:
                    pytest.fail("empty owner marker did not stop RiskServiceHost.run")

            real_host.run_once = one_tick
            real_host.run(stop_file=stop_file)
            assert ticks == 2

        def close(self):
            events.append("cleanup")
            return True

    def record_print(*args, **kwargs):
        line = " ".join(str(value) for value in args)
        printed.append((line, kwargs))
        if line.startswith("RISK_SERVICE_STOP_FILE "):
            events.append("announce")
        elif line == "RISK_SERVICE_STOPPED":
            events.append("stopped_output")

    monkeypatch.setattr(module, "load_risk_service_config", lambda _path: config)
    monkeypatch.setattr(module, "RiskServiceHost", CliOwner)
    monkeypatch.setattr(module.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(module, "print", record_print, raising=False)

    assert module.main(["run", "--config", str(config_path)]) == 0
    assert events == ["start", "run_default_without_marker", "default_tick", "cleanup"]
    assert printed == []
    assert owners[-1].stop_path is None

    events.clear()
    assert module.main(
        ["run", "--config", str(config_path), "--enable-owner-stop-file"]
    ) == 0
    marker_line = printed[0][0]
    marker_name = marker_line.removeprefix("RISK_SERVICE_STOP_FILE ")
    assert marker_line.startswith("RISK_SERVICE_STOP_FILE ")
    assert re.fullmatch(r"risk-[0-9a-f]{32}\.stop", marker_name)
    assert printed == [
        ("RISK_SERVICE_STOP_FILE " + marker_name, {"flush": True}),
        ("RISK_SERVICE_STOPPED", {"flush": True}),
    ]
    assert events == [
        "start",
        "announce",
        "run_with_marker",
        "nonempty_created",
        "nonempty_ignored_and_retained",
        "empty_created",
        "cleanup",
        "stopped_output",
    ]
    assert owners[-1].stop_path == config_path.parent / marker_name
    assert owners[-1].stop_path.is_file() and owners[-1].stop_path.read_bytes() == b""


def test_cli_run_refuses_with_a_finite_code(tmp_path, capsys):
    path = setup(tmp_path)
    raw = json.loads(path.read_text())
    raw["deadlines"]["reservation_ttl_seconds"] = 31
    path.write_text(json.dumps(raw))
    assert main(["run", "--config", str(path)]) == 2
    assert capsys.readouterr().err.strip() == (
        "RISK_SERVICE_START_REFUSED RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE")
