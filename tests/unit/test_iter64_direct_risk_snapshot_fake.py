"""Direct options and known Gateway sections must be checked as snapshots."""

from collections.abc import Mapping

import pytest

from bt_api_py import direct_api_assembly as module
from bt_api_py.direct_api_assembly import DirectApiAssembly, DirectApiAssemblyError


def _construct(options, factory):
    return DirectApiAssembly.from_options(
        options=options,
        exchange_kwargs={},
        execution_config={},
        api_factory=factory,
        api=None,
        expected_exchange_names=(),
        execution_option_keys=(),
        market_data_only=False,
    )


class _ChangingMapping(Mapping):
    """A dynamic view whose membership check is older than its iterator."""

    def __init__(self, data):
        self.data = data

    def __contains__(self, key):
        if key == "risk":
            return False
        return key in self.data

    def __getitem__(self, key):
        return self.data[key]

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)


@pytest.mark.parametrize("section", [None, "gateway", "gateway_strategy"])
def test_dynamic_mapping_risk_visible_in_snapshot_is_rejected(monkeypatch, section):
    def forbidden(*_args, **_kwargs):
        pytest.fail("construction and factory must not run")

    monkeypatch.setattr(DirectApiAssembly, "__init__", forbidden)
    changing = _ChangingMapping({"risk": None})
    options = changing if section is None else {section: changing}
    with pytest.raises(DirectApiAssemblyError, match="^risk_requires_gateway$"):
        _construct(options, forbidden)


@pytest.mark.parametrize("section", [None, "gateway", "gateway_strategy"])
def test_copy_failure_is_finite_and_precedes_factory(monkeypatch, section):
    class BrokenCopy(_ChangingMapping):
        def __iter__(self):
            raise RuntimeError("sensitive copy failure")

    def forbidden(*_args, **_kwargs):
        pytest.fail("construction and factory must not run")

    monkeypatch.setattr(DirectApiAssembly, "__init__", forbidden)
    changing = BrokenCopy({})
    options = changing if section is None else {section: changing}
    with pytest.raises(DirectApiAssemblyError, match="^DIRECT_API_OPTIONS_INVALID$"):
        _construct(options, forbidden)


def test_safe_options_are_read_once_and_factory_receives_the_snapshot(monkeypatch):
    class SnapshotOnly(_ChangingMapping):
        def __init__(self, data):
            super().__init__(data)
            self.reads = []

        def __contains__(self, key):
            assert key == "risk", "factory extraction must use the snapshot"
            return super().__contains__(key)

        def __getitem__(self, key):
            assert key not in self.reads, "source items must not be reread"
            self.reads.append(key)
            return super().__getitem__(key)

    class Condition:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def notify_all(self):
            pass

    monkeypatch.setattr(module, "RLock", object)
    monkeypatch.setattr(module, "Condition", lambda _lock: Condition())
    monkeypatch.setattr(module, "is_unified_bt_api_facade", lambda _api: True)
    options = SnapshotOnly({"debug": True, "transport_mode": "local"})
    received = []

    class Facade:
        def list_exchanges(self):
            return []

    facade = Facade()

    def factory(**kwargs):
        received.append(kwargs)
        return facade

    assembly = _construct(options, factory)
    assert received == []
    assert assembly.ensure_api() is facade
    assert received == [{
        "exchange_kwargs": {},
        "execution_config": None,
        "debug": True,
        "transport_mode": "local",
    }]
    assert options.data == {"debug": True, "transport_mode": "local"}
