"""Direct assembly must not silently discard an explicit Risk selection."""

from collections.abc import Mapping

import pytest

from bt_api_py import direct_api_assembly as module
from bt_api_py.direct_api_assembly import DirectApiAssembly, DirectApiAssemblyError


def _construct(options, *, factory=None, execution_option_keys=(), market_data_only=False):
    return DirectApiAssembly.from_options(
        options=options,
        exchange_kwargs={"EXAMPLE": {}},
        execution_config={},
        api_factory=factory,
        api=None,
        expected_exchange_names=("EXAMPLE",),
        execution_option_keys=execution_option_keys,
        market_data_only=market_data_only,
    )


@pytest.mark.parametrize("value", [None, "none", {}, {"service": "risk"}])
@pytest.mark.parametrize("market_data_only", [False, True])
def test_explicit_risk_is_rejected_before_construction_or_factory(monkeypatch, value, market_data_only):
    def forbidden(*_args, **_kwargs):
        pytest.fail("assembly or factory must not run")

    monkeypatch.setattr(DirectApiAssembly, "__init__", forbidden)
    options = {"risk": value, "debug": True}
    before = dict(options)
    with pytest.raises(DirectApiAssemblyError) as caught:
        _construct(options, factory=forbidden, market_data_only=market_data_only)
    assert caught.value.code == "risk_requires_gateway"
    assert str(caught.value) == "risk_requires_gateway"
    assert options == before


@pytest.mark.parametrize("section", ["gateway_strategy", "gateway"])
@pytest.mark.parametrize("value", [None, "none", {}, {"service": "risk"}])
def test_nested_risk_is_rejected_before_construction_or_factory(monkeypatch, section, value):
    def forbidden(*_args, **_kwargs):
        pytest.fail("assembly or factory must not run")

    monkeypatch.setattr(DirectApiAssembly, "__init__", forbidden)
    options = {section: {"risk": value}, "debug": True}
    before = {section: dict(options[section]), "debug": True}
    with pytest.raises(DirectApiAssemblyError, match="^risk_requires_gateway$"):
        _construct(options, factory=forbidden)
    assert options == before


class _RiskOnlyMapping(Mapping):
    def __contains__(self, key):
        assert key == "risk", "option extraction must follow the Risk check"
        return True

    def __getitem__(self, _key):
        pytest.fail("the Risk value must not be read")

    def __iter__(self):
        pytest.fail("the options must not be enumerated")

    def __len__(self):
        pytest.fail("the options must not be sized")


def test_risk_key_is_checked_before_other_mapping_or_constructor_operations(monkeypatch):
    monkeypatch.setattr(DirectApiAssembly, "__init__", lambda *_a, **_k: pytest.fail("constructed"))
    with pytest.raises(DirectApiAssemblyError, match="^risk_requires_gateway$"):
        _construct(_RiskOnlyMapping(), execution_option_keys=iter(("option",)))


class _BrokenMapping(Mapping):
    def __contains__(self, _key):
        raise RuntimeError("synthetic membership failure")

    def __getitem__(self, _key):
        pytest.fail("unexpected item read")

    def __iter__(self):
        pytest.fail("unexpected iteration")

    def __len__(self):
        return 0


def test_broken_membership_fails_before_factory_or_constructor(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("assembly or factory must not run")

    monkeypatch.setattr(DirectApiAssembly, "__init__", forbidden)
    with pytest.raises(DirectApiAssemblyError, match="^DIRECT_API_OPTIONS_INVALID$"):
        _construct(_BrokenMapping(), factory=forbidden)


@pytest.mark.parametrize("section", ["gateway_strategy", "gateway"])
def test_broken_nested_membership_fails_with_finite_error(monkeypatch, section):
    def forbidden(*_args, **_kwargs):
        pytest.fail("assembly or factory must not run")

    monkeypatch.setattr(DirectApiAssembly, "__init__", forbidden)
    with pytest.raises(DirectApiAssemblyError, match="^DIRECT_API_OPTIONS_INVALID$"):
        _construct({section: _BrokenMapping()}, factory=forbidden)


@pytest.mark.parametrize("section", ["gateway_strategy", "gateway"])
def test_broken_nested_section_lookup_fails_with_finite_error(monkeypatch, section):
    class BrokenLookup(dict):
        def __getitem__(self, key):
            if key == section:
                raise RuntimeError("private option")
            return super().__getitem__(key)

    def forbidden(*_args, **_kwargs):
        pytest.fail("assembly or factory must not run")

    monkeypatch.setattr(DirectApiAssembly, "__init__", forbidden)
    with pytest.raises(DirectApiAssemblyError, match="^DIRECT_API_OPTIONS_INVALID$"):
        _construct(BrokenLookup({section: {}}), factory=forbidden)


def test_broken_factory_option_lookup_fails_with_finite_error(monkeypatch):
    class BrokenLookup(dict):
        def __getitem__(self, key):
            if key == "transport_mode":
                raise RuntimeError("private option")
            return super().__getitem__(key)

    def forbidden(*_args, **_kwargs):
        pytest.fail("assembly or factory must not run")

    monkeypatch.setattr(DirectApiAssembly, "__init__", forbidden)
    with pytest.raises(DirectApiAssemblyError, match="^DIRECT_API_OPTIONS_INVALID$"):
        _construct(BrokenLookup({"transport_mode": "local"}), factory=forbidden)


class _Condition:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def notify_all(self):
        pass


def test_without_risk_real_assembly_preserves_options_and_lazy_factory(monkeypatch):
    # Replace local synchronization primitives with inert fakes; no lock or
    # provider is created by this test.
    monkeypatch.setattr(module, "RLock", lambda: object())
    monkeypatch.setattr(module, "Condition", lambda _lock: _Condition())
    monkeypatch.setattr(module, "is_unified_bt_api_facade", lambda _api: True)
    options = {"debug": True, "transport_mode": "local", "unrelated": {"risk": None},
               "gateway": {"other": True}, "gateway_strategy": None}
    before = dict(options)
    received = []

    class Facade:
        def list_exchanges(self):
            return ["EXAMPLE"]

    facade = Facade()

    def factory(**kwargs):
        received.append(kwargs)
        return facade

    assembly = _construct(options, factory=factory)
    assert received == []
    assert assembly.ensure_api() is facade
    assert received == [{
        "exchange_kwargs": {"EXAMPLE": {}},
        "execution_config": None,
        "debug": True,
        "transport_mode": "local",
    }]
    assert options == before


def test_factory_options_still_reject_unknown_risk_key():
    with pytest.raises(DirectApiAssemblyError, match="^DIRECT_API_FACTORY_OPTIONS_INVALID$"):
        DirectApiAssembly(factory_options={"risk": None})
