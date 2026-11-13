"""Local cache key checks; matching keys do not establish market provenance."""

from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_risk.pretrade import PriceFact

from bt_api_py.risk_service_config import RiskInstrumentBinding
from bt_api_py.risk_service_gateway import RiskFactsCache


KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")


def _reference(key):
    return {"key": MarketSubscriptionKey("src", "ent", key, "tick").to_dict(),
            "epoch": "ep", "sequence": 1, "message_id": "m1"}


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", "other"), ("venue", "DCE"), ("environment", "live"),
     ("product_type", "spot"), ("native_symbol", "RB")],
)
def test_same_id_other_full_key_has_no_price_facts_or_market_reference(field, value):
    foreign = replace(KEY, **{field: value})
    cache = RiskFactsCache({"rb": RiskInstrumentBinding("rb", KEY, Decimal(10), Decimal(1))})
    fact = PriceFact(KEY, Decimal(100))
    ref = _reference(KEY)
    cache.note_tick("rb", fact, ref, None)

    assert cache.reference_price(KEY) is fact
    assert cache.market_ref(KEY) == ref
    assert cache.instrument_facts(KEY).instrument == KEY
    assert cache.reference_price(foreign) is None
    assert cache.market_ref(foreign) is None
    assert cache.instrument_facts(foreign) is None
    assert cache.margin_rate(foreign) is None


def test_wrong_cached_price_or_wrong_reference_is_not_projected():
    other = replace(KEY, venue="DCE")
    cache = RiskFactsCache({})
    cache.note_tick("rb", PriceFact(other, Decimal(100)), _reference(other), None)
    assert cache.reference_price(KEY) is None
    assert cache.market_ref(KEY) is None

    fact = PriceFact(KEY, Decimal(100))
    cache.note_tick("rb", fact, _reference(other), None)
    assert cache.reference_price(KEY) is fact
    assert cache.market_ref(KEY) is None

    cache.note_tick("rb", fact, {"key": {}}, None)
    assert cache.market_ref(KEY) is None
    cache.note_tick("rb", None, _reference(KEY), None)
    assert cache.reference_price(KEY) is None
    assert cache.market_ref(KEY) is None


def test_absent_or_foreign_id_and_non_key_do_not_match():
    cache = RiskFactsCache({})
    cache.note_tick("rb", PriceFact(KEY, Decimal(100)), _reference(KEY), None)
    assert cache.reference_price(replace(KEY, instrument_id="hc")) is None
    assert cache.market_ref(replace(KEY, instrument_id="hc")) is None
    assert cache.reference_price(None) is None
    assert cache.market_ref(None) is None


def test_market_reference_is_detached_from_input_and_returned_copy():
    cache = RiskFactsCache({})
    ref = _reference(KEY)
    cache.note_tick("rb", PriceFact(KEY, Decimal(100)), ref, None)
    ref["key"]["source_ref"] = "changed"
    first = cache.market_ref(KEY)
    assert first["key"]["source_ref"] == "src"
    first["key"]["instrument"]["venue"] = "changed"
    assert cache.market_ref(KEY)["key"]["instrument"]["venue"] == "SHFE"


@pytest.mark.parametrize(
    "change",
    [lambda ref: ref.pop("epoch"), lambda ref: ref.pop("sequence"),
     lambda ref: ref.pop("message_id"), lambda ref: ref.update(extra="x"),
     lambda ref: ref.update(epoch=""), lambda ref: ref.update(epoch=" ep"),
     lambda ref: ref.update(epoch="ep\nmid"), lambda ref: ref.update(epoch="ep\x85mid"),
     lambda ref: ref.update(message_id="雪" * 86),
     lambda ref: ref.update(message_id="\ud800"),
     lambda ref: ref.update(message_id=""), lambda ref: ref.update(sequence=0),
     lambda ref: ref.update(sequence=-1), lambda ref: ref.update(sequence=True),
     lambda ref: ref.update(sequence="1")],
)
def test_invalid_reference_clears_prior_reference_without_losing_new_price(change):
    cache = RiskFactsCache({})
    cache.note_tick("rb", PriceFact(KEY, Decimal(100)), _reference(KEY), None)
    ref = _reference(KEY)
    change(ref)
    new_price = PriceFact(KEY, Decimal(101))
    cache.note_tick("rb", new_price, ref, None)
    assert cache.reference_price(KEY) is new_price
    assert cache.market_ref(KEY) is None


def test_reference_accepts_wire_text_at_utf8_byte_limit():
    cache = RiskFactsCache({})
    ref = _reference(KEY)
    ref["message_id"] = "雪" * 85 + "a"
    cache.note_tick("rb", PriceFact(KEY, Decimal(100)), ref, None)
    assert cache.market_ref(KEY) == ref


def test_snapshot_exception_clears_previous_reference():
    class BrokenKey(dict):
        def __iter__(self):
            raise RuntimeError("sensitive error")

    cache = RiskFactsCache({})
    cache.note_tick("rb", PriceFact(KEY, Decimal(100)), _reference(KEY), None)
    bad_ref = _reference(KEY)
    bad_ref["key"] = BrokenKey(bad_ref["key"])
    new_price = PriceFact(KEY, Decimal(101))
    cache.note_tick("rb", new_price, bad_ref, None)
    assert cache.reference_price(KEY) is new_price
    assert cache.market_ref(KEY) is None


@pytest.mark.parametrize("nested", ["key", "instrument"])
def test_nested_mapping_cannot_reenter_tick_and_mix_price_with_reference(nested):
    cache = RiskFactsCache({})
    cache.note_tick("rb", PriceFact(KEY, Decimal(100)), _reference(KEY), None)
    calls = []

    class ReentrantDict(dict):
        def __iter__(self):
            calls.append(True)
            newer_ref = _reference(KEY)
            newer_ref["sequence"] = 2
            cache.note_tick("rb", PriceFact(KEY, Decimal(102)), newer_ref, None)
            return super().__iter__()

    ref = _reference(KEY)
    if nested == "key":
        ref["key"] = ReentrantDict(ref["key"])
    else:
        ref["key"]["instrument"] = ReentrantDict(ref["key"]["instrument"])
    new_price = PriceFact(KEY, Decimal(101))
    cache.note_tick("rb", new_price, ref, None)
    assert calls == []
    assert cache.reference_price(KEY) is new_price
    assert cache.market_ref(KEY) is None


def test_non_string_instrument_id_cannot_reenter_cache():
    cache = RiskFactsCache({})
    cache.note_tick("rb", PriceFact(KEY, Decimal(100)), _reference(KEY), None)
    calls = []

    class ReentrantString(str):
        def __hash__(self):
            calls.append(True)
            cache.note_tick("rb", PriceFact(KEY, Decimal(102)), _reference(KEY), None)
            return super().__hash__()

    cache.note_tick(ReentrantString("rb"), PriceFact(KEY, Decimal(101)), _reference(KEY), None)
    assert calls == []
    assert cache.reference_price(KEY).last_price == Decimal(100)
    assert cache.market_ref(KEY) == _reference(KEY)
