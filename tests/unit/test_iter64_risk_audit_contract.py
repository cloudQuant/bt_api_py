"""Risk Web -> real Risk adapter and temporary SQLite, with fake authorization.

The injected size checker uses the actual service reply-envelope encoder.  No
transport, Registry, Gateway, real account or external host is started here.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    encode_wire_value,
)
from bt_api_risk.web.app import AuthenticatedRiskSession, create_app
from bt_api_transport_zmq.service_v2 import service_reply_fits
from fastapi.testclient import TestClient

from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)
from tests.unit.test_iter64_risk_service_component import (
    ACCOUNT,
    DOMAIN,
    V2_POLICY_BLOCKED,
    ctx,
    operator_ctx,
)
from tests.unit.test_iter64_risk_service_component import (
    service as _service_fixture,
)
from tests.unit.test_service_registry import _next_request, _register

ORIGIN = "http://127.0.0.1:8710"
SERVICE_ID = WireIdentity(WireRole.SERVICE, "risk-service", "risk-host")


def _request():
    return WireEnvelope(
        WireHeader(
            protocol_version=WIRE_PROTOCOL_VERSION,
            kind=WireMessageKind.REQUEST,
            message_id="audit-request",
            correlation_id=None,
            identity=WireIdentity(WireRole.APPLICATION_CLIENT, "operator-1", "ops"),
            scope=WireScope(DOMAIN, account_ref=ACCOUNT),
            operation="service.call",
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc="2026-11-13T00:00:00Z",
        ),
        {"operation": "risk.audit.read"},
    )


def _fits(max_bytes):
    request = _request()

    def check(result):
        if len(encode_wire_value(result)) > 900 * 1024:
            return False
        return service_reply_fits(
            SERVICE_ID, request, {"ok": True, "code": "OK", "result": result},
            max_message_bytes=max_bytes,
        )

    return check


@pytest.fixture
def live_adapter(tmp_path):
    # Ledger is thread-bound. The single owned worker constructs, serves and
    # closes it; FastAPI's worker only forwards each fake call to that worker.
    with ThreadPoolExecutor(max_workers=1) as executor:
        def make_service():
            fixture = _service_fixture.__wrapped__(tmp_path)
            return fixture, next(fixture)

        fixture, service = executor.submit(make_service).result(timeout=10)

        def run(operation, *args):
            return executor.submit(operation, *args).result(timeout=10)

        try:
            yield service, run
        finally:
            def close_service():
                with suppress(StopIteration):
                    next(fixture)

            executor.submit(close_service).result(timeout=10)


def _append(service, count, *, size=0):
    with service._ledger.transaction() as txn:
        after = txn._c.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM risk_pretrade_audit WHERE scope_key=?", (txn._scope,)
        ).fetchone()[0]
        for index in range(count):
            txn.audit("audit_test", at_ns=index, detail={"index": index, "body": "界" * size})
        return after


def _call(service, after, limit, checker, caller=None, through=None, snapshot=None):
    source = operator_ctx() if caller is None else caller
    context = SimpleNamespace(**vars(source), result_fits=checker)
    return service.handle("risk.audit.read", context=context, arguments={"after": after, "limit": limit,
                                                                         "through": through, "snapshot": snapshot})


def _append_raw(service, detail_json):
    after = _append(service, 0)
    with service._ledger.transaction() as txn:
        txn._c.execute(
            "INSERT INTO risk_pretrade_audit "
            "(scope_key,at_ns,kind,detail_json,writer_owner_id,writer_fencing_token) "
            "VALUES (?,?,?,?,?,?)",
            (txn._scope, 1, "audit_raw", detail_json,
             txn._fence.owner_id, txn._fence.fencing_token),
        )
    return after


@pytest.fixture
def web(live_adapter):
    service, run = live_adapter
    budget = [1024 * 1024]
    session = AuthenticatedRiskSession("operator-1", "ops", "x" * 64, frozenset({"risk.audit.read"}))

    def call(operation, arguments, timeout_seconds):
        assert operation == "risk.audit.read" and timeout_seconds > 0
        return run(_call, service, arguments["after"], arguments["limit"], _fits(budget[0]),
                   None, arguments["through"], arguments["snapshot"])

    app = create_app(origin=ORIGIN, authenticate=lambda request: session, admin_caller=call)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as client:
        yield client, service, run, budget


@V2_POLICY_BLOCKED
@pytest.mark.parametrize("limit", [500, 501, 1000])
def test_documented_audit_limits_reach_real_component(web, limit):
    client, service, run, _ = web
    after = run(_append, service, 1000)
    response = client.get(f"/api/v1/risk/audit?after={after}&limit={limit}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["records"]) == limit
    assert [row["detail"]["index"] for row in body["records"]] == list(range(limit))
    assert body["next_after"] == body["records"][-1]["seq"]


@V2_POLICY_BLOCKED
def test_over_limit_is_rejected_by_web_and_component(web):
    client, service, run, _ = web
    assert client.get("/api/v1/risk/audit?limit=1001").status_code == 422
    assert run(_call, service, 0, 1001, _fits(1024 * 1024)) == {
        "status": "REQUEST_REJECTED", "code": "risk_request_invalid"}


@V2_POLICY_BLOCKED
def test_small_frame_requires_smaller_limit_and_pages_without_skipping(web):
    client, service, run, budget = web
    after = run(lambda: _append(service, 12, size=350))
    budget[0] = 4096
    refused = client.get(f"/api/v1/risk/audit?after={after}&limit=1000")
    assert refused.status_code == 409
    assert refused.json()["code"] == "risk_audit_page_too_large"
    received = []
    through = snapshot = None
    for _ in range(20):
        suffix = "" if through is None else f"&through={through}&snapshot={snapshot}"
        response = client.get(f"/api/v1/risk/audit?after={after}&limit=1{suffix}")
        assert response.status_code == 200, response.text
        page = response.json()
        records = page["records"]
        assert _fits(budget[0])(page)
        if not records:
            assert page["next_after"] == after
            break
        assert page["next_after"] == records[-1]["seq"] > after
        received.extend(row["detail"]["index"] for row in records if row["kind"] == "audit_test")
        after = page["next_after"]
        through, snapshot = page["through"], page["snapshot"]
        if not page["has_more"]:
            break
    assert received == list(range(12))


@V2_POLICY_BLOCKED
def test_web_node_budget_refuses_page_before_old_clients_can_treat_it_as_eof(web):
    client, service, run, _ = web

    def append_dense():
        with service._ledger.transaction() as txn:
            after = txn._c.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM risk_pretrade_audit WHERE scope_key=?", (txn._scope,)
            ).fetchone()[0]
            for index in range(600):
                txn.audit("audit_test", at_ns=index, detail={"index": index, "items": [1] * 400})
            return after

    after = run(append_dense)
    refused = client.get(f"/api/v1/risk/audit?after={after}&limit=1000")
    assert refused.status_code == 409
    assert refused.json()["code"] == "risk_audit_page_too_large"
    received = []
    through = snapshot = None
    for _ in range(8):
        suffix = "" if through is None else f"&through={through}&snapshot={snapshot}"
        response = client.get(f"/api/v1/risk/audit?after={after}&limit=100{suffix}")
        assert response.status_code == 200, response.text
        page = response.json()
        if not page["records"]:
            break
        received.extend(row["detail"]["index"] for row in page["records"] if row["kind"] == "audit_test")
        after = page["next_after"]
        through, snapshot = page["through"], page["snapshot"]
        if not page["has_more"]:
            break
    assert received == list(range(600))


@V2_POLICY_BLOCKED
def test_single_oversized_record_is_finite_error_without_advancing_cursor(web):
    client, service, run, budget = web
    after = run(lambda: _append(service, 1, size=2500))
    budget[0] = 4096
    for _ in range(2):
        response = client.get(f"/api/v1/risk/audit?after={after}&limit=1000")
        assert response.status_code == 409
        assert response.json() == {"status": "REQUEST_REJECTED", "code": "risk_audit_record_too_large"}


@V2_POLICY_BLOCKED
def test_oversized_record_after_fitting_prefix_is_not_skipped(web):
    client, service, run, budget = web
    after = run(_append, service, 1)
    run(lambda: _append(service, 1, size=2500))
    run(_append, service, 1)
    budget[0] = 4096
    response = client.get(f"/api/v1/risk/audit?after={after}&limit=1000")
    assert response.status_code == 409
    assert response.json()["code"] == "risk_audit_page_too_large"
    response = client.get(f"/api/v1/risk/audit?after={after}&limit=1")
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page["records"]) == 1 and page["records"][0]["detail"]["index"] == 0
    assert page["next_after"] == page["records"][-1]["seq"]
    for _ in range(2):
        blocked = client.get(f"/api/v1/risk/audit?after={page['next_after']}&limit=1000"
                             f"&through={page['through']}&snapshot={page['snapshot']}")
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "risk_audit_record_too_large"


@V2_POLICY_BLOCKED
def test_oversized_page_is_not_a_short_success_for_eof_clients(live_adapter):
    service, run = live_adapter
    after = run(_append, service, 1000)
    checks = []

    def fits(page):
        checks.append(len(page["records"]))
        return len(page["records"]) <= 319

    response = run(_call, service, after, 1000, fits)
    assert response == {"status": "REQUEST_REJECTED", "code": "risk_audit_page_too_large"}
    assert len(checks) == 2  # full page, then first record; no per-row serialization
    page = run(_call, service, after, 319, fits)
    assert len(page["records"]) == 319
    assert page["next_after"] == page["records"][-1]["seq"]
    followup = run(_call, service, page["next_after"], 319, fits, None, page["through"], page["snapshot"])
    assert [row["detail"]["index"] for row in followup["records"]] == list(range(319, 638))


@V2_POLICY_BLOCKED
def test_checker_cannot_mutate_page_or_receive_an_untrusted_success(live_adapter):
    service, run = live_adapter
    after = run(_append, service, 1)

    def malicious(page):
        page["records"][0]["seq"] = 10**6
        return True

    assert run(_call, service, after, 1000, malicious) == {
        "status": "REQUEST_REJECTED", "code": "risk_audit_size_unavailable"}
    normal = run(_call, service, after, 1000, _fits(4096))
    assert normal["records"][0]["seq"] == after + 1


@V2_POLICY_BLOCKED
@pytest.mark.parametrize("checker", [None, lambda result: None, lambda result: 1,
                                     lambda result: (_ for _ in ()).throw(RuntimeError("private"))])
def test_missing_or_failed_size_checker_is_not_treated_as_success(live_adapter, checker):
    service, run = live_adapter
    after = run(_append, service, 1)
    assert run(_call, service, after, 1000, checker) == {
        "status": "REQUEST_REJECTED", "code": "risk_audit_size_unavailable"}


@V2_POLICY_BLOCKED
def test_wrong_scope_cannot_read_audit_even_with_permissive_checker(live_adapter):
    service, run = live_adapter
    after = run(_append, service, 1)
    wrong = ctx("operator-1", role=WireRole.APPLICATION_CLIENT, strategy=None, account="other")
    assert run(_call, service, after, 1000, lambda result: True, wrong) == {
        "status": "REQUEST_REJECTED", "code": "risk_unauthorized"}


@V2_POLICY_BLOCKED
def test_wrong_identity_cannot_read_audit_or_invoke_size_checker(live_adapter):
    service, run = live_adapter
    after = run(_append, service, 1)
    wrong = ctx("operator-1", role=WireRole.STRATEGY_CLIENT, strategy="strategy-a")
    calls = []

    def checker(result):
        calls.append(result)
        return True

    assert run(_call, service, after, 1000, checker, wrong) == {
        "status": "REQUEST_REJECTED", "code": "risk_unauthorized"}
    assert calls == []


def test_web_session_without_audit_grant_never_calls_the_service():
    session = AuthenticatedRiskSession("operator-1", "ops", "x" * 64, frozenset())
    calls = []
    app = create_app(
        origin=ORIGIN,
        authenticate=lambda request: session,
        admin_caller=lambda *args: calls.append(args),
    )
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as client:
        response = client.get("/api/v1/risk/audit?limit=1000")
    assert response.status_code == 403
    assert response.json() == {"error": "OPERATION_NOT_GRANTED"}
    assert calls == []


@V2_POLICY_BLOCKED
def test_overflowing_after_is_finite_rejection_not_sqlite_overflow(live_adapter):
    service, run = live_adapter
    assert run(_call, service, 2**63, 1, _fits(4096)) == {
        "status": "REQUEST_REJECTED", "code": "risk_request_invalid"}


@V2_POLICY_BLOCKED
def test_old_engine_without_bounded_reader_fails_without_calling_legacy_reader(live_adapter):
    service, run = live_adapter

    def call_old():
        old = service._engine
        legacy_calls = []
        service._engine = SimpleNamespace(read_audit=lambda *args: legacy_calls.append(args),
                                          audit_read_rejection=lambda *_: None)
        try:
            result = _call(service, 0, 1, _fits(4096))
            return result, legacy_calls
        finally:
            service._engine = old

    result, calls = run(call_old)
    assert result == {"status": "REQUEST_REJECTED", "code": "risk_audit_size_unavailable"}
    assert calls == []


@V2_POLICY_BLOCKED
@pytest.mark.parametrize("number", [1.5, -0.0, 1e-100])
def test_finite_audit_float_survives_web_and_actual_envelope(web, number):
    client, service, run, _ = web

    def append_float():
        after = _append(service, 0)
        with service._ledger.transaction() as txn:
            txn.audit("audit_float", at_ns=1, detail={"value": number})
        return after

    after = run(append_float)
    response = client.get(f"/api/v1/risk/audit?after={after}&limit=1")
    assert response.status_code == 200, response.text
    value = response.json()["records"][0]["detail"]["value"]
    assert type(value) is float and value == number


@V2_POLICY_BLOCKED
@pytest.mark.parametrize("kind,code", [
    ("nan", "risk_audit_number_unrepresentable"),
    ("inf", "risk_audit_number_unrepresentable"),
    ("deep", "risk_audit_record_too_large"),
])
def test_unreadable_audit_detail_is_finite_refusal(web, kind, code):
    client, service, run, _ = web

    def append_corrupt():
        after = _append(service, 0)
        if kind == "nan":
            detail = {"value": float("nan")}
        elif kind == "inf":
            detail = {"value": float("inf")}
        else:
            detail = {}
            cursor = detail
            for _ in range(16):
                cursor["nested"] = {}
                cursor = cursor["nested"]
        with service._ledger.transaction() as txn:
            txn.audit("audit_corrupt", at_ns=1, detail=detail)
        return after

    after = run(append_corrupt)
    response = client.get(f"/api/v1/risk/audit?after={after}&limit=1")
    assert response.status_code == 409
    assert response.json() == {"status": "REQUEST_REJECTED", "code": code}


@V2_POLICY_BLOCKED
@pytest.mark.parametrize("number", ["1e-999", "1e309", "18446744073709551616"])
def test_valid_but_unrepresentable_json_number_has_distinct_finite_refusal(web, number):
    client, service, run, _ = web
    after = run(_append_raw, service, '{"value":' + number + "}")
    response = client.get(f"/api/v1/risk/audit?after={after}&limit=1")
    assert response.status_code == 409
    assert response.json() == {
        "status": "REQUEST_REJECTED", "code": "risk_audit_number_unrepresentable"}


@V2_POLICY_BLOCKED
def test_real_registry_size_checker_preserves_complete_pages(live_adapter, tmp_path):
    service, run = live_adapter
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, "operator-1", "ops")
    scope = WireScope(DOMAIN, account_ref=ACCOUNT)
    operations = frozenset({"risk.audit.read"})
    server_limit = [1024 * 1024]

    def make_registry(with_checker=True):
        def checker(request, payload, hint):
            effective = server_limit[0] if hint is None else min(server_limit[0], hint)
            return service_reply_fits(SERVICE_ID, request, payload, max_message_bytes=effective)

        registry = ServiceRegistry(
            tmp_path / ("registry.sqlite" if with_checker else "unbounded-registry.sqlite"),
            service_id="audit-service",
            capabilities=(ServiceCapability("risk-instance", "service.risk_admin", operations),),
            grants={"operator-1": (PrincipalGrant(identity, (ResourceGrant(
                "risk-instance", "service.risk_admin", "audit-scope", scope, operations,
            ),)),)},
            policy_version="unit-policy",
            requirements_version="unit-requirements",
            reply_size_checker=checker if with_checker else None,
        )
        registry.register_handler(
            "risk-instance", "risk.audit.read",
            lambda context, arguments: service.handle(
                "risk.audit.read", context=context, arguments=arguments
            ),
        )
        registered = _register(
            registry, identity=identity, scope=scope, principal="operator-1",
            instance_id="risk-instance", port="service.risk_admin", scope_ref="audit-scope",
            operations=("risk.audit.read",),
        )
        assert registered["ok"] is True, registered
        return registry, registered["result"]

    registry, registration = run(make_registry)

    def request(registry, registration, after, limit, hint=None, through=None, snapshot=None):
        payload = {
            "registration_id": registration["registration_id"],
            "service_epoch": registration["service_epoch"],
            "instance_id": "risk-instance",
            "arguments": {"after": after, "limit": limit, "through": through, "snapshot": snapshot},
        }
        if hint is not None:
            payload["max_reply_message_bytes"] = hint
        envelope = _next_request("risk.audit.read", payload, identity=identity, scope=scope)
        return envelope, dict(registry.handle("operator-1", envelope))

    try:
        after = run(_append, service, 1000)
        _, reply = run(request, registry, registration, after, 1000, 1024 * 1024)
        assert reply["ok"] is True and len(reply["result"]["records"]) == 1000
        _, invalid = run(request, registry, registration, after, 1001, 1024 * 1024)
        assert invalid["result"] == {"status": "REQUEST_REJECTED", "code": "risk_request_invalid"}

        # Actual Web adapter -> actual Registry -> actual RiskOrderService,
        # with only the identity, ports and SQLite data supplied as local fakes.
        session = AuthenticatedRiskSession("operator-1", "ops", "x" * 64, operations)
        web_calls = []

        def registry_admin(operation, arguments, timeout_seconds):
            assert operation == "risk.audit.read" and timeout_seconds > 0
            web_calls.append(dict(arguments))
            _, result = run(request, registry, registration, arguments["after"], arguments["limit"],
                            1024 * 1024, arguments["through"], arguments["snapshot"])
            assert result["ok"] is True
            return result["result"]

        app = create_app(origin=ORIGIN, authenticate=lambda request: session, admin_caller=registry_admin)
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50000)) as client:
            response = client.get(f"/api/v1/risk/audit?after={after}&limit=1000")
            assert response.status_code == 200 and len(response.json()["records"]) == 1000
            assert client.get("/api/v1/risk/audit?limit=1001").status_code == 422
            assert len(web_calls) == 1  # invalid limit refused before Registry
            for detail, code in (
                ('{"value":1e-999}', "risk_audit_number_unrepresentable"),
                ('{"value":', "risk_audit_corrupt"),
                ('{"value":"' + "z" * (800 * 1024) + '"}', "risk_audit_record_too_large"),
            ):
                cursor = run(_append_raw, service, detail)
                refusal = client.get(f"/api/v1/risk/audit?after={cursor}&limit=1")
                assert refusal.status_code == 409
                assert refusal.json() == {"status": "REQUEST_REJECTED", "code": code}

            cursor = run(_append_raw, service, '{"value":"' + "a" * 400_000 + '"}')
            run(_append_raw, service, '{"value":"' + "b" * 400_000 + '"}')
            refusal = client.get(f"/api/v1/risk/audit?after={cursor}&limit=2")
            assert refusal.status_code == 409
            assert refusal.json() == {"status": "REQUEST_REJECTED", "code": "risk_audit_page_too_large"}
            first = client.get(f"/api/v1/risk/audit?after={cursor}&limit=1")
            assert first.status_code == 200
            cursor = first.json()["next_after"]
            first_page = first.json()
            second = client.get(f"/api/v1/risk/audit?after={cursor}&limit=1"
                                f"&through={first_page['through']}&snapshot={first_page['snapshot']}")
            assert second.status_code == 200
            assert first.json()["records"][0]["detail"]["value"] == "a" * 400_000
            assert second.json()["records"][0]["detail"]["value"] == "b" * 400_000

            # An older installed Risk package must not silently use its old,
            # unbounded reader, even through the real Registry + Web adapter.
            previous_engine = run(lambda: service._engine)
            legacy_calls = []
            run(setattr, service, "_engine", SimpleNamespace(
                read_audit=lambda *args: legacy_calls.append(args), audit_read_rejection=lambda *_: None))
            try:
                unavailable = client.get("/api/v1/risk/audit?after=0&limit=1")
                assert unavailable.status_code == 409
                assert unavailable.json() == {
                    "status": "REQUEST_REJECTED", "code": "risk_audit_size_unavailable"}
                assert legacy_calls == []
            finally:
                run(setattr, service, "_engine", previous_engine)

            # Bounded-reader storage errors retain their finite category all
            # the way through Registry and the Web adapter.
            from bt_api_risk.pretrade import RiskAdminError

            def unavailable_reader(*args):
                raise RiskAdminError("risk_audit_unavailable")

            run(setattr, service, "_engine", SimpleNamespace(read_audit_page=unavailable_reader,
                                                             audit_read_rejection=lambda *_: None))
            try:
                unavailable = client.get("/api/v1/risk/audit?after=0&limit=1")
                assert unavailable.status_code == 409
                assert unavailable.json() == {
                    "status": "REQUEST_REJECTED", "code": "risk_audit_unavailable"}
            finally:
                run(setattr, service, "_engine", previous_engine)

        def append_float():
            cursor = _append(service, 0)
            with service._ledger.transaction() as txn:
                txn.audit("audit_float", at_ns=1, detail={"value": 1.5})
            return cursor

        float_after = run(append_float)
        envelope, float_page = run(request, registry, registration, float_after, 1, 1024 * 1024)
        assert float_page["ok"] is True
        assert float_page["result"]["records"][0]["detail"]["value"] == 1.5
        assert service_reply_fits(SERVICE_ID, envelope, float_page, max_message_bytes=1024 * 1024)

        for detail, code in (
            ('{"value":1e-999}', "risk_audit_number_unrepresentable"),
            ('{"value":', "risk_audit_corrupt"),
            ('{"value":"' + "z" * (800 * 1024) + '"}', "risk_audit_record_too_large"),
        ):
            invalid_after = run(_append_raw, service, detail)
            envelope, refusal = run(request, registry, registration, invalid_after, 1, 1024 * 1024)
            assert refusal["result"] == {"status": "REQUEST_REJECTED", "code": code}
            assert service_reply_fits(SERVICE_ID, envelope, refusal, max_message_bytes=1024 * 1024)

        def old_engine_refusal():
            old = service._engine
            calls = []
            service._engine = SimpleNamespace(read_audit=lambda *args: calls.append(args),
                                              audit_read_rejection=lambda *_: None)
            try:
                envelope, reply = request(registry, registration, 0, 1, 1024 * 1024)
                return envelope, reply, calls
            finally:
                service._engine = old

        envelope, unavailable, calls = run(old_engine_refusal)
        assert unavailable["result"] == {
            "status": "REQUEST_REJECTED", "code": "risk_audit_size_unavailable"}
        assert calls == []
        assert service_reply_fits(SERVICE_ID, envelope, unavailable, max_message_bytes=1024 * 1024)

        # A storage-budget overflow is also a whole-page refusal; retry with a
        # smaller limit consumes both complete rows in order.
        page_after = run(_append_raw, service, '{"value":"' + "a" * 400_000 + '"}')
        run(_append_raw, service, '{"value":"' + "b" * 400_000 + '"}')
        _, oversized = run(request, registry, registration, page_after, 2, 1024 * 1024)
        assert oversized["result"] == {
            "status": "REQUEST_REJECTED", "code": "risk_audit_page_too_large"}
        _, first = run(request, registry, registration, page_after, 1, 1024 * 1024)
        _, second = run(request, registry, registration, first["result"]["next_after"], 1, 1024 * 1024,
                        first["result"]["through"], first["result"]["snapshot"])
        assert first["result"]["records"][0]["detail"]["value"] == "a" * 400_000
        assert second["result"]["records"][0]["detail"]["value"] == "b" * 400_000

        after = run(lambda: _append(service, 5, size=350))
        server_limit[0] = 8192
        envelope, too_large = run(request, registry, registration, after, 1000, 4096)
        assert too_large["result"] == {"status": "REQUEST_REJECTED", "code": "risk_audit_page_too_large"}
        assert service_reply_fits(SERVICE_ID, envelope, too_large, max_message_bytes=4096)
        received = []
        through = snapshot = None
        for _ in range(10):
            envelope, page = run(request, registry, registration, after, 1, 4096, through, snapshot)
            assert page["ok"] is True
            result = page["result"]
            assert service_reply_fits(SERVICE_ID, envelope, page, max_message_bytes=4096)
            if not result["records"]:
                break
            if result["records"][0]["kind"] == "audit_test":
                received.append(result["records"][0]["detail"]["index"])
            after = result["next_after"]
            through, snapshot = result["through"], result["snapshot"]
            if not result["has_more"]:
                break
        assert received == list(range(5))

        # A server-side frame smaller than the client hint also limits the reply.
        probe_after = run(lambda: _append(service, 1, size=350))
        server_limit[0] = 1024
        envelope, too_small = run(request, registry, registration, probe_after, 1, 8192)
        assert too_small["result"] == {
            "status": "REQUEST_REJECTED", "code": "risk_audit_record_too_large"}
        assert service_reply_fits(SERVICE_ID, envelope, too_small, max_message_bytes=1024)

        # A tiny frame may be too small even for a finite error wrapper. The
        # registry can classify it but cannot guarantee a deliverable reply.
        server_limit[0] = 128
        envelope, tiny = run(request, registry, registration, probe_after, 1, 8192)
        assert tiny["ok"] is False
        assert not service_reply_fits(SERVICE_ID, envelope, tiny, max_message_bytes=128)
    finally:
        run(registry.close)

    # Registry intentionally does not promise exact frame sizing without its
    # configured checker. A requested client hint then fails before the handler.
    bare, bare_registration = run(make_registry, False)
    try:
        _, result = run(request, bare, bare_registration, 0, 1, 4096)
        assert result["ok"] is False and result["code"] == "CAPABILITY_UNAVAILABLE"
        envelope, unchecked = run(request, bare, bare_registration, 0, 1)
        assert unchecked["ok"] is True
        assert not service_reply_fits(SERVICE_ID, envelope, unchecked, max_message_bytes=128)
    finally:
        run(bare.close)
