"""Pure account-facts consumer identity projection, not authentication."""
from __future__ import annotations

import hashlib
import json


def account_facts_consumer_ref(*, principal_ref, identity, scope):
    """Derive the stable local cursor label from caller and scope fields."""
    return 'account-facts:' + hashlib.sha256(json.dumps([
        principal_ref, identity.role.value, identity.actor_ref, scope.execution_domain_ref,
        scope.account_ref, scope.strategy_ref,
    ], separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
