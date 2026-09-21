"""Regression coverage for runtime type-hint introspection."""

from __future__ import annotations

from typing import get_type_hints

import pytest

from bt_api_py import _feed_adapter, _plugin_catalog
from bt_api_py._contracts import models
from bt_api_py._venue_mappers import binance, okx
from bt_api_py.brokers import base as broker_base
from bt_api_py.brokers import loader as broker_loader
from bt_api_py.forwarding import (
    btapi_backend,
    btapi_bridge,
    private_event_pump,
    router,
    service,
    source_supervisor,
)
from bt_api_py.monitoring import exchange_health
from bt_api_py.risk_management.core import (
    actions,
    limits_manager,
    policy_engine,
    risk_assessor,
)
from bt_api_py.security_compliance.auth import oauth2_provider
from bt_api_py.security_compliance.core import audit_logger, compliance_monitor
from bt_api_py.security_compliance.monitoring import security_monitoring
from bt_api_py.testing import contract_cases
from scripts import fill_missing_docstrings
from scripts.ci import check_format_ratchet, check_quality_ratchet, submodule_validation

TYPE_HINT_TARGETS = (
    ("fill_missing_docstrings.find_python_files", fill_missing_docstrings.find_python_files),
    (
        "submodule_validation._artifact_subprocess_env",
        submodule_validation._artifact_subprocess_env,
    ),
    ("submodule_validation.write_junit", submodule_validation.write_junit),
    ("submodule_validation.write_markdown", submodule_validation.write_markdown),
    (
        "check_quality_ratchet.counts_from_ruff_payload",
        check_quality_ratchet.counts_from_ruff_payload,
    ),
    ("check_quality_ratchet.missing_scope_paths", check_quality_ratchet.missing_scope_paths),
    ("check_quality_ratchet._run_ruff", check_quality_ratchet._run_ruff),
    ("check_quality_ratchet.scan_ruff", check_quality_ratchet.scan_ruff),
    ("check_quality_ratchet.build_snapshot", check_quality_ratchet.build_snapshot),
    ("check_quality_ratchet.main", check_quality_ratchet.main),
    ("check_format_ratchet.scope_paths", check_format_ratchet.scope_paths),
    ("check_format_ratchet._run_ruff", check_format_ratchet._run_ruff),
    ("check_format_ratchet.scan_modules", check_format_ratchet.scan_modules),
    (
        "check_format_ratchet.module_scope_differences",
        check_format_ratchet.module_scope_differences,
    ),
    ("check_format_ratchet.build_snapshot", check_format_ratchet.build_snapshot),
    ("check_format_ratchet._write_snapshot", check_format_ratchet._write_snapshot),
    ("check_format_ratchet._warn_version_mismatch", check_format_ratchet._warn_version_mismatch),
    ("check_format_ratchet.main", check_format_ratchet.main),
    ("models.InstrumentSpec", models.InstrumentSpec),
    ("models.FeeSchedule", models.FeeSchedule),
    ("models.FundingSnapshot", models.FundingSnapshot),
    ("models.TradingReadiness", models.TradingReadiness),
    ("PluginCatalog.__init__", _plugin_catalog.PluginCatalog.__init__),
    ("BrokerAdapter.stream_events", broker_base.BrokerAdapter.stream_events),
    ("ZmqBtApiBackend._private_cache_event", btapi_backend.ZmqBtApiBackend._private_cache_event),
    (
        "ZmqBtApiBackend._positions_from_payloads",
        btapi_backend.ZmqBtApiBackend._positions_from_payloads,
    ),
    ("ZmqBtApiBackend._orders_from_payloads", btapi_backend.ZmqBtApiBackend._orders_from_payloads),
    ("ZmqBtApiBackend._fills_from_payloads", btapi_backend.ZmqBtApiBackend._fills_from_payloads),
    ("HealthCheck", exchange_health.HealthCheck),
    ("ActionMixin", actions.ActionMixin),
    ("ActionMixin._initialize_default_actions", actions.ActionMixin._initialize_default_actions),
    ("PolicyEngine", policy_engine.PolicyEngine),
    ("OAuth2Provider._normalize_scopes", oauth2_provider.OAuth2Provider._normalize_scopes),
    (
        "OAuth2Provider._normalize_redirect_uris",
        oauth2_provider.OAuth2Provider._normalize_redirect_uris,
    ),
    (
        "OAuth2Provider._normalize_grant_types",
        oauth2_provider.OAuth2Provider._normalize_grant_types,
    ),
    ("AuditLogger.subscribe", audit_logger.AuditLogger.subscribe),
    ("ComplianceRule", compliance_monitor.ComplianceRule),
    (
        "SecurityMonitoring.add_alert_handler",
        security_monitoring.SecurityMonitoring.add_alert_handler,
    ),
    ("FeedAdapter.__init__", _feed_adapter.FeedAdapter.__init__),
    ("FeedAdapter._call_arguments", _feed_adapter.FeedAdapter._call_arguments),
    ("FeedAdapter.make_order", _feed_adapter.FeedAdapter.make_order),
    ("FeedAdapter.async_make_order", _feed_adapter.FeedAdapter.async_make_order),
    ("binance.map_order_request", binance.map_order_request),
    ("okx.map_order_request", okx.map_order_request),
    ("broker_loader.load_adapter", broker_loader.load_adapter),
    ("run_broker_contract_cases", contract_cases.run_broker_contract_cases),
    ("BtApiForwardingBridge.__init__", btapi_bridge.BtApiForwardingBridge.__init__),
    ("PrivateEventPump.__init__", private_event_pump.PrivateEventPump.__init__),
    ("OrderRouter.__init__", router.OrderRouter.__init__),
    ("ForwardingRuntime", service.ForwardingRuntime),
    ("ZmqForwardingRuntime.__init__", service.ZmqForwardingRuntime.__init__),
    ("UpstreamSource.start", source_supervisor.UpstreamSource.start),
    ("SourceSupervisor.subscribe", source_supervisor.SourceSupervisor.subscribe),
    ("LimitsManager.check_pre_trade_limits", limits_manager.LimitsManager.check_pre_trade_limits),
    ("LimitsManager.check_position_limits", limits_manager.LimitsManager.check_position_limits),
    (
        "LimitsManager._check_compliance_limits",
        limits_manager.LimitsManager._check_compliance_limits,
    ),
    (
        "LimitsManager._check_margin_requirement",
        limits_manager.LimitsManager._check_margin_requirement,
    ),
    (
        "LimitsManager._check_max_order_size",
        limits_manager.LimitsManager._check_max_order_size,
    ),
    (
        "LimitsManager._check_position_limits",
        limits_manager.LimitsManager._check_position_limits,
    ),
    ("LimitsManager._check_risk_limits", limits_manager.LimitsManager._check_risk_limits),
    ("PolicyEngine.evaluate_order_policy", policy_engine.PolicyEngine.evaluate_order_policy),
    ("PolicyEngine.evaluate_risk_policy", policy_engine.PolicyEngine.evaluate_risk_policy),
    ("RiskAssessor.assess_risk", risk_assessor.RiskAssessor.assess_risk),
)


@pytest.mark.parametrize(("target_name", "target"), TYPE_HINT_TARGETS)
def test_runtime_type_hints_resolve(target_name, target):
    try:
        get_type_hints(target)
    except NameError as error:
        pytest.fail(f"get_type_hints({target_name}) could not resolve a runtime type: {error}")
