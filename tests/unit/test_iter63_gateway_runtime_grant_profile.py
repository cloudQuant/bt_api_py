"""Installed resource-bundle parsing of a Runtime control grant (D66-08 / 63.12).

No native account or Gateway process: only the trusted profile parser.
"""
from __future__ import annotations

import pytest

from bt_api_py import ctp_gateway_local_profile as profile
from bt_api_py.gateway_direct_service import (
    CONTROL_RUNTIME_OPERATIONS,
    GatewayControlGrant,
    GatewayRuntimeControlProof,
)

ACCOUNT_SCOPE = {
    "execution_domain_ref": "domain", "account_ref": "account", "account_refs": [],
    "strategy_ref": None, "run_id": None, "manual_book_ref": None, "portfolio_ref": None,
    "group_id": None, "leg_id": None,
}
STRATEGY_SCOPE = dict(ACCOUNT_SCOPE, strategy_ref="strategy-a")
OPERATOR_IDENTITY = {
    "role": "application_client", "principal_ref": "ops-p", "actor_ref": "operator-a",
    "delegate_ref": None,
}
RUNTIME_IDENTITY = {
    "role": "application_client", "principal_ref": "runtime-p", "actor_ref": "runtime-a",
    "delegate_ref": None,
}


def entry(identity, public, *, operations, account_scope_allowed, strategies, **extra):
    return {
        "identity": identity,
        "curve_public_key": public,
        "resources": [{
            "instance_id": "gateway-a", "port": "service.direct_write", "scope_ref": "scope",
            "scope": STRATEGY_SCOPE if strategies else ACCOUNT_SCOPE,
            "operations": list(operations),
        }],
        "account_scope_allowed": account_scope_allowed,
        "strategies": list(strategies),
        **extra,
    }


def proof(**changes):
    values = {"delegation_id": "delegation-1", "operator_ref": "operator-a", "expires_at": 200.0}
    values.update(changes)
    return values


def value(*operators):
    return {"upgrade_controls_from_v9": True, "operators": list(operators)}


def parse(raw):
    principals, grants = {}, {}
    control, upgrade, drain, lease, release = profile._controls(raw, principals, grants)
    return control, principals, grants


def operator_entry(**extra):
    return entry(OPERATOR_IDENTITY, "a" * 40, operations={"direct.control.apply"},
                 account_scope_allowed=True, strategies=(), **extra)


def runtime_entry(**extra):
    values = {"kind": "runtime", "runtime_proof": proof(),
              "operations": CONTROL_RUNTIME_OPERATIONS,
              "account_scope_allowed": False, "strategies": ("strategy-a",)}
    values.update(extra)
    return entry(RUNTIME_IDENTITY, "b" * 40, **values)


def test_a_runtime_grant_is_parsed_with_its_explicit_proof():
    control, principals, grants = parse(value(operator_entry(), runtime_entry()))
    assert principals == {b"a" * 40: "ops-p", b"b" * 40: "runtime-p"}
    operator_grant = control["ops-p"]
    assert type(operator_grant) is GatewayControlGrant and operator_grant.kind == "operator"
    grant = control["runtime-p"]
    assert grant.kind == "runtime"
    assert grant.operations == CONTROL_RUNTIME_OPERATIONS
    assert grant.account_scope_allowed is False and grant.strategies == frozenset({"strategy-a"})
    assert grant.runtime_proof == GatewayRuntimeControlProof(
        "delegation-1", "operator-a", 200.0, revoked=False)
    assert "runtime-p" in grants


def test_a_revoked_runtime_grant_still_parses_and_is_refused_at_call_time():
    control, _, _ = parse(value(operator_entry(), runtime_entry(
        runtime_proof=proof(revoked=True))))
    assert control["runtime-p"].runtime_proof.revoked is True


@pytest.mark.parametrize("runtime", [
    # The proof names an operator that is not installed in this bundle.
    {"kind": "runtime", "runtime_proof": proof(operator_ref="missing-operator")},
    # No proof / proof on a non-runtime grant.
    {"kind": "runtime", "runtime_proof": None},
    # Operator authority in a Runtime grant.
    {"kind": "runtime", "runtime_proof": proof(), "account_scope_allowed": True},
    {"kind": "runtime", "runtime_proof": proof(), "strategies": ()},
    {"kind": "runtime", "runtime_proof": proof(),
     "operations": {"direct.control.list"}},
    {"kind": "runtime", "runtime_proof": proof(),
     "operations": {"direct.control.cancel_scope"}},
    # Malformed proof.
    {"kind": "runtime", "runtime_proof": proof(expires_at=float("inf"))},
    {"kind": "runtime", "runtime_proof": proof(expires_at="200")},
    {"kind": "runtime", "runtime_proof": proof(delegation_id="")},
    {"kind": "runtime", "runtime_proof": {**proof(), "extra": 1}},
])
def test_an_unproven_or_overreaching_runtime_entry_is_refused(runtime):
    with pytest.raises(profile.CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        parse(value(operator_entry(), runtime_entry(**runtime)))


def test_a_non_runtime_entry_carrying_a_runtime_proof_is_refused():
    entry_with_proof = entry(OPERATOR_IDENTITY, "a" * 40, operations={"direct.control.apply"},
                             account_scope_allowed=True, strategies=(),
                             runtime_proof=proof())
    with pytest.raises(profile.CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        parse(value(entry_with_proof))


def test_a_risk_grant_is_not_an_operator_an_operator_runtime_proof_may_name():
    risk_identity = {
        "role": "application_client", "principal_ref": "risk-p", "actor_ref": "risk-a",
        "delegate_ref": None,
    }
    risk = entry(risk_identity, "c" * 40, operations={"direct.control.apply"},
                 account_scope_allowed=True, strategies=(), kind="risk",
                 modes=["STOP_NEW", "FREEZE"])
    runtime = runtime_entry(runtime_proof=proof(operator_ref="risk-a"))
    with pytest.raises(profile.CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        parse(value(risk, runtime))
