"""Providerless Gateway limit-rule wire values; no session or venue evidence."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType

import pytest

from bt_api_py._contracts.models import Freshness, InstrumentSpec
from bt_api_py.gateway_instrument_spec_codec import (
    decode_gateway_instrument_spec,
    encode_gateway_instrument_spec,
)


def _spec(**overrides):
    values = dict(
        exchange_name="CTP___FUTURE",
        symbol="IF2612",
        asset_type="future",
        base_currency="CNY",
        quote_currency="CNY",
        contract_type="future",
        linear=True,
        contract_value=Decimal("1.0000000000000000001"),
        contract_multiplier=Decimal("300"),
        price_tick=Decimal("0.2000"),
        quantity_step=Decimal("1"),
        min_quantity=Decimal("2"),
        max_quantity=Decimal("20"),
        min_notional=Decimal("0.000"),
        quantity_unit="contracts",
        status="trading",
        freshness=Freshness("synthetic", datetime(2026, 11, 9, 8, 30, 11, 123456, tzinfo=UTC)),
        raw_rule_fingerprint="a" * 64,
        source="synthetic",
        raw={},
    )
    values.update(overrides)
    return InstrumentSpec(**values)


def _decode(payload, **expected):
    return decode_gateway_instrument_spec(
        payload,
        expected_exchange_name=expected.get("exchange", "CTP___FUTURE"),
        expected_symbol=expected.get("symbol", "IF2612"),
    )


def _set(payload, path, value):
    node = payload
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return payload


def test_limit_roundtrip_preserves_precision_rules_and_original_freshness():
    original = _spec()
    wire = encode_gateway_instrument_spec(original)
    assert set(wire) == {"schema_version", "order_type", "spec"}
    assert set(wire["spec"]) == {field.name for field in fields(InstrumentSpec)} - {"raw"}
    assert wire["schema_version"] == 1 and wire["order_type"] == "limit"
    assert wire["spec"]["price_tick"] == "0.2000"
    assert wire["spec"]["contract_value"] == "1.0000000000000000001"
    assert wire["spec"]["min_quantity"] == "2" and wire["spec"]["min_notional"] == "0.000"
    assert wire["spec"]["freshness"] == {
        "source": "synthetic",
        "observed_at": "2026-11-09T08:30:11.123456Z",
        "stale": False,
        "stale_reason": None,
    }
    # Wire is JSON, with no hidden Decimal, datetime, raw metadata or provider objects.
    assert json.loads(json.dumps(wire, allow_nan=False)) == wire
    result = _decode(wire)
    assert result == original and result.raw == {}
    assert result.price_tick.as_tuple() == original.price_tick.as_tuple()
    assert result.contract_value.as_tuple() == original.contract_value.as_tuple()
    assert result.min_notional.as_tuple() == original.min_notional.as_tuple()
    with pytest.raises(TypeError):
        result.raw["private"] = "forbidden"
    assert result.min_quantity == Decimal("2")


def test_unavailable_exact_existing_bt_api_shape_roundtrips_without_inventing_fields():
    unavailable = _spec(
        asset_type="future",
        base_currency="",
        quote_currency="",
        contract_type="unknown",
        linear=True,
        contract_value=None,
        contract_multiplier=None,
        price_tick=None,
        quantity_step=None,
        min_quantity=None,
        max_quantity=None,
        min_notional=None,
        quantity_unit="native",
        status="unknown",
        freshness=Freshness(
            "unavailable", datetime(2026, 11, 9, tzinfo=UTC), True, "instrument_rules_unavailable"
        ),
        raw_rule_fingerprint="",
        source="unavailable",
        available=False,
        unavailable_reason="instrument_rules_unavailable",
        raw={},
    )
    wire = encode_gateway_instrument_spec(unavailable)
    result = _decode(wire)
    assert result == unavailable
    assert all(
        wire["spec"][name] is None
        for name in (
            "contract_value",
            "contract_multiplier",
            "price_tick",
            "quantity_step",
            "min_quantity",
            "max_quantity",
            "min_notional",
        )
    )
    assert wire["spec"]["base_currency"] == wire["spec"]["raw_rule_fingerprint"] == ""
    with pytest.raises(ValueError, match="instrument_spec_unavailable"):
        result.quantize_quantity(Decimal("2"))


def test_disabled_status_remains_disabled_and_utc_conversion_preserves_instant():
    observed = datetime(2026, 11, 9, 16, 30, tzinfo=timezone(timedelta(hours=8)))
    original = _spec(status="halted", freshness=Freshness("cache", observed, True, "stale-cache"))
    wire = encode_gateway_instrument_spec(original)
    assert wire["spec"]["freshness"]["observed_at"] == "2026-11-09T08:30:00Z"
    result = _decode(wire)
    assert result.status == "halted" and result.available is True
    assert result.freshness.observed_at == observed and result.freshness.observed_at.tzinfo == UTC
    assert result.freshness.stale is True and result.freshness.stale_reason == "stale-cache"
    with pytest.raises(ValueError, match="instrument_not_tradable"):
        result.quantize_quantity(Decimal("2"))


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), True),
        (("schema_version",), 2),
        (("order_type",), "market"),
        (("order_type",), None),
        (("spec", "linear"), 1),
        (("spec", "available"), 1),
        (("spec", "exchange_name"), ""),
        (("spec", "symbol"), " IF2612"),
        (("spec", "asset_type"), ""),
        (("spec", "base_currency"), ""),
        (("spec", "quote_currency"), ""),
        (("spec", "contract_type"), ""),
        (("spec", "status"), ""),
        (("spec", "source"), ""),
        (("spec", "quantity_unit"), "contract"),
        (("spec", "raw_rule_fingerprint"), "A" * 64),
        (("spec", "raw_rule_fingerprint"), "a" * 63),
        (("spec", "unavailable_reason"), "unexpected"),
        (("spec", "freshness", "source"), ""),
        (("spec", "freshness", "stale"), 0),
        (("spec", "freshness", "stale_reason"), ""),
        (("spec", "freshness", "observed_at"), "2026-11-09T08:30:11"),
        (("spec", "freshness", "observed_at"), "2026-11-09T08:30:11+01:00"),
        (("spec", "freshness", "observed_at"), "2026-13-09T08:30:11Z"),
        (("spec", "freshness", "observed_at"), "x" * 65537),
    ],
)
def test_rejects_invalid_or_non_exact_wire_field(path, value):
    wire = _set(encode_gateway_instrument_spec(_spec()), path, value)
    with pytest.raises(ValueError, match="INVALID_GATEWAY_INSTRUMENT_SPEC"):
        _decode(wire)


@pytest.mark.parametrize(
    "value",
    [
        True,
        1,
        1.2,
        "NaN",
        "Infinity",
        "-Infinity",
        "0",
        "-0.1",
        "1e101",
        "1e-101",
        "1" * 65,
        "1e9999999",
        "0.1 ",
        "+1",
        "01",
        "1_0",
    ],
)
def test_rejects_nonpositive_nonfinite_or_unbounded_price(value):
    wire = _set(encode_gateway_instrument_spec(_spec()), ("spec", "price_tick"), value)
    with pytest.raises(ValueError):
        _decode(wire)


@pytest.mark.parametrize(
    "change",
    [
        lambda w: w.update(extra="x"),
        lambda w: w["spec"].update(raw={}),
        lambda w: w["spec"].pop("source"),
        lambda w: w["spec"]["freshness"].update(extra="x"),
        lambda w: w["spec"]["freshness"].pop("source"),
        lambda w: w["spec"].update(freshness=[]),
        lambda w: w["spec"].update(raw_rule_fingerprint="a" * 65536),
        lambda w: w.update(extra={"nested": [{"too_large": "x" * 65536}]}),
    ],
)
def test_rejects_unknown_missing_oversize_or_nested_shape(change):
    wire = encode_gateway_instrument_spec(_spec())
    change(wire)
    with pytest.raises(ValueError):
        _decode(wire)


def test_route_symbol_and_numeric_invariants_fail_closed():
    wire = encode_gateway_instrument_spec(_spec())
    with pytest.raises(ValueError):
        _decode(wire, exchange="OTHER")
    with pytest.raises(ValueError):
        _decode(wire, symbol="OTHER")
    with pytest.raises(ValueError):
        _decode(wire, exchange="")
    for field, value in (("min_quantity", None), ("max_quantity", "1"), ("min_notional", "-0.01")):
        invalid = copy.deepcopy(wire)
        invalid["spec"][field] = value
        with pytest.raises(ValueError):
            _decode(invalid)
    invalid = copy.deepcopy(wire)
    invalid["spec"].update(available=False, unavailable_reason=None)
    with pytest.raises(ValueError):
        _decode(invalid)


def test_encode_rejects_raw_and_dto_fields_not_validated_by_model():
    with pytest.raises(ValueError):
        encode_gateway_instrument_spec(_spec(raw={"provider_row": "never serialize"}))
    with pytest.raises(ValueError):
        encode_gateway_instrument_spec(_spec(freshness=Freshness("cache", datetime(2026, 11, 9))))
    with pytest.raises(ValueError):
        encode_gateway_instrument_spec(_spec(min_quantity=Decimal("3"), max_quantity=Decimal("2")))
    for field, value in (("linear", 1), ("available", 1), ("source", ""), ("status", "")):
        invalid = _spec()
        object.__setattr__(invalid, field, value)
        with pytest.raises(ValueError):
            encode_gateway_instrument_spec(invalid)
    for value in (Decimal("1e101"), Decimal("1e-101"), Decimal("1" * 65)):
        with pytest.raises(ValueError):
            encode_gateway_instrument_spec(_spec(price_tick=value))


def test_immutable_rpc_mappings_are_accepted_without_retaining_mutable_payload():
    wire = encode_gateway_instrument_spec(_spec())
    readonly = MappingProxyType(
        {
            **wire,
            "spec": MappingProxyType(
                {
                    **wire["spec"],
                    "freshness": MappingProxyType(wire["spec"]["freshness"]),
                }
            ),
        }
    )
    decoded = _decode(readonly)
    wire["spec"]["price_tick"] = "9"
    assert decoded.price_tick == Decimal("0.2000")
    assert decoded.freshness.source == "synthetic"


def test_codec_imports_with_all_providers_blocked():
    script = r"""
import builtins, sys
old_import = builtins.__import__
def checked(name, *args, **kwargs):
    if name == "bt_api_ctp" or name.startswith("bt_api_ctp.") or name.startswith("bt_api_okx") or name.startswith("bt_api_binance"):
        raise AssertionError("provider import is forbidden")
    return old_import(name, *args, **kwargs)
builtins.__import__ = checked
from bt_api_py.gateway_instrument_spec_codec import encode_gateway_instrument_spec, decode_gateway_instrument_spec
blocked = ("bt_api_ctp", "bt_api_okx", "bt_api_binance", "vnpy_ctp")
assert not any(any(name == prefix or name.startswith(prefix + ".") for prefix in blocked) for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15, check=False
    )
    assert result.returncode == 0, result.stderr


def test_rejects_mapping_keys_that_only_compare_equal_to_strings():
    class TextSubclass(str):
        pass

    wire = encode_gateway_instrument_spec(_spec())
    malformed = {TextSubclass(key): value for key, value in wire.items()}
    with pytest.raises(ValueError):
        _decode(malformed)


@pytest.mark.parametrize(
    "field",
    [
        "contract_value",
        "contract_multiplier",
        "price_tick",
        "quantity_step",
        "min_quantity",
        "max_quantity",
        "min_notional",
    ],
)
def test_every_numeric_field_rejects_boolean_without_coercion(field):
    wire = encode_gateway_instrument_spec(_spec())
    wire["spec"][field] = True
    with pytest.raises(ValueError):
        _decode(wire)


def test_optional_numeric_rules_remain_unavailable_instead_of_being_invented():
    original = _spec(max_quantity=None, min_notional=None)
    decoded = _decode(encode_gateway_instrument_spec(original))
    assert decoded == original and decoded.max_quantity is decoded.min_notional is None


def test_unavailable_may_preserve_empty_optional_metadata():
    original = _spec(
        available=False,
        unavailable_reason="missing_rules",
        asset_type="",
        base_currency="",
        quote_currency="",
        contract_type="",
        status="",
        raw_rule_fingerprint="",
        contract_value=None,
        contract_multiplier=None,
        price_tick=None,
        quantity_step=None,
        min_quantity=None,
    )
    decoded = _decode(encode_gateway_instrument_spec(original))
    assert decoded == original and not decoded.available
    assert decoded.asset_type == decoded.base_currency == decoded.quote_currency == ""
    assert decoded.contract_type == decoded.status == decoded.raw_rule_fingerprint == ""
