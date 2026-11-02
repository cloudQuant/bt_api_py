"""SDK approval validation at the CTP native gate boundary.

The provider calls this while holding its existing request/state lock.  This
module checks the SDK-owned verifier, context, approval and freshness; it does
not issue a capability, select a provider, or establish native session facts.
"""

import hashlib
from collections.abc import Mapping

from ._ctp_credential_binding import _is_verifier, _new_scope
from ._ctp_execution_authorization import (
    CtpExecutionApprovalContext,
    _CAPABILITY_SEAL,
    SIMNOW_APPROVAL_SCHEMA_VERSION,
    SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION,
    SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
    _refresh_runtime_context,
)


def validate_ctp_gate_approval(binding, *, now_utc, parse_utc_timestamp):
    """Refresh and compare the exact owner-bound SDK approval and binding."""
    if (
        _is_verifier(binding.credential_binding_verifier, owner=binding.owner) is not True
        or type(binding.approval_context) is not CtpExecutionApprovalContext
        or getattr(binding.approval_context, "_owner", None) is not binding.owner
    ):
        raise ValueError("binding identity rejected")
    approval = binding.approval
    if (
        type(approval).__module__ != "bt_api_py._ctp_execution_authorization"
        or type(approval).__name__ != "CtpExecutionApproval"
        or getattr(approval, "_seal", None) is not _CAPABILITY_SEAL
        or getattr(approval, "schema_version", None)
        not in {
            SIMNOW_APPROVAL_SCHEMA_VERSION,
            SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION,
            SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
        }
    ):
        raise ValueError("approval type rejected")

    context_refresh = getattr(binding.approval_context, "_refresh", None)
    code = getattr(context_refresh, "__code__", None)
    closure = getattr(context_refresh, "__closure__", None) or ()
    closure_values = dict(zip(getattr(code, "co_freevars", ()), closure))
    captured = {
        name: getattr(cell, "cell_contents", None)
        for name, cell in closure_values.items()
    }
    if (
        captured.get("credential_binding_verifier")
        is not binding.credential_binding_verifier
        or captured.get("self") is not binding.owner
    ):
        raise ValueError("approval verifier owner mismatch")
    refreshed = _refresh_runtime_context(binding.approval_context, binding.owner)
    current = refreshed.as_dict()
    payload = approval.payload
    if not isinstance(payload, Mapping):
        raise ValueError("approval payload rejected")

    # This verifies the current key/config/front HMAC through the
    # context's owner-bound refresh closure, then pins its result to
    # the already signature-verified approval. No HMAC key or
    # credential value crosses the SDK boundary.
    binding_fields = (
        "credential_binding_key_id",
        "credential_binding_hmac_sha256",
    )
    if any(
        not isinstance(current.get(name), str)
        or not current.get(name)
        or current.get(name) != payload.get(name)
        for name in binding_fields
    ):
        raise ValueError("credential binding changed")

    context_fields = (
        "account_fingerprint",
        "trading_day",
        "connection_generation",
        "environment_profile",
        "strategy_identity_sha256",
        "execution_cycle_id",
        "configuration_sha256",
        "backtrader_sha256",
        "bt_api_py_sha256",
        "bt_api_ctp_sha256",
        "bt_api_base_sha256",
        "native_sha256",
        "dependency_hashes_sha256",
        "preflight_sha256",
        "evidence_sha256",
    )
    if any(current.get(name) != payload.get(name) for name in context_fields):
        raise ValueError("approval scope changed")

    # The signature/revocation list were checked when the approval
    # was installed. This client has no fresh deployment trust root,
    # but it still rejects expired approval and revocation windows.
    now = now_utc()
    revocation_snapshot = getattr(approval, "revocation_snapshot", None)
    if (
        now < parse_utc_timestamp(payload.get("not_before"))
        or now >= parse_utc_timestamp(payload.get("expires_at"))
        or not isinstance(revocation_snapshot, Mapping)
        or revocation_snapshot.get("version")
        != payload.get("revocation_snapshot_version")
        or now >= parse_utc_timestamp(revocation_snapshot.get("expires_at"))
    ):
        raise ValueError("approval or revocation snapshot expired")

    return current, payload, captured, binding_fields


def validate_ctp_gate_write_scope(
    binding, *, instrument_id, exchange_id, runtime_action_id,
    managed_cancel_intent_id, now_utc, parse_utc_timestamp,
):
    """Check approved entry or recovery scope after current binding refresh."""
    payload = binding.approval.payload
    if runtime_action_id is None:
        from ._ctp_execution_authorization import (
            APPROVAL_PURPOSE,
            SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION,
        )

        instrument = {"instrument_id": instrument_id, "exchange_id": exchange_id}
        authorized = payload.get("authorized_instruments")
        if (
            payload.get("purpose") != APPROVAL_PURPOSE
            or payload.get("schema_version")
            != SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION
            or instrument not in authorized
            or payload.get("primary_instrument") != instrument
            or managed_cancel_intent_id is not None
        ):
            raise ValueError("entry approval scope mismatch")
    else:
        from ._ctp_execution_authorization import (
            RECOVERY_APPROVAL_PURPOSE,
            SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
        )

        now = now_utc()
        actions = payload.get("recovery_actions")
        matching = [
            action
            for action in actions or ()
            if isinstance(action, Mapping)
            and action.get("action_id") == managed_cancel_intent_id
        ]
        if (
            payload.get("purpose") != RECOVERY_APPROVAL_PURPOSE
            or payload.get("schema_version")
            != SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION
            or len(matching) != 1
            or matching[0].get("action_kind") != "cancel"
            or matching[0].get("instrument_id") != instrument_id
            or matching[0].get("exchange_id") != exchange_id
            or matching[0].get("account_fingerprint")
            != payload.get("account_fingerprint")
            or matching[0].get("trading_day") != payload.get("trading_day")
            or matching[0].get("connection_generation")
            != payload.get("connection_generation")
            or matching[0].get("environment_profile")
            != payload.get("environment_profile")
            or now >= parse_utc_timestamp(matching[0].get("expires_at"))
        ):
            raise ValueError("recovery approval scope mismatch")


def validate_ctp_gate_selected_front(
    binding, *, current, payload, expected_account_sha256, front_scope, binding_fields
):
    """Bind the SDK verifier to the native-selected, provider-checked pair."""
    front_binding_scope = _new_scope(
        {
            "account_fingerprint": current["account_fingerprint"],
            "account_fingerprint_sha256": expected_account_sha256,
            "trading_day": current["trading_day"],
            "connection_generation": current["connection_generation"],
            "environment_profile": current["environment_profile"],
            "td_front": binding.td_front,
            "md_front": binding.md_front,
            "td_front_sha256": hashlib.sha256(
                binding.td_front.encode("utf-8", "strict")
            ).hexdigest(),
            "md_front_sha256": hashlib.sha256(
                binding.md_front.encode("utf-8", "strict")
            ).hexdigest(),
            "backtrader_sha256": payload["backtrader_sha256"],
            "backtrader_runtime_sha256": (
                binding.credential_binding_verifier.package_sha256
            ),
            "bt_api_py_sha256": payload["bt_api_py_sha256"],
            "bt_api_ctp_sha256": payload["bt_api_ctp_sha256"],
            "bt_api_base_sha256": payload["bt_api_base_sha256"],
            "native_sha256": payload["native_sha256"],
            "dependency_hashes_sha256": payload["dependency_hashes_sha256"],
            "configuration_sha256": payload["configuration_sha256"],
            "strategy_identity_sha256": payload["strategy_identity_sha256"],
            "preflight_sha256": payload["preflight_sha256"],
            "evidence_sha256": payload["evidence_sha256"],
            "md_connection_generation": front_scope[
                "md_connection_generation"
            ],
            "md_stream_generation": front_scope["md_stream_generation"],
        }
    )
    selected_pair_binding = binding.credential_binding_verifier.refresh(
        front_binding_scope,
        owner=binding.owner,
        operation="configure_runtime_simnow_credential_binding",
    )
    if any(
        selected_pair_binding.get(name) != payload.get(name)
        for name in binding_fields
    ):
        raise ValueError("selected front pair credential binding mismatch")
