"""Public persistence contracts shared by Gateway client and host."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding, GatewayCommandRecord
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.gateway_direct_client import (
    GatewayClientRoute,
    GatewayReleasePlan,
    build_gateway_order_intent,
    gateway_intent_from_mapping,
    gateway_intent_to_mapping,
    gateway_release_from_mapping,
    gateway_release_to_mapping,
    validate_gateway_record_progress,
)
from tests.unit.test_iter63_gateway_direct_client import _wire_record


@pytest.fixture
def intent():
    binding = GatewayCommandBinding("journal-a", "account-a", "strategy-a", "principal-a")
    route = GatewayClientRoute(
        "broker.direct_write",
        "service-a",
        "instance-a",
        "scope-a",
        WireScope("paper:demo", account_ref="account-a", strategy_ref="strategy-a", run_id="run-a"),
        WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a"),
    )
    request = OrderRequest(
        symbol="rb",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        account_id="account-a",
        client_order_id="client-a",
        price=Decimal("10"),
        idempotency_key="key-a",
    )
    return build_gateway_order_intent(binding, route, request)


def test_intent_roundtrip_is_detached_and_pins_binding(intent):
    raw = gateway_intent_to_mapping(intent)
    assert gateway_intent_from_mapping(raw, expected_binding=intent.binding) == intent
    raw["route"]["scope"]["run_id"] = "run-b"
    assert intent.route.scope.run_id == "run-a"
    assert gateway_intent_from_mapping(raw).route.scope.run_id == "run-b"
    with pytest.raises(ValueError):
        gateway_intent_from_mapping(
            raw, expected_binding=replace(intent.binding, journal_id="other")
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "bool_version",
        "missing",
        "extra",
        "scope_extra",
        "identity_extra",
        "bad_role",
        "strategy",
        "principal",
        "actor",
        "account",
        "digest",
        "duplicate_payload",
        "noncanonical",
        "ttl_nan",
        "ttl_infinity",
        "ttl_boolean",
        "ttl_zero",
        "ttl_large",
        "key",
        "wrong_refs",
    ],
)
def test_reject_invalid_intent_mapping(intent, mutation):
    raw = deepcopy(gateway_intent_to_mapping(intent))
    if mutation == "version":
        raw["schema_version"] = 2
    elif mutation == "bool_version":
        raw["schema_version"] = True
    elif mutation == "missing":
        raw.pop("digest")
    elif mutation == "extra":
        raw["extra"] = 1
    elif mutation == "scope_extra":
        raw["route"]["scope"]["extra"] = 1
    elif mutation == "identity_extra":
        raw["route"]["identity"]["extra"] = 1
    elif mutation == "bad_role":
        raw["route"]["identity"]["role"] = "service"
    elif mutation == "strategy":
        raw["route"]["scope"]["strategy_ref"] = "other"
    elif mutation == "principal":
        raw["route"]["identity"]["principal_ref"] = "other"
    elif mutation == "actor":
        raw["route"]["identity"]["actor_ref"] = "other"
    elif mutation == "account":
        raw["binding"]["account_scope"] = "other"
    elif mutation == "digest":
        raw["digest"] = "a" * 64
    elif mutation == "duplicate_payload":
        raw["canonical_payload"] = raw["canonical_payload"][:-1] + ',"symbol":"rb"}'
    elif mutation == "noncanonical":
        raw["canonical_payload"] += " "
    elif mutation == "ttl_nan":
        raw["ttl_seconds"] = float("nan")
    elif mutation == "ttl_infinity":
        raw["ttl_seconds"] = float("inf")
    elif mutation == "ttl_boolean":
        raw["ttl_seconds"] = True
    elif mutation == "ttl_zero":
        raw["ttl_seconds"] = 0
    elif mutation == "ttl_large":
        raw["ttl_seconds"] = 31
    elif mutation == "key":
        raw["request_key"] = "different"
    else:
        raw["route"]["scope"]["account_refs"] = ()
    with pytest.raises(ValueError):
        gateway_intent_from_mapping(raw)


def test_release_roundtrip_and_validation(intent):
    plan = GatewayReleasePlan(intent.request_key, "reservation-a", intent.digest, "release-a", 1)
    raw = gateway_release_to_mapping(plan)
    assert gateway_release_from_mapping(raw) == plan
    for key, value in (
        ("schema_version", True),
        ("request_key", ""),
        ("digest", "A" * 64),
        ("expected_version", True),
        ("expected_version", 0),
    ):
        bad = dict(raw, **{key: value})
        with pytest.raises(ValueError):
            gateway_release_from_mapping(bad)
    with pytest.raises(ValueError):
        gateway_release_from_mapping(dict(raw, extra=True))


def test_observation_progress_dynamic_and_expired_release_augmentation(intent):
    def record(**changes):
        return GatewayCommandRecord.from_mapping(
            _wire_record(intent, **changes), expected_binding=intent.binding
        )

    first = record()
    later = record(remaining_ttl_seconds=2.0)
    validate_gateway_record_progress(None, first)
    validate_gateway_record_progress(first, later)
    expired = record(state="EXPIRED", version=2, remaining_ttl_seconds=None)
    validate_gateway_record_progress(later, expired)
    augmented = record(
        state="EXPIRED",
        version=2,
        remaining_ttl_seconds=None,
        release_operation_id="release-a",
        release_expected_version=2,
    )
    validate_gateway_record_progress(expired, augmented)
    with pytest.raises(ValueError):
        validate_gateway_record_progress(augmented, expired)
    with pytest.raises(ValueError):
        validate_gateway_record_progress(expired, later)
    with pytest.raises(ValueError):
        validate_gateway_record_progress(first, record(reservation_id="other"))


@pytest.mark.parametrize("bad", [None, 7, b"a" * 64])
def test_intent_digest_requires_exact_string(intent, bad):
    class EqualToAnything:
        def __eq__(self, other):
            return True

    class DigestSubclass(str):
        pass

    for value in (bad, EqualToAnything(), DigestSubclass(intent.digest)):
        raw = gateway_intent_to_mapping(intent)
        raw["digest"] = value
        with pytest.raises(ValueError):
            gateway_intent_from_mapping(raw)
        with pytest.raises(ValueError):
            gateway_intent_to_mapping(replace(intent, digest=value))


def test_oversized_payload_is_rejected_before_json_parse(intent, monkeypatch):
    import bt_api_py.gateway_direct_client as module

    raw = gateway_intent_to_mapping(intent)
    raw["canonical_payload"] = " " * 65537

    def unexpected(_):
        raise AssertionError("must not parse oversized input")

    monkeypatch.setattr(module.json, "loads", unexpected)
    with pytest.raises(ValueError, match="INVALID_HOST_INTENT"):
        gateway_intent_from_mapping(raw)
    raw["canonical_payload"] = "é" * 40000
    with pytest.raises(ValueError, match="INVALID_HOST_INTENT"):
        gateway_intent_from_mapping(raw)


@pytest.mark.parametrize("next_version", [2, 3])
def test_native_identity_comparison_does_not_coerce_bool_to_int(intent, next_version):
    def record(value, version):
        return GatewayCommandRecord.from_mapping(
            _wire_record(
                intent,
                state="DISPATCHING" if version == 2 else "SENT",
                version=version,
                native_identity={
                    "exchange_name": "ctp",
                    "client_order_id": "client-a",
                    "fields": {"front_id": value},
                    "session_generation": 1,
                },
                strong_identity_key_digests=["a" * 64],
            ),
            expected_binding=intent.binding,
        )

    prior = record(True, 2)
    validate_gateway_record_progress(prior, record(True, next_version))
    with pytest.raises(ValueError):
        validate_gateway_record_progress(prior, record(1, next_version))
    with pytest.raises(ValueError):
        record(1.0, next_version)
