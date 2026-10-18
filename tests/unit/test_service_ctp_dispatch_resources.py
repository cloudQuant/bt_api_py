"""Component-root wiring for trusted, separate CTP cancellation dispatch."""

from __future__ import annotations

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution import CancelObservation, ProviderObservation, SqliteExecutionStore

from bt_api_py.service_components import (
    ComponentResources,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_registry import ServiceCallContext
from tests.unit import test_service_order_entry_components as fixtures


class _RecordingDispatch:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, intent) -> ProviderObservation:
        self.events.append("submit")
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, f"provider-{intent.intent_id}")

    def cancel(self, intent) -> CancelObservation:
        self.events.append("base-cancel")
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _CancelPort:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.cancelled: list[str] = []

    def cancel(self, intent) -> CancelObservation:
        self.events.append("selected-cancel")
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _prepared_without_listener():
    # This exercises component construction only; no Host binds this endpoint.
    config = fixtures._service_config(
        {"execution": {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "execution-owner",
                "bind_ports": ["service.order_entry", "service.order_query"],
                "options": {"role": "account_owner"},
            },
        }},
        service_id="execution-service",
        listener_endpoint="tcp://127.0.0.1:17999",
    )
    return validate_components(component_specs(config.components))[0]


@pytest.mark.parametrize("use_selected_cancel_port", [False, True])
def test_account_owner_resources_route_cancel_port_and_before_dispatch_hook(
    tmp_path, use_selected_cancel_port
) -> None:
    events: list[str] = []
    dispatch = _RecordingDispatch(events)
    selected = _CancelPort(events)
    hook_calls: list[str] = []

    def before_cancel(intent) -> None:
        events.append("before-cancel")
        hook_calls.append(intent.cancel_id)

    store = SqliteExecutionStore(tmp_path / f"component-cancel-{use_selected_cancel_port}.sqlite3")
    try:
        values = dict(fixtures._resources(store, dispatch).values)
        values["cancel_before_dispatch"] = before_cancel
        if use_selected_cancel_port:
            values["cancel_dispatch"] = selected
        resources = ComponentResources.borrowed(values, ready=lambda: True)
        component = build_component(
            _prepared_without_listener(),
            scope=fixtures._SCOPE,
            resource_provider=lambda *_: resources,
        )
        context = ServiceCallContext(
            WireIdentity(WireRole.STRATEGY_CLIENT, fixtures._PRINCIPAL, fixtures._STRATEGY),
            fixtures._SCOPE,
            "registration-cancel-dispatch",
            fixtures._PRINCIPAL,
        )
        try:
            submitted = component.handlers["orders.submit"](
                context, fixtures._arguments("component-order")
            )
            assert submitted["ok"] is True
            assert dispatch.submitted == ["component-order"]

            cancel_id = "component-cancel"
            cancelled = component.handlers["orders.cancel"](
                context,
                {
                    "cancel_id": cancel_id,
                    "target_intent_id": "component-order",
                    "provider_order_id": "provider-component-order",
                    "metadata_version": "metadata-v1",
                    "tags": {},
                },
            )
            assert cancelled["ok"] is True
            assert hook_calls == [cancel_id]
            assert events[-2:] == ["before-cancel", "selected-cancel" if use_selected_cancel_port else "base-cancel"]
            assert dispatch.cancelled == ([] if use_selected_cancel_port else [cancel_id])
            assert selected.cancelled == ([cancel_id] if use_selected_cancel_port else [])
        finally:
            component.close()
    finally:
        store.close()
