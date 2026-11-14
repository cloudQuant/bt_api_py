"""First-login query binding through the retained direct runtime sentinel.

The native Trader API is synthetic.  This checks the retained managed runtime
with its simulation credential-binding fixture, not the retired SDK-admitted
Gateway builder or a live account.
"""

import pytest

import test_ctp_execution_runtime as runtime_fixtures


@pytest.mark.unit
def test_direct_runtime_bootstraps_after_zero_generation_spi_registration(tmp_path, monkeypatch):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        resources = runtime_fixtures._resolve_resources(case)
        runtime = case.provider.last_runtime
        assert resources.ready() is True
        assert runtime.status.ready is True

        # SPI provenance is captured before the first front connection; the
        # current query session has its own, strictly positive generation.
        trader = runtime._trader
        scope = trader.get_query_session_scope()
        assert trader._spi._callback_source_tags.connection_generation == 0
        assert scope.source_tags[-1] == 0
        assert scope.connection_generation > 0
        assert trader.is_trading_ready is True
        calls = [call[0] for api in case.factory.instances for call in api.calls]
        assert "ReqQrySettlementInfoConfirm" in calls
        assert "ReqSettlementInfoConfirm" not in calls
        assert "ReqOrderInsert" not in calls and "ReqOrderAction" not in calls
