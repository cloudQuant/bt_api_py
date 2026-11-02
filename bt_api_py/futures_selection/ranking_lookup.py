"""Validated, value-only lookup in a completed futures selection manifest.

These checks establish internal consistency, not provider authority,
selection freshness, or permission to trade.
"""

from .models import (
    ContractAssessment,
    MainContractManifest,
    MainContractSelectionBlocked,
    ProductKey,
)


def get_ranked_contract(manifest, exchange_id, product_id, rank):
    """Return the one-based ranked ID, or None if that rank is unavailable."""
    if type(rank) is not int or rank < 1:
        raise ValueError("rank must be a positive integer")
    if type(manifest) is not MainContractManifest:
        raise TypeError("manifest must be a MainContractManifest")
    product = ProductKey(exchange_id, product_id)
    mapping = dict(manifest.selected_mapping())
    if product not in mapping:
        raise KeyError("product is not in the selected manifest")
    selected_id = mapping[product]
    selected = [
        item for item in manifest.products if item.in_target_pool and item.product == product
    ]
    if len(selected) != 1:
        raise MainContractSelectionBlocked("product selection is ambiguous")
    selection = selected[0]
    ranked = selection.ranked_instrument_ids
    excluded = selection.excluded_instrument_ids
    if (
        type(ranked) is not tuple
        or not ranked
        or type(excluded) is not tuple
        or any(
            type(item) is not str or not item or item != item.strip()
            for item in ranked + excluded
        )
    ):
        raise MainContractSelectionBlocked("product ranking is incomplete")
    ranked_keys = tuple(item.casefold() for item in ranked)
    excluded_keys = {item.casefold() for item in excluded}
    if (
        ranked[0] != selected_id
        or len(set(ranked_keys)) != len(ranked_keys)
        or bool(set(ranked_keys) & excluded_keys)
    ):
        raise MainContractSelectionBlocked("product ranking is inconsistent")
    if any(type(item) is not ContractAssessment for item in manifest.candidates):
        raise MainContractSelectionBlocked("product assessments are invalid")
    assessments = [item for item in manifest.candidates if item.product == product]
    by_id = {}
    for item in assessments:
        if (
            type(item.instrument_id) is not str
            or not item.instrument_id
            or item.instrument_id != item.instrument_id.strip()
            or (item.status != "RANKED" and item.rank is not None)
        ):
            raise MainContractSelectionBlocked("product assessment identity or rank is invalid")
        key = item.instrument_id.casefold()
        if key in by_id:
            raise MainContractSelectionBlocked("product assessments contain duplicates")
        by_id[key] = item
    for expected_rank, instrument_id in enumerate(ranked, 1):
        assessment = by_id.get(instrument_id.casefold())
        if (
            assessment is None
            or assessment.instrument_id != instrument_id
            or assessment.status != "RANKED"
            or assessment.reason is not None
            or type(assessment.rank) is not int
            or assessment.rank != expected_rank
        ):
            raise MainContractSelectionBlocked("product ranking lacks matching assessments")
    if any(
        item.status == "RANKED" and item.instrument_id.casefold() not in ranked_keys
        for item in assessments
    ):
        raise MainContractSelectionBlocked("product ranking omits ranked assessments")
    return ranked[rank - 1] if len(ranked) >= rank else None


__all__ = ["get_ranked_contract"]
