"""Risk Web -> SDK service -> engine using only in-memory synthetic resources.

Requires the Risk Web strategy-cancel contract. Calls route endpoints directly;
this does not exercise HTTP middleware, a real Registry, Gateway, or account.
"""

import asyncio
import json

import bt_api_risk.web.app as web
import pytest
from fastapi.routing import APIRoute
from starlette.requests import Request

from bt_api_py.risk_service_gateway import GatewayControlAdapter
from tests.unit.test_iter64_operator_cancel_grants_fake import context, principal, setup_service

ORIGIN = "http://127.0.0.1:8710"
CSRF = "c" * 64
OPS = frozenset({"risk.cancel.request", "risk.cancel.status"})


def _request(method, path, *, body=None, operation_id=None, csrf=CSRF):
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    scope = {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"x-csrf-token", csrf.encode("ascii"))],
        "server": ("127.0.0.1", 8710), "client": ("127.0.0.1", 1234), "root_path": "",
        "path_params": {} if operation_id is None else {"operation_id": operation_id},
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(scope, receive)


def _call(app, method, route_path, *, body=None, operation_id=None, csrf=CSRF):
    path = web.API_PREFIX + route_path
    route = next(route for route in app.routes if isinstance(route, APIRoute)
                 and route.path == path and method in route.methods)
    response = asyncio.run(route.endpoint(_request(method, path, body=body,
                                                   operation_id=operation_id, csrf=csrf)))
    return response.status_code, json.loads(response.body)


class ForbiddenRuntime:
    def status(self):
        pytest.fail("unsupported strategy must not access the Gateway runtime")

    def call(self, *_args, **_kwargs):
        pytest.fail("unsupported strategy must not send to the Gateway")


def test_web_strategy_cancel_uses_risk_grants_and_keeps_unavailable_outcome_unknown(monkeypatch):
    async def invoke_without_threadpool(function, *args):
        return function(*args)

    monkeypatch.setattr(web, "run_in_threadpool", invoke_without_threadpool)
    principals = {"p-a": principal(), "p-b": principal("p-b")}
    with setup_service(principals) as (service, engine, txn, forwarded, reads):
        del engine._apply_cancel
        engine._control = GatewayControlAdapter(
            ForbiddenRuntime(), mount="synthetic", service_id="gateway", instance_id="instance",
            scope_ref="scope", account_ref="account", timeout_seconds=1,
        )

        def app_for(name):
            session = web.AuthenticatedRiskSession(name, "actor-" + name, CSRF, OPS)

            def admin(operation, arguments, _timeout):
                return service.handle(operation, context=context(name), arguments=arguments)

            return web.create_app(origin=ORIGIN, authenticate=lambda _: session, admin_caller=admin)

        app_a, app_b = app_for("p-a"), app_for("p-b")
        request_a = {"operation_id": "operation-a", "scope_kind": "strategy", "scope_value": "A"}
        code, reply = _call(app_a, "POST", "/cancel", body=request_a)
        assert (code, reply) == (200, {"status": "OBSERVED", "operation_id": "operation-a",
                                      "state": "REJECTED"})
        assert txn.cancel_intent("operation-a")["last_code"] == "RISK_CONTROL_SCOPE_UNSUPPORTED"

        request_b = {"operation_id": "operation-b", "scope_kind": "strategy", "scope_value": "B"}
        code, reply = _call(app_a, "POST", "/cancel", body=request_b)
        assert (code, reply) == (409, {"status": "REQUEST_REJECTED", "code": "risk_unauthorized"})
        assert txn.cancel_intent("operation-b") is None

        code, reply = _call(app_a, "GET", "/cancel/{operation_id}", operation_id="operation-a")
        assert (code, reply) == (200, {"status": "OBSERVED", "operation_id": "operation-a",
                                      "state": "UNKNOWN", "gateway": None})
        code, reply = _call(app_b, "GET", "/cancel/{operation_id}", operation_id="operation-a")
        assert (code, reply) == (404, {"status": "NOT_FOUND", "operation_id": "operation-a"})
        assert not forwarded and not reads
