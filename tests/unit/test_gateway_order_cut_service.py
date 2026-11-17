"""Order-cut scope and codec tests use synthetic public inputs."""

import hashlib
import json

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_order_cut_codec import (
    CURRENT,
    QUERY_PAGE,
    RECEIPT_PAGE,
    canonical,
    decode_query,
    validate_response,
)
from bt_api_py.gateway_order_cut_service import GatewayOrderCutService
from bt_api_py.service_registry import ServiceCallContext


@pytest.fixture
def service():
    result = object.__new__(GatewayOrderCutService)
    result._principals = frozenset({"reader"})
    result._domain, result._account_scope = "domain", "account"
    return result


@pytest.mark.parametrize(
    "identity,scope",
    [
        (
            WireIdentity(WireRole.STRATEGY_CLIENT, "reader", "strategy"),
            WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run"),
        ),
        (
            WireIdentity(WireRole.SERVICE, "other", "actor"),
            WireScope("domain", account_ref="account"),
        ),
        (
            WireIdentity(WireRole.SERVICE, "reader", "actor"),
            WireScope("domain", account_ref="other"),
        ),
        (
            WireIdentity(WireRole.SERVICE, "reader", "actor"),
            WireScope("domain", account_ref="account", strategy_ref="x", run_id="run"),
        ),
        (
            WireIdentity(WireRole.SERVICE, "reader", "actor", delegate_ref="delegate"),
            WireScope("domain", account_ref="account"),
        ),
    ],
)
def test_scope_denial_happens_before_owner_or_journal(service, identity, scope):
    context = ServiceCallContext(identity, scope, "reg", identity.principal_ref)
    assert service.handle(CURRENT, context, {}) == {
        "status": "REQUEST_REJECTED",
        "code": "SCOPE_DENIED",
    }


@pytest.mark.parametrize(
    "operation,args",
    [
        (CURRENT, {"extra": 1}),
        (
            QUERY_PAGE,
            {
                "cut_ref": "a" * 64,
                "after_ordinal": 0,
                "limit": 257,
                "expected_manifest_digest": None,
            },
        ),
        (
            QUERY_PAGE,
            {
                "cut_ref": "a" * 64,
                "after_ordinal": True,
                "limit": 1,
                "expected_manifest_digest": None,
            },
        ),
        (
            RECEIPT_PAGE,
            {
                "cut_ref": "a" * 64,
                "after_source_sequence": -1,
                "limit": 1,
                "expected_manifest_digest": None,
            },
        ),
    ],
)
def test_injected_or_unbounded_parameters_rejected(operation, args):
    with pytest.raises(ValueError):
        decode_query(operation, args)


def _current_wire():
    header = {
        "journal_id": "journal",
        "owner_generation": "generation",
        "fact_epoch": "a" * 64,
        "fact_watermark": 0,
        "source_binding_ref": "b" * 64,
        "current_session_trading_day": "20300101",
        "base_sequence": 0,
        "begin_sequence": 0,
        "end_sequence": 0,
        "source_prefix": 0,
        "query_digest": "c" * 64,
        "query_scope": "ctp.futures.account_orders.v1",
        "query_row_count": 0,
        "cut_record_digest": "d" * 64,
        "query_fact_digests_digest": "e" * 64,
        "source_history_has_gap": True,
    }
    query_manifest = "f" * 64
    ref = hashlib.sha256(
        json.dumps(
            ["sdk.gateway.order_cut.ref/1", header, query_manifest],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
    return {
        "status": "OBSERVED",
        "schema": "sdk.gateway.order_cut.current/1",
        "registration_id": "reg",
        "service_epoch": "epoch",
        "cut_ref": ref,
        "header": header,
        "query_manifest_digest": query_manifest,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(extra=1),
        lambda v: v.update(cut_ref="0" * 64),
        lambda v: v["header"].update(source_history_has_gap=1),
        lambda v: v["header"].update(current_session_trading_day="20301399"),
        lambda v: v["header"].update(query_row_count=True),
        lambda v: v.update(service_epoch="other"),
    ],
)
def test_bad_response_never_becomes_issued(mutate):
    wire = _current_wire()
    assert validate_response(
        CURRENT,
        wire,
        arguments={},
        account_ref="account",
        registration_id="reg",
        service_epoch="epoch",
    ) == canonical(wire)
    mutate(wire)
    with pytest.raises((ValueError, TypeError)):
        validate_response(
            CURRENT,
            wire,
            arguments={},
            account_ref="account",
            registration_id="reg",
            service_epoch="epoch",
        )


def test_canonical_response_size_is_bounded():
    with pytest.raises(ValueError, match="ORDER_CUT_TOO_LARGE"):
        canonical({"value": "x" * (1024 * 1024)})
