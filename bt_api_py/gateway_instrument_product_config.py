"""Validation for explicitly configured ProductID observer grants."""

from dataclasses import dataclass

from bt_api_base.account_protocol import account_venue_contains_instrument_venue
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireRole, WireScope

from bt_api_py.gateway_instrument_product_codec import OPERATIONS


@dataclass(frozen=True)
class InstrumentProductGrant:
    principal_ref: str
    actor_ref: str
    execution_domain_ref: str
    account_ref: str
    instruments: frozenset[MarketInstrumentKey]

    def __post_init__(self):
        if (
            any(
                type(v) is not str
                or not v
                or v != v.strip()
                or not v.isprintable()
                or len(v.encode("utf-8")) > 256
                for v in (
                    self.principal_ref,
                    self.actor_ref,
                    self.execution_domain_ref,
                    self.account_ref,
                )
            )
            or type(self.instruments) is not frozenset
            or not self.instruments
            or len(self.instruments) > 4096
            or any(type(k) is not MarketInstrumentKey for k in self.instruments)
        ):
            raise ValueError("INVALID_INSTRUMENT_PRODUCT_GRANT")


PORT = "service.instrument_product_read"


def validate_product_grants(grants, *, config, authority, account_key, account_scope):
    item = config.components["gateway"]["settings"]
    enabled = PORT in item["bind_ports"]
    if type(grants) is not frozenset or enabled != bool(grants) or len(grants) > 4096:
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_CONFIGURATION")
    if any(type(g) is not InstrumentProductGrant for g in grants):
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_CONFIGURATION")
    if len({g.principal_ref for g in grants}) != len(grants):
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_CONFIGURATION")
    by_principal = {g.principal_ref: g for g in grants}
    seen = set()
    for principal, entries in authority.grants.items():
        for entry in entries:
            for resource in entry.resources:
                if resource.port != PORT:
                    continue
                grant = by_principal.get(principal)
                identity = entry.identity
                if (
                    grant is None
                    or principal in seen
                    or identity.role is not WireRole.SERVICE
                    or identity.delegate_ref is not None
                    or identity.principal_ref != principal
                    or identity.actor_ref != grant.actor_ref
                    or grant.execution_domain_ref != authority.scope.execution_domain_ref
                    or grant.account_ref != account_scope
                    or resource.scope
                    != WireScope(grant.execution_domain_ref, account_ref=account_scope)
                    or resource.instance_id != item["instance_id"]
                    or resource.scope_ref != config.scope_ref
                    or resource.operations != OPERATIONS
                ):
                    raise ValueError("INVALID_INSTRUMENT_PRODUCT_CONFIGURATION")
                seen.add(principal)
    if seen != set(by_principal):
        raise ValueError("INVALID_INSTRUMENT_PRODUCT_CONFIGURATION")
    for grant in grants:
        if any(
            key.provider != account_key.provider
            or key.environment != account_key.environment
            or key.product_type != "future"
            or not account_venue_contains_instrument_venue(account_key, key.venue)
            for key in grant.instruments
        ):
            raise ValueError("INVALID_INSTRUMENT_PRODUCT_CONFIGURATION")
