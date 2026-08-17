from bt_api_py.backtrader.btapibroker import BtApiBroker
from bt_api_py.brokers.mock import MockBrokerAdapter


def test_canonical_btapibroker_import_exists() -> None:
    broker = BtApiBroker()

    assert broker.adapter_name == "mock"


def test_btapibroker_can_create_builtin_mock_adapter() -> None:
    broker = BtApiBroker(adapter_name="mock")

    adapter = broker.create_adapter()

    assert isinstance(adapter, MockBrokerAdapter)
