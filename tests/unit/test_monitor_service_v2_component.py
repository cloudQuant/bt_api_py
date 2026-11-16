"""The optional producer-binding resource is typed and passed through explicitly."""

import pytest
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_ingress import MonitorIngressProducerBinding, MonitorIngressTarget

from bt_api_py import service_components
from bt_api_py.service_components import ComponentResources, ServiceComponentError, component_specs
from tests.unit.test_service_monitor_ingress import _BIND_SCOPE, _PRODUCER, _service_config


def build(tmp_path, bindings):
    prepared = service_components.validate_components(
        component_specs(_service_config().components)
    )[0]
    outbox = DurableOutbox(tmp_path / "events.sqlite")
    resources = ComponentResources.borrowed(
        {
            "outbox": outbox,
            "targets": {_BIND_SCOPE: MonitorIngressTarget("outbox", ("risk.alert",))},
            "producer_bindings": bindings,
        },
        ready=lambda: True,
    )
    return service_components.build_component(
        prepared, scope=_BIND_SCOPE, resource_provider=lambda *_: resources
    )


def test_component_accepts_exact_binding_resource(tmp_path):
    binding = MonitorIngressProducerBinding(
        "binding", "source", _PRODUCER, _BIND_SCOPE, "outbox", ("risk.alert",)
    )
    component = build(tmp_path, (binding,))
    assert component.capabilities[0].operations == frozenset({"events.append"})
    assert component.capabilities[0].write_operations == frozenset()


@pytest.mark.parametrize("invalid", [[object()], (object(),), None])
def test_component_rejects_invalid_binding_resource(tmp_path, invalid):
    with pytest.raises(ServiceComponentError, match="MONITOR_PRODUCER_BINDINGS_INVALID"):
        build(tmp_path, invalid)
