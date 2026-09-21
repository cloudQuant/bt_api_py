from __future__ import annotations

import logging

import pytest

from bt_api_py.brokers import loader as loader_module
from bt_api_py.brokers import registry as registry_module
from bt_api_py.brokers.errors import BrokerError, BrokerErrorCode
from bt_api_py.brokers.gateway_bridge import GatewayBridgeAdapter
from bt_api_py.brokers.loader import load_adapter
from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.brokers.registry import list_registered_adapters, register_adapter


@pytest.fixture
def isolated_adapter_registry(monkeypatch):
    monkeypatch.setattr(
        registry_module,
        "_ADAPTER_FACTORIES",
        registry_module._ADAPTER_FACTORIES.copy(),
    )


def test_load_adapter_returns_builtin_mock() -> None:
    adapter = load_adapter("mock")

    assert isinstance(adapter, MockBrokerAdapter)


def test_load_adapter_returns_builtin_gateway_bridge() -> None:
    adapter = load_adapter("gateway_bridge")

    assert isinstance(adapter, GatewayBridgeAdapter)


def test_list_builtin_adapters_contains_mock_and_gateway_bridge() -> None:
    load_adapter("mock")

    adapters = list_registered_adapters()

    assert "mock" in adapters
    assert "gateway_bridge" in adapters


def test_load_adapter_raises_structured_error_for_unknown_name() -> None:
    try:
        load_adapter("missing")
    except BrokerError as exc:
        assert exc.code == BrokerErrorCode.ADAPTER_NOT_INSTALLED
    else:
        raise AssertionError("expected BrokerError for missing adapter")


def test_register_adapter_accepts_external_factory() -> None:
    register_adapter("external_mock", MockBrokerAdapter)

    adapter = load_adapter("external_mock")

    assert isinstance(adapter, MockBrokerAdapter)


def test_load_adapter_discovers_entry_point_registration(monkeypatch) -> None:
    def register_fake() -> None:
        register_adapter("entrypoint_mock", MockBrokerAdapter)

    class FakeEntryPoint:
        def load(self):
            return register_fake

    class FakeEntryPoints(list):
        def select(self, *, group: str):
            if group == loader_module.ENTRY_POINT_GROUP:
                return self
            return []

    monkeypatch.setattr(loader_module, "_ENTRY_POINTS_LOADED", False)
    monkeypatch.setattr(
        loader_module.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )

    adapter = load_adapter("entrypoint_mock")

    assert isinstance(adapter, MockBrokerAdapter)


@pytest.mark.parametrize("failure_phase", ["load", "register"])
def test_entry_point_failure_does_not_block_later_adapters(
    monkeypatch, caplog, failure_phase, isolated_adapter_registry
) -> None:
    bad_name = f"broken_{failure_phase}"
    good_name = f"entrypoint_good_{failure_phase}"
    attempts = {"load": 0, "register": 0}

    def bad_load():
        attempts["load"] += 1
        if failure_phase == "load":
            raise RuntimeError("entry point load failed")

        def bad_register():
            attempts["register"] += 1
            raise RuntimeError("entry point registration failed")

        return bad_register

    def good_load():
        def register_good() -> None:
            register_adapter(good_name, MockBrokerAdapter)

        return register_good

    class FakeEntryPoint:
        def __init__(self, name, load_callback):
            self.name = name
            self._load_callback = load_callback
            self.load_calls = 0

        def load(self):
            self.load_calls += 1
            return self._load_callback()

    class FakeEntryPoints(list):
        def select(self, *, group: str):
            assert group == loader_module.ENTRY_POINT_GROUP
            return self

    bad_entry_point = FakeEntryPoint(bad_name, bad_load)
    monkeypatch.setattr(loader_module, "_ENTRY_POINTS_LOADED", False)
    monkeypatch.setattr(
        loader_module.metadata,
        "entry_points",
        lambda: FakeEntryPoints([bad_entry_point, FakeEntryPoint(good_name, good_load)]),
    )

    with caplog.at_level(logging.WARNING, logger=loader_module.__name__):
        assert isinstance(load_adapter("mock"), MockBrokerAdapter)
        assert isinstance(load_adapter(good_name), MockBrokerAdapter)
        assert isinstance(load_adapter("mock"), MockBrokerAdapter)

    warning_records = [record for record in caplog.records if bad_name in record.getMessage()]
    assert len(warning_records) == 1
    assert warning_records[0].exc_info is not None
    assert bad_entry_point.load_calls == 1
    assert attempts["load"] == 1
    assert attempts["register"] == (1 if failure_phase == "register" else 0)
    assert loader_module._ENTRY_POINTS_LOADED is True


@pytest.mark.parametrize("log_failure", ["name", "warning"])
def test_logging_failures_do_not_block_later_entry_points(
    monkeypatch, caplog, log_failure, isolated_adapter_registry
) -> None:
    good_name = f"entrypoint_good_after_log_failure_{log_failure}"

    class BadEntryPoint:
        @property
        def name(self):
            if log_failure == "name":
                raise RuntimeError("entry point name unavailable")
            return "broken-warning"

        def load(self):
            raise RuntimeError("entry point load failed")

    class GoodEntryPoint:
        name = good_name

        def load(self):
            def register_good() -> None:
                register_adapter(good_name, MockBrokerAdapter)

            return register_good

    class FakeEntryPoints(list):
        def select(self, *, group: str):
            assert group == loader_module.ENTRY_POINT_GROUP
            return self

    monkeypatch.setattr(loader_module, "_ENTRY_POINTS_LOADED", False)
    monkeypatch.setattr(
        loader_module.metadata,
        "entry_points",
        lambda: FakeEntryPoints([BadEntryPoint(), GoodEntryPoint()]),
    )
    if log_failure == "warning":

        def fail_warning(*args, **kwargs):
            raise RuntimeError("logging failed")

        monkeypatch.setattr(loader_module.logger, "warning", fail_warning)

    with caplog.at_level(logging.WARNING, logger=loader_module.__name__):
        assert isinstance(load_adapter("mock"), MockBrokerAdapter)
        assert isinstance(load_adapter(good_name), MockBrokerAdapter)

    assert loader_module._ENTRY_POINTS_LOADED is True
    if log_failure == "name":
        warning_records = [
            record for record in caplog.records if "<unknown>" in record.getMessage()
        ]
        assert len(warning_records) == 1
        assert warning_records[0].exc_info is not None
        assert str(warning_records[0].exc_info[1]) == "entry point load failed"


def test_base_exception_propagates_and_allows_entry_point_retry(
    monkeypatch, isolated_adapter_registry
) -> None:
    class EntryPointAbort(BaseException):
        pass

    class FakeEntryPoint:
        name = "abort_once"

        def __init__(self):
            self.load_calls = 0

        def load(self):
            self.load_calls += 1
            if self.load_calls == 1:
                raise EntryPointAbort("stop discovery")

            def register_after_retry() -> None:
                register_adapter("entrypoint_after_abort", MockBrokerAdapter)

            return register_after_retry

    class FakeEntryPoints(list):
        def select(self, *, group: str):
            assert group == loader_module.ENTRY_POINT_GROUP
            return self

    entry_point = FakeEntryPoint()
    monkeypatch.setattr(loader_module, "_ENTRY_POINTS_LOADED", False)
    monkeypatch.setattr(
        loader_module.metadata,
        "entry_points",
        lambda: FakeEntryPoints([entry_point]),
    )

    with pytest.raises(EntryPointAbort, match="stop discovery"):
        load_adapter("mock")

    assert loader_module._ENTRY_POINTS_LOADED is False
    assert isinstance(load_adapter("mock"), MockBrokerAdapter)
    assert isinstance(load_adapter("entrypoint_after_abort"), MockBrokerAdapter)
    assert entry_point.load_calls == 2
    assert loader_module._ENTRY_POINTS_LOADED is True


@pytest.mark.parametrize("failure_stage", ["entry_points", "selection"])
def test_entry_point_enumeration_errors_propagate_and_allow_retry(
    monkeypatch, failure_stage
) -> None:
    attempts = {"entry_points": 0, "selection": 0}

    class FakeEntryPoints(list):
        def select(self, *, group: str):
            attempts["selection"] += 1
            if failure_stage == "selection" and attempts["selection"] == 1:
                raise RuntimeError("selection failed")
            return self

    def get_entry_points():
        attempts["entry_points"] += 1
        if failure_stage == "entry_points" and attempts["entry_points"] == 1:
            raise RuntimeError("enumeration failed")
        return FakeEntryPoints()

    monkeypatch.setattr(loader_module, "_ENTRY_POINTS_LOADED", False)
    monkeypatch.setattr(loader_module.metadata, "entry_points", get_entry_points)

    with pytest.raises(RuntimeError):
        load_adapter("mock")

    assert loader_module._ENTRY_POINTS_LOADED is False
    assert isinstance(load_adapter("mock"), MockBrokerAdapter)
    assert loader_module._ENTRY_POINTS_LOADED is True
