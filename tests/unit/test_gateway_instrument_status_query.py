"""Strict diagnostic wire tests: no account, network or venue observation."""

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_gateway.instrument_status_forwarding import GatewayInstrumentStatusProjection

from bt_api_py.gateway_instrument_status_query import (
    OPERATION,
    OPERATIONS,
    decode_query,
    encode_observation,
    validate_response,
)

KEY = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "neutral-id", "IF2612")
PROJECTION = GatewayInstrumentStatusProjection(
    venue="CFFEX",
    instrument="IF2612",
    session_generation="session-1",
    trading_day="20261116",
    state="CURRENT",
    status_category="continuous",
    stale=False,
    usable=True,
    source_sequence=5,
    received_at=123456,
    receive_clock_domain="monotonic_ns",
    reason_code="instrument_status_continuous",
)


def observed():
    return encode_observation(
        instrument=KEY,
        registration_id="registration",
        service_epoch="epoch",
        owner_generation="owner-1",
        projection=PROJECTION,
    )


def test_exact_request_and_detached_projection():
    assert OPERATION == "direct.instrument_status" and frozenset({OPERATION}) == OPERATIONS
    assert decode_query(MappingProxyType({"instrument": MappingProxyType(KEY.to_dict())})) == KEY
    value = observed()
    copied = validate_response(
        value,
        expected_instrument=KEY,
        expected_registration_id="registration",
        expected_service_epoch="epoch",
    )
    value["projection"]["venue"] = "changed"
    value["instrument"]["venue"] = "changed"
    assert copied["projection"]["venue"] == copied["instrument"]["venue"] == "CFFEX"
    assert (
        copied["decision_eligible"]
        is copied["continuity_proven"]
        is copied["authorizes_trading"]
        is False
    )
    assert copied["projection"]["usable"] is True and copied["product_mapping"] == "NOT_PROVEN"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra=True),
        lambda d: d.update(decision_eligible=True),
        lambda d: d.update(continuity_proven=0),
        lambda d: d.update(authorizes_trading=True),
        lambda d: d.update(product_mapping="PROVEN"),
        lambda d: d.update(registration_id=""),
        lambda d: d.update(owner_generation="x" * 10000),
        lambda d: d["projection"].update(venue="OTHER"),
        lambda d: d["projection"].update(instrument="other"),
        lambda d: d["projection"].update(usable=False),
        lambda d: d["projection"].update(source_sequence=True),
        lambda d: d["projection"].update(source_sequence=2**63),
        lambda d: d["projection"].update(received_at=2**63),
        lambda d: d["projection"].update(schema="invalid"),
        lambda d: d["projection"].update(reason_code="private exception /tmp/file"),
        lambda d: d["projection"].update(extra="value"),
    ],
)
def test_invalid_projection_and_envelope_rejected(change):
    value = deepcopy(observed())
    change(value)
    with pytest.raises(ValueError, match="INVALID_INSTRUMENT_STATUS_RESPONSE"):
        validate_response(value)


def test_expected_identity_and_rejections_are_finite():
    for kw in (
        {"expected_instrument": replace(KEY, venue="SHFE")},
        {"expected_registration_id": "other"},
        {"expected_service_epoch": "other"},
    ):
        with pytest.raises(ValueError):
            validate_response(observed(), **kw)
    for code in ("SCOPE_DENIED", "INVALID_REQUEST", "INSTRUMENT_STATUS_UNAVAILABLE"):
        assert validate_response({"status": "REQUEST_REJECTED", "code": code}) == {
            "status": "REQUEST_REJECTED",
            "code": code,
        }
    for invalid in (
        {"status": "REQUEST_REJECTED", "code": "path /secret"},
        {"status": "REQUEST_REJECTED", "code": "SCOPE_DENIED", "projection": {}},
    ):
        with pytest.raises(ValueError):
            validate_response(invalid)
    for invalid in (
        {"instrument": KEY.to_dict(), "extra": True},
        {"instrument": {**KEY.to_dict(), "native_symbol": "X" * 10000}},
    ):
        with pytest.raises(ValueError):
            decode_query(invalid)


def test_absent_projection_is_an_observation_not_trading_authority():
    projection = replace(
        PROJECTION,
        state="ABSENT",
        status_category="unknown",
        usable=False,
        source_sequence=None,
        received_at=None,
        receive_clock_domain=None,
        reason_code="instrument_status_absent",
    )
    value = encode_observation(
        instrument=KEY,
        registration_id="r",
        service_epoch="e",
        owner_generation="g",
        projection=projection,
    )
    assert value["status"] == "OBSERVED" and value["projection"]["state"] == "ABSENT"
    assert (
        value["decision_eligible"]
        is value["continuity_proven"]
        is value["authorizes_trading"]
        is False
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"session_generation": None},
        {"trading_day": None},
        {"reason_code": "instrument_status_closed"},
        {"observed_status_category": "closed"},
    ],
)
def test_current_inconsistent_projection_refused_by_encode_decode_and_typed(changes):
    from bt_api_py.gateway_strategy_instrument_status import GatewayInstrumentStatusQueryResult

    invalid = replace(PROJECTION, **changes)
    with pytest.raises(ValueError):
        encode_observation(
            instrument=KEY,
            registration_id="r",
            service_epoch="e",
            owner_generation="g",
            projection=invalid,
        )
    wire = observed()
    wire["projection"].update(changes)
    with pytest.raises(ValueError):
        validate_response(wire)
    with pytest.raises(ValueError):
        GatewayInstrumentStatusQueryResult.from_wire(wire, instrument=KEY)


@pytest.mark.parametrize("observed_category", [None, "continuous"])
def test_current_matching_projection_is_valid(observed_category):
    from bt_api_py.gateway_strategy_instrument_status import GatewayInstrumentStatusQueryResult

    projection = replace(PROJECTION, observed_status_category=observed_category)
    wire = encode_observation(
        instrument=KEY,
        registration_id="r",
        service_epoch="e",
        owner_generation="g",
        projection=projection,
    )
    assert validate_response(wire)["projection"]["observed_status_category"] == observed_category
    assert (
        GatewayInstrumentStatusQueryResult.from_wire(wire, instrument=KEY).projection == projection
    )


@pytest.mark.parametrize("field,value", [("session_generation", ""), ("trading_day", "20260231")])
def test_current_malformed_identity_is_revalidated(field, value):
    from bt_api_py.gateway_strategy_instrument_status import GatewayInstrumentStatusQueryResult

    invalid = replace(PROJECTION)
    object.__setattr__(invalid, field, value)
    with pytest.raises(ValueError):
        encode_observation(
            instrument=KEY,
            registration_id="r",
            service_epoch="e",
            owner_generation="g",
            projection=invalid,
        )
    wire = observed()
    wire["projection"][field] = value
    with pytest.raises(ValueError):
        validate_response(wire)
    with pytest.raises(ValueError):
        GatewayInstrumentStatusQueryResult.from_wire(wire, instrument=KEY)
