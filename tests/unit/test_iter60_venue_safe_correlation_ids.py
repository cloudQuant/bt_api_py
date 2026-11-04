"""Iteration 60: SDK order correlations are valid venue client order ids.

The correlation's client order id is sent to the venue verbatim (OKX
``clOrdId``: up to 32 alphanumerics; Binance ``newClientOrderId``: up to 36 of
``[.A-Z:/a-z0-9_-]``). The v1 form (89 characters with dashes) was refused by
both. Offline: temporary SQLite only.
"""

from __future__ import annotations

import re

from bt_api_base.execution_row_observation_protocol import strategy_client_order_prefix

from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer

_OKX = re.compile(r"[A-Za-z0-9]{1,32}\Z")
_BINANCE = re.compile(r"[.A-Z:/a-z0-9_-]{1,36}\Z")


def test_issued_client_order_ids_fit_okx_and_binance(tmp_path):
    issuer = DurableOrderCorrelationIssuer(tmp_path / "issuer.sqlite3")
    try:
        ids = {issuer.issue_order_correlation("OKX___SWAP", "BTC-USDT")[0].client_order_id
               for _ in range(50)}
        assert len(ids) == 50
        for client_id in ids:
            assert _OKX.match(client_id) and _BINANCE.match(client_id), client_id
            assert client_id.startswith("c") and len(client_id) == 32
    finally:
        issuer.close()


def test_a_strategy_attributed_id_carries_the_strategy_prefix(tmp_path):
    issuer = DurableOrderCorrelationIssuer(tmp_path / "issuer.sqlite3")
    try:
        correlation, handle = issuer.issue_order_correlation(
            "BINANCE___SWAP", "BTCUSDT", strategy_ref="strategy-a"
        )
        client_id = correlation.client_order_id
        assert client_id.startswith(strategy_client_order_prefix("strategy-a"))
        assert _OKX.match(client_id) and _BINANCE.match(client_id)
        # The reservation stays resolvable (durable, MAC-bound) like an unattributed one.
        assert issuer.resolve_order_correlation(correlation) is not None
    finally:
        issuer.close()
