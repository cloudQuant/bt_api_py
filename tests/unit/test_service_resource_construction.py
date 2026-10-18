"""Partial bootstrap resources remain reachable through build failure paths."""

from __future__ import annotations

import pytest

from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    ServiceResourceConstructionError,
    build_component,
)
from tests.unit.test_service_ctp_dispatch_resources import _prepared_without_listener
from tests.unit.test_service_order_entry_components import _SCOPE


def _failed_provider(resources):
    failure = ServiceResourceConstructionError(resources)

    def provide(*_args):
        raise failure

    return failure, provide


def test_partial_bootstrap_handoff_retains_exact_resource_before_host_cleanup():
    calls = []
    resources = ComponentResources.owned({}, ready=lambda: False, close=lambda: calls.append("close"))
    _failure, provider = _failed_provider(resources)
    retained = []
    with pytest.raises(ServiceComponentError, match="^RESOURCE_RESOLUTION_FAILED$"):
        build_component(_prepared_without_listener(), scope=_SCOPE,
                        resource_provider=provider, on_resources=retained.append)
    assert retained == [resources]
    assert retained[0] is resources
    assert calls == []
    assert resources.ready() is False
    retained[0].close()
    retained[0].close()
    assert calls == ["close"]


def test_direct_build_closes_partial_resource_once_when_cleanup_succeeds():
    calls = []
    resources = ComponentResources.owned({}, ready=lambda: False, close=lambda: calls.append("close"))
    _failure, provider = _failed_provider(resources)
    with pytest.raises(ServiceComponentError, match="^RESOURCE_RESOLUTION_FAILED$"):
        build_component(_prepared_without_listener(), scope=_SCOPE, resource_provider=provider)
    resources.close()
    assert calls == ["close"]


def test_direct_build_preserves_failed_close_resource_for_normal_retry():
    calls = []

    def close():
        calls.append("close")
        if len(calls) == 1:
            raise RuntimeError("synthetic incomplete close")

    resources = ComponentResources.owned({}, ready=lambda: False, close=close)
    failure, provider = _failed_provider(resources)
    with pytest.raises(ServiceResourceConstructionError) as raised:
        build_component(_prepared_without_listener(), scope=_SCOPE, resource_provider=provider)
    assert raised.value is failure
    assert raised.value.resources is resources
    assert calls == ["close"]
    raised.value.resources.close()
    raised.value.resources.close()
    assert calls == ["close", "close"]


def test_failed_handoff_preserves_resource_without_uncoordinated_cleanup():
    calls = []
    retained = []
    resources = ComponentResources.owned({}, ready=lambda: False, close=lambda: calls.append("close"))
    failure, provider = _failed_provider(resources)

    def track(resource):
        retained.append(resource)
        raise RuntimeError("synthetic tracking failure")

    with pytest.raises(ServiceResourceConstructionError) as raised:
        build_component(_prepared_without_listener(), scope=_SCOPE,
                        resource_provider=provider, on_resources=track)
    assert raised.value is failure
    assert retained == [resources]
    assert raised.value.resources is retained[0]
    assert calls == []
    retained[0].close()
    raised.value.resources.close()
    assert calls == ["close"]


def test_partial_bootstrap_rejects_borrowed_noop_cleanup():
    resources = ComponentResources.borrowed({}, ready=lambda: False)
    with pytest.raises(ServiceComponentError, match="^INVALID_PARTIAL_COMPONENT_RESOURCES$"):
        ServiceResourceConstructionError(resources)
