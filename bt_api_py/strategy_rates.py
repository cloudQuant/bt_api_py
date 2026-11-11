"""Shared local rates file for strategy cash and value (D63-55).

One file per account holds what the venue's trade returns do not carry: the
contract multiplier, the margin ratio per side and the commission per kind of
trade, by product, with optional overrides per instrument. Every strategy of
the account is valued with the same file, so a rate is changed in one place.

The numbers are the operator's estimate of what the broker charges. Nothing
here reads the broker's rates, and a missing product is never treated as free:
``StrategyRates.for_instrument`` returns ``None`` and the caller reports the
strategy's cash as unavailable.

File format (YAML)::

    schema_version: 1
    currency: CNY
    products:
      rb:
        multiplier: 10
        margin_ratio: {long: "0.13", short: "0.13"}
        commission:
          open: {per_lot: "0", ratio: "0.0001"}
          close: {per_lot: "0", ratio: "0.0001"}
          close_today: {per_lot: "0", ratio: "0.0001"}
    instruments:          # optional, same fields, wins over the product
      rb2701: {...}

``ratio`` is a share of the traded notional (price x quantity x multiplier);
``per_lot`` is an amount per contract. The product of an instrument is the
leading letters of its native symbol (``rb2701`` -> ``rb``, ``SA701`` -> ``SA``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

_MAX_BYTES = 1024 * 1024
_MAX_ENTRIES = 4096
_PRODUCT = re.compile(r"[A-Za-z]+")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{0,63}\Z")
_CURRENCY = re.compile(r"[A-Z]{3,8}\Z")
_KINDS = ("open", "close", "close_today")
_SIDES = ("long", "short")


class StrategyRatesError(ValueError):
    """The rates file cannot be used; the code names the first problem found."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str):
    raise StrategyRatesError(code)


def _number(value: object, code: str, *, minimum: Decimal, maximum: Decimal, exclusive_minimum: bool = False) -> Decimal:
    if type(value) not in (int, float, str):  # a bool is none of these
        _fail(code)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        _fail(code)
    if (not result.is_finite() or result > maximum or result < minimum
            or (exclusive_minimum and result == minimum)):
        _fail(code)
    return result


def _mapping(value: object, keys: tuple[str, ...], code: str) -> dict:
    if type(value) is not dict or set(value) != set(keys):
        _fail(code)
    return value


@dataclass(frozen=True)
class CommissionRate:
    """``per_lot`` per contract plus ``ratio`` of the traded notional."""

    per_lot: Decimal
    ratio: Decimal

    def amount(self, *, price: Decimal, quantity: Decimal, multiplier: Decimal) -> Decimal:
        return self.per_lot * quantity + self.ratio * price * quantity * multiplier


@dataclass(frozen=True)
class InstrumentRates:
    multiplier: Decimal
    long_margin_ratio: Decimal
    short_margin_ratio: Decimal
    open: CommissionRate
    close: CommissionRate
    close_today: CommissionRate

    def margin_ratio(self, direction: str) -> Decimal:
        if direction not in _SIDES:
            _fail("RATES_DIRECTION_INVALID")
        return self.long_margin_ratio if direction == "long" else self.short_margin_ratio

    def commission(self, offset: str) -> CommissionRate:
        """The rate for a trade's offset; ``close_yesterday`` and a plain ``close`` share one rate."""
        if offset == "open":
            return self.open
        if offset == "close_today":
            return self.close_today
        if offset in ("close", "close_yesterday"):
            return self.close
        _fail("RATES_OFFSET_INVALID")


def _entry(raw: object) -> InstrumentRates:
    raw = _mapping(raw, ("multiplier", "margin_ratio", "commission"), "RATES_ENTRY_INVALID")
    margin = _mapping(raw["margin_ratio"], _SIDES, "RATES_MARGIN_INVALID")
    commission = _mapping(raw["commission"], _KINDS, "RATES_COMMISSION_INVALID")
    rates = {}
    for kind in _KINDS:
        item = _mapping(commission[kind], ("per_lot", "ratio"), "RATES_COMMISSION_INVALID")
        rates[kind] = CommissionRate(
            _number(item["per_lot"], "RATES_COMMISSION_INVALID", minimum=Decimal(0), maximum=Decimal(100000)),
            _number(item["ratio"], "RATES_COMMISSION_INVALID", minimum=Decimal(0), maximum=Decimal(1)))
    return InstrumentRates(
        _number(raw["multiplier"], "RATES_MULTIPLIER_INVALID", minimum=Decimal(0),
                maximum=Decimal(10_000_000), exclusive_minimum=True),
        _number(margin["long"], "RATES_MARGIN_INVALID", minimum=Decimal(0), maximum=Decimal(1),
                exclusive_minimum=True),
        _number(margin["short"], "RATES_MARGIN_INVALID", minimum=Decimal(0), maximum=Decimal(1),
                exclusive_minimum=True),
        rates["open"], rates["close"], rates["close_today"])


@dataclass(frozen=True)
class StrategyRates:
    currency: str
    products: Any
    instruments: Any
    digest: str

    def for_instrument(self, native_symbol: object) -> InstrumentRates | None:
        """The instrument's own entry, else its product's, else ``None`` (never a zero rate)."""
        if type(native_symbol) is not str or not native_symbol:
            return None
        found = self.instruments.get(native_symbol)
        if found is not None:
            return found
        match = _PRODUCT.match(native_symbol)
        return None if match is None else self.products.get(match.group(0))


def parse_strategy_rates(content: bytes) -> StrategyRates:
    """Parse the bytes of a rates file; the digest names exactly these bytes."""
    import yaml

    if type(content) is not bytes or not content or len(content) > _MAX_BYTES:
        _fail("RATES_FILE_INVALID")
    try:
        raw = yaml.safe_load(content.decode("utf-8"))
    except Exception:
        _fail("RATES_FILE_INVALID")
    if (type(raw) is not dict or not {"schema_version", "currency", "products"} <= set(raw)
            or not set(raw) <= {"schema_version", "currency", "products", "instruments"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
        _fail("RATES_FILE_INVALID")
    currency = raw["currency"]
    if type(currency) is not str or _CURRENCY.fullmatch(currency) is None:
        _fail("RATES_CURRENCY_INVALID")
    products, instruments = raw["products"], raw.get("instruments", {})
    if (type(products) is not dict or not products or type(instruments) is not dict
            or len(products) + len(instruments) > _MAX_ENTRIES):
        _fail("RATES_FILE_INVALID")
    parsed_products, parsed_instruments = {}, {}
    for name, entry in products.items():
        if type(name) is not str or _PRODUCT.fullmatch(name) is None:
            _fail("RATES_PRODUCT_INVALID")
        parsed_products[name] = _entry(entry)
    for name, entry in instruments.items():
        if type(name) is not str or _NAME.fullmatch(name) is None:
            _fail("RATES_INSTRUMENT_INVALID")
        parsed_instruments[name] = _entry(entry)
    return StrategyRates(currency, MappingProxyType(parsed_products), MappingProxyType(parsed_instruments),
                         hashlib.sha256(content).hexdigest())


def load_strategy_rates(path: str | Path) -> StrategyRates:
    """Read one regular file of at most 1 MiB; an unreadable file is an error, not an empty table."""
    try:
        target = Path(path)
        if not target.is_file() or target.stat().st_size > _MAX_BYTES:
            raise OSError
        content = target.read_bytes()
    except OSError:
        _fail("RATES_FILE_UNREADABLE")
    return parse_strategy_rates(content)


__all__ = (
    "CommissionRate",
    "InstrumentRates",
    "StrategyRates",
    "StrategyRatesError",
    "load_strategy_rates",
    "parse_strategy_rates",
)
