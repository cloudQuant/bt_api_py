"""Route context must reach readiness normalization and refusal details.

Providers receive the neutral ``rows`` helper through the readiness
composition. That helper resolves the provider normalization policy from the
route, so the composition must bind the exact selected route instead of
letting the policy be looked up without a venue.
"""

from __future__ import annotations

import pytest

from bt_api_py._contracts.errors import CapabilityNotSupportedError, NormalizedApiError
from bt_api_py.order_readiness_composition import (
    readiness_normalizers,
    resolve_readiness_provider,
)


class _RoutePolicy:
    response_error_item_fields = ()
    result_collection_fields = ("data",)


def test_readiness_rows_receive_the_selected_route_exchange_name(monkeypatch):
    seen = []

    def policy(exchange_name):
        seen.append(exchange_name)
        return _RoutePolicy()

    monkeypatch.setattr("bt_api_py._normalization.normalization_policy", policy)

    projected = readiness_normalizers("BINANCE___SWAP")["rows"](
        {"code": "0", "data": [{"status": "ok"}]}, "get_order_readiness"
    )

    assert projected == [{"status": "ok"}]
    assert seen and set(seen) == {"BINANCE___SWAP"}


def test_unknown_route_rows_fail_closed_without_a_default_provider(monkeypatch):
    seen = []

    def unavailable(exchange_name):
        seen.append(exchange_name)
        raise RuntimeError("no declared policy")

    monkeypatch.setattr("bt_api_py._normalization.normalization_policy", unavailable)

    with pytest.raises(NormalizedApiError) as excinfo:
        readiness_normalizers("OTHER___SWAP")["rows"](
            {"code": "0", "data": []}, "get_order_readiness"
        )

    assert excinfo.value.code == "provider_response_policy_unavailable"
    assert seen == ["OTHER___SWAP"]


def test_missing_readiness_provider_names_the_refused_route():
    with pytest.raises(CapabilityNotSupportedError, match="OTHER___SWAP"):
        resolve_readiness_provider("OTHER___SWAP")
