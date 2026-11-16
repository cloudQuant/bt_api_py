"""Local CURVE/Registry/CTP status observation; native CTP is synthetic."""

import queue
import threading
import time
from dataclasses import replace

import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope

from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_host import ServiceHost
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A,
    DOMAIN,
    SERVICE,
    _client_config,
)
from tests.unit.test_gateway_instrument_status_service import KEY, actual_owner  # noqa: F401
from tests.unit.test_iter63_gateway_strategy_client import Host


def _with_host(host, fn):
    result = queue.Queue()

    def run():
        try:
            result.put((True, fn()))
        except BaseException as error:
            result.put((False, error))

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 8
    while thread.is_alive() and time.monotonic() < deadline:
        host.serve_once(10)
    thread.join(1)
    assert not thread.is_alive()
    ok, value = result.get_nowait()
    if not ok:
        raise value
    return value


def test_real_curve_registry_and_shared_runtime_status(request, tmp_path):
    owner_fixture = request.getfixturevalue("actual_owner")
    from test_iter67_instrument_status_ingress import native

    resources, feeds, natives, config, authority = owner_fixture
    binding = resources.values["binding"]
    client_public, client_secret = zmq.curve_keypair()
    authority = replace(authority, principals={client_public: CLIENT_A.principal_ref})
    host = ServiceHost(config, authority, tmp_path / "host-registry.sqlite", lambda *_: resources)
    endpoint = config.communication["profiles"]["server-transport"]["endpoints"]["rpc_bind"]
    raw = _client_config(endpoint, CLIENT_A).to_public_dict()
    raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = binding.account_scope
    scope = WireScope(
        DOMAIN, account_ref=binding.account_scope, strategy_ref=CLIENT_A.actor_ref, run_id="run"
    )
    route = GatewayClientRoute(
        "broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, CLIENT_A
    )
    session = GatewayStrategyClientSession(
        parse_client_config(raw),
        binding=GatewayCommandBinding(
            binding._journal.journal_id,
            binding.account_scope,
            CLIENT_A.actor_ref,
            CLIENT_A.principal_ref,
        ),
        route=route,
        peer=SERVICE,
        security_profiles={
            "security-a": ClientCurveKeys(authority.curve_public_key, client_public, client_secret)
        },
        host=Host(),
        requested_operations=frozenset({"direct.instrument_status"}),
        strict_cleanup=True,
    )
    api = GatewayBtApi(session, exchange_name="CTP___FUTURE")
    host.start()
    try:
        _with_host(host, lambda: session.start(timeout_seconds=5))
        # Bridge creation deliberately refuses its first observation; an
        # explicitly repeated read occurs only after that confirmed response.
        first = _with_host(host, lambda: api.query_instrument_status("CTP___FUTURE", KEY))
        assert first.status == "UNAVAILABLE"
        binding._owner.submit_service_call(
            lambda: feeds[0].trader_client._spi.OnRtnInstrumentStatus(
                native("2", ExchangeInstID="rb", InstrumentID="rb")
            )
        ).result(timeout_seconds=1)
        result = _with_host(host, lambda: api.query_instrument_status("CTP___FUTURE", KEY))
        assert result.status == "OBSERVED" and result.projection.status_category == "continuous"
        assert (
            result.decision_eligible
            is result.continuity_proven
            is result.authorizes_trading
            is False
        )
        assert result.product_mapping == "NOT_PROVEN"
        denied = _with_host(
            host, lambda: api.query_instrument_status("CTP___FUTURE", replace(KEY, venue="DCE"))
        )
        assert denied.status == "UNAVAILABLE" and denied.code == "SCOPE_DENIED"
        assert set(natives[0].calls) <= {"query_settlement"}
    finally:
        try:
            closed = _with_host(host, lambda: session.close(timeout_seconds=5))
            assert closed.state == "closed" and not closed.thread_alive
            assert closed.strict_unregister_ack is True
            assert closed.owner_cleanup_uncertainties == 0 and runtime_cleanup_confirmed(closed)
        finally:
            host.close()
