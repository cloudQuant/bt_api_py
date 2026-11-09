"""Authenticated account-wide net comparison; no caller-supplied account facts."""
from __future__ import annotations

from collections.abc import Mapping
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireRole
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayOwnerServiceCallError
from bt_api_py.service_registry import ServiceCallContext


class GatewayAccountComparison:
    def __init__(self, owner, journal, *, account_scope, principals, instruments):
        if (type(owner) is not GatewayDirectOwner or not owner.is_bound_to(journal)
                or journal.account_scope != account_scope or journal._schema_version < 12
                or type(principals) is not frozenset
                or any(type(item) is not str or not item or item != item.strip() for item in principals)
                or type(instruments) is not frozenset or not instruments
                or any(type(item) is not MarketInstrumentKey for item in instruments)):
            raise ValueError('INVALID_ACCOUNT_COMPARISON')
        self.owner, self.journal = owner, journal
        self.account_scope, self.principals, self.instruments = account_scope, principals, instruments

    def handle(self, context, arguments):
        if (type(context) is not ServiceCallContext or not isinstance(arguments, Mapping)
                or context.principal_ref not in self.principals
                or context.identity.role is not WireRole.APPLICATION_CLIENT
                or context.identity.principal_ref != context.principal_ref
                or context.identity.delegate_ref is not None
                or context.scope.account_ref != self.account_scope
                or context.scope.account_refs or context.scope.strategy_ref is not None
                or context.scope.run_id is not None or context.scope.portfolio_ref is not None
                or context.scope.manual_book_ref is not None or context.scope.group_id is not None
                or context.scope.leg_id is not None):
            return {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
        if set(arguments) != {'instrument'}:
            return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
        try:
            instrument = MarketInstrumentKey.from_dict(arguments['instrument'])
        except Exception:
            return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
        if instrument not in self.instruments:
            return {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
        def read():
            result = self.journal.read_account_net_comparison(instrument)
            if result.status not in {'MATCH', 'MISMATCH'}:
                return {'status': 'NOT_COMPARABLE', 'instrument': instrument.to_dict()}
            return {'status': result.status, 'instrument': instrument.to_dict(),
                'strategy_net': format(result.strategy_net, 'f'), 'external_net': format(result.external_net, 'f'),
                'account_net': format(result.account_net, 'f'), 'difference': format(result.difference, 'f'),
                'cut': result.cut, 'owner_generation': self.journal.generation}
        try:
            return self.owner.submit_service_call(read).result(timeout_seconds=1.0)
        except GatewayOwnerServiceCallError:
            return {'status': 'NOT_COMPARABLE', 'instrument': instrument.to_dict()}
