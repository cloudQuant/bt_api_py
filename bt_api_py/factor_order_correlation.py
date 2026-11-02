"""Correlate neutral local and provider order observations by client ID.

The caller supplies detached local rows and a result factory. This module does
not read provider state, own framework orders, or establish execution authority.
"""

from __future__ import annotations


def correlate_factor_orders(local_by_client_id, provider_order_rows, *, make_correlation):
    """Return unique exact-ID matches and finite ambiguity/mismatch blockers."""
    duplicate_local = {
        client_id for client_id, rows in local_by_client_id.items() if len(rows) != 1
    }
    provider_by_client_id = {}
    for row in provider_order_rows:
        client_id = row.client_order_id
        if client_id is not None:
            provider_by_client_id.setdefault(client_id, []).append(row)
    duplicate_provider = {
        client_id for client_id, rows in provider_by_client_id.items() if len(rows) != 1
    }
    blockers = [
        "client_order_id_correlation_ambiguous:" + client_id
        for client_id in sorted(duplicate_local | duplicate_provider)
    ]

    correlations = []
    for client_id, local_rows in local_by_client_id.items():
        provider_rows = provider_by_client_id.get(client_id, ())
        if client_id in duplicate_local or client_id in duplicate_provider or not provider_rows:
            continue
        local = local_rows[0]
        provider_row = provider_rows[0]
        if provider_row.instrument != local.instrument:
            blockers.append("client_order_id_instrument_mismatch:" + client_id)
            continue
        correlations.append(
            make_correlation(
                target_id=local.target_id,
                client_order_id=client_id,
                local_order_ref=local.order_ref,
                provider_order_id=provider_row.order_id,
                provider_status=provider_row.status,
            )
        )
    return correlations, blockers


__all__ = ("correlate_factor_orders",)
