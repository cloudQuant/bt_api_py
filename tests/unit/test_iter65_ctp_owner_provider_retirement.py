"""The retired CTP owner cannot bootstrap outside the direct composition."""

from threading import Lock
from types import MappingProxyType, SimpleNamespace

import pytest

import bt_api_py.ctp_execution_runtime as runtime_module


@pytest.mark.parametrize(
    "options",
    [
        {"role": "account_owner"},
        MappingProxyType({"role": "account_owner"}),
        {"role": "account_owner", "command_bindings": ({"synthetic": "binding"},)},
    ],
)
def test_retired_owner_options_do_not_consume_provider_or_bootstrap(monkeypatch, options):
    scope = object()
    provider = object.__new__(runtime_module.CtpExecutionResourceProvider)
    provider.config = SimpleNamespace(service_scope=scope)
    provider.last_runtime = None
    provider._used = False
    provider._lock = Lock()
    calls = []
    closed = []
    resources = SimpleNamespace(close=lambda: closed.append(True))

    class FakeRuntime:
        def __init__(self, config, instance_id):
            calls.append((config, instance_id))

        def bootstrap(self):
            return resources

    monkeypatch.setattr(runtime_module, "CtpExecutionRuntime", FakeRuntime)
    with pytest.raises(runtime_module.CtpExecutionRuntimeError) as exc:
        provider("execution", "instance", options, scope)
    assert exc.value.code == "LEGACY_EXECUTION_OWNER_RETIRED"
    assert calls == []
    assert provider.last_runtime is None
    assert provider._used is False

    # A rejected old request must not consume the exact direct path.
    assert provider("execution", "instance", runtime_module.DIRECT_MANAGED_OWNER_OPTIONS, scope) is resources
    assert len(calls) == 1
    assert isinstance(provider.last_runtime, FakeRuntime)
    resources.close()
    assert closed == [True]
    with pytest.raises(runtime_module.CtpExecutionRuntimeError) as repeated:
        provider("execution", "instance", runtime_module.DIRECT_MANAGED_OWNER_OPTIONS, scope)
    assert repeated.value.code == "RUNTIME_PROVIDER_ONE_SHOT"


def test_direct_sentinel_still_requires_matching_component_and_scope(monkeypatch):
    scope = object()
    provider = object.__new__(runtime_module.CtpExecutionResourceProvider)
    provider.config = SimpleNamespace(service_scope=scope)
    provider.last_runtime = None
    provider._used = False
    provider._lock = Lock()
    calls = []
    monkeypatch.setattr(runtime_module, "CtpExecutionRuntime", lambda *args: calls.append(args))

    for component, candidate_scope in (("risk", scope), ("execution", object())):
        with pytest.raises(runtime_module.CtpExecutionRuntimeError) as exc:
            provider(component, "instance", runtime_module.DIRECT_MANAGED_OWNER_OPTIONS, candidate_scope)
        assert exc.value.code == "RUNTIME_COMPONENT_BINDING_INVALID"
    assert calls == []
    assert provider.last_runtime is None
    assert provider._used is False
