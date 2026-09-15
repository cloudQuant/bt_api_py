"""Execution environment guard tests without network access or credentials."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from bt_api_py import (
    BtApi,
    CancelOrderRequest,
    ForwardingConfig,
    NormalizedApiError,
    OrderRequest,
    OrderType,
    QueryOrderRequest,
    Side,
)

OKX = "OKX___SWAP"
BINANCE = "BINANCE___SWAP"
SYMBOL = "BTC-USDT-SWAP"


class Feed:
    def __init__(
        self,
        exchange_name: str,
        environment: str,
        *,
        api_region: str | None = None,
        simulated: bool | None = None,
    ) -> None:
        rest_endpoints = {
            (OKX, "demo"): "https://openapi.okx.com",
            (OKX, "production"): "https://openapi.okx.com",
            (BINANCE, "demo"): "https://demo-fapi.binance.com",
            (BINANCE, "production"): "https://fapi.binance.com",
        }
        self._expected_rest_url = rest_endpoints.get((exchange_name, environment))
        values = {"environment": environment, "rest_url": self._expected_rest_url}
        if simulated is not None:
            values["simulated_trading"] = simulated
        self._params = SimpleNamespace(**values)
        self.api_region = (
            "global" if exchange_name.startswith("OKX___") and api_region is None else api_region
        )
        self.disconnect = Mock()

    def get_environment_info(self):
        environment = str(self._params.environment).strip().lower()
        simulated = getattr(self._params, "simulated_trading", environment != "production")
        proof = {
            "environment": environment,
            "simulated": simulated,
            "verified": self._params.rest_url == self._expected_rest_url,
        }
        if self.api_region is not None:
            proof["api_region"] = self.api_region
        return proof


class Backend:
    def __init__(self) -> None:
        self.placed: list[Any] = []
        self.queried: list[Any] = []
        self.canceled: list[Any] = []
        self.open_orders: list[Any] = []

    def make_order(self, venue, request):
        self.placed.append((venue, request))
        return {
            "order_id": "order-1",
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "status": "accepted",
            "filled": 0,
        }

    def query_order(self, venue, request, **kwargs):
        self.queried.append((venue, request))
        return {
            "order_id": request.order_id,
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "status": "accepted",
            "filled": 0,
        }

    def cancel_order(self, venue, request, **kwargs):
        self.canceled.append((venue, request))
        return {
            "order_id": request.order_id,
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "status": "canceled",
            "filled": 0,
        }

    def get_open_orders(self, venue, symbol=None, **kwargs):
        self.open_orders.append((venue, symbol, kwargs))
        return []


@pytest.fixture
def direct_factory(monkeypatch, tmp_path):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    feeds = {}

    def create_feed(exchange_name, data_queue, **kwargs):
        del data_queue
        environment = kwargs.pop("resolved_environment", kwargs.get("environment", "production"))
        simulated = kwargs.pop("resolved_simulated", None)
        feed = Feed(
            exchange_name,
            environment,
            api_region=kwargs.get("api_region"),
            simulated=simulated,
        )
        feeds[exchange_name] = feed
        return feed

    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.create_feed", Mock(side_effect=create_feed)
    )

    clients: list[Any] = []

    def make(exchange_kwargs, *, required=None, journal=True, market_data_only=False):
        exchange_kwargs = {venue: dict(parameters) for venue, parameters in exchange_kwargs.items()}
        for venue, parameters in exchange_kwargs.items():
            if venue.startswith("OKX___"):
                parameters.setdefault("api_key", "fixture-okx-public")
                parameters.setdefault("api_secret", "fixture-okx-secret")
                parameters.setdefault("passphrase", "fixture-okx-passphrase")
            elif venue.startswith("BINANCE___"):
                parameters.setdefault("api_key", "fixture-binance-public")
                parameters.setdefault("api_secret", "fixture-binance-secret")
        required_environments = {} if required is None else required
        configured_venues = set(exchange_kwargs) | set(required_environments)
        config = {
            "required_environments": required_environments,
            "account_ids": dict.fromkeys(configured_venues, "demo"),
            "require_order_journal": journal,
            "market_data_only": market_data_only,
        }
        if journal:
            config["order_journal"] = tmp_path / f"orders-{len(clients)}.jsonl"
        api = BtApi(exchange_kwargs, debug=False, execution_config=config)
        api._backend = Backend()
        clients.append(api)
        return api

    yield make, feeds
    for api in clients:
        api.close()


def order(client_order_id="123456789012"):
    return OrderRequest(
        symbol=SYMBOL,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("60000"),
        account_id="demo",
        client_order_id=client_order_id,
        quantity_unit="contracts",
        position_mode="net",
    )


def test_direct_environment_info_is_resolved_and_secret_free(direct_factory):
    make, _ = direct_factory
    secret = "must-never-be-returned"
    api = make(
        {
            OKX: {
                "environment": "demo",
                "api_region": "eea",
                "resolved_simulated": True,
                "api_key": secret,
            }
        },
        required={OKX: "demo"},
    )

    info = api.get_environment_info(OKX)

    assert info == {
        "exchange_name": OKX,
        "environment": "demo",
        "api_region": "eea",
        "simulated": True,
        "transport_mode": "direct",
        "verified": True,
    }
    assert set(info) == {
        "exchange_name",
        "environment",
        "api_region",
        "simulated",
        "transport_mode",
        "verified",
    }
    assert secret not in repr(info)


def test_missing_okx_region_proof_is_unverified(direct_factory):
    make, feeds = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
    )
    feeds[OKX].get_environment_info = Mock(
        return_value={
            "environment": "demo",
            "simulated": True,
            "verified": True,
        }
    )

    assert api.get_environment_info(OKX) == {
        "exchange_name": OKX,
        "environment": "unknown",
        "api_region": None,
        "simulated": None,
        "transport_mode": "direct",
        "verified": False,
    }


@pytest.mark.parametrize("api_region", [None, "apac", 1, True])
def test_invalid_okx_region_proof_is_unverified(direct_factory, api_region):
    make, feeds = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
    )
    feeds[OKX].get_environment_info = Mock(
        return_value={
            "environment": "demo",
            "api_region": api_region,
            "simulated": True,
            "verified": True,
        }
    )

    assert api.get_environment_info(OKX) == {
        "exchange_name": OKX,
        "environment": "unknown",
        "api_region": None,
        "simulated": None,
        "transport_mode": "direct",
        "verified": False,
    }


def test_valid_okx_tr_production_proof_is_exposed(direct_factory):
    make, _ = direct_factory
    api = make(
        {
            OKX: {
                "environment": "production",
                "api_region": "tr",
                "resolved_simulated": False,
            }
        },
        required={OKX: "production"},
    )

    assert api.get_environment_info(OKX)["api_region"] == "tr"


def test_okx_tr_demo_proof_is_unverified(direct_factory):
    make, feeds = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
    )
    feeds[OKX].get_environment_info = Mock(
        return_value={
            "environment": "demo",
            "api_region": "tr",
            "simulated": True,
            "verified": True,
        }
    )

    assert api.get_environment_info(OKX)["verified"] is False


def test_order_environment_is_rechecked_before_intent_and_dispatch(direct_factory):
    make, feeds = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
    )
    journal = api._execution_session.path
    feeds[OKX]._params.environment = "production"
    feeds[OKX]._params.simulated_trading = False

    with pytest.raises(NormalizedApiError) as excinfo:
        api.make_order(OKX, order(), normalized=True)

    assert excinfo.value.code == "required_environment_mismatch"
    assert excinfo.value.definite_reject is True
    assert api._backend.placed == []
    assert api.get_execution_summary()["submit_calls"] == 0
    assert not journal.exists() or journal.read_text() == ""


@pytest.mark.parametrize(
    "rest_url",
    [
        "https://fapi.binance.com",
        "https://demo-fapi.binance.com.evil.invalid",
        "https://demo-fapi.binance.com:8443",
        "https://demo-fapi.binance.com/api",
    ],
)
def test_endpoint_drift_is_rejected_before_intent_and_dispatch(direct_factory, rest_url):
    make, feeds = direct_factory
    api = make(
        {BINANCE: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={BINANCE: "demo"},
    )
    journal = api._execution_session.path
    feeds[BINANCE]._params.rest_url = rest_url

    with pytest.raises(NormalizedApiError) as excinfo:
        api.make_order(BINANCE, order(), normalized=True)

    assert excinfo.value.code == "required_environment_unverified"
    assert excinfo.value.definite_reject is True
    assert api._backend.placed == []
    assert api.get_execution_summary()["submit_calls"] == 0
    assert not journal.exists() or journal.read_text() == ""


@pytest.mark.parametrize(
    "environment,simulated",
    [("demo", True), ("production", False)],
)
def test_verified_direct_environments_allow_normalized_orders(
    direct_factory, environment, simulated
):
    make, _ = direct_factory
    api = make(
        {
            BINANCE: {
                "resolved_environment": environment,
                "resolved_simulated": simulated,
            }
        },
        required={BINANCE: environment},
    )

    result = api.make_order(BINANCE, order(), normalized=True)

    assert result["status"] == "accepted"
    assert len(api._backend.placed) == 1
    assert api.get_execution_summary()["submit_calls"] == 1


def test_unverified_direct_environment_blocks_before_intent(direct_factory):
    make, feeds = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
    )
    del feeds[OKX]._params

    with pytest.raises(NormalizedApiError) as excinfo:
        api.make_order(OKX, order(), normalized=True)

    assert excinfo.value.code == "required_environment_unverified"
    assert excinfo.value.definite_reject is True
    assert api._backend.placed == []
    assert api.get_execution_summary()["submit_calls"] == 0


def test_adapter_environment_failure_is_sanitized_and_fails_closed(direct_factory):
    make, feeds = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
    )
    secret = "credential-must-not-cross-boundary"

    class BrokenParams:
        @property
        def environment(self):
            raise RuntimeError(secret)

    feeds[OKX]._params = BrokenParams()

    assert api.get_environment_info(OKX)["verified"] is False
    with pytest.raises(NormalizedApiError) as excinfo:
        api.make_order(OKX, order(), normalized=True)
    assert excinfo.value.code == "required_environment_unverified"
    assert excinfo.value.definite_reject is True
    assert secret not in str(excinfo.value)
    assert api._backend.placed == []


@pytest.mark.parametrize(
    "venue,rest_url",
    [
        (OKX, "https://openapi.okx.com.evil.invalid"),
        (BINANCE, "https://fapi.binance.com"),
        (BINANCE, "https://demo-fapi.binance.com:8443"),
        (BINANCE, "https://demo-fapi.binance.com/api"),
    ],
    ids=["evil-host", "production-host", "wrong-port", "path-prefix"],
)
@pytest.mark.parametrize(
    "operation,request_type,backend_calls",
    [
        ("query_order", QueryOrderRequest, "queried"),
        ("cancel_order", CancelOrderRequest, "canceled"),
    ],
)
def test_reconciliation_rechecks_environment_before_dispatch_or_journal(
    direct_factory, venue, rest_url, operation, request_type, backend_calls
):
    make, feeds = direct_factory
    api = make(
        {venue: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={venue: "demo"},
    )
    journal = api._execution_session.path
    before = journal.read_text() if journal.exists() else ""
    feeds[venue]._params.rest_url = rest_url
    identity = {
        "symbol": SYMBOL,
        "account_id": "demo",
        "order_id": "order-1",
        "client_order_id": "123456789012",
    }

    with pytest.raises(NormalizedApiError) as excinfo:
        getattr(api, operation)(venue, request_type(**identity), normalized=True)

    assert excinfo.value.operation == operation
    assert excinfo.value.code == "required_environment_unverified"
    assert excinfo.value.definite_reject is True
    assert getattr(api._backend, backend_calls) == []
    assert api.get_execution_summary()["cancel_calls"] == 0
    assert (journal.read_text() if journal.exists() else "") == before


@pytest.mark.parametrize(
    "operation,request_type,backend_calls,expected_status",
    [
        ("query_order", QueryOrderRequest, "queried", "accepted"),
        ("cancel_order", CancelOrderRequest, "canceled", "canceled"),
    ],
)
def test_verified_environment_keeps_reconciliation_available(
    direct_factory, operation, request_type, backend_calls, expected_status
):
    make, _ = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
    )
    identity = {
        "symbol": SYMBOL,
        "account_id": "demo",
        "order_id": "order-1",
        "client_order_id": "123456789012",
    }

    result = getattr(api, operation)(OKX, request_type(**identity), normalized=True)

    assert result["status"] == expected_status
    assert len(getattr(api._backend, backend_calls)) == 1


@pytest.mark.parametrize(
    "operation,request_type,backend_calls",
    [
        ("query_order", QueryOrderRequest, "queried"),
        ("cancel_order", CancelOrderRequest, "canceled"),
    ],
)
def test_market_data_only_never_dispatches_authenticated_order_operations(
    direct_factory, operation, request_type, backend_calls
):
    make, _ = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
        market_data_only=True,
    )
    identity = {
        "symbol": SYMBOL,
        "account_id": "demo",
        "order_id": "order-1",
        "client_order_id": "123456789012",
    }

    with pytest.raises(NormalizedApiError) as excinfo:
        getattr(api, operation)(OKX, request_type(**identity), normalized=True)

    assert excinfo.value.operation == operation
    assert excinfo.value.code == "market_data_only"
    assert excinfo.value.definite_reject is True
    assert getattr(api._backend, backend_calls) == []


def test_private_normalized_read_rechecks_environment_before_dispatch(direct_factory):
    make, feeds = direct_factory
    api = make(
        {OKX: {"resolved_environment": "demo", "resolved_simulated": True}},
        required={OKX: "demo"},
    )
    feeds[OKX]._params.rest_url = "https://openapi.okx.com.evil.invalid"

    with pytest.raises(NormalizedApiError) as excinfo:
        api.get_open_orders(OKX, SYMBOL, normalized=True)

    assert excinfo.value.operation == "get_open_orders"
    assert excinfo.value.code == "required_environment_unverified"
    assert api._backend.open_orders == []


def test_mixed_direct_environments_fail_closed_when_both_require_demo(direct_factory):
    make, _ = direct_factory

    with pytest.raises(NormalizedApiError) as excinfo:
        make(
            {
                OKX: {"resolved_environment": "demo", "resolved_simulated": True},
                BINANCE: {
                    "resolved_environment": "production",
                    "resolved_simulated": False,
                },
            },
            required={OKX: "demo", BINANCE: "demo"},
        )

    assert excinfo.value.code == "required_environment_mismatch"
    assert excinfo.value.definite_reject is True


def test_missing_required_venue_credentials_fail_before_any_transport(direct_factory):
    make, _ = direct_factory
    with pytest.raises(NormalizedApiError) as excinfo:
        make({}, required={BINANCE: "demo"})

    assert excinfo.value.code == "private_credentials_missing"
    assert excinfo.value.definite_reject is True


def test_configured_venue_without_requirement_fails_closed(direct_factory):
    make, _ = direct_factory

    with pytest.raises(NormalizedApiError) as excinfo:
        make(
            {
                OKX: {"resolved_environment": "demo", "resolved_simulated": True},
                BINANCE: {"resolved_environment": "demo", "resolved_simulated": True},
            },
            required={OKX: "demo"},
        )

    assert excinfo.value.code == "required_environment_missing"
    assert excinfo.value.definite_reject is True


def test_crypto_execution_derives_account_authority_from_credentials(direct_factory, tmp_path):
    del direct_factory
    api = BtApi(
        {
            OKX: {
                "resolved_environment": "demo",
                "resolved_simulated": True,
                "api_key": "fixture-public",
                "api_secret": "fixture-secret",
                "passphrase": "fixture-passphrase",
            }
        },
        debug=False,
        execution_config={
            "order_journal": tmp_path / "derived-account.jsonl",
            "required_environments": {OKX: "demo"},
        },
    )
    try:
        identity = api.get_execution_identity(OKX)
        assert identity["account_id"].startswith("okx-credential-")
        assert identity["credential_fingerprint"]
    finally:
        api.close()


@pytest.mark.parametrize(
    "credentials,code",
    [
        (
            {
                "api_key": " fixture-public ",
                "api_secret": "fixture-secret",
                "passphrase": "fixture-passphrase",
            },
            "private_credentials_malformed",
        ),
        (
            {
                "api_key": "fixture-public-a",
                "public_key": "fixture-public-b",
                "api_secret": "fixture-secret",
                "passphrase": "fixture-passphrase",
            },
            "credential_alias_conflict",
        ),
        (
            {
                "api_key": "fixture-public",
                "api_secret": "   ",
                "passphrase": "fixture-passphrase",
            },
            "private_credentials_malformed",
        ),
    ],
)
def test_write_credential_preflight_rejects_before_feed_construction(
    direct_factory, credentials, code
):
    make, feeds = direct_factory
    with pytest.raises(NormalizedApiError) as excinfo:
        make(
            {
                OKX: {
                    "resolved_environment": "demo",
                    "resolved_simulated": True,
                    **credentials,
                }
            },
            required={OKX: "demo"},
        )
    assert excinfo.value.code == code
    assert feeds == {}


def test_dynamic_crypto_configuration_without_credentials_fails_closed(direct_factory):
    make, _ = direct_factory
    with pytest.raises(NormalizedApiError) as excinfo:
        make({}, required={OKX: "demo"})

    assert excinfo.value.code == "private_credentials_missing"
    assert excinfo.value.definite_reject is True


def forwarding_config():
    return ForwardingConfig(
        command_endpoint="inproc://environment-command",
        market_endpoint="inproc://environment-market",
        private_endpoint="inproc://environment-private",
        account_id="demo",
        strategy_id="environment-guard",
    )


def test_zmq_environment_is_explicitly_unverified(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    api = BtApi(
        {OKX: {"environment": "demo", "api_key": "not-returned"}},
        debug=False,
        transport_mode="zmq",
        forwarding_config=forwarding_config(),
    )
    try:
        assert api.get_environment_info(OKX) == {
            "exchange_name": OKX,
            "environment": "unknown",
            "api_region": None,
            "simulated": None,
            "transport_mode": "zmq",
            "verified": False,
        }
    finally:
        api.close()


def test_zmq_required_environment_fails_closed(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)

    with pytest.raises(NormalizedApiError) as excinfo:
        BtApi(
            {OKX: {"environment": "demo"}},
            debug=False,
            transport_mode="zmq",
            forwarding_config=forwarding_config(),
            execution_config={
                "require_order_journal": False,
                "required_environments": {OKX: "demo"},
                "account_ids": {OKX: "demo"},
            },
        )

    assert excinfo.value.code == "required_environment_unverified"
    assert excinfo.value.definite_reject is True


def test_market_data_only_zmq_remains_available_for_reads(monkeypatch):
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    api = BtApi(
        {OKX: {"environment": "demo"}},
        debug=False,
        transport_mode="zmq",
        forwarding_config=forwarding_config(),
        execution_config={
            "market_data_only": True,
            "required_environments": {OKX: "demo"},
        },
    )
    try:
        assert api.get_environment_info(OKX)["verified"] is False
        with pytest.raises(NormalizedApiError) as excinfo:
            api.make_order(OKX, order(), normalized=True)
        assert excinfo.value.code == "market_data_only"
        assert excinfo.value.definite_reject is True
    finally:
        api.close()


@pytest.mark.parametrize(
    "required",
    [
        [],
        {"": "demo"},
        {OKX: ""},
        {OKX: "paper"},
        {OKX: 1},
        {f" {OKX}": "demo", OKX: "demo"},
    ],
)
def test_required_environments_reject_invalid_config(direct_factory, required):
    make, _ = direct_factory

    with pytest.raises(NormalizedApiError) as excinfo:
        make({}, required=required, journal=False)

    assert excinfo.value.code == "invalid_execution_config"
    assert excinfo.value.definite_reject is False
