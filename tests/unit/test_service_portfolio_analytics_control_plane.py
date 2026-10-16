"""CFG06: real risk portfolio_analytics execution control plane over the wire.

Covers the iteration-10 gap clause "portfolio_analytics has no real SDK
registration" with a real ZMQ+CURVE ``ServiceHost`` serving the fixed
``portfolio_analytics`` role over ``service.risk_analysis``:

* an application-client manager runtime drives the full ``analysis.input_upload``
  / ``analysis.submit`` / ``analysis.query`` / ``analysis.cancel`` surface over
  the wire against a durable SQLite ``AnalysisTaskStore``; task state
  progression is deterministic because the host executor advances at most one
  transition per second of the injected store clock;
* a reader client whose grant covers only ``analysis.query`` registers exactly
  that route and is refused the write actions locally
  (``AMBIGUOUS_OR_MISSING_ROUTE``); a binding whose scope has no grant cannot
  register at all (``SCOPE_DENIED``);
* per-scope task quota, pending-task expiry, cancel idempotence, terminal-state
  fixes, upload digest reproducibility, and the ``unavailable`` outcome all
  surface as finite constant codes;
* role/port mistakes (``account_admission`` claiming ``service.risk_analysis``)
  fail closed before any resource provider call, options validate at both the
  risk package boundary and the SDK boundary, and the built-in adapter rejects
  a store whose identity does not match the validated options
  (``ANALYSIS_STORE_INVALID``/``BUILTIN_COMPONENT_INVALID``);
* the base install serves all four actions without numpy/sklearn.
"""

from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
import threading
import time
from contextlib import suppress
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_components import (
    ComponentResources,
    ServiceComponentError,
    build_component,
    component_specs,
    validate_components,
)
from bt_api_py.service_config import (
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from bt_api_risk.portfolio_analytics import (
    CashBalanceV1,
    ExternalCapitalFlowV1,
    ExternalFlowCoverageV1,
    FxObservationV1,
    PortfolioAnalyticsFactsV1,
    PortfolioAnalyticsModelProfileV1,
    PortfolioAnalyticsRequestV1,
    PortfolioMembershipV1,
    PortfolioValuationPointV1,
    SpotPositionV1,
    _canonical_json,
    _facts_payload,
)
from bt_api_risk.service_analytics import (
    AnalysisTaskError,
    AnalysisTaskStore,
    PortfolioAnalyticsTaskService,
)
from bt_api_risk.service_component import RiskServiceError, validate_service_options

_DOMAIN = "cfg06:analysis-execution"
_ANALYSIS_SERVICE_ID = "cfg06-analysis-service"
_ANALYSIS_INSTANCE = "analysis-risk"
_MANAGER_PRINCIPAL = "cfg06-analysis-manager"
_READER_PRINCIPAL = "cfg06-analysis-reader"
_FOREIGN_PRINCIPAL = "cfg06-analysis-foreign"
_MANAGER_SCOPE = WireScope(_DOMAIN, portfolio_ref="portfolio-01")
_FOREIGN_SCOPE = WireScope(_DOMAIN, portfolio_ref="portfolio-02")
_MANAGER_OPERATIONS = frozenset(
    {"analysis.submit", "analysis.query", "analysis.cancel", "analysis.input_upload"}
)
_READER_OPERATIONS = frozenset({"analysis.query"})
_WRITE_OPERATIONS = frozenset(
    {"analysis.submit", "analysis.cancel", "analysis.input_upload"}
)
_PROFILE_REF = "cfg06-analysis-profile-v1"
_NANOSECONDS_PER_DAY = 86_400_000_000_000
_EPOCH = date(1970, 1, 1)
_START = date(2026, 1, 1)
_END = date(2026, 1, 11)
_FLOW_DATE = date(2026, 1, 6)
_T0_NS = 1_800_000_000_000_000_000
_SERVER_OPTIONS = {"high_water_mark": 32, "max_message_bytes": 65_536}
_HANDLED_CHECKS = 45


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _base_profile(endpoint: str, security_ref: str, endpoint_key: str) -> dict[str, Any]:
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {endpoint_key: endpoint},
        "security_profile_ref": security_ref,
        "options": dict(_SERVER_OPTIONS),
    }


class _TestClock:
    """Deterministic store clock shared by the operator and the manager script."""

    def __init__(self, value: int = _T0_NS) -> None:
        self.value = value

    def now(self) -> int:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += seconds * 1_000_000_000


def _day_end_ns(day: date) -> int:
    return (day - _EPOCH).days * _NANOSECONDS_PER_DAY + _NANOSECONDS_PER_DAY - 1


def _position(day: date) -> SpotPositionV1:
    cutoff = _day_end_ns(day)
    return SpotPositionV1(
        position_ref="position-a",
        member_ref="strategy-a",
        instrument_ref="ABC-EUR-SPOT",
        product_kind="SPOT",
        base_currency="ABC",
        quote_currency="EUR",
        price_unit="QUOTE_PER_BASE",
        quantity=Decimal("10"),
        unit_price=Decimal("10"),
        price_as_of_ns=cutoff,
        price_valid_until_ns=cutoff,
        price_source_ref="marks",
        price_source_version="marks-v1",
        price_source_generation="portfolio-generation-1",
        price_source_cursor=f"mark-{day.isoformat()}",
        price_observation_ref=f"observation-{day.isoformat()}",
        instrument_metadata_ref="instrument-catalog",
        instrument_metadata_version="instruments-v1",
    )


def _point(day: date, *, usd_cash: str) -> PortfolioValuationPointV1:
    cutoff = _day_end_ns(day)
    return PortfolioValuationPointV1(
        valuation_date_utc=day,
        as_of_ns=cutoff,
        snapshot_ref=f"snapshot-{day.isoformat()}",
        source_generation="portfolio-generation-1",
        source_cursor=f"portfolio-cursor-{day.isoformat()}",
        valuation_cut_policy="UTC_CALENDAR_DAY_END_V1",
        coverage_complete=True,
        covered_member_refs=("strategy-a",),
        covered_asset_classes=("cash", "spot"),
        cash_balances=(
            CashBalanceV1(
                balance_ref="usd-cash",
                member_ref="strategy-a",
                currency="USD",
                amount=Decimal(usd_cash),
            ),
        ),
        spot_positions=(_position(day),),
    )


def _fx(day: date, rate: str) -> FxObservationV1:
    cutoff = _day_end_ns(day)
    return FxObservationV1(
        observation_ref=f"fx-eur-usd-{day.isoformat()}",
        base_currency="EUR",
        quote_currency="USD",
        direction="BASE_TO_QUOTE",
        rate_unit="QUOTE_PER_BASE",
        rate=Decimal(rate),
        as_of_ns=cutoff,
        valid_until_ns=cutoff,
        source_ref="fx-source",
        source_version="fx-v1",
        source_generation="portfolio-generation-1",
        source_cursor=f"fx-cursor-{day.isoformat()}",
    )


def _facts(
    *,
    portfolio_ref: str = "portfolio-01",
    source_mode: str = "imported",
    membership_version: int = 3,
) -> PortfolioAnalyticsFactsV1:
    return PortfolioAnalyticsFactsV1(
        portfolio_ref=portfolio_ref,
        source_mode=source_mode,
        source_ref="trusted-facts-source",
        source_version="facts-v1",
        source_generation="portfolio-generation-1",
        snapshot_ref="facts-cut-2026-01",
        valuation_basis_ref="cash_spot_after_booked_internal_flows_v1",
        membership=PortfolioMembershipV1(
            portfolio_ref=portfolio_ref,
            membership_version=membership_version,
            member_refs=("strategy-a",),
            effective_start_date_utc=date(2025, 1, 1),
            effective_end_date_utc_exclusive=None,
            source_ref="membership-source",
            source_version="membership-v1",
        ),
        beginning=_point(_START, usd_cash="122"),
        ending=_point(_END, usd_cash="186"),
        external_flows=(
            ExternalCapitalFlowV1(
                flow_ref="capital-1",
                direction="CONTRIBUTION",
                amount=Decimal("20"),
                currency="USD",
                effective_at_ns=_day_end_ns(_FLOW_DATE),
                source_ref="capital-ledger",
                source_version="capital-v1",
                source_generation="portfolio-generation-1",
                source_cursor="capital-cursor-6",
            ),
        ),
        flow_coverage=ExternalFlowCoverageV1(
            source_ref="capital-ledger",
            source_version="capital-v1",
            source_generation="portfolio-generation-1",
            start_as_of_ns=_day_end_ns(_START),
            end_as_of_ns=_day_end_ns(_END),
            start_cursor="capital-cursor-start",
            end_cursor="capital-cursor-end",
            complete=True,
        ),
        fx_observations=(_fx(_START, "1.1"), _fx(_END, "1.2")),
    )


def _facts_dict(**kwargs: Any) -> dict[str, object]:
    return _facts_payload(_facts(**kwargs))


def _request_dict(
    *, portfolio_ref: str = "portfolio-01", membership_version: int = 3
) -> dict[str, object]:
    request = PortfolioAnalyticsRequestV1(
        portfolio_ref=portfolio_ref,
        membership_version=membership_version,
        period_start_date_utc=_START,
        period_end_date_utc=_END,
        reporting_currency="USD",
    )
    return {
        "membership_version": request.membership_version,
        "period_end_date_utc": request.period_end_date_utc.isoformat(),
        "period_start_date_utc": request.period_start_date_utc.isoformat(),
        "portfolio_ref": request.portfolio_ref,
        "reporting_currency": request.reporting_currency,
        "version": 1,
    }


def _upload_digest() -> str:
    return hashlib.sha256(_canonical_json(_facts_payload(_facts()))).hexdigest()


def _model_profile(model_profile_ref: str = _PROFILE_REF) -> PortfolioAnalyticsModelProfileV1:
    return PortfolioAnalyticsModelProfileV1(
        model_profile_ref=model_profile_ref,
        max_mark_age_ns=2 * _NANOSECONDS_PER_DAY,
        max_fx_age_ns=20 * _NANOSECONDS_PER_DAY,
    )


def _analysis_service_config(
    endpoint: str,
    *,
    singleton: tuple[dict[str, Any], tuple[str, ...], str] | None = None,
) -> dict[str, Any]:
    if singleton is None:
        options: dict[str, Any] = {
            "role": "portfolio_analytics",
            "model_profile_ref": _PROFILE_REF,
            "allowed_source_modes": ["research", "imported", "paper"],
            "max_concurrent_tasks": 1,
        }
        bind_ports = ("service.risk_analysis",)
        instance_id = _ANALYSIS_INSTANCE
    else:
        options, bind_ports, instance_id = singleton
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _ANALYSIS_SERVICE_ID,
        "scope_ref": "cfg06-analysis-scope-v1",
        "transport_ref": "analysis-listener",
        "registration_policy_ref": "cfg06-analysis-registration-v1",
        "service_requirements_ref": "cfg06-analysis-requirements-v1",
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": instance_id,
                    "bind_ports": list(bind_ports),
                    "options": options,
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "analysis-listener": _base_profile(
                    endpoint, "cfg06-analysis-server-v1", "rpc_bind"
                )
            },
        },
    }


def _analysis_client_config(
    endpoint: str,
    *,
    application_ref: str,
    scope_ref: str,
    portfolio_ref: str,
) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": application_ref},
        "clients": {
            "risk": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.risk_analysis",
                            "service_id": _ANALYSIS_SERVICE_ID,
                            "instance_id": _ANALYSIS_INSTANCE,
                            "portfolio_ref": portfolio_ref,
                            "scope_ref": scope_ref,
                            "transport_ref": "analysis-connect",
                        }
                    ],
                    "options": {"request_timeout_ms": 3_000},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "analysis-connect": _base_profile(
                    endpoint, "cfg06-analysis-server-v1", "rpc_connect"
                )
            },
        },
    }


def _peer_resolver(_service_id: str) -> WireIdentity:
    return WireIdentity(
        WireRole.SERVICE, f"{_ANALYSIS_SERVICE_ID}-principal", _ANALYSIS_SERVICE_ID
    )


def _analysis_authority(keys: dict[str, bytes]) -> TrustedServiceAuthority:
    manager_identity = WireIdentity(
        WireRole.APPLICATION_CLIENT, _MANAGER_PRINCIPAL, "cfg06-analysis-manager"
    )
    reader_identity = WireIdentity(
        WireRole.APPLICATION_CLIENT, _READER_PRINCIPAL, "cfg06-analysis-reader"
    )
    foreign_identity = WireIdentity(
        WireRole.APPLICATION_CLIENT, _FOREIGN_PRINCIPAL, "cfg06-analysis-foreign"
    )
    return TrustedServiceAuthority(
        identity=WireIdentity(
            WireRole.SERVICE, f"{_ANALYSIS_SERVICE_ID}-principal", _ANALYSIS_SERVICE_ID
        ),
        scope=WireScope(_DOMAIN),
        scope_ref="cfg06-analysis-scope-v1",
        scope_version="cfg06-scope-v1",
        security_ref="cfg06-analysis-server-v1",
        security_version="curve-key-v1",
        registration_policy_ref="cfg06-analysis-registration-v1",
        registration_policy_version="cfg06-registration-v1",
        service_requirements_ref="cfg06-analysis-requirements-v1",
        service_requirements_version="cfg06-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={
            keys["manager_public"]: _MANAGER_PRINCIPAL,
            keys["reader_public"]: _READER_PRINCIPAL,
            keys["foreign_public"]: _FOREIGN_PRINCIPAL,
        },
        grants={
            _MANAGER_PRINCIPAL: (
                PrincipalGrant(
                    manager_identity,
                    (
                        ResourceGrant(
                            _ANALYSIS_INSTANCE,
                            "service.risk_analysis",
                            "cfg06-analysis-manager-v1",
                            _MANAGER_SCOPE,
                            _MANAGER_OPERATIONS,
                        ),
                    ),
                ),
            ),
            _READER_PRINCIPAL: (
                PrincipalGrant(
                    reader_identity,
                    (
                        ResourceGrant(
                            _ANALYSIS_INSTANCE,
                            "service.risk_analysis",
                            "cfg06-analysis-reader-v1",
                            _MANAGER_SCOPE,
                            _READER_OPERATIONS,
                        ),
                    ),
                ),
            ),
            _FOREIGN_PRINCIPAL: (
                PrincipalGrant(
                    foreign_identity,
                    (
                        # Exact scope_ref addressing: the runtime binding below
                        # asks for a reference no grant carries.
                        ResourceGrant(
                            _ANALYSIS_INSTANCE,
                            "service.risk_analysis",
                            "cfg06-analysis-other-v1",
                            _FOREIGN_SCOPE,
                            _READER_OPERATIONS,
                        ),
                    ),
                ),
            ),
        },
    )


def _placeholder_keys() -> dict[str, bytes]:
    names = (
        "server_public",
        "server_secret",
        "manager_public",
        "manager_secret",
        "reader_public",
        "reader_secret",
        "foreign_public",
        "foreign_secret",
    )
    return {name: bytes([65 + index]) * 40 for index, name in enumerate(names)}


def _runtime(
    config: dict[str, Any],
    identity: WireIdentity,
    client_keys: tuple[bytes, bytes, bytes],
    scope_resolver: Any,
    *,
    requested: dict[str, frozenset[str]] | None = None,
) -> ServiceClientRuntime:
    return ServiceClientRuntime(
        parse_client_config(config),
        identity=identity,
        scope_resolver=scope_resolver,
        peer_resolver=_peer_resolver,
        transport_factory=ZmqTransportFactory(
            {"cfg06-analysis-server-v1": ClientCurveKeys(*client_keys)}
        ),
        requested_operations=requested,
    )


def _manager_steps() -> tuple[tuple[Any, ...], ...]:
    facts = _facts_dict()
    live = _facts_dict(source_mode="live")
    foreign = _facts_dict(portfolio_ref="portfolio-99")
    bad_membership = _facts_dict(membership_version=4)
    request = _request_dict()
    divergent = _request_dict(membership_version=4)
    foreign_request = _request_dict(portfolio_ref="portfolio-99")
    cut = {"input_snapshot_id": "cut-2026-01", "facts": facts}
    return (
        ("u1", "call", "analysis.input_upload", cut),
        ("u2", "call", "analysis.input_upload", cut),
        (
            "u3",
            "call",
            "analysis.input_upload",
            {"input_snapshot_id": "cut-live", "facts": live},
        ),
        (
            "u4",
            "call",
            "analysis.input_upload",
            {"input_snapshot_id": "cut-malformed", "facts": {"version": 1}},
        ),
        (
            "u5",
            "call",
            "analysis.input_upload",
            {"input_snapshot_id": "bad id!", "facts": facts},
        ),
        (
            "u6",
            "call",
            "analysis.input_upload",
            {"input_snapshot_id": "cut-foreign", "facts": foreign},
        ),
        (
            "u7",
            "call",
            "analysis.input_upload",
            {"input_snapshot_id": "cut-2026-02", "facts": facts, "note": "extra"},
        ),
        (
            "u8",
            "call",
            "analysis.input_upload",
            {"input_snapshot_id": "cut-bad-membership", "facts": bad_membership},
        ),
        (
            "t1-submit",
            "call",
            "analysis.submit",
            {"task_ref": "task-1", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        ("t1-running", "call", "analysis.query", {"task_ref": "task-1"}),
        ("t1-cancel", "call", "analysis.cancel", {"task_ref": "task-1"}),
        ("t1-cancel-again", "call", "analysis.cancel", {"task_ref": "task-1"}),
        ("t1-cancel-missing", "call", "analysis.cancel", {"task_ref": "task-404"}),
        ("t1-query-missing", "call", "analysis.query", {"task_ref": "task-404"}),
        (
            "t2-submit",
            "call",
            "analysis.submit",
            {"task_ref": "task-2", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        ("t2-pending", "call", "analysis.query", {"task_ref": "task-2"}),
        ("advance-1", "advance", 1),
        ("probe-1", "call", "analysis.query", {"task_ref": "task-1"}),
        ("t2-running", "call", "analysis.query", {"task_ref": "task-2"}),
        ("advance-2", "advance", 1),
        ("probe-2", "call", "analysis.query", {"task_ref": "task-1"}),
        ("t2-succeeded", "call", "analysis.query", {"task_ref": "task-2"}),
        (
            "t2-resubmit",
            "call",
            "analysis.submit",
            {"task_ref": "task-2", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        (
            "t3-submit",
            "call",
            "analysis.submit",
            {"task_ref": "task-3", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        ("advance-3", "advance", 1),
        ("probe-3", "call", "analysis.query", {"task_ref": "task-1"}),
        ("t3-running", "call", "analysis.query", {"task_ref": "task-3"}),
        ("advance-4", "advance", 1),
        ("probe-4", "call", "analysis.query", {"task_ref": "task-1"}),
        ("t3-succeeded", "call", "analysis.query", {"task_ref": "task-3"}),
        ("t3-cancel", "call", "analysis.cancel", {"task_ref": "task-3"}),
        (
            "t4-submit",
            "call",
            "analysis.submit",
            {"task_ref": "task-4", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        (
            "t5-quota",
            "call",
            "analysis.submit",
            {"task_ref": "task-5", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        ("advance-5", "advance", 1),
        ("probe-5", "call", "analysis.query", {"task_ref": "task-1"}),
        ("t4-running", "call", "analysis.query", {"task_ref": "task-4"}),
        (
            "t5-quota-running",
            "call",
            "analysis.submit",
            {"task_ref": "task-5", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        ("t4-cancel", "call", "analysis.cancel", {"task_ref": "task-4"}),
        (
            "t5-submit",
            "call",
            "analysis.submit",
            {"task_ref": "task-5", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        ("advance-6", "advance", 11),
        ("probe-6", "call", "analysis.query", {"task_ref": "task-1"}),
        ("t5-expired", "call", "analysis.query", {"task_ref": "task-5"}),
        (
            "t5-resubmit",
            "call",
            "analysis.submit",
            {"task_ref": "task-5", "input_snapshot_id": "cut-2026-01", "request": request},
        ),
        (
            "t5-conflict",
            "call",
            "analysis.submit",
            {"task_ref": "task-5", "input_snapshot_id": "cut-2026-01", "request": divergent},
        ),
        (
            "t6-no-input",
            "call",
            "analysis.submit",
            {"task_ref": "task-6", "input_snapshot_id": "cut-unknown", "request": request},
        ),
        (
            "t6-submit",
            "call",
            "analysis.submit",
            {
                "task_ref": "task-6",
                "input_snapshot_id": "cut-bad-membership",
                "request": request,
            },
        ),
        ("t6-running", "call", "analysis.query", {"task_ref": "task-6"}),
        ("advance-7", "advance", 1),
        ("probe-7", "call", "analysis.query", {"task_ref": "task-1"}),
        ("t6-unavailable", "call", "analysis.query", {"task_ref": "task-6"}),
        (
            "t7-mismatch",
            "call",
            "analysis.submit",
            {
                "task_ref": "task-7",
                "input_snapshot_id": "cut-2026-01",
                "request": foreign_request,
            },
        ),
        (
            "t7-extra",
            "call",
            "analysis.submit",
            {
                "task_ref": "task-7",
                "input_snapshot_id": "cut-2026-01",
                "request": request,
                "note": "extra",
            },
        ),
    )


def _run_manager_script(
    endpoint: str, keys: dict[str, bytes], clock: _TestClock, done: threading.Event
) -> tuple[list[tuple[Any, ...]], list[BaseException]]:
    """Run the application-client manager on its own owner thread."""

    results: list[tuple[Any, ...]] = []
    errors: list[BaseException] = []
    runtime = None
    try:
        runtime = _runtime(
            _analysis_client_config(
                endpoint,
                application_ref="cfg06-analysis-manager",
                scope_ref="cfg06-analysis-manager-v1",
                portfolio_ref="portfolio-01",
            ),
            WireIdentity(
                WireRole.APPLICATION_CLIENT, _MANAGER_PRINCIPAL, "cfg06-analysis-manager"
            ),
            (keys["server_public"], keys["manager_public"], keys["manager_secret"]),
            lambda ref: {"cfg06-analysis-manager-v1": _MANAGER_SCOPE}[ref],
        )
        runtime.start()
        for step in _manager_steps():
            if step[1] == "advance":
                clock.advance(step[2])
                continue
            label, _, operation, arguments = step
            body = runtime.call(
                "application.risk_analysis",
                operation,
                arguments,
                portfolio_ref="portfolio-01",
            )
            results.append(
                (label, operation, body.get("ok"), body.get("code"), dict(body.get("result") or {}))
            )
    except BaseException as exc:  # pragma: no cover - surfaced to the test
        errors.append(exc)
    finally:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        done.set()
    return results, errors


def _run_reader_script(
    endpoint: str, keys: dict[str, bytes], done: threading.Event
) -> tuple[list[tuple[Any, ...]], list[BaseException]]:
    """Run a query-only application client on its own owner thread."""

    results: list[tuple[Any, ...]] = []
    errors: list[BaseException] = []
    runtime = None
    try:
        runtime = _runtime(
            _analysis_client_config(
                endpoint,
                application_ref="cfg06-analysis-reader",
                scope_ref="cfg06-analysis-reader-v1",
                portfolio_ref="portfolio-01",
            ),
            WireIdentity(
                WireRole.APPLICATION_CLIENT, _READER_PRINCIPAL, "cfg06-analysis-reader"
            ),
            (keys["server_public"], keys["reader_public"], keys["reader_secret"]),
            lambda ref: {"cfg06-analysis-reader-v1": _MANAGER_SCOPE}[ref],
            requested={"application.risk_analysis": _READER_OPERATIONS},
        )
        runtime.start()
        body = runtime.call(
            "application.risk_analysis",
            "analysis.query",
            {"task_ref": "task-2"},
            portfolio_ref="portfolio-01",
        )
        results.append(
            ("reader-query", body.get("ok"), body.get("code"), dict(body.get("result") or {}))
        )
        for label, operation in (
            ("reader-submit", "analysis.submit"),
            ("reader-cancel", "analysis.cancel"),
            ("reader-upload", "analysis.input_upload"),
        ):
            try:
                runtime.call(
                    "application.risk_analysis",
                    operation,
                    {"task_ref": "task-2"},
                    portfolio_ref="portfolio-01",
                )
            except ServiceRuntimeError as exc:
                results.append((label, exc.code))
            else:  # pragma: no cover - fail-closed guard
                results.append((label, "UNEXPECTED"))
    except BaseException as exc:  # pragma: no cover - surfaced to the test
        errors.append(exc)
    finally:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        done.set()
    return results, errors


def _run_foreign_script(
    endpoint: str, keys: dict[str, bytes], done: threading.Event
) -> tuple[list[tuple[Any, ...]], list[BaseException]]:
    """Run a client whose exact scope reference carries no grant."""

    results: list[tuple[Any, ...]] = []
    errors: list[BaseException] = []
    runtime = None
    try:
        runtime = _runtime(
            _analysis_client_config(
                endpoint,
                application_ref="cfg06-analysis-foreign",
                scope_ref="cfg06-analysis-foreign-v1",
                portfolio_ref="portfolio-02",
            ),
            WireIdentity(
                WireRole.APPLICATION_CLIENT, _FOREIGN_PRINCIPAL, "cfg06-analysis-foreign"
            ),
            (keys["server_public"], keys["foreign_public"], keys["foreign_secret"]),
            lambda _ref: _FOREIGN_SCOPE,
        )
        try:
            runtime.start()
        except ServiceRuntimeError as exc:
            results.append(("foreign-start", exc.code))
        else:  # pragma: no cover - fail-closed guard
            results.append(("foreign-start", "UNEXPECTED"))
    except BaseException as exc:  # pragma: no cover - surfaced to the test
        errors.append(exc)
    finally:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        done.set()
    return results, errors


def _serve_until(host: ServiceHost, done: threading.Event, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not done.is_set():
        if time.monotonic() > deadline:
            raise AssertionError("registered client did not finish within its bound")
        host.serve_once(25)


def _run_on_thread(target: Any) -> tuple[threading.Thread, list[Any], list[Any]]:
    results: list[Any] = []
    errors: list[Any] = []

    def runner() -> None:
        thread_results, thread_errors = target()
        results.extend(thread_results)
        errors.extend(thread_errors)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread, results, errors


def test_portfolio_analytics_real_registration_control_plane(tmp_path) -> None:
    endpoint = _endpoint()
    config = parse_service_config(_analysis_service_config(endpoint))
    server_public, server_secret = zmq.curve_keypair()
    manager_public, manager_secret = zmq.curve_keypair()
    reader_public, reader_secret = zmq.curve_keypair()
    foreign_public, foreign_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "manager_public": manager_public,
        "manager_secret": manager_secret,
        "reader_public": reader_public,
        "reader_secret": reader_secret,
        "foreign_public": foreign_public,
        "foreign_secret": foreign_secret,
    }
    clock = _TestClock()
    stores: list[AnalysisTaskStore] = []
    analysis_path = str(tmp_path / "analysis-tasks.sqlite")

    def resources(component, instance_id, options, _scope):
        if component != "risk" or options.get("role") != "portfolio_analytics":
            raise AssertionError("only the fixed portfolio_analytics route is selected")
        if instance_id != _ANALYSIS_INSTANCE:
            raise AssertionError("unexpected instance")
        store = AnalysisTaskStore(
            analysis_path,
            model_profile=_model_profile(),
            allowed_source_modes=options["allowed_source_modes"],
            max_concurrent_tasks=options.get("max_concurrent_tasks", 4),
            pending_ttl_seconds=10,
            clock=clock.now,
        )
        stores.append(store)
        return ComponentResources.borrowed(
            {"analysis": store},
            ready=lambda: True,
        )

    host = None
    try:
        host = ServiceHost(
            config, _analysis_authority(keys), str(tmp_path / "registry.sqlite"), resources
        )
        host.start()
        summary = host.summary
        assert summary["state"] == "started"
        component_summary = summary["components"][0]
        assert component_summary["declared_ports"] == ("service.risk_analysis",)
        assert component_summary["capabilities"] == (
            {
                "port": "service.risk_analysis",
                "operations": (
                    "analysis.cancel",
                    "analysis.input_upload",
                    "analysis.query",
                    "analysis.submit",
                ),
            },
        )

        manager_done = threading.Event()
        manager_thread, manager_results, manager_errors = _run_on_thread(
            lambda: _run_manager_script(endpoint, keys, clock, manager_done)
        )
        _serve_until(host, manager_done)
        manager_thread.join(10)
        assert not manager_thread.is_alive()
        assert not manager_errors, manager_errors
        assert len(manager_results) == _HANDLED_CHECKS
        by = {row[0]: row for row in manager_results}
        assert len(by) == _HANDLED_CHECKS
        digest = _upload_digest()

        # Upload: immutable offline inputs, digest verified independently.
        u1 = by["u1"]
        assert u1[2] is True and u1[3] == "OK"
        assert u1[4]["input_snapshot_id"] == "cut-2026-01"
        assert u1[4]["input_sha256"] == digest
        assert u1[4]["source_mode"] == "imported"
        assert u1[4]["created_ns"] == _T0_NS
        u2 = by["u2"]
        assert u2[2] is True and u2[4] == u1[4]
        u3 = by["u3"]
        assert u3[2] is False and u3[3] == "SOURCE_MODE_NOT_ALLOWED"
        u4 = by["u4"]
        assert u4[2] is False and u4[3] == "INVALID_INPUT"
        u5 = by["u5"]
        assert u5[2] is False and u5[3] == "INVALID_REFERENCE"
        u6 = by["u6"]
        assert u6[2] is False and u6[3] == "INPUT_PORTFOLIO_MISMATCH"
        u7 = by["u7"]
        assert u7[2] is False and u7[3] == "INVALID_REQUEST"
        u8 = by["u8"]
        assert u8[2] is True and u8[4]["input_sha256"] != digest

        # The first submit's post-serve maintain claims the task immediately.
        t1_submit = by["t1-submit"]
        assert t1_submit[2] is True and t1_submit[4]["state"] == "pending"
        assert t1_submit[4]["input_sha256"] == digest
        t1_running = by["t1-running"]
        assert t1_running[2] is True and t1_running[4]["state"] == "running"
        assert t1_running[4]["state_ns"] == _T0_NS
        t1_cancel = by["t1-cancel"]
        assert t1_cancel[2] is True and t1_cancel[4]["state"] == "cancelled"
        t1_cancel_again = by["t1-cancel-again"]
        assert t1_cancel_again[2] is True and t1_cancel_again[4]["state"] == "cancelled"
        assert t1_cancel_again[4]["state_ns"] == t1_cancel[4]["state_ns"]
        assert by["t1-cancel-missing"][2] is False
        assert by["t1-cancel-missing"][3] == "TASK_NOT_FOUND"
        assert by["t1-query-missing"][3] == "TASK_NOT_FOUND"

        # One task transition per store-clock second: task-2 stays pending
        # until the clock advances, then one transition per advance.
        assert by["t2-submit"][2] is True and by["t2-submit"][4]["state"] == "pending"
        t2_pending = by["t2-pending"]
        assert t2_pending[4]["state"] == "pending"
        assert t2_pending[4]["state_ns"] == _T0_NS
        for probe in ("probe-1", "probe-2", "probe-3", "probe-4", "probe-5", "probe-6", "probe-7"):
            assert by[probe][2] is True and by[probe][4]["state"] == "cancelled"
        t2_running = by["t2-running"]
        assert t2_running[2] is True and t2_running[4]["state"] == "running"
        assert t2_running[4]["state_ns"] == _T0_NS + 1_000_000_000
        t2_succeeded = by["t2-succeeded"]
        assert t2_succeeded[2] is True and t2_succeeded[4]["state"] == "succeeded"
        assert t2_succeeded[4]["detail_code"] is None
        assert t2_succeeded[4]["state_ns"] == _T0_NS + 2_000_000_000
        payload = t2_succeeded[4]["result"]
        assert payload["status"] == "AVAILABLE"
        # Wire decoding freezes JSON arrays into tuples.
        assert payload["reason_codes"] == ()
        assert payload["return_ratio"] == "0.223140495867768595"
        assert payload["beginning"]["nav"] == "232"
        assert payload["ending"]["nav"] == "306"
        assert payload["external_cashflow_net"] == "20"
        assert payload["model_profile_ref"] == _PROFILE_REF
        assert len(payload["input_sha256"]) == 64
        assert payload["input_sha256"] != digest
        t2_resubmit = by["t2-resubmit"]
        assert t2_resubmit[2] is True and t2_resubmit[4] == t2_succeeded[4]

        # Identical inputs under a fresh task reference reproduce the exact
        # same deterministic result payload.
        assert by["t3-submit"][2] is True and by["t3-submit"][4]["state"] == "pending"
        t3_running = by["t3-running"]
        assert t3_running[2] is True and t3_running[4]["state"] == "running"
        t3_succeeded = by["t3-succeeded"]
        assert t3_succeeded[2] is True and t3_succeeded[4]["state"] == "succeeded"
        assert t3_succeeded[4]["result"] == t2_succeeded[4]["result"]
        assert t3_succeeded[4]["input_sha256"] == t2_succeeded[4]["input_sha256"]
        assert by["t3-cancel"][2] is False and by["t3-cancel"][3] == "TASK_TERMINAL"

        # Per-scope quota counts pending and running work, and is released by
        # cancel; pending tasks older than the TTL expire fail-closed.
        assert by["t4-submit"][2] is True and by["t4-submit"][4]["state"] == "pending"
        assert by["t5-quota"][2] is False and by["t5-quota"][3] == "TASK_QUOTA_EXCEEDED"
        t4_running = by["t4-running"]
        assert t4_running[2] is True and t4_running[4]["state"] == "running"
        assert by["t5-quota-running"][2] is False
        assert by["t5-quota-running"][3] == "TASK_QUOTA_EXCEEDED"
        t4_cancel = by["t4-cancel"]
        assert t4_cancel[2] is True and t4_cancel[4]["state"] == "cancelled"
        t5_submit = by["t5-submit"]
        assert t5_submit[2] is True and t5_submit[4]["state"] == "pending"
        assert t5_submit[4]["created_ns"] == _T0_NS + 5_000_000_000
        t5_expired = by["t5-expired"]
        assert t5_expired[2] is True and t5_expired[4]["state"] == "expired"
        assert t5_expired[4]["detail_code"] == "TASK_EXPIRED"
        assert t5_expired[4]["result"] is None
        assert t5_expired[4]["state_ns"] == _T0_NS + 16_000_000_000
        t5_resubmit = by["t5-resubmit"]
        assert t5_resubmit[2] is True and t5_resubmit[4] == t5_expired[4]
        assert by["t5-conflict"][2] is False and by["t5-conflict"][3] == "TASK_REF_CONFLICT"
        assert by["t6-no-input"][2] is False and by["t6-no-input"][3] == "INPUT_NOT_FOUND"

        # A well-formed but stale-membership input completes as unavailable.
        t6_submit = by["t6-submit"]
        assert t6_submit[2] is True and t6_submit[4]["state"] == "pending"
        assert by["t6-running"][2] is True and by["t6-running"][4]["state"] == "running"
        t6_unavailable = by["t6-unavailable"]
        assert t6_unavailable[2] is True and t6_unavailable[4]["state"] == "unavailable"
        assert t6_unavailable[4]["detail_code"] == "MEMBERSHIP_VERSION_MISMATCH"
        assert t6_unavailable[4]["state_ns"] == _T0_NS + 17_000_000_000
        unavailable = t6_unavailable[4]["result"]
        assert unavailable["status"] == "UNAVAILABLE"
        assert unavailable["reason_codes"] == ("MEMBERSHIP_VERSION_MISMATCH",)
        assert unavailable["return_ratio"] is None
        assert unavailable["beginning"]["nav"] is None
        assert by["t7-mismatch"][2] is False
        assert by["t7-mismatch"][3] == "INPUT_PORTFOLIO_MISMATCH"
        assert by["t7-extra"][2] is False and by["t7-extra"][3] == "INVALID_REQUEST"

        # A grant restricted to analysis.query registers exactly that route:
        # the read works and every write action is refused locally.
        reader_done = threading.Event()
        reader_thread, reader_results, reader_errors = _run_on_thread(
            lambda: _run_reader_script(endpoint, keys, reader_done)
        )
        _serve_until(host, reader_done)
        reader_thread.join(10)
        assert not reader_thread.is_alive()
        assert not reader_errors, reader_errors
        reader_query = reader_results[0]
        assert reader_query[0] == "reader-query" and reader_query[1] is True
        assert reader_query[2] == "OK" and reader_query[3]["state"] == "succeeded"
        assert reader_query[3]["result"] == t2_succeeded[4]["result"]
        assert reader_results[1:] == [
            ("reader-submit", "AMBIGUOUS_OR_MISSING_ROUTE"),
            ("reader-cancel", "AMBIGUOUS_OR_MISSING_ROUTE"),
            ("reader-upload", "AMBIGUOUS_OR_MISSING_ROUTE"),
        ]

        # A binding whose exact scope_ref carries no grant cannot register.
        foreign_done = threading.Event()
        foreign_thread, foreign_results, foreign_errors = _run_on_thread(
            lambda: _run_foreign_script(endpoint, keys, foreign_done)
        )
        _serve_until(host, foreign_done)
        foreign_thread.join(10)
        assert not foreign_thread.is_alive()
        assert not foreign_errors, foreign_errors
        assert foreign_results == [("foreign-start", "SCOPE_DENIED")]
    finally:
        if host is not None:
            with suppress(BaseException):
                host.close()
        for store in stores:
            with suppress(BaseException):
                store.close()


def test_portfolio_analytics_options_fail_closed() -> None:
    ports = ("service.risk_analysis",)
    good = {
        "role": "portfolio_analytics",
        "model_profile_ref": _PROFILE_REF,
        "allowed_source_modes": ["research", "imported"],
        "max_concurrent_tasks": 1,
        "max_input_bytes": 4_096,
    }
    with pytest.raises(RiskServiceError) as missing:
        validate_service_options(
            {"role": "portfolio_analytics", "allowed_source_modes": ["research"]}, ports
        )
    assert missing.value.code == "INVALID_OPTIONS"
    with pytest.raises(RiskServiceError) as modes:
        validate_service_options(
            {
                "role": "portfolio_analytics",
                "model_profile_ref": _PROFILE_REF,
                "allowed_source_modes": ["live"],
            },
            ports,
        )
    assert modes.value.code == "INVALID_SOURCE_MODES"
    with pytest.raises(RiskServiceError) as extra:
        validate_service_options({**good, "note": True}, ports)
    assert extra.value.code == "INVALID_OPTIONS"
    with pytest.raises(RiskServiceError) as unversioned:
        validate_service_options(
            {**good, "model_profile_ref": "cfg06-analysis-profile"}, ports
        )
    assert unversioned.value.code == "VERSIONED_REFERENCE_REQUIRED"
    with pytest.raises(RiskServiceError) as repeated:
        validate_service_options(
            {**good, "allowed_source_modes": ["research", "research"]}, ports
        )
    assert repeated.value.code == "DUPLICATE_SOURCE_MODE"
    with pytest.raises(RiskServiceError) as quota:
        validate_service_options({**good, "max_concurrent_tasks": 0}, ports)
    assert quota.value.code == "INVALID_OPTION_VALUE"
    with pytest.raises(RiskServiceError) as mismatch:
        validate_service_options(
            {"role": "account_admission", "account_policy_ref": "cfg06-account-policy-v1"},
            ports,
        )
    assert mismatch.value.code == "PORT_ROLE_MISMATCH"
    accepted = validate_service_options(good, ports)
    assert dict(accepted) == {
        "role": "portfolio_analytics",
        "model_profile_ref": _PROFILE_REF,
        "allowed_source_modes": ("research", "imported"),
        "max_concurrent_tasks": 1,
        "max_input_bytes": 4_096,
    }


def test_analysis_port_rejects_other_roles_and_bad_options_before_resources(
    tmp_path,
) -> None:
    """Bad analysis options and role claims fail closed before any resource.

    The SDK config gate is the first refusal layer: every malformed or
    disallowed analysis option document is rejected with a static
    ``ServiceConfigError`` code while parsing, before a host, listener,
    registry, or resource provider exists. The risk-package validator is the
    second, independent gate: an out-of-band ``account_admission`` claim on the
    ``service.risk_analysis`` port is rejected while preparing components,
    still before any resource provider call.
    """

    keys = _placeholder_keys()
    resource_requests: list[tuple[str, str]] = []

    def resources(component, instance_id, _options, _scope):
        resource_requests.append((component, instance_id))
        raise AssertionError("rejected options must never reach the resources")

    def rejected_code(options: dict[str, Any]) -> str:
        """Return the static rejection code for one analysis option document."""

        try:
            config = parse_service_config(
                _analysis_service_config(
                    _endpoint(),
                    singleton=(options, ("service.risk_analysis",), _ANALYSIS_INSTANCE),
                )
            )
        except ServiceConfigError as exc:
            return exc.code
        host = ServiceHost(
            config, _analysis_authority(keys), str(tmp_path / "rejected.sqlite"), resources
        )
        try:
            with pytest.raises(ServiceHostError) as excinfo:
                host.start()
            return excinfo.value.code
        finally:
            with suppress(BaseException):
                host.close()

    # The SDK config gate refuses each option document with a static code
    # before any host object exists.
    assert (
        rejected_code(
            {"role": "portfolio_analytics", "allowed_source_modes": ["research", "imported"]}
        )
        == "MISSING_FIELD"
    )
    assert (
        rejected_code(
            {
                "role": "portfolio_analytics",
                "model_profile_ref": _PROFILE_REF,
                "allowed_source_modes": ["live"],
            }
        )
        == "INVALID_VALUE"
    )
    assert (
        rejected_code(
            {
                "role": "portfolio_analytics",
                "model_profile_ref": _PROFILE_REF,
                "allowed_source_modes": ["research"],
                "note": "extra",
            }
        )
        == "UNKNOWN_FIELD"
    )
    assert (
        rejected_code(
            {
                "role": "portfolio_analytics",
                "model_profile_ref": "cfg06-analysis-profile",
                "allowed_source_modes": ["research"],
            }
        )
        == "VERSIONED_REFERENCE_REQUIRED"
    )
    assert (
        rejected_code(
            {
                "role": "portfolio_analytics",
                "model_profile_ref": _PROFILE_REF,
                "allowed_source_modes": ["research", "research"],
            }
        )
        == "DUPLICATE_VALUE"
    )
    assert (
        rejected_code(
            {
                "role": "portfolio_analytics",
                "model_profile_ref": _PROFILE_REF,
                "allowed_source_modes": ["research"],
                "max_concurrent_tasks": 0,
            }
        )
        == "INVALID_VALUE"
    )

    # The account_admission role cannot claim the analysis port surface: the
    # SDK role/port table refuses the binding outright, and the risk-package
    # validator refuses an out-of-band claim while preparing components.
    assert (
        rejected_code(
            {"role": "account_admission", "account_policy_ref": "cfg06-account-policy-v1"}
        )
        == "PORT_ROLE_MISMATCH"
    )
    specs = component_specs(
        {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _ANALYSIS_INSTANCE,
                    "bind_ports": ["service.risk_analysis"],
                    "options": {
                        "role": "account_admission",
                        "account_policy_ref": "cfg06-account-policy-v1",
                    },
                },
            }
        }
    )
    with pytest.raises(ServiceComponentError) as prepared_error:
        validate_components(specs)
    assert prepared_error.value.code == "COMPONENT_OPTIONS_INVALID"
    assert resource_requests == []


def test_analysis_task_store_fail_closed_semantics(tmp_path) -> None:
    with pytest.raises(AnalysisTaskError) as bad_path:
        AnalysisTaskStore("", model_profile=_model_profile(), allowed_source_modes=("research",))
    assert bad_path.value.code == "INVALID_STORE_PATH"
    with pytest.raises(AnalysisTaskError) as bad_profile:
        AnalysisTaskStore(
            str(tmp_path / "p1.sqlite"), model_profile=object(), allowed_source_modes=("research",)
        )
    assert bad_profile.value.code == "INVALID_MODEL_PROFILE"
    with pytest.raises(AnalysisTaskError) as bad_modes:
        AnalysisTaskStore(
            str(tmp_path / "p2.sqlite"),
            model_profile=_model_profile(),
            allowed_source_modes=("live",),
        )
    assert bad_modes.value.code == "INVALID_SOURCE_MODES"
    with pytest.raises(AnalysisTaskError) as repeated:
        AnalysisTaskStore(
            str(tmp_path / "p3.sqlite"),
            model_profile=_model_profile(),
            allowed_source_modes=("research", "research"),
        )
    assert repeated.value.code == "DUPLICATE_SOURCE_MODE"
    with pytest.raises(AnalysisTaskError) as bad_quota:
        AnalysisTaskStore(
            str(tmp_path / "p4.sqlite"),
            model_profile=_model_profile(),
            allowed_source_modes=("research",),
            max_concurrent_tasks=0,
        )
    assert bad_quota.value.code == "INVALID_TASK_QUOTA"
    with pytest.raises(AnalysisTaskError) as bad_ttl:
        AnalysisTaskStore(
            str(tmp_path / "p5.sqlite"),
            model_profile=_model_profile(),
            allowed_source_modes=("research",),
            pending_ttl_seconds=0,
        )
    assert bad_ttl.value.code == "INVALID_TASK_TTL"
    with pytest.raises(AnalysisTaskError) as frozen_clock:
        AnalysisTaskStore(
            str(tmp_path / "p6.sqlite"),
            model_profile=_model_profile(),
            allowed_source_modes=("research",),
            clock=lambda: 0,
        )
    assert frozen_clock.value.code == "INVALID_CLOCK"

    clock = _TestClock()
    store = AnalysisTaskStore(
        str(tmp_path / "semantics.sqlite"),
        model_profile=_model_profile(),
        allowed_source_modes=("research", "imported"),
        max_concurrent_tasks=2,
        pending_ttl_seconds=10,
        clock=clock.now,
    )
    try:
        service = PortfolioAnalyticsTaskService(store)
        uploaded = service.handle(
            "analysis.input_upload",
            principal_ref="direct-principal",
            scope=_MANAGER_SCOPE,
            arguments={"input_snapshot_id": "cut-1", "facts": _facts_dict()},
        )
        assert uploaded["ok"] is True
        assert uploaded["result"]["input_sha256"] == _upload_digest()
        submitted = service.handle(
            "analysis.submit",
            principal_ref="direct-principal",
            scope=_MANAGER_SCOPE,
            arguments={
                "task_ref": "task-a",
                "input_snapshot_id": "cut-1",
                "request": _request_dict(),
            },
        )
        assert submitted["ok"] is True and submitted["result"]["state"] == "pending"
        assert service.maintain(frozenset()) == {
            "ok": True,
            "code": "OK",
            "result": {"expired": 0, "advanced": True},
        }
        assert service.maintain(frozenset())["result"]["advanced"] is False
        running = service.handle(
            "analysis.query",
            principal_ref="direct-principal",
            scope=_MANAGER_SCOPE,
            arguments={"task_ref": "task-a"},
        )
        assert running["result"]["state"] == "running"
        clock.advance(1)
        assert service.maintain(frozenset())["result"]["advanced"] is True
        finished = service.handle(
            "analysis.query",
            principal_ref="direct-principal",
            scope=_MANAGER_SCOPE,
            arguments={"task_ref": "task-a"},
        )
        assert finished["result"]["state"] == "succeeded"
        assert finished["result"]["detail_code"] is None
        assert finished["result"]["result"]["return_ratio"] == "0.223140495867768595"
        assert finished["result"]["result"]["input_sha256"] != _upload_digest()
        fixed = service.handle(
            "analysis.cancel",
            principal_ref="direct-principal",
            scope=_MANAGER_SCOPE,
            arguments={"task_ref": "task-a"},
        )
        assert fixed["ok"] is False and fixed["code"] == "TASK_TERMINAL"
        foreign = service.handle(
            "analysis.query",
            principal_ref="direct-principal",
            scope=WireScope(_DOMAIN, portfolio_ref="portfolio-02"),
            arguments={"task_ref": "task-a"},
        )
        assert foreign["ok"] is False and foreign["code"] == "TASK_NOT_FOUND"
        run_bound = service.handle(
            "analysis.query",
            principal_ref="direct-principal",
            scope=WireScope(
                _DOMAIN, strategy_ref="strategy-a", run_id="run-1", portfolio_ref="portfolio-01"
            ),
            arguments={"task_ref": "task-a"},
        )
        assert run_bound["ok"] is False and run_bound["code"] == "INVALID_SCOPE"
        unknown = service.handle(
            "analysis.erase",
            principal_ref="direct-principal",
            scope=_MANAGER_SCOPE,
            arguments={"task_ref": "task-a"},
        )
        assert unknown["ok"] is False and unknown["code"] == "UNKNOWN_OPERATION"
        service.close()
        closed = service.handle(
            "analysis.query",
            principal_ref="direct-principal",
            scope=_MANAGER_SCOPE,
            arguments={"task_ref": "task-a"},
        )
        assert closed["ok"] is False and closed["code"] == "CLOSED"
        assert service.maintain(frozenset())["code"] == "CLOSED"
    finally:
        store.close()

    # Fail closed on restart: a running row is never silently re-executed.
    recovery_clock = _TestClock()
    recovery_path = str(tmp_path / "recovery.sqlite")
    first = AnalysisTaskStore(
        recovery_path,
        model_profile=_model_profile(),
        allowed_source_modes=("research", "imported"),
        max_concurrent_tasks=2,
        pending_ttl_seconds=10,
        clock=recovery_clock.now,
    )
    first_service = PortfolioAnalyticsTaskService(first)
    assert first_service.handle(
        "analysis.input_upload",
        principal_ref="direct-principal",
        scope=_MANAGER_SCOPE,
        arguments={"input_snapshot_id": "cut-r", "facts": _facts_dict()},
    )["ok"] is True
    assert first_service.handle(
        "analysis.submit",
        principal_ref="direct-principal",
        scope=_MANAGER_SCOPE,
        arguments={
            "task_ref": "task-r",
            "input_snapshot_id": "cut-r",
            "request": _request_dict(),
        },
    )["ok"] is True
    assert first_service.maintain(frozenset())["result"]["advanced"] is True
    assert first_service.handle(
        "analysis.query",
        principal_ref="direct-principal",
        scope=_MANAGER_SCOPE,
        arguments={"task_ref": "task-r"},
    )["result"]["state"] == "running"
    first.close()
    reopened = AnalysisTaskStore(
        recovery_path,
        model_profile=_model_profile(),
        allowed_source_modes=("research", "imported"),
        max_concurrent_tasks=2,
        pending_ttl_seconds=10,
        clock=recovery_clock.now,
    )
    try:
        interrupted = PortfolioAnalyticsTaskService(reopened).handle(
            "analysis.query",
            principal_ref="direct-principal",
            scope=_MANAGER_SCOPE,
            arguments={"task_ref": "task-r"},
        )
        assert interrupted["result"]["state"] == "failed"
        assert interrupted["result"]["detail_code"] == "TASK_INTERRUPTED"
        assert interrupted["result"]["result"] is None
    finally:
        reopened.close()


def test_build_component_binds_the_analysis_task_store(tmp_path, monkeypatch) -> None:
    config = parse_service_config(_analysis_service_config(_endpoint()))
    prepared = validate_components(component_specs(config.components))
    assert len(prepared) == 1
    assert dict(prepared[0].operations_by_port) == {
        "service.risk_analysis": _MANAGER_OPERATIONS
    }
    assert dict(prepared[0].write_operations_by_port) == {
        "service.risk_analysis": _WRITE_OPERATIONS
    }

    def provider(value: object):
        def _provide(component, instance_id, options, _scope):
            assert component == "risk" and options.get("role") == "portfolio_analytics"
            assert instance_id == _ANALYSIS_INSTANCE
            return ComponentResources.borrowed({"analysis": value}, ready=lambda: True)

        return _provide

    def _open_store(path_name: str, **overrides: Any) -> AnalysisTaskStore:
        kwargs: dict[str, Any] = {
            "model_profile": _model_profile(),
            "allowed_source_modes": ("research", "imported", "paper"),
            "max_concurrent_tasks": 1,
            "pending_ttl_seconds": 10,
        }
        kwargs.update(overrides)
        return AnalysisTaskStore(str(tmp_path / path_name), **kwargs)

    matching = _open_store("match.sqlite")
    mismatched_profile = _open_store(
        "profile.sqlite", model_profile=_model_profile("cfg06-analysis-profile-v2")
    )
    over_quota = _open_store("quota.sqlite", max_concurrent_tasks=2)
    try:
        with pytest.raises(ServiceComponentError) as wrong_type:
            build_component(
                prepared[0], scope=_MANAGER_SCOPE, resource_provider=provider(object())
            )
        assert wrong_type.value.code == "ANALYSIS_STORE_INVALID"

        with pytest.raises(ServiceComponentError) as profile_error:
            build_component(
                prepared[0],
                scope=_MANAGER_SCOPE,
                resource_provider=provider(mismatched_profile),
            )
        assert profile_error.value.code == "ANALYSIS_STORE_INVALID"

        with pytest.raises(ServiceComponentError) as quota_error:
            build_component(
                prepared[0], scope=_MANAGER_SCOPE, resource_provider=provider(over_quota)
            )
        assert quota_error.value.code == "ANALYSIS_STORE_INVALID"

        component = build_component(
            prepared[0], scope=_MANAGER_SCOPE, resource_provider=provider(matching)
        )
        assert set(component.handlers) == set(_MANAGER_OPERATIONS)
        assert callable(component.maintain)
        assert len(component.capabilities) == 1
        capability = component.capabilities[0]
        assert capability.instance_id == _ANALYSIS_INSTANCE
        assert capability.port == "service.risk_analysis"
        assert capability.operations == _MANAGER_OPERATIONS
        assert capability.write_operations == _WRITE_OPERATIONS

        import bt_api_risk.service_analytics as analytics_module

        monkeypatch.setattr(
            analytics_module, "SUPPORTED_OPERATIONS", frozenset({"analysis.query"})
        )
        with pytest.raises(ServiceComponentError) as identity:
            build_component(
                prepared[0], scope=_MANAGER_SCOPE, resource_provider=provider(matching)
            )
        assert identity.value.code == "BUILTIN_COMPONENT_INVALID"
    finally:
        matching.close()
        mismatched_profile.close()
        over_quota.close()


_SUBPROCESS_BOUNDARY = r'''
import os
import sys
import tempfile
from datetime import date
from decimal import Decimal

class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {"numpy", "scipy", "sklearn", "pandas"}:
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, _Blocker())

import bt_api_risk
import bt_api_risk.portfolio_analytics as primitive
import bt_api_risk.service_analytics as analytics
from bt_api_base.service_protocol import WireScope

_NPD = 86_400_000_000_000
_EPOCH = date(1970, 1, 1)
_START = date(2026, 1, 1)
_END = date(2026, 1, 11)
_FLOW = date(2026, 1, 6)

def _end_ns(day):
    return (day - _EPOCH).days * _NPD + _NPD - 1

def _point(day, cash):
    cut = _end_ns(day)
    return primitive.PortfolioValuationPointV1(
        valuation_date_utc=day,
        as_of_ns=cut,
        snapshot_ref=f"snapshot-{day.isoformat()}",
        source_generation="portfolio-generation-1",
        source_cursor=f"portfolio-cursor-{day.isoformat()}",
        valuation_cut_policy="UTC_CALENDAR_DAY_END_V1",
        coverage_complete=True,
        covered_member_refs=("strategy-a",),
        covered_asset_classes=("cash", "spot"),
        cash_balances=(
            primitive.CashBalanceV1("usd-cash", "strategy-a", "USD", Decimal(cash)),
        ),
        spot_positions=(
            primitive.SpotPositionV1(
                "position-a", "strategy-a", "ABC-EUR-SPOT", "SPOT", "ABC", "EUR",
                "QUOTE_PER_BASE", Decimal("10"), Decimal("10"), cut, cut,
                "marks", "marks-v1", "portfolio-generation-1",
                f"mark-{day.isoformat()}", f"observation-{day.isoformat()}",
                "instrument-catalog", "instruments-v1",
            ),
        ),
    )

facts = primitive.PortfolioAnalyticsFactsV1(
    portfolio_ref="portfolio-01",
    source_mode="imported",
    source_ref="trusted-facts-source",
    source_version="facts-v1",
    source_generation="portfolio-generation-1",
    snapshot_ref="facts-cut-2026-01",
    valuation_basis_ref="cash_spot_after_booked_internal_flows_v1",
    membership=primitive.PortfolioMembershipV1(
        portfolio_ref="portfolio-01",
        membership_version=3,
        member_refs=("strategy-a",),
        effective_start_date_utc=date(2025, 1, 1),
        effective_end_date_utc_exclusive=None,
        source_ref="membership-source",
        source_version="membership-v1",
    ),
    beginning=_point(_START, "122"),
    ending=_point(_END, "186"),
    external_flows=(
        primitive.ExternalCapitalFlowV1(
            "capital-1", "CONTRIBUTION", Decimal("20"), "USD", _end_ns(_FLOW),
            "capital-ledger", "capital-v1", "portfolio-generation-1", "capital-cursor-6",
        ),
    ),
    flow_coverage=primitive.ExternalFlowCoverageV1(
        "capital-ledger", "capital-v1", "portfolio-generation-1",
        _end_ns(_START), _end_ns(_END), "capital-cursor-start", "capital-cursor-end", True,
    ),
    fx_observations=(
        primitive.FxObservationV1(
            "fx-eur-usd-2026-01-01", "EUR", "USD", "BASE_TO_QUOTE", "QUOTE_PER_BASE",
            Decimal("1.1"), _end_ns(_START), _end_ns(_START),
            "fx-source", "fx-v1", "portfolio-generation-1", "fx-cursor-1",
        ),
        primitive.FxObservationV1(
            "fx-eur-usd-2026-01-11", "EUR", "USD", "BASE_TO_QUOTE", "QUOTE_PER_BASE",
            Decimal("1.2"), _end_ns(_END), _end_ns(_END),
            "fx-source", "fx-v1", "portfolio-generation-1", "fx-cursor-11",
        ),
    ),
)

state = {"now": 1_800_000_000_000_000_000}
tmp = tempfile.mkdtemp()
store = analytics.AnalysisTaskStore(
    os.path.join(tmp, "analysis.sqlite"),
    model_profile=primitive.PortfolioAnalyticsModelProfileV1(
        model_profile_ref="boundary-profile-v1",
        max_mark_age_ns=2 * _NPD,
        max_fx_age_ns=20 * _NPD,
    ),
    allowed_source_modes=("research", "imported", "paper"),
    max_concurrent_tasks=2,
    pending_ttl_seconds=5,
    clock=lambda: state["now"],
)
service = analytics.PortfolioAnalyticsTaskService(store)
scope = WireScope("cfg06:analysis-execution", portfolio_ref="portfolio-01")
request = {
    "membership_version": 3,
    "period_end_date_utc": "2026-01-11",
    "period_start_date_utc": "2026-01-01",
    "portfolio_ref": "portfolio-01",
    "reporting_currency": "USD",
    "version": 1,
}

uploaded = service.handle(
    "analysis.input_upload",
    principal_ref="boundary-principal",
    scope=scope,
    arguments={"input_snapshot_id": "cut-1", "facts": primitive._facts_payload(facts)},
)
assert uploaded["ok"] is True, uploaded
assert uploaded["result"]["source_mode"] == "imported"
submitted = service.handle(
    "analysis.submit",
    principal_ref="boundary-principal",
    scope=scope,
    arguments={"task_ref": "task-1", "input_snapshot_id": "cut-1", "request": request},
)
assert submitted["ok"] is True, submitted
assert submitted["result"]["state"] == "pending"
assert submitted["result"]["input_sha256"] == uploaded["result"]["input_sha256"]
assert service.maintain(frozenset())["result"]["advanced"] is True
running = service.handle(
    "analysis.query", principal_ref="p", scope=scope, arguments={"task_ref": "task-1"}
)
assert running["result"]["state"] == "running", running
assert service.maintain(frozenset())["result"]["advanced"] is False
state["now"] += 1_000_000_000
assert service.maintain(frozenset())["result"]["advanced"] is True
done = service.handle(
    "analysis.query", principal_ref="p", scope=scope, arguments={"task_ref": "task-1"}
)
assert done["result"]["state"] == "succeeded", done
assert done["result"]["result"]["return_ratio"] == "0.223140495867768595", done
cancelled = service.handle(
    "analysis.cancel", principal_ref="p", scope=scope, arguments={"task_ref": "task-1"}
)
assert cancelled["ok"] is False and cancelled["code"] == "TASK_TERMINAL", cancelled
unknown = service.handle(
    "analysis.erase", principal_ref="p", scope=scope, arguments={"task_ref": "task-1"}
)
assert unknown["ok"] is False and unknown["code"] == "UNKNOWN_OPERATION", unknown
service.close()
closed = service.handle(
    "analysis.query", principal_ref="p", scope=scope, arguments={"task_ref": "task-1"}
)
assert closed["ok"] is False and closed["code"] == "CLOSED", closed
store.close()

for name, marker in (("RiskManager", "analytics"), ("AnomalyDetector", "ml"), ("RiskEnsembleModel", "ml")):
    try:
        getattr(bt_api_risk, name)
    except ImportError as error:
        assert marker in str(error), (name, str(error))
    else:
        raise AssertionError(name + " unexpectedly loaded without its extra")
print("BOUNDARY-OK")
'''


def test_base_install_serves_portfolio_analytics_without_numpy_or_sklearn() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_BOUNDARY],
        capture_output=True,
        text=True,
        cwd=r"D:\bt_api_py",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "BOUNDARY-OK" in proc.stdout
