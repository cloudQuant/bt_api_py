"""Configured instrument metadata lookup for already-normalized labels.

These candidates support configured metadata lookup only.  They are not a
provider symbol mapping or evidence that two instrument identities match.
"""

from collections.abc import Mapping
from decimal import Decimal


def contract_metadata_aliases(raw):
    """Return the historical ordered alias candidates for a text label."""
    aliases = [raw]

    if "." in raw:
        head, tail = (part.strip() for part in raw.split(".", 1))
        aliases.extend([head, head.upper(), head.lower(), tail, tail.upper(), tail.lower()])

    if "_" in raw:
        head, tail = (part.strip() for part in raw.split("_", 1))
        aliases.extend([head, head.upper(), head.lower(), tail, tail.upper(), tail.lower()])

    aliases.extend([raw.upper(), raw.lower()])
    compact = "".join(ch for ch in raw if ch.isalnum())
    aliases.extend([compact, compact.upper(), compact.lower()])

    result = []
    seen = set()
    for alias in aliases:
        if alias and alias not in seen:
            result.append(alias)
            seen.add(alias)
    return result


def configured_market_instrument_key(metadata_by_name, dataname, *, instrument_type, error_type):
    """Select one exact configured neutral key without creating source authority."""
    candidates = []
    metadata = metadata_by_name.get(dataname)
    if isinstance(metadata, Mapping):
        candidates.append(metadata.get("instrument_key"))
    for alias in contract_metadata_aliases(dataname):
        if alias == dataname:
            continue
        metadata = metadata_by_name.get(alias)
        if isinstance(metadata, Mapping):
            candidates.append(metadata.get("instrument_key"))
    keys = [item for item in candidates if item is not None]
    if not keys or any(type(item) is not instrument_type for item in keys):
        raise error_type("an exact configured MarketInstrumentKey is unavailable")
    if any(item != keys[0] for item in keys[1:]):
        raise error_type("instrument key metadata is ambiguous for this feed")
    key = keys[0]
    if key.native_symbol != dataname:
        raise error_type("instrument key does not exactly match the configured feed name")
    return key


def find_alias_match(values, aliases, *, alias_fn, require_truthy):
    """Find the first configured legacy alias without changing its owner."""
    for alias in aliases:
        if require_truthy:
            value = values.get(alias, {})
            if value:
                return True, value
        elif alias in values:
            return True, values[alias]
    alias_set = set(aliases)
    for key, value in values.items():
        if (not require_truthy or value) and alias_set.intersection(alias_fn(key)):
            return True, value
    return False, None


def legacy_instrument_spec_fields(metadata):
    """Fill the historical neutral InstrumentSpec mapping aliases in place."""
    contract_value = metadata.get("contract_value")
    contract_multiplier = metadata.get("contract_multiplier")
    if contract_value is not None and contract_multiplier is not None:
        metadata.setdefault(
            "multiplier",
            Decimal(str(contract_value)) * Decimal(str(contract_multiplier)),
        )
    metadata.setdefault("tick_size", metadata.get("price_tick"))
    metadata.setdefault("lot_size", metadata.get("quantity_step"))
    metadata.setdefault("min_size", metadata.get("min_quantity"))
    metadata.setdefault("settlement_currency", metadata.get("quote_currency"))
    return metadata


def configured_position_quantity_unit(
    metadata_by_name, dataname, instrument, *, alias_fn, unavailable
):
    """Resolve one configured unit without asserting provider authority."""
    units = set()
    for alias in alias_fn(dataname):
        metadata = metadata_by_name.get(alias)
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("instrument_key") != instrument:
            continue
        unit = metadata.get("quantity_unit")
        if type(unit) is not str or not unit or unit != unit.strip():
            unavailable("account_position_snapshot_quantity_unit_unavailable")
        units.add(unit)
    if len(units) != 1:
        unavailable("account_position_snapshot_quantity_unit_unavailable")
    return next(iter(units))



def configured_metadata_price_tick(metadata, dataname, *, finite_number):
    """Read the first historical configured tick candidate, without source authority."""
    candidates = [dataname]
    text = str(dataname or "")
    for separator in (".", ":", "/"):
        candidates.extend(part for part in text.split(separator) if part)
    for key in candidates:
        row = metadata.get(key) if isinstance(metadata, dict) else None
        if not isinstance(row, dict):
            continue
        value = finite_number(
            row.get("price_tick") or row.get("tick_size") or row.get("min_price_tick")
        )
        if value is not None and value > 0:
            return value
    return None
