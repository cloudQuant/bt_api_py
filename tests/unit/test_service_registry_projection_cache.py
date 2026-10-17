from __future__ import annotations

import json
from pathlib import Path

import pytest
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCapability,
    ServiceRegistry,
)


def test_active_registration_projection_memo_is_exact_bounded_and_keeps_authority(
    tmp_path: Path,
) -> None:
    now = [100.0]
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        "principal-a",
        "actor-a",
    )
    scope_template = WireScope(
        "live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-a",
    )
    scope = WireScope(
        "live:venue-a",
        account_ref="account-a",
        strategy_ref="strategy-a",
        run_id="run-a",
    )
    operations = frozenset({"market.subscribe", "market.unsubscribe"})
    registry = ServiceRegistry(
        tmp_path / "projection-cache.sqlite",
        service_id="market-service",
        capabilities=(
            ServiceCapability(
                "market-hub",
                "service.market_source",
                operations,
                frozenset(),
            ),
        ),
        grants={
            "curve-a": (
                PrincipalGrant(
                    identity,
                    (
                        ResourceGrant(
                            "market-hub",
                            "service.market_source",
                            "market-grant-a",
                            scope_template,
                            operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="policy-v1",
        requirements_version="requirements-v1",
        lease_seconds=10,
        max_sessions=1,
        clock=lambda: now[0],
    )
    try:
        registration_request = WireEnvelope(
            header=WireHeader(
                protocol_version=2,
                kind=WireMessageKind.REQUEST,
                message_id="registration-message",
                correlation_id=None,
                identity=identity,
                scope=scope,
                operation="service.register",
                schema_id="sdk.service.control",
                schema_version=1,
                sent_at_utc="2026-10-17T12:00:00Z",
            ),
            payload={
                "registration_key": "registration-a",
                "client_session_id": "client-session-a",
                "instance_id": "market-hub",
                "port": "service.market_source",
                "scope_ref": "market-grant-a",
                "operations": tuple(sorted(operations)),
            },
        )
        response = registry.handle("curve-a", registration_request)
        assert response["ok"] is True
        registration_id = response["result"]["registration_id"]

        decode_calls = {"identity": 0, "scope": 0}
        stored_identity = registry._stored_identity
        stored_scope = registry._stored_scope

        def count_identity(value: str):
            decode_calls["identity"] += 1
            return stored_identity(value)

        def count_scope(value: str):
            decode_calls["scope"] += 1
            return stored_scope(value)

        registry._stored_identity = count_identity
        registry._stored_scope = count_scope

        expected = frozenset({registration_id})
        assert registry.active_registration_ids(instance_id="market-hub") == expected
        assert registry.active_registration_ids(instance_id="market-hub") == expected
        assert decode_calls == {"identity": 1, "scope": 1}

        # A current row's Risk receipt is still checked on every projection,
        # even when its exact identity/scope JSON has a decoded-cache hit.
        with registry._transaction():
            registry._db.execute(
                "UPDATE sessions SET risk_policy_status='APPLIED', risk_policy_ref=?, "
                "risk_policy_version=? WHERE registration_id=?",
                ("forged-risk-policy", "a" * 64, registration_id),
            )
        assert registry.active_registration_ids(instance_id="market-hub") == frozenset()
        assert decode_calls == {"identity": 1, "scope": 1}
        with registry._transaction():
            registry._db.execute(
                "UPDATE sessions SET risk_policy_status='NOT_APPLICABLE', "
                "risk_policy_ref=NULL, risk_policy_version=NULL WHERE registration_id=?",
                (registration_id,),
            )

        # A valid text change misses the memo and refreshes its exact-key entry.
        with registry._transaction():
            original_scope_json = registry._db.execute(
                "SELECT scope_json FROM sessions WHERE registration_id=?",
                (registration_id,),
            ).fetchone()["scope_json"]
            changed_scope = json.loads(original_scope_json)
            changed_scope["run_id"] = "run-b"
            changed_scope_json = json.dumps(
                changed_scope, sort_keys=True, separators=(",", ":")
            )
            registry._db.execute(
                "UPDATE sessions SET scope_json=? WHERE registration_id=?",
                (changed_scope_json, registration_id),
            )
        assert registry.active_registration_ids(instance_id="market-hub") == expected
        assert decode_calls == {"identity": 2, "scope": 2}

        # Malformed persisted JSON still takes the strict parser path instead
        # of reusing the previous DTO from the exact-text cache.
        with registry._transaction():
            registry._db.execute(
                "UPDATE sessions SET scope_json=? WHERE registration_id=?",
                ("{", registration_id),
            )
        with pytest.raises(json.JSONDecodeError):
            registry.active_registration_ids(instance_id="market-hub")
        assert decode_calls == {"identity": 3, "scope": 3}

        with registry._transaction():
            registry._db.execute(
                "UPDATE sessions SET scope_json=? WHERE registration_id=?",
                (original_scope_json, registration_id),
            )
        assert registry.active_registration_ids(instance_id="market-hub") == expected
        assert decode_calls == {"identity": 4, "scope": 4}

        # The memo contains no lease/grace state: authoritative clock expiry
        # removes the durable registration before the projection is returned.
        now[0] = 110.0
        assert registry.active_registration_ids(instance_id="market-hub") == frozenset()
        with registry._transaction():
            row = registry._db.execute(
                "SELECT registration_id FROM sessions WHERE registration_id=?",
                (registration_id,),
            ).fetchone()
        assert row is None
        assert decode_calls == {"identity": 4, "scope": 4}

        # The memo is bounded by the configured session ceiling. A later
        # registration gets a new key and evicts the expired lease's DTO.
        next_registration = WireEnvelope(
            header=WireHeader(
                protocol_version=2,
                kind=WireMessageKind.REQUEST,
                message_id="registration-message-b",
                correlation_id=None,
                identity=identity,
                scope=scope,
                operation="service.register",
                schema_id="sdk.service.control",
                schema_version=1,
                sent_at_utc="2026-10-17T12:00:01Z",
            ),
            payload={
                "registration_key": "registration-b",
                "client_session_id": "client-session-b",
                "instance_id": "market-hub",
                "port": "service.market_source",
                "scope_ref": "market-grant-a",
                "operations": tuple(sorted(operations)),
            },
        )
        next_response = registry.handle("curve-a", next_registration)
        assert next_response["ok"] is True
        next_registration_id = next_response["result"]["registration_id"]
        assert registry.active_registration_ids(instance_id="market-hub") == frozenset(
            {next_registration_id}
        )
        assert len(registry._registration_identity_scope_cache) == registry.max_sessions == 1
        assert next_registration_id in registry._registration_identity_scope_cache
        assert registration_id not in registry._registration_identity_scope_cache
    finally:
        registry.close()
