"""SDK-owned CTP recovery query binding and event-fence orchestration.

The provider projects native rows. The SDK retains session checks, evidence
binding, query barriers, public-plan filtering and its authorization state.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ._contracts.errors import NormalizedApiError

_CTP_EXECUTION_RECOVERY_PUBLIC_FIELDS = (
    "schema_version",
    "status",
    "recovery_required",
    "can_arm_execution",
    "can_arm_recovery",
    "account_fingerprint",
    "trading_day",
    "instrument",
    "connection_generation",
    "strategy_id",
    "execution_cycle_id",
    "remote_position",
    "owned_position",
    "allowed_closes",
    "allowed_cancels",
    "allowed_actions",
    "unknown_ids",
    "evidence_errors",
    "journal_sha256",
    "fencing_epoch",
    "recovery_token_sha256",
)
_CTP_EXECUTION_RECOVERY_BUNDLE_SCOPE_VERSION = "ctp-contract-bundle-v1"
_CTP_EXECUTION_RECOVERY_BUNDLE_PUBLIC_FIELDS = (
    "scope_version",
    "authorized_instruments",
    "remote_positions_by_instrument",
    "owned_positions_by_instrument",
)


class CtpRecoveryQueryMixin:
    """Bind provider query facts to the SDK session and ingress fence."""

    def _ctp_bound_query_records(
        self,
        session: Any,
        exchange_name: str,
        query_type: str,
        *,
        operation: str = "get_account_risk_snapshot",
        read_only: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        """Return complete CTP rows bound to one account/day/generation."""
        evidence = self._ctp_bound_query_evidence(
            session,
            exchange_name,
            query_type,
            operation=operation,
            read_only=read_only,
        )
        return list(evidence["records"]), evidence["account_fingerprint"]

    def _ctp_bound_query_evidence(
        self,
        session: Any,
        exchange_name: str,
        query_type: str,
        *,
        operation: str,
        read_only: bool,
    ) -> dict[str, Any]:
        """Return records plus terminal-packet evidence for one CTP request."""
        from bt_api_ctp.query_result_evidence import (
            CtpQueryRecordsInvalid,
            inspect_ctp_query_result,
            project_ctp_query_records,
        )

        guard = session.require_bound_read if read_only else session.require_write
        guard(operation, venue=exchange_name)
        identity = session.execution_identity(exchange_name)
        expected_generation = identity.get("connection_generation")
        expected_fingerprint = str(identity.get("account_id") or "")
        expected_trading_day = str(identity.get("trading_day") or "")
        try:
            result = self.query_ctp_result(exchange_name, query_type)
            inspected = inspect_ctp_query_result(
                result,
                query_type=query_type,
                expected_generation=expected_generation,
                expected_fingerprint=expected_fingerprint,
            )
        except Exception:
            inspected = None
        guard(operation, venue=exchange_name)
        if inspected is None:
            raise NormalizedApiError(
                operation,
                f"ctp_{query_type}_query_incomplete",
                definite_reject=True,
            )
        records, result_fingerprint, request_id = inspected
        try:
            safe_rows = project_ctp_query_records(
                records,
                fingerprint=result_fingerprint,
                generation=expected_generation,
                trading_day=expected_trading_day,
            )
        except CtpQueryRecordsInvalid:
            raise NormalizedApiError(
                operation,
                f"ctp_{query_type}_records_invalid",
                definite_reject=True,
            ) from None
        return {
            "query_type": query_type,
            "request_id": request_id,
            "connection_generation": expected_generation,
            "account_fingerprint": result_fingerprint,
            "trading_day": expected_trading_day,
            "records": safe_rows,
        }

    @staticmethod
    def _canonical_ctp_recovery_rows(
        records: tuple[dict[str, Any], ...],
        *,
        operation: str,
    ) -> tuple[dict[str, Any], ...]:
        try:
            keyed = [
                (
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    row,
                )
                for row in records
            ]
        except (TypeError, ValueError):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_records_not_canonical",
                definite_reject=True,
            ) from None
        return tuple(dict(row) for _key, row in sorted(keyed, key=lambda item: item[0]))

    def _ctp_recovery_query_round(
        self,
        session: Any,
        exchange_name: str,
        *,
        operation: str,
    ) -> dict[str, Any]:
        """Run one complete account/position/order/trade recovery query set."""
        from bt_api_ctp.query_result_evidence import ctp_recovery_row_matches_identity

        identity = session.execution_identity(exchange_name)
        expected_account = str(identity.get("account_id") or "")
        expected_day = str(identity.get("trading_day") or "")
        expected_generation = identity.get("connection_generation")
        results = {
            query_type: self._ctp_bound_query_evidence(
                session,
                exchange_name,
                query_type,
                operation=operation,
                read_only=True,
            )
            for query_type in ("account", "positions", "orders", "trades")
        }
        request_ids = [result["request_id"] for result in results.values()]
        if len(set(request_ids)) != len(request_ids):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_query_id_reused",
                definite_reject=True,
            )
        if len(results["account"]["records"]) != 1:
            raise NormalizedApiError(
                operation,
                "ctp_recovery_account_query_incomplete",
                definite_reject=True,
            )
        for query_type, result in results.items():
            if (
                result["account_fingerprint"] != expected_account
                or result["trading_day"] != expected_day
                or result["connection_generation"] != expected_generation
            ):
                raise NormalizedApiError(
                    operation,
                    "ctp_recovery_query_identity_mismatch",
                    definite_reject=True,
                )
            for row in result["records"]:
                if not ctp_recovery_row_matches_identity(
                    row, account=expected_account, trading_day=expected_day
                ):
                    raise NormalizedApiError(
                        operation,
                        f"ctp_recovery_{query_type}_row_identity_incomplete",
                        definite_reject=True,
                    )
        account_snapshot = self._canonical_ctp_recovery_rows(
            results["account"]["records"],
            operation=operation,
        )
        snapshot = {
            query_type: self._canonical_ctp_recovery_rows(
                results[query_type]["records"],
                operation=operation,
            )
            for query_type in ("positions", "orders", "trades")
        }
        full_snapshot = {"account": account_snapshot, **snapshot}
        snapshot_json = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        account_snapshot_json = json.dumps(
            account_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        full_snapshot_json = json.dumps(
            full_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return {
            "snapshot": snapshot,
            "full_snapshot": full_snapshot,
            "request_ids": {
                query_type: results[query_type]["request_id"]
                for query_type in ("account", "positions", "orders", "trades")
            },
            "account_fingerprint": expected_account,
            "trading_day": expected_day,
            "connection_generation": expected_generation,
            "snapshot_sha256": hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
            "account_snapshot_sha256": hashlib.sha256(
                account_snapshot_json.encode("utf-8")
            ).hexdigest(),
            "full_snapshot_sha256": hashlib.sha256(full_snapshot_json.encode("utf-8")).hexdigest(),
        }

    def _ctp_recovery_query_barrier(
        self,
        session: Any,
        exchange_name: str,
        *,
        operation: str,
        max_attempts: int = 3,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return two independent stable query rounds with an event fence."""
        if type(max_attempts) is not int or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        last_second = {"positions": (), "orders": (), "trades": ()}
        last_rounds: list[dict[str, Any]] = []
        stable = False
        attempt = 0
        start_revision = middle_revision = end_revision = session.recovery_event_revision()
        private_start_revision = private_end_revision = session.recovery_private_event_revision()
        ingress_start_epoch, ingress_start_pending = self._ctp_private_ingress_snapshot(
            exchange_name
        )
        ingress_end_epoch = ingress_start_epoch
        ingress_end_pending = ingress_start_pending
        ingress_start_revision = ingress_end_revision = session.recovery_private_ingress_revision()
        for current_attempt in range(1, max_attempts + 1):
            attempt = current_attempt
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            start_revision = session.recovery_event_revision()
            first = self._ctp_recovery_query_round(
                session,
                exchange_name,
                operation=operation,
            )
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            middle_revision = session.recovery_event_revision()
            second = self._ctp_recovery_query_round(
                session,
                exchange_name,
                operation=operation,
            )
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            end_revision = session.recovery_event_revision()
            private_end_revision = session.recovery_private_event_revision()
            ingress_end_epoch, ingress_end_pending = self._ctp_private_ingress_snapshot(
                exchange_name
            )
            ingress_end_revision = session.recovery_private_ingress_revision()
            all_request_ids = [
                request_id
                for round_result in (first, second)
                for request_id in round_result["request_ids"].values()
            ]
            unique_requests = len(set(all_request_ids)) == len(all_request_ids)
            stable = bool(
                unique_requests
                and start_revision == middle_revision == end_revision
                and private_start_revision == private_end_revision
                and ingress_start_epoch == ingress_end_epoch
                and ingress_start_pending == ingress_end_pending == 0
                and ingress_start_revision == ingress_end_revision
                and first["full_snapshot"] == second["full_snapshot"]
            )
            last_second = second["snapshot"]
            last_rounds = [first, second]
            if stable:
                break
        barrier_material = {
            "schema_version": "bt-api-py.ctp-recovery-query-barrier.v1",
            "stable": stable,
            "attempts": attempt,
            "event_revisions": {
                "start": start_revision,
                "middle": middle_revision,
                "end": end_revision,
            },
            "private_event_revisions": {
                "start": private_start_revision,
                "end": private_end_revision,
            },
            "private_ingress_revisions": {
                "start": ingress_start_revision,
                "end": ingress_end_revision,
            },
            "private_ingress_epochs": {
                "start": ingress_start_epoch,
                "end": ingress_end_epoch,
            },
            "private_ingress_pending": {
                "start": ingress_start_pending,
                "end": ingress_end_pending,
            },
            "rounds": [
                {
                    "request_ids": dict(round_result["request_ids"]),
                    "account_fingerprint": round_result["account_fingerprint"],
                    "trading_day": round_result["trading_day"],
                    "connection_generation": round_result["connection_generation"],
                    "snapshot_sha256": round_result["snapshot_sha256"],
                    "account_snapshot_sha256": round_result["account_snapshot_sha256"],
                    "full_snapshot_sha256": round_result["full_snapshot_sha256"],
                }
                for round_result in last_rounds
            ],
        }
        barrier_json = json.dumps(
            barrier_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        barrier = {
            **barrier_material,
            "barrier_sha256": hashlib.sha256(barrier_json.encode("utf-8")).hexdigest(),
        }
        return last_second, barrier

    @staticmethod
    def _public_ctp_recovery_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
        """Detach the closed public recovery shape from SDK-owned mutable state."""
        if not isinstance(plan, Mapping):
            raise NormalizedApiError(
                "prepare_execution_recovery",
                "execution_recovery_plan_invalid",
                definite_reject=True,
            )
        scope_version = plan.get("scope_version")
        if scope_version is None:
            fields = _CTP_EXECUTION_RECOVERY_PUBLIC_FIELDS
        elif scope_version == _CTP_EXECUTION_RECOVERY_BUNDLE_SCOPE_VERSION:
            fields = (
                *_CTP_EXECUTION_RECOVERY_PUBLIC_FIELDS,
                *_CTP_EXECUTION_RECOVERY_BUNDLE_PUBLIC_FIELDS,
            )
        else:
            raise NormalizedApiError(
                "prepare_execution_recovery",
                "execution_recovery_plan_invalid",
                definite_reject=True,
            )
        if any(field not in plan for field in fields):
            raise NormalizedApiError(
                "prepare_execution_recovery",
                "execution_recovery_plan_invalid",
                definite_reject=True,
            )
        return {field: deepcopy(plan[field]) for field in fields}
