"""Bounded market replay through the real Host builder and Registry checker."""

from __future__ import annotations

from collections import UserDict
from dataclasses import replace

from bt_api_base.service_protocol import (
    WireIdentity,
    WireScope,
)
from bt_api_gateway.market_hub import (
    MARKET_OPERATIONS,
    MARKET_OPERATIONS_WITH_BOUNDED_READ_AFTER,
    MARKET_READ_AFTER_BOUNDED_OPERATION,
)

from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost
from bt_api_py.service_registry import ServiceCallContext
from bt_api_transport_zmq.service_v2 import service_reply_fits
from tests.unit.test_service_host import (
    _CLIENT_IDENTITY,
    _endpoint,
    _market_authority,
    _market_hub_component,
    _market_resource,
    _request,
    _service_config,
)


_SERVICE_ID = "market-bounded-integration"
_INSTANCE_ID = "market-source"
_PRINCIPAL = "curve-principal"
_REPLY_CEILING = 65_536
_SENT_AT = "2026-10-21T00:00:00Z"


def _configured_host(tmp_path, *, enabled: bool):
    component = _market_hub_component()
    if enabled:
        component["settings"]["options"]["bounded_read_after"] = True
    parsed = _service_config(
        {"gateway": component},
        service_id=_SERVICE_ID,
        market_endpoint=_endpoint(),
    )
    raw = parsed.to_public_dict()
    raw["communication"]["profiles"]["listener-v1"]["options"][
        "max_message_bytes"
    ] = _REPLY_CEILING
    config = parse_service_config(raw)
    authority, grant_scope = _market_authority(config)

    if enabled:
        principal_grant = authority.grants[_PRINCIPAL][0]
        resource_grant = principal_grant.resources[0]
        expanded = replace(
            resource_grant,
            operations=MARKET_OPERATIONS_WITH_BOUNDED_READ_AFTER,
        )
        authority = replace(
            authority,
            grants={
                _PRINCIPAL: (replace(principal_grant, resources=(expanded,)),)
            },
        )

    resources, provider, key = _market_resource(authority)
    host = ServiceHost(
        config,
        authority,
        tmp_path / f"market-bounded-{enabled}.sqlite3",
        lambda *_args: resources,
    )
    scope = WireScope(
        grant_scope.execution_domain_ref,
        strategy_ref=grant_scope.strategy_ref,
        run_id="bounded-read-run",
    )
    return host, authority, resources, provider, key, scope


def _register(host, scope, operations):
    registry = host._registry
    assert registry is not None and registry.ready is True
    reply = registry.handle(
        _PRINCIPAL,
        _request(
            "service.register",
            {
                "registration_key": "bounded-market-registration",
                "client_session_id": "bounded-market-session",
                "instance_id": _INSTANCE_ID,
                "port": "service.market_source",
                "scope_ref": "market-grant-v1",
                "operations": tuple(sorted(operations)),
            },
            schema_id="sdk.service.control",
            identity=_CLIENT_IDENTITY,
            scope=scope,
        ),
    )
    assert reply["code"] == "OK"
    return reply["result"]


def _request_read(scope, registration, key, *, epoch, after, limit, reply_limit):
    return _request(
        MARKET_READ_AFTER_BOUNDED_OPERATION,
        {
            "registration_id": registration["registration_id"],
            "service_epoch": registration["service_epoch"],
            "instance_id": _INSTANCE_ID,
            "arguments": {
                "key": key.to_dict(),
                "epoch": epoch,
                "after": after,
                "limit": limit,
            },
            "max_reply_message_bytes": reply_limit,
        },
        schema_id="sdk.service.call",
        identity=_CLIENT_IDENTITY,
        scope=scope,
    )


def _reply_fits(identity: WireIdentity, request, result, budget: int) -> bool:
    return service_reply_fits(
        identity,
        request,
        {"ok": True, "code": "OK", "result": result},
        max_message_bytes=budget,
    )


def _minimum_budget(identity: WireIdentity, request, result) -> int:
    low = 128
    high = _REPLY_CEILING
    assert _reply_fits(identity, request, result, high)
    while low < high:
        middle = (low + high) // 2
        if _reply_fits(identity, request, result, middle):
            high = middle
        else:
            low = middle + 1
    return low


def _prefix(result, length: int):
    selected = tuple(result["events"][:length])
    next_after = (
        selected[-1]["header"]["sequence"] if selected else result["after"]
    )
    candidate = dict(result)
    candidate["events"] = selected
    candidate["next_after"] = next_after
    candidate["has_more"] = result["has_more"] or length < len(result["events"])
    return candidate


def test_market_bounded_read_after_remains_opt_in_by_profile(tmp_path):
    host, authority, _resources, _provider, _key, scope = _configured_host(
        tmp_path, enabled=False
    )
    try:
        host.start()
        registry = host._registry
        assert registry is not None
        component = host._components[0]
        assert component.capabilities[0].operations == MARKET_OPERATIONS
        assert set(component.handlers) == MARKET_OPERATIONS
        assert MARKET_READ_AFTER_BOUNDED_OPERATION not in component.handlers
        assert (
            MARKET_READ_AFTER_BOUNDED_OPERATION
            not in authority.grants[_PRINCIPAL][0].resources[0].operations
        )
        discovered = registry.handle(
            _PRINCIPAL,
            _request(
                "service.discover",
                {},
                schema_id="sdk.service.control",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert discovered["code"] == "OK"
        assert (
            set(discovered["result"]["capabilities"][0]["operations"])
            == MARKET_OPERATIONS
        )
    finally:
        host.close()
    assert host.state == "closed"


def test_opted_in_market_alias_fits_continuous_prefix_and_fails_single_oversize_reply(
    tmp_path,
):
    host, authority, resources, provider, key, scope = _configured_host(
        tmp_path, enabled=True
    )
    host.start()
    try:
        registry = host._registry
        assert registry is not None
        component = host._components[0]
        assert (
            component.capabilities[0].operations
            == MARKET_OPERATIONS_WITH_BOUNDED_READ_AFTER
        )
        assert MARKET_READ_AFTER_BOUNDED_OPERATION in component.handlers
        registration = _register(
            host, scope, MARKET_OPERATIONS_WITH_BOUNDED_READ_AFTER
        )
        subscribed = registry.handle(
            _PRINCIPAL,
            _request(
                "market.subscribe",
                {
                    "registration_id": registration["registration_id"],
                    "service_epoch": registration["service_epoch"],
                    "instance_id": _INSTANCE_ID,
                    "arguments": {"key": key.to_dict()},
                },
                schema_id="sdk.service.call",
                identity=_CLIENT_IDENTITY,
                scope=scope,
            ),
        )
        assert subscribed["code"] == "OK"
        hub = resources.values["hub"]
        epoch = hub.head(key)["epoch"]
        for index in range(1, 5):
            hub.publish(
                key,
                {"marker": f"雪λ🛰:{index}:" + "x" * 3_000},
                _SENT_AT,
                generation=provider.generations[0],
            )

        call_arguments = {
            "key": key.to_dict(),
            "epoch": epoch,
            "after": 0,
            "limit": 4,
        }
        sizing_request = _request_read(
            scope,
            registration,
            key,
            epoch=epoch,
            after=0,
            limit=4,
            reply_limit=_REPLY_CEILING,
        )
        sizing_context = ServiceCallContext(
            _CLIENT_IDENTITY,
            scope,
            registration["registration_id"],
            _PRINCIPAL,
            lambda _candidate: True,
        )
        full_page = component.handlers[MARKET_READ_AFTER_BOUNDED_OPERATION](
            sizing_context, call_arguments
        )
        assert len(full_page["events"]) == 4
        result_budget_checker = registry._reply_result_budget_checker
        assert callable(result_budget_checker)
        assert result_budget_checker(sizing_request, None) == result_budget_checker(
            sizing_request, _REPLY_CEILING + 1
        )

        budget_calls = []

        def counted_result_budget_checker(request, client_limit):
            budget_calls.append(client_limit)
            return result_budget_checker(request, client_limit)

        registry._reply_result_budget_checker = counted_result_budget_checker

        first_budget = _minimum_budget(
            authority.identity, sizing_request, _prefix(full_page, 1)
        )
        full_budget = _minimum_budget(authority.identity, sizing_request, full_page)
        assert first_budget < full_budget
        page_budget = (first_budget + full_budget) // 2
        expected_prefix = max(
            length
            for length in range(5)
            if _reply_fits(
                authority.identity,
                sizing_request,
                _prefix(full_page, length),
                page_budget,
            )
        )
        assert 1 <= expected_prefix < 4

        bounded_reply = registry.handle(
            _PRINCIPAL,
            _request_read(
                scope,
                registration,
                key,
                epoch=epoch,
                after=0,
                limit=4,
                reply_limit=page_budget,
            ),
        )
        assert bounded_reply["code"] == "OK"
        page = bounded_reply["result"]
        assert len(page["events"]) == expected_prefix
        assert [
            event["header"]["sequence"] for event in page["events"]
        ] == list(range(1, expected_prefix + 1))
        assert page["next_after"] == expected_prefix
        assert page["has_more"] is True
        assert budget_calls == [page_budget]

        one_event_page = _prefix(full_page, 1)
        exact_boundary_request = _request_read(
            scope,
            registration,
            key,
            epoch=epoch,
            after=0,
            limit=4,
            reply_limit=_REPLY_CEILING,
        )
        single_event_budget = _minimum_budget(
            authority.identity, exact_boundary_request, one_event_page
        )
        exact_boundary_request = replace(
            exact_boundary_request,
            payload={
                **exact_boundary_request.payload,
                "max_reply_message_bytes": single_event_budget,
            },
        )
        assert _reply_fits(
            authority.identity,
            exact_boundary_request,
            one_event_page,
            single_event_budget,
        )
        assert not _reply_fits(
            authority.identity,
            exact_boundary_request,
            one_event_page,
            single_event_budget - 1,
        )
        exact_boundary_reply = registry.handle(_PRINCIPAL, exact_boundary_request)
        assert exact_boundary_reply["code"] == "OK"
        assert len(exact_boundary_reply["result"]["events"]) == 1
        assert exact_boundary_reply["result"]["next_after"] == 1
        assert exact_boundary_reply["result"]["has_more"] is True
        assert budget_calls == [page_budget, single_event_budget]

        too_small_request = replace(
            exact_boundary_request,
            payload={
                **exact_boundary_request.payload,
                "max_reply_message_bytes": single_event_budget - 1,
            },
        )
        oversized_reply = registry.handle(
            _PRINCIPAL, too_small_request
        )
        assert oversized_reply["ok"] is False
        assert oversized_reply["code"] == "REPLY_TOO_LARGE"
        assert oversized_reply["result"] == {}
        assert budget_calls == [page_budget, single_event_budget, single_event_budget - 1]

        empty_page = _prefix(full_page, 0)
        empty_frame_budget = _minimum_budget(
            authority.identity, sizing_request, empty_page
        )
        assert _reply_fits(
            authority.identity,
            sizing_request,
            empty_page,
            empty_frame_budget,
        )
        assert not _reply_fits(
            authority.identity,
            sizing_request,
            empty_page,
            empty_frame_budget - 1,
        )
        empty_page_too_small_limit = empty_frame_budget - 1
        assert empty_page_too_small_limit > 0
        assert not _reply_fits(
            authority.identity,
            sizing_request,
            empty_page,
            empty_page_too_small_limit,
        )
        assert result_budget_checker(sizing_request, empty_page_too_small_limit) > 0
        registry._reply_result_budget_checker = counted_result_budget_checker
        empty_too_small_request = replace(
            sizing_request,
            payload={
                **sizing_request.payload,
                "max_reply_message_bytes": empty_page_too_small_limit,
            },
        )
        empty_too_small_reply = registry.handle(
            _PRINCIPAL, empty_too_small_request
        )
        assert empty_too_small_reply["ok"] is False
        assert empty_too_small_reply["code"] == "REPLY_TOO_LARGE"
        assert empty_too_small_reply["result"] == {}
        assert budget_calls == [
            page_budget,
            single_event_budget,
            single_event_budget - 1,
            empty_page_too_small_limit,
        ]

        empty_object_frame_budget = _minimum_budget(
            authority.identity, sizing_request, {}
        )
        negative_result_limit = empty_object_frame_budget - 3
        assert negative_result_limit > 0
        assert not _reply_fits(
            authority.identity,
            sizing_request,
            {},
            negative_result_limit,
        )
        assert result_budget_checker(sizing_request, negative_result_limit) == -1
        negative_budget_request = replace(
            sizing_request,
            payload={
                **sizing_request.payload,
                "max_reply_message_bytes": negative_result_limit,
            },
        )
        negative_budget_reply = registry.handle(
            _PRINCIPAL, negative_budget_request
        )
        assert negative_budget_reply["ok"] is False
        assert negative_budget_reply["code"] == "REPLY_TOO_LARGE"
        assert negative_budget_reply["result"] == {}
        assert budget_calls == [
            page_budget,
            single_event_budget,
            single_event_budget - 1,
            empty_page_too_small_limit,
            negative_result_limit,
        ]

        original_reply_size_checker = registry._reply_size_checker
        full_reply_checks = []

        def counted_reply_size_checker(request, payload, client_limit):
            full_reply_checks.append(client_limit)
            return original_reply_size_checker(request, payload, client_limit)

        registry._reply_size_checker = counted_reply_size_checker
        registry._reply_result_budget_checker = None
        fallback_sizing_request = _request_read(
            scope,
            registration,
            key,
            epoch=epoch,
            after=0,
            limit=4,
            reply_limit=_REPLY_CEILING,
        )
        fallback_budget = _minimum_budget(
            authority.identity, fallback_sizing_request, one_event_page
        )
        fallback_request = replace(
            fallback_sizing_request,
            payload={
                **fallback_sizing_request.payload,
                "max_reply_message_bytes": fallback_budget,
            },
        )
        fallback_reply = registry.handle(_PRINCIPAL, fallback_request)
        assert fallback_reply["code"] == "OK"
        assert len(fallback_reply["result"]["events"]) == 1
        assert fallback_reply["result"]["next_after"] == 1
        assert len(full_reply_checks) > 1
        assert full_reply_checks == [fallback_budget] * len(full_reply_checks)
        assert budget_calls == [
            page_budget,
            single_event_budget,
            single_event_budget - 1,
            empty_page_too_small_limit,
            negative_result_limit,
        ]

        def check_invalid_boolean_budget(context, _arguments):
            assert context.result_fits is not None
            context.result_fits({"marker": "雪"})
            return {"marker": "雪"}

        registry._handlers[
            (_INSTANCE_ID, MARKET_READ_AFTER_BOUNDED_OPERATION)
        ] = check_invalid_boolean_budget
        registry._reply_result_budget_checker = lambda _request, _limit: True
        invalid_budget_request = _request_read(
            scope,
            registration,
            key,
            epoch=epoch,
            after=0,
            limit=4,
            reply_limit=_REPLY_CEILING,
        )
        invalid_budget_reply = registry.handle(
            _PRINCIPAL,
            invalid_budget_request,
        )
        assert invalid_budget_reply["ok"] is False
        assert invalid_budget_reply["code"] == "HANDLER_FAILED"
        assert invalid_budget_reply["result"] == {}
    finally:
        host.close()
    assert host.state == "closed"


def test_bounded_reply_budget_is_cached_but_mutable_results_are_renormalized(
    tmp_path,
):
    host, _authority, _resources, _provider, key, scope = _configured_host(
        tmp_path, enabled=True
    )
    host.start()
    try:
        registry = host._registry
        assert registry is not None
        registration = _register(
            host, scope, MARKET_OPERATIONS_WITH_BOUNDED_READ_AFTER
        )
        original_budget_checker = registry._reply_result_budget_checker
        assert callable(original_budget_checker)
        budget_calls = []

        def counted_result_budget_checker(request, client_limit):
            budget_calls.append(client_limit)
            return original_budget_checker(request, client_limit)

        registry._reply_result_budget_checker = counted_result_budget_checker
        mutable_result = UserDict({"marker": "雪"})
        initial_fits = []

        def mutate_after_size_check(context, _arguments):
            assert context.result_fits is not None
            initial_fits.append(context.result_fits(mutable_result))
            mutable_result[1] = "invalid non-string key"
            return mutable_result

        handler_key = (_INSTANCE_ID, MARKET_READ_AFTER_BOUNDED_OPERATION)
        registry._handlers[handler_key] = mutate_after_size_check
        reply = registry.handle(
            _PRINCIPAL,
            _request_read(
                scope,
                registration,
                key,
                epoch="market-test-epoch",
                after=0,
                limit=1,
                reply_limit=_REPLY_CEILING,
            ),
        )
        assert initial_fits == [True]
        assert budget_calls == [_REPLY_CEILING]
        assert reply["ok"] is False
        assert reply["code"] == "HANDLER_FAILED"
        assert reply["result"] == {}
    finally:
        host.close()
    assert host.state == "closed"
