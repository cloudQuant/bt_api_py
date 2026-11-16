"""Explicit read-only simulation calendar configuration for the CTP operator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from bt_api_base.service_protocol import WireRole, WireScope
from bt_api_gateway.calendar_config import parse_gateway_calendar_config
from bt_api_py.gateway_calendar_service import CalendarGrant, GatewayCalendarService, ReceiptCheckedCalendarStore
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy
from bt_api_py.trading_calendar_store import TradingCalendarStore

_OPS = frozenset({"calendar.query.v1", "calendar.windows.v1"})


@dataclass(frozen=True)
class CtpGatewayCalendarSettings:
    root: Path | None
    route: str
    grants: frozenset[CalendarGrant]
    range_policies: frozenset[CalendarRangePolicy]
    trading_day: date | None


def parse_calendar_settings(raw, *, config_file, config, authority, account_scope):
    if type(raw) is not dict or set(raw) != {
        "root", "mode", "route", "grants", "range_policies", "simulation_bootstrap"
    } or raw["mode"] != "SIMULATION_ONLY":
        raise ValueError("INVALID_CALENDAR_CONFIGURATION")
    candidate = parse_gateway_calendar_config({"calendars": {"root": raw["root"]}}, config_file=config_file)
    if candidate.status != "CONFIGURED":
        raise ValueError("INVALID_CALENDAR_CONFIGURATION")
    if type(raw["grants"]) is not list or type(raw["range_policies"]) is not list:
        raise ValueError("INVALID_CALENDAR_CONFIGURATION")
    grants = tuple(CalendarGrant(**item) for item in raw["grants"] if type(item) is dict)
    policies = tuple(CalendarRangePolicy(**item) for item in raw["range_policies"] if type(item) is dict)
    if (len(grants) != len(raw["grants"]) or len(set(grants)) != len(grants)
            or len(policies) != len(raw["range_policies"]) or len(set(policies)) != len(policies)):
        raise ValueError("INVALID_CALENDAR_CONFIGURATION")
    bootstrap = raw["simulation_bootstrap"]
    day = None
    if bootstrap is not None:
        if type(bootstrap) is not dict or set(bootstrap) != {"trading_day"}:
            raise ValueError("INVALID_CALENDAR_CONFIGURATION")
        text = bootstrap["trading_day"]
        if type(text) is not str:
            raise ValueError("INVALID_CALENDAR_CONFIGURATION")
        day = date.fromisoformat(text)
        if day.isoformat() != text or candidate.root_path is None:
            raise ValueError("INVALID_CALENDAR_CONFIGURATION")
    settings = CtpGatewayCalendarSettings(candidate.root_path, raw["route"], frozenset(grants),
                                          frozenset(policies), day)
    validate_calendar_settings(settings, config=config, authority=authority, account_scope=account_scope)
    return settings


def validate_calendar_settings(settings, *, config, authority, account_scope):
    item = config.components["gateway"]["settings"]
    enabled = "service.calendar_read" in item["bind_ports"]
    if (settings is None) != (not enabled):
        raise ValueError("INVALID_CALENDAR_CONFIGURATION")
    if settings is None:
        return
    if (type(settings) is not CtpGatewayCalendarSettings or settings.route != "CTP___FUTURE"
            or (settings.root is not None and (not isinstance(settings.root, Path) or not settings.root.is_absolute()))
            or (settings.trading_day is not None and (type(settings.trading_day) is not date or settings.root is None))
            or type(settings.grants) is not frozenset or not settings.grants or len(settings.grants) > 4096
            or any(type(grant) is not CalendarGrant for grant in settings.grants)
            or type(settings.range_policies) is not frozenset or len(settings.range_policies) > 4096
            or any(type(policy) is not CalendarRangePolicy for policy in settings.range_policies)
            or len({p.scope_key for p in settings.range_policies}) != len(settings.range_policies)):
        raise ValueError("INVALID_CALENDAR_CONFIGURATION")
    resources = []
    for principal, entries in authority.grants.items():
        for entry in entries:
            for resource in entry.resources:
                if resource.port != "service.calendar_read":
                    continue
                identity, scope = entry.identity, resource.scope
                if (identity.role is not WireRole.STRATEGY_CLIENT or identity.principal_ref != principal
                        or identity.delegate_ref is not None or scope != WireScope(
                            authority.scope.execution_domain_ref, account_ref=account_scope,
                            strategy_ref=identity.actor_ref, run_id=scope.run_id)
                        or resource.instance_id != item["instance_id"] or resource.scope_ref != config.scope_ref
                        or not resource.operations or not resource.operations <= _OPS):
                    raise ValueError("INVALID_CALENDAR_CONFIGURATION")
                matches = [g for g in settings.grants if g.principal_ref == principal
                           and g.execution_domain_ref == scope.execution_domain_ref
                           and g.account_ref == scope.account_ref and g.strategy_ref == scope.strategy_ref
                           and (scope.run_id is None or g.run_id == scope.run_id)
                           and g.route == settings.route]
                if not matches:
                    raise ValueError("INVALID_CALENDAR_CONFIGURATION")
                resources.append((identity, resource, matches))
    for grant in settings.grants:
        if not any(grant in matches and "calendar.query.v1" in resource.operations
                   for _, resource, matches in resources):
            raise ValueError("INVALID_CALENDAR_CONFIGURATION")
    for policy in settings.range_policies:
        if not any("calendar.windows.v1" in resource.operations and any(
                (g.route, g.venue, g.kind, g.product, g.contract) == policy.scope_key for g in matches)
                   for _, resource, matches in resources):
            raise ValueError("INVALID_CALENDAR_CONFIGURATION")


class _RejectSwitch:
    def verify(self, event, context):
        return None


def create_calendar_service(settings):
    service = GatewayCalendarService(grants=settings.grants, range_policies=settings.range_policies)
    if settings.trading_day is not None:
        from bt_api_ctp.calendar_snapshot import CalendarSnapshot
        binding = SimulationCalendarBinding(
            mode="SIMULATION_ONLY", store=ReceiptCheckedCalendarStore(TradingCalendarStore(settings.root)),
            snapshot_factory=CalendarSnapshot, gate=_RejectSwitch(), route=settings.route,
            source_generation="operator-simulation-" + uuid4().hex,
            trading_day=settings.trading_day, last_sequence=0,
        )
        service.rebind(binding)
    return service
