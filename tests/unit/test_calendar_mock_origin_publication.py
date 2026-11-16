"""Origin claims follow reviewed, code-generated simulation publications."""

import json
import queue
import shutil
import subprocess
import sys
import threading
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest
import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope

from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_mock_cli as mock_cli
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_calendar_service import (
    CalendarGrant,
    GatewayCalendarService,
    ReceiptCheckedCalendarStore,
)
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_calendar import GatewayCalendarClientSettings
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.trading_calendar_binding import _covered_source
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.calendar_test_store import FixtureCalendarStore
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_iter63_gateway_dual_ports import _resources
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_strategy_client import Host
from tests.unit.test_trading_calendar_binding import binding, content, decision, publish
from tests.unit.test_trading_calendar_observation_run import env_without_network
from tests.unit.test_trading_calendar_runtime_mixed_query import publish as publish_mixed

DAY = date(2026, 6, 2)
SCOPE = {
    "route": "route-a",
    "venue": "SHFE",
    "kind": "future",
    "product": "mock_future",
    "contract": "mock_shfe_future_contract",
}


@pytest.fixture(scope="module")
def publication(tmp_path_factory):
    temp = tmp_path_factory.mktemp("mock-origin")
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "bt_api_py.trading_calendar_observation_run",
            "run",
            "--mock",
            "--mock-year",
            "2026",
            "--budget-seconds",
            "30",
            "--trusted-local-posix",
        ],
        env=env_without_network(temp),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert child.returncode == 10, (child.stdout, child.stderr)
    run = json.loads(child.stdout.splitlines()[-1])
    root = run["simulation_root"]
    try:
        operator = MockCalendarPublication(root=root, run_id=run["run_id"])
        assert operator.initialize(mode="manual")["status"] == "INITIALIZED"
        proposal = operator.prepare(candidate_id=run["derived_id"])
        assert proposal["status"] == "FIRST_SIMULATION_PUBLICATION", proposal
        assert (
            operator.publish(
                proposal=proposal,
                publication_id="MOCK_pub1",
                version_id="MOCK_v1",
                emit=lambda _: None,
            )["status"]
            == "PENDING_REVIEW"
        )
        result = operator.publish(
            proposal=proposal,
            publication_id="MOCK_pub1",
            version_id="MOCK_v1",
            reviewer="MOCK_reviewer",
            confirmed_at="2026-11-13T00:00:00.000000Z",
            emit=lambda _: None,
        )
        assert result["status"] == "COMMITTED", result
        yield root, operator
    finally:
        shutil.rmtree(root)


def test_reviewed_publication_update_and_bound_origin(publication):
    root, operator = publication
    store = TradingCalendarStore(root)
    first = store.read_current()
    manifest = json.loads(first.coverage_manifest_bytes)
    assert (first.snapshot_schema, manifest["schema"], manifest["data_origin"]) == (
        "bt_api.calendar-store.snapshot/2",
        "bt_api.calendar-simulation-coverage/2",
        "SYNTHETIC_MOCK",
    )
    assert _covered_source(first)
    bad = replace(
        first,
        coverage_manifest_bytes=first.coverage_manifest_bytes.replace(
            b"SYNTHETIC_MOCK", b"FORGED_ORIGIN"
        ),
    )
    with pytest.raises(ValueError):
        _covered_source(bad)
    view = binding(ReceiptCheckedCalendarStore(store), day=DAY)
    assert view.status().data_origin == "SYNTHETIC_MOCK"
    assert (
        view.query(
            **{k: v for k, v in SCOPE.items() if k != "route"}, trading_day=DAY
        ).binding.data_origin
        == "SYNTHETIC_MOCK"
    )
    # A user-supplied row cannot gain a label merely by using MOCK metadata or IDs.
    candidate = operator._candidate(operator._audit["derived_id"])[1]
    changed = deepcopy(candidate)
    changed["snapshot"]["trading_days"][0]["status"] = (
        "open" if changed["snapshot"]["trading_days"][0]["status"] == "closed" else "closed"
    )
    changed["manifest"]["schema"] = "bt_api.calendar-simulation-coverage/1"
    changed["manifest"].pop("data_origin")
    assert mock.declared_publication_composite(changed, 2026) is changed
    pending = mock_cli.update(root=root, run_id=operator.run_id, variant="AFTERNOON_CHANGE")
    assert pending["status"] == "PENDING_REVIEW", pending
    assert pending["proposal"]["status"] == "CHANGED"
    result = mock_cli._confirm(
        operator,
        review_id=pending["review_id"],
        review_sha256=pending["review_payload_sha256"],
        reviewer="MOCK_reviewer",
        confirmed_at="2026-11-13T00:00:00.000000Z",
    )
    assert result["status"] == "COMMITTED", result
    assert view.status().calendar_ref == "MOCK_v1" and view.status().data_origin == "SYNTHETIC_MOCK"
    assert view.activate(decision(day=date(2026, 6, 3))).outcome == "ACTIVATED"
    assert (
        view.status().calendar_ref == pending["version_id"]
        and view.status().data_origin == "SYNTHETIC_MOCK"
    )
    assert (
        json.loads(store.read_current().coverage_manifest_bytes)["data_origin"] == "SYNTHETIC_MOCK"
    )


def test_old_and_mixed_classifications_are_unchanged(tmp_path):
    old = tmp_path / "old"
    assert publish(FixtureCalendarStore(old), "old", content()).visibility == "COMMITTED"
    assert binding(TradingCalendarStore(old)).status().data_origin == "UNSPECIFIED"
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    publish_mixed(mixed, mixed_version=True)
    assert binding(TradingCalendarStore(mixed), day=DAY).status().data_origin == "AKSHARE_MOCK"


@pytest.mark.skipif(not zmq.has("curve"), reason="CURVE unavailable")
def test_formal_publication_over_service_host_curve(publication, tmp_path):
    root, _ = publication
    bound = binding(ReceiptCheckedCalendarStore(TradingCalendarStore(root)), day=DAY)
    endpoint = _free_endpoint()
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    raw = _service_config(endpoint).to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    client_raw = _client_config(endpoint, CLIENT_A).to_public_dict()
    binds = client_raw["clients"]["gateway"]["settings"]["bindings"]
    binds.append(dict(binds[0], mount="store.calendar"))
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref)
    direct_ops = frozenset({"direct.command_status"})
    calendar_ops = frozenset({"calendar.query.v1", "calendar.windows.v1"})
    principal = PrincipalGrant(
        CLIENT_A,
        (
            ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, direct_ops),
            ResourceGrant("gateway-a", "service.calendar_read", "scope-a", scope, calendar_ops),
        ),
    )
    authority = TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="v1",
        security_ref="security-a",
        security_version="v1",
        registration_policy_ref="direct-policy-v1",
        registration_policy_version="v1",
        service_requirements_ref="direct-requirements-v1",
        service_requirements_version="v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals={client_keys[0]: CLIENT_A.principal_ref},
        grants={CLIENT_A.principal_ref: (principal,)},
    )
    ready, stop = queue.Queue(), threading.Event()

    def serve():
        host = None
        try:
            direct, _, _ = _resources(tmp_path)
            grant = CalendarGrant(
                CLIENT_A.principal_ref,
                DOMAIN,
                ACCOUNT,
                CLIENT_A.actor_ref,
                "run-a",
                *SCOPE.values(),
            )
            policy = CalendarRangePolicy("mock-policy", *SCOPE.values(), 3, 0)
            service = GatewayCalendarService(
                grants=frozenset({grant}), range_policies=frozenset({policy})
            )
            service.rebind(bound)
            host = ServiceHost(
                parse_service_config(raw),
                authority,
                tmp_path / "registry.sqlite",
                lambda *_: ComponentResources.borrowed(
                    {"binding": direct, "calendar_service": service}, ready=lambda: True
                ),
            )
            host.start()
            ready.put(("ready", direct._journal.journal_id))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as exc:
            ready.put(("error", type(exc).__name__, str(exc)))
        finally:
            if host is not None and host.state == "started":
                host.close()

    thread = threading.Thread(target=serve)
    thread.start()
    session = None
    try:
        started = ready.get(timeout=15)
        assert started[0] == "ready", started
        route = GatewayClientRoute(
            "broker.direct_write",
            SERVICE.actor_ref,
            "gateway-a",
            "scope-a",
            WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_A.actor_ref, run_id="run-a"),
            CLIENT_A,
        )
        settings = GatewayCalendarClientSettings("SIMULATION_ONLY", (SCOPE,), True)
        session = GatewayStrategyClientSession(
            parse_client_config(client_raw),
            binding=GatewayCommandBinding(
                started[1], ACCOUNT, CLIENT_A.actor_ref, CLIENT_A.principal_ref
            ),
            route=route,
            peer=SERVICE,
            security_profiles={"security-a": ClientCurveKeys(server_keys[0], *client_keys)},
            host=Host(),
            requested_operations=direct_ops,
            calendar_settings=settings,
        )
        session.start(timeout_seconds=10)
        api = GatewayBtApi(session, exchange_name="route-a")
        one = api.trading_calendar_query("route-a", **SCOPE, trading_day=DAY.isoformat())
        windows = api.trading_calendar_windows(
            "route-a",
            **SCOPE,
            start_utc="2026-06-02T01:30:00Z",
            end_utc="2026-06-02T02:00:00Z",
            timeout_seconds=5,
        )
        for result in (one, windows):
            assert result.status == "COMPLETE", result
            assert result.binding["data_origin"] == "SYNTHETIC_MOCK"
            assert result.binding["calendar_ref"] == bound.status().calendar_ref
            assert (result.source_authentication, result.trade_authorization) == (
                "NOT_PROVEN",
                "NONE",
            )
    finally:
        if session is not None:
            session.close(timeout_seconds=10)
        stop.set()
        thread.join(15)
        assert not thread.is_alive()
