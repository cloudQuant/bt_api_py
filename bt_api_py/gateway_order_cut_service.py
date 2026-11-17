"""Authenticated read-only view of the original owner's current order cut."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from bt_api_base.gateway_execution_stream_protocol import project_gateway_execution_observation
from bt_api_base.service_protocol import WireRole, WireScope
from bt_api_gateway.observed_order_source import ObservedOrderQueryCut

from bt_api_py.gateway_order_cut_codec import (
    CURRENT,
    OPERATIONS,
    QUERY_PAGE,
    canonical,
    decode_query,
)
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


class GatewayOrderCutService:
    """Borrow source and original owner; no query, ACK, cursor or lifecycle action."""

    def __init__(self, owner, journal, source, *, account_scope):
        from bt_api_py.ctp_gateway_order_cut_source import CtpGatewayOrderCutSource
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner

        if (
            type(owner) is not GatewayDirectOwner
            or type(source) is not CtpGatewayOrderCutSource
            or not owner.is_bound_to(journal)
            or type(account_scope) is not str
            or not account_scope
        ):
            raise ValueError("INVALID_ORDER_CUT_SERVICE")
        self._owner, self._journal, self._source = owner, journal, source
        self._account_scope = account_scope
        self._principals, self._domain = source.principals, source.execution_domain_ref
        self._opaque_key = secrets.token_bytes(32)

    def handle(self, operation, context, arguments):
        def reject(code):
            return {"status": "REQUEST_REJECTED", "code": code}

        if type(context) is not ServiceCallContext or operation not in OPERATIONS:
            return reject("INVALID_REQUEST")
        identity, scope = context.identity, context.scope
        if (
            identity.role is not WireRole.SERVICE
            or identity.delegate_ref is not None
            or identity.principal_ref != context.principal_ref
            or context.principal_ref not in self._principals
            or scope != WireScope(self._domain, account_ref=self._account_scope)
        ):
            return reject("SCOPE_DENIED")
        try:
            args = decode_query(operation, dict(arguments))
        except Exception:
            return reject("INVALID_REQUEST")
        admission = context.dispatch_admission
        if (
            type(admission) is not ServiceDispatchAdmission
            or not admission.matches_context(context)
            or not admission.matches_operation(operation)
            or not admission.matches(arguments)
            or admission.service_epoch is None
            or not callable(context.result_fits)
        ):
            return reject("SCOPE_DENIED")

        def execute():
            try:
                with admission.scope() as check:
                    check()
                    source, journal, owner = self._source, self._journal, self._owner
                    if (
                        not owner.is_bound_to(journal)
                        or source.journal is not journal
                        or journal.account_scope != self._account_scope
                    ):
                        return {"status": "UNAVAILABLE", "code": "SOURCE_UNAVAILABLE"}
                    guard = source.guard
                    cut = source.current()
                    binding = source.current_binding()
                    if (
                        type(cut) is not ObservedOrderQueryCut
                        or cut.status != "CANDIDATE"
                        or binding is None
                    ):
                        return {"status": "UNAVAILABLE", "code": "SOURCE_UNAVAILABLE"}

                    def same():
                        return (
                            self._source is source
                            and self._journal is journal
                            and self._owner is owner
                            and owner.is_bound_to(journal)
                            and source.journal is journal
                            and source.guard is guard
                            and source.current() == cut
                            and source.current_binding() is binding
                        )

                    if not same():
                        return {"status": "UNKNOWN", "code": "SOURCE_CHANGED"}
                    source_ref, session_ref = binding.source_ref, binding.generation
                    common = {
                        "source_ref": source_ref,
                        "session_ref": session_ref,
                        "begin_sequence": cut.begin_sequence,
                        "end_sequence": cut.end_sequence,
                        "query_digest": cut.query_digest,
                    }
                    first = journal.read_observed_order_cut_manifest(
                        guard, **common, after_ordinal=0, limit=1
                    )
                    h = first.header
                    if (
                        h.journal_id != journal.journal_id
                        or h.owner_generation != journal.generation
                        or h.account_scope != self._account_scope
                        or h.source_ref != source_ref
                        or h.session_ref != session_ref
                        or h.current_session_trading_day != binding.trading_day
                        or h.fact_epoch != cut.fact_epoch
                        or h.fact_watermark != cut.fact_watermark
                        or h.base_sequence != cut.base_sequence
                        or h.begin_sequence != cut.begin_sequence
                        or h.end_sequence != cut.end_sequence
                        or h.query_digest != cut.query_digest
                        or h.query_scope != cut.query_scope
                        or h.query_row_count != len(cut.query_fact_digests)
                        or h.source_prefix != cut.source_prefix
                        or h.source_history_has_gap != cut.source_history_has_gap
                    ):
                        return {"status": "UNKNOWN", "code": "SOURCE_CHANGED"}
                    opaque_values = [
                        "sdk.gateway.order_cut.source/1",
                        self._account_scope,
                        h.journal_id,
                        h.owner_generation,
                        source_ref,
                        session_ref,
                    ]
                    opaque = hmac.new(
                        self._opaque_key,
                        json.dumps(opaque_values, separators=(",", ":"), ensure_ascii=True).encode(
                            "ascii"
                        ),
                        hashlib.sha256,
                    ).hexdigest()
                    header = {
                        k: getattr(h, k)
                        for k in (
                            "journal_id",
                            "owner_generation",
                            "fact_epoch",
                            "fact_watermark",
                            "current_session_trading_day",
                            "base_sequence",
                            "begin_sequence",
                            "end_sequence",
                            "source_prefix",
                            "query_digest",
                            "query_scope",
                            "query_row_count",
                            "cut_record_digest",
                            "query_fact_digests_digest",
                            "source_history_has_gap",
                        )
                    }
                    header["source_binding_ref"] = opaque
                    cut_ref = _digest(
                        ["sdk.gateway.order_cut.ref/1", header, first.manifest_digest]
                    )
                    base = {
                        "status": "OBSERVED",
                        "registration_id": context.registration_id,
                        "service_epoch": admission.service_epoch,
                        "cut_ref": cut_ref,
                        "header": header,
                        "query_manifest_digest": first.manifest_digest,
                    }
                    if operation == CURRENT:
                        result = dict(base, schema="sdk.gateway.order_cut.current/1")
                        canonical(result)
                        if not context.result_fits(result):
                            return {"status": "UNAVAILABLE", "code": "RESULT_TOO_LARGE"}
                    else:
                        if args["cut_ref"] != cut_ref:
                            return {"status": "UNAVAILABLE", "code": "CUT_UNAVAILABLE"}
                        cursor = (
                            "after_ordinal" if operation == QUERY_PAGE else "after_source_sequence"
                        )
                        initial = 0 if operation == QUERY_PAGE else cut.begin_sequence
                        if args[cursor] != initial and args["expected_manifest_digest"] is None:
                            return reject("INVALID_REQUEST")
                        limit = args["limit"]
                        while True:
                            if operation == QUERY_PAGE:
                                page = journal.read_observed_order_cut_manifest(
                                    guard,
                                    **common,
                                    after_ordinal=args[cursor],
                                    limit=limit,
                                    expected_manifest_digest=args["expected_manifest_digest"],
                                )
                                if (
                                    page.header != h
                                    or page.manifest_digest != first.manifest_digest
                                ):
                                    return {"status": "UNKNOWN", "code": "SOURCE_CHANGED"}
                                items = [
                                    {
                                        "ordinal": page.after_ordinal + i,
                                        "fact_digest": fact.fact_digest(),
                                        "fact": project_gateway_execution_observation(
                                            fact
                                        ).to_mapping(),
                                    }
                                    for i, fact in enumerate(page.items, 1)
                                ]
                                result = dict(
                                    base,
                                    schema="sdk.gateway.order_cut.query_page/1",
                                    manifest_digest=page.manifest_digest,
                                    after_ordinal=page.after_ordinal,
                                    next_ordinal=page.next_ordinal,
                                    items=items,
                                    has_more=page.has_more,
                                )
                            else:
                                page = journal.read_observed_order_cut_receipts(
                                    guard,
                                    **common,
                                    after_source_sequence=args[cursor],
                                    limit=limit,
                                    expected_manifest_digest=args["expected_manifest_digest"],
                                )
                                if (
                                    page.header != h
                                    or page.query_manifest_digest != first.manifest_digest
                                ):
                                    return {"status": "UNKNOWN", "code": "SOURCE_CHANGED"}
                                items = [
                                    {
                                        "source_sequence": item.source_sequence,
                                        "source_record_digest": item.source_record_digest,
                                        "fact_digest": item.fact_digest,
                                        "fact_sequence": item.fact_sequence,
                                        "disposition": item.disposition,
                                        "fact": project_gateway_execution_observation(
                                            item.fact
                                        ).to_mapping(),
                                    }
                                    for item in page.items
                                ]
                                result = dict(
                                    base,
                                    schema="sdk.gateway.order_cut.receipt_page/1",
                                    manifest_digest=page.manifest_digest,
                                    after_source_sequence=page.after_source_sequence,
                                    next_source_sequence=page.next_source_sequence,
                                    items=items,
                                    has_more=page.has_more,
                                )
                            try:
                                canonical(result)
                                fits = context.result_fits(result)
                            except Exception:
                                fits = False
                            if fits:
                                break
                            if len(items) <= 1:
                                return {"status": "UNAVAILABLE", "code": "RESULT_TOO_LARGE"}
                            limit = max(1, len(items) // 2)
                    if not same():
                        return {"status": "UNKNOWN", "code": "SOURCE_CHANGED"}
                    check()
                    return result
            except Exception:
                return {"status": "UNKNOWN", "code": "SOURCE_UNAVAILABLE"}

        try:
            return self._owner.submit_service_call(execute).result(timeout_seconds=1.0)
        except Exception:
            return {"status": "UNKNOWN", "code": "OWNER_UNAVAILABLE"}
        finally:
            admission.close()
