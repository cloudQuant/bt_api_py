"""Stable bt_api_py re-exports for typed execution journal contracts.

Backtrader must obtain these DTO and journal identities through bt_api_py,
not import the execution implementation package directly. The aliases below
preserve the exact class and callable objects used by the SDK journal backend.
"""

from bt_api_execution import journal as _journal

ExecutionDecisionJournal = _journal.ExecutionDecisionJournal
ExecutionDecisionJournalError = _journal.ExecutionDecisionJournalError
ExecutionDecisionKey = _journal.ExecutionDecisionKey
ExecutionDecisionRecord = _journal.ExecutionDecisionRecord
ExecutionFeePairCandidate = _journal.ExecutionFeePairCandidate
ExecutionFeePairOpenContext = _journal.ExecutionFeePairOpenContext
ExecutionFeePairScopeRegistration = _journal.ExecutionFeePairScopeRegistration
ExecutionFeePairTargetRoute = _journal.ExecutionFeePairTargetRoute
ExecutionIntentKey = _journal.ExecutionIntentKey
ExecutionRunLossGuardContext = _journal.ExecutionRunLossGuardContext
ExecutionRunLossGuardSnapshot = _journal.ExecutionRunLossGuardSnapshot
ExecutionSourceMemberReceipt = _journal.ExecutionSourceMemberReceipt
ExecutionSourceReceipt = _journal.ExecutionSourceReceipt
ExecutionSourceRestartMarker = _journal.ExecutionSourceRestartMarker

canonical_json = _journal._canonical_json
decision_key_values = _journal._decision_key_values
fee_pair_candidate_values = _journal._fee_pair_candidate_values
fee_pair_quote_identity_sha256 = _journal._fee_pair_quote_identity_sha256
fee_pair_receipt_from_candidate = _journal._fee_pair_receipt_from_candidate
fee_pair_single_row_economic_payload_sha256 = (
    _journal._fee_pair_single_row_economic_payload_sha256
)
fee_pair_single_row_tape_plan_matches_economics = (
    _journal._fee_pair_single_row_tape_plan_matches_economics
)
fee_pair_target_route_values = _journal._fee_pair_target_route_values
make_verified_fee_pair_single_row_tape_readback = (
    _journal._make_verified_fee_pair_single_row_tape_readback
)
make_verified_source_tape_readback = _journal._make_verified_source_tape_readback
source_member_values = _journal._source_member_values
source_receipt_from_values = _journal._source_receipt_from_values
source_receipt_sha256 = _journal._source_receipt_sha256

__all__ = [
    "ExecutionDecisionJournal",
    "ExecutionDecisionJournalError",
    "ExecutionDecisionKey",
    "ExecutionDecisionRecord",
    "ExecutionFeePairCandidate",
    "ExecutionFeePairOpenContext",
    "ExecutionFeePairScopeRegistration",
    "ExecutionFeePairTargetRoute",
    "ExecutionIntentKey",
    "ExecutionRunLossGuardContext",
    "ExecutionRunLossGuardSnapshot",
    "ExecutionSourceMemberReceipt",
    "ExecutionSourceReceipt",
    "ExecutionSourceRestartMarker",
    "canonical_json",
    "decision_key_values",
    "fee_pair_candidate_values",
    "fee_pair_quote_identity_sha256",
    "fee_pair_receipt_from_candidate",
    "fee_pair_single_row_economic_payload_sha256",
    "fee_pair_single_row_tape_plan_matches_economics",
    "fee_pair_target_route_values",
    "make_verified_fee_pair_single_row_tape_readback",
    "make_verified_source_tape_readback",
    "source_member_values",
    "source_receipt_from_values",
    "source_receipt_sha256",
]