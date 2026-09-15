"""Contract tests for the stateless synthetic CTP close planner.

The fixtures in this file are deliberately structural.  They describe the
shape of one completed positions query, but they do not represent an issued
account capability or an order permission.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from bt_api_py.ctp_close_plan import (
    CtpClosePlanError,
    build_ctp_close_plan,
    canonical_ctp_close_plan_json,
)

BASE_COMPLETED = datetime(2026, 9, 11, 1, 0, tzinfo=UTC)
BASE_SOURCE_HASH = "a" * 64
BASE_CONTEXT = {
    "account_fingerprint": "SYNTHETIC-ACCOUNT",
    "trading_day": "20260911",
    "connection_generation": 7,
    "query_request_id": 71,
    "clock_domain_id": "SYNTHETIC-CLOCK-1",
    "session_binding_id": "CALLER-ASSERTED-SESSION-1",
    "native_profile": "SYNTHETIC_FULL_CTP_6.7.7",
    "evidence_source_hash": BASE_SOURCE_HASH,
    "expires_at_utc": BASE_COMPLETED + timedelta(seconds=5),
    "expires_monotonic_ns": 105_000_000_000,
}

SPLIT_POLICY = {
    "status": "SYNTHETIC_PLANNING_PROFILE",
    "execution_policy_verified": False,
    "version": "1",
    "source_document_sha256": "ec5dfea45f52e85a484332a2cd7890cedf9d8369946cbcb07b7c203c24d8d15a",
    "source_location": ["printed55", "printed102-103"],
    "document_family": "CTPIIMini",
    "document_version": "1.4",
    "native_profile": "SYNTHETIC_FULL_CTP_6.7.7",
    "real_full_ctp_applicability": "BLOCKED_POLICY_APPLICABILITY",
    "freeze_mode": "zero_freeze_only",
    "required_explicit_zero_fields": [
        "LongFrozen",
        "ShortFrozen",
        "CombLongFrozen",
        "CombShortFrozen",
        "StrikeFrozen",
        "AbandonFrozen",
        "YdStrikeFrozen",
        "CombPosition",
    ],
    "forbidden_products": ["TAS", "combination_position"],
    "ordinary_nonzero_error": "O3B_FROZEN_ALLOCATION_UNPROVEN",
    "special_nonzero_error": "O3B_SPECIAL_POSITION_UNSUPPORTED",
    "policy_id": "SYNTHETIC-SPLIT-v1",
    "exchanges": ["SHFE", "INE"],
    "row_model": "split_by_position_date",
    "today_row": {
        "PositionDate": "1",
        "current_quantity": "Position",
        "consistency": "TodayPosition == Position",
    },
    "history_row": {
        "PositionDate": "2",
        "current_quantity": "Position",
        "consistency": "TodayPosition == 0",
    },
    "missing_age_row": "zero_only_under_complete_all_account_query_and_explicit_profile_rule",
    "offsets": {"today": "close_today", "history": "close_yesterday"},
    "wire_offsets": {"close_today": "3", "close_yesterday": "4"},
    "allocation_priority": "caller_choice_today_first_or_yesterday_first_not_exchange_rule",
}

GENERIC_POLICY = {
    **SPLIT_POLICY,
    "policy_id": "SYNTHETIC-GENERIC-v1",
    "exchanges": ["CZCE", "DCE"],
    "row_model": "combined_today_and_history",
    "required_position_date": "1",
    "current_total": "Position",
    "current_today": "TodayPosition",
    "current_history": "Position - TodayPosition under this explicit combined-row semantic profile",
    "yd_position_role": "audit_only_static_start_of_day",
    "offsets": {"generic": "close"},
    "wire_offsets": {"close": "1"},
    "allocation_priority": "UNSPECIFIED",
    "possible_allocation": {
        "today_min": "max(0, requested - history)",
        "today_max": "min(requested, today)",
        "history": "requested - allocated_today",
        "integer": True,
    },
    "must_cover_all_possible_allocations_at_execution": True,
}


def _row(
    instrument: str,
    exchange: str,
    direction: str,
    hedge: str,
    position_date: str,
    position: int,
    today: int,
    yd: int,
    **changes: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "BrokerID": "SYNTHB",
        "InvestorID": "SYNTHI",
        "TradingDay": "20260911",
        "InstrumentID": instrument,
        "ExchangeID": exchange,
        "PosiDirection": direction,
        "HedgeFlag": hedge,
        "PositionDate": position_date,
        "Position": position,
        "TodayPosition": today,
        "YdPosition": yd,
        "LongFrozen": 0,
        "ShortFrozen": 0,
        "CombLongFrozen": 0,
        "CombShortFrozen": 0,
        "StrikeFrozen": 0,
        "AbandonFrozen": 0,
        "YdStrikeFrozen": 0,
        "CombPosition": 0,
    }
    row.update(changes)
    return row


SHFE_MIXED = (
    _row("rb2610", "SHFE", "2", "1", "2", 2, 0, 7),
    _row("rb2610", "SHFE", "2", "1", "1", 1, 1, 0),
)
INE_TODAY_SHORT = (_row("sc2610", "INE", "3", "1", "1", 1, 1, 0),)
SHFE_HISTORY_SHORT = (_row("rb2610", "SHFE", "3", "1", "2", 1, 0, 9),)
CZCE_MIXED = (_row("SA701", "CZCE", "2", "1", "1", 3, 1, 4),)
DCE_TODAY_SHORT = (_row("m2701", "DCE", "3", "1", "1", 2, 2, 0),)
HEDGE_AND_SIDES = (
    _row("rb2610", "SHFE", "2", "1", "1", 1, 1, 0),
    _row("rb2610", "SHFE", "3", "1", "2", 1, 0, 3),
    _row("rb2610", "SHFE", "2", "2", "1", 1, 1, 0),
)


def _evidence(rows, **changes):
    value = {
        "request_type": "positions",
        "query_request_id": 71,
        "account_fingerprint": "SYNTHETIC-ACCOUNT",
        "connection_generation": 7,
        "trading_day": "20260911",
        "completed_at_utc": BASE_COMPLETED,
        "expires_at_utc": BASE_COMPLETED + timedelta(seconds=5),
        "completed_monotonic_ns": 100_000_000_000,
        "expires_monotonic_ns": 105_000_000_000,
        "clock_domain_id": "SYNTHETIC-CLOCK-1",
        "source_hash": BASE_SOURCE_HASH,
        "complete": True,
        "is_last_seen": True,
        "timed_out": False,
        "unsupported": False,
        "error_code": None,
        "records": tuple(deepcopy(rows)),
        "query_scope": "all_account_positions",
    }
    value.update(changes)
    return value


def _request(legs, **changes):
    value = {
        "candidate_id": "SYNTHETIC-CANDIDATE",
        "candidate_sha256": "c" * 64,
        "cycle_id": "SYNTHETIC-CYCLE-1",
        "execution_role": "exit",
        "expires_at_utc": BASE_COMPLETED + timedelta(seconds=5),
        "expires_monotonic_ns": 105_000_000_000,
        "legs": deepcopy(list(legs)),
    }
    value.update(changes)
    return value


def _call(rows, request, policy=None, *, context=None, now=None, monotonic=None):
    return _call_evidence(
        _evidence(rows),
        request,
        policy,
        context=context,
        now=now,
        monotonic=monotonic,
    )


def _call_evidence(
    evidence,
    request,
    policy=None,
    *,
    context=None,
    now=None,
    monotonic=None,
):
    return build_ctp_close_plan(
        evidence,
        request,
        policy or SPLIT_POLICY,
        current_context={**BASE_CONTEXT, **(context or {})},
        now_utc=now or (BASE_COMPLETED + timedelta(seconds=1)),
        monotonic_now=monotonic if monotonic is not None else 101_000_000_000,
    )


def _leg(instrument, exchange, hedge, side, quantity, leg_id="L1"):
    return {
        "leg_id": leg_id,
        "InstrumentID": instrument,
        "ExchangeID": exchange,
        "HedgeFlag": hedge,
        "position_side": side,
        "quantity": quantity,
    }


def _assert_code(call, code):
    with pytest.raises(CtpClosePlanError) as raised:
        call()
    assert raised.value.code == code


def test_g01_shfe_mixed_age_split_and_explicit_priority():
    plan = _call(
        SHFE_MIXED,
        _request([_leg("rb2610", "SHFE", "1", "long", 3)]),
    )
    assert [(a["offset"], a["quantity"]) for a in plan.actions] == [
        ("close_yesterday", 2),
        ("close_today", 1),
    ]
    today_first = _call(
        SHFE_MIXED,
        _request([_leg("rb2610", "SHFE", "1", "long", 3)], priority="today_first"),
    )
    assert [a["offset"] for a in today_first.actions] == [
        "close_today",
        "close_yesterday",
    ]


def test_g02_ine_one_lot_close_today_opposite_side():
    plan = _call(
        INE_TODAY_SHORT,
        _request([_leg("sc2610", "INE", "1", "short", 1)]),
    )
    assert plan.actions[0]["side"] == "buy"
    assert plan.actions[0]["offset"] == "close_today"


def test_g03_shfe_uses_current_history_not_static_yd_position():
    plan = _call(
        SHFE_HISTORY_SHORT,
        _request([_leg("rb2610", "SHFE", "1", "short", 1)]),
    )
    assert plan.actions[0]["offset"] == "close_yesterday"
    assert plan.actions[0]["quantity"] == 1


def test_g04_czce_mixed_age_returns_compact_allocation_and_fee_roles():
    plan = _call(
        CZCE_MIXED,
        _request([_leg("SA701", "CZCE", "1", "long", 2)]),
        GENERIC_POLICY,
    )
    allocation = plan.actions[0]["age_allocation"]
    assert allocation["today_min"] == 0 and allocation["today_max"] == 1
    assert allocation["history_equals_quantity_minus_today"] is True
    assert plan.fee_roles_required == ("close", "close_today")
    assert plan.execution_eligible is False


def test_g05_dce_single_age_has_unique_compact_allocation():
    plan = _call(
        DCE_TODAY_SHORT,
        _request([_leg("m2701", "DCE", "1", "short", 1)]),
        GENERIC_POLICY,
    )
    assert plan.actions[0]["age_allocation"] == {
        "today_min": 1,
        "today_max": 1,
        "history_equals_quantity_minus_today": True,
    }


def test_g06_same_symbol_sides_and_hedges_are_not_netted():
    plan = _call(
        HEDGE_AND_SIDES,
        _request(
            [
                _leg("rb2610", "SHFE", "1", "long", 1, "L1"),
                _leg("rb2610", "SHFE", "1", "short", 1, "L2"),
                _leg("rb2610", "SHFE", "2", "long", 1, "L3"),
            ]
        ),
    )
    assert len(plan.actions) == 3
    assert [(a["HedgeFlag"], a["position_side"]) for a in plan.actions] == [
        ("1", "long"),
        ("1", "short"),
        ("2", "long"),
    ]


def test_g07_quantity_boundaries_are_exact_and_no_partial_plan():
    _assert_code(
        lambda: _call(SHFE_MIXED, _request([_leg("rb2610", "SHFE", "1", "long", 4)])),
        "O3B_CLOSE_EXCEEDS_POSITION",
    )
    _assert_code(
        lambda: _call(SHFE_MIXED, _request([_leg("rb2610", "SHFE", "1", "long", 0)])),
        "O3B_QUANTITY_INVALID",
    )


def test_g08_empty_query_and_empty_request_never_verify_flat():
    _assert_code(
        lambda: _call((), _request([_leg("rb2610", "SHFE", "1", "long", 1)])),
        "O3B_CLOSE_EXCEEDS_POSITION",
    )
    _assert_code(lambda: _call(SHFE_MIXED, _request([])), "O3B_REQUEST_EMPTY")
    _assert_code(
        lambda: _call(
            (),
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
            context={"query_scope": "all_account_positions"},
        ),
        "O3B_CLOSE_EXCEEDS_POSITION",
    )


@pytest.mark.parametrize(
    "quantity",
    [True, 1.5, "1", -1, float("nan"), float("inf"), object()],
    ids=["bool", "fraction", "string", "negative", "nan", "inf", "custom"],
)
def test_g09_request_quantity_rejects_coercion(quantity):
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", quantity)]),
        ),
        "O3B_QUANTITY_INVALID",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"PositionDate": "3"},
        {"PosiDirection": "1"},
        {"Position": 1, "TodayPosition": 2},
    ],
    ids=["position-date", "direction", "today-exceeds"],
)
def test_g09_position_row_semantics_reject_malformed_values(change):
    rows = list(SHFE_MIXED)
    rows[0] = {**rows[0], **change}
    _assert_code(
        lambda: _call(
            rows,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_POSITION_ROW_INVALID",
    )


@pytest.mark.parametrize(
    "field",
    [
        "LongFrozen",
        "ShortFrozen",
        "CombLongFrozen",
        "CombShortFrozen",
        "StrikeFrozen",
        "AbandonFrozen",
        "YdStrikeFrozen",
        "CombPosition",
        "Position",
        "TodayPosition",
        "YdPosition",
        "InstrumentID",
        "ExchangeID",
        "PosiDirection",
        "HedgeFlag",
        "PositionDate",
        "TradingDay",
    ],
)
def test_g10_missing_raw_fields_never_default_to_zero(field):
    rows = [
        {key: value for key, value in SHFE_MIXED[0].items() if key != field},
        SHFE_MIXED[1],
    ]
    _assert_code(
        lambda: _call(
            rows,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_REQUIRED_FIELD_MISSING",
    )


@pytest.mark.parametrize(
    "field",
    ["LongFrozen", "ShortFrozen"],
    ids=["ordinary-long", "ordinary-short"],
)
def test_g11_nonzero_ordinary_freeze_is_explicitly_unsupported(field):
    rows = [{**SHFE_MIXED[0], field: 1}, SHFE_MIXED[1]]
    _assert_code(
        lambda: _call(
            rows,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_FROZEN_ALLOCATION_UNPROVEN",
    )


@pytest.mark.parametrize(
    "field",
    [
        "CombLongFrozen",
        "CombShortFrozen",
        "StrikeFrozen",
        "AbandonFrozen",
        "YdStrikeFrozen",
        "CombPosition",
    ],
)
def test_g11_nonzero_special_position_is_explicitly_unsupported(field):
    rows = [{**SHFE_MIXED[0], field: 1}, SHFE_MIXED[1]]
    _assert_code(
        lambda: _call(
            rows,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_SPECIAL_POSITION_UNSUPPORTED",
    )


def test_g12_duplicate_position_row_and_duplicate_requested_leg_reject():
    _assert_code(
        lambda: _call(
            (*SHFE_MIXED, SHFE_MIXED[0]),
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_POSITION_ROW_DUPLICATE",
    )
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request(
                [
                    _leg("rb2610", "SHFE", "1", "long", 1, "L1"),
                    _leg("rb2610", "SHFE", "1", "long", 1, "L2"),
                ]
            ),
        ),
        "O3B_REQUEST_LEG_DUPLICATE",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"policy_id": "unknown"},
        {"native_profile": "different version"},
        {"source_document_sha256": ""},
        {"row_model": "guessed from symbol prefix"},
    ],
    ids=["policy-id", "profile", "source", "row-model"],
)
def test_g13_policy_identity_and_source_completeness(change):
    policy = {**SPLIT_POLICY, **change}
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
            policy,
        ),
        "O3B_POLICY_INVALID_OR_MISMATCH",
    )


@pytest.mark.parametrize(
    "field",
    [
        "account_fingerprint",
        "trading_day",
        "connection_generation",
        "clock_domain_id",
        "evidence_source_hash",
    ],
)
def test_g14_current_scope_and_source_digest_bindings_are_exact(field):
    context = {"evidence_source_hash": BASE_SOURCE_HASH}
    context[field] = "other" if field not in {"connection_generation"} else 8
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
            context=context,
        ),
        "O3B_CURRENT_SCOPE_MISMATCH",
    )


def test_g14_instrument_case_is_not_an_alias():
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("RB2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_TARGET_NOT_IN_SOURCE",
    )


def test_g15_two_clock_expiry_is_minimum_and_never_renewed():
    plan = _call(
        SHFE_MIXED,
        _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        monotonic=104_999_999_999,
    )
    assert plan.actions[0]["expires_monotonic_ns"] == 105_000_000_000
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
            monotonic=105_000_000_000,
        ),
        "O3B_SOURCE_EXPIRED",
    )
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
            monotonic=99_999_999_999,
        ),
        "O3B_CLOCK_INVALID",
    )
    short_request = _request(
        [_leg("rb2610", "SHFE", "1", "long", 1)],
        expires_at_utc=BASE_COMPLETED + timedelta(seconds=2),
        expires_monotonic_ns=102_000_000_000,
    )
    plan = _call(SHFE_MIXED, short_request)
    assert plan.effective_expiry["expires_monotonic_ns"] == 102_000_000_000


def test_g16_role_side_leg_limit_and_generic_age_guarantee_are_strict():
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)], execution_role="entry"),
        ),
        "O3B_EXECUTION_ROLE_INVALID",
    )
    _assert_code(
        lambda: _call(
            CZCE_MIXED,
            _request(
                [_leg("SA701", "CZCE", "1", "long", 2)],
                generic_age_request="only_today",
            ),
            GENERIC_POLICY,
        ),
        "O3B_GENERIC_AGE_NOT_GUARANTEED",
    )
    legs = [_leg("rb2610", "SHFE", "1", "long", 1, f"L{i}") for i in range(4)]
    _assert_code(
        lambda: _call(SHFE_MIXED, _request(legs)),
        "O3B_REQUEST_TOO_MANY_LEGS",
    )


def test_g17_canonical_digest_action_ids_and_deep_immutability():
    request = _request([_leg("rb2610", "SHFE", "1", "long", 3)])
    plan = _call(SHFE_MIXED, request)
    same = _call(SHFE_MIXED, deepcopy(request), monotonic=102_000_000_000)
    assert plan.plan_sha256 == same.plan_sha256
    assert plan.actions[0]["action_id"] == sha256(f"{plan.plan_sha256}:0".encode()).hexdigest()
    with pytest.raises(TypeError):
        plan.actions[0]["quantity"] = 99
    with pytest.raises(TypeError):
        plan.actions[0]["age_allocation"]["today"] = 99
    request["legs"][0]["quantity"] = 1
    assert plan.actions[0]["quantity"] == 2
    reordered = dict(reversed(list(request.items())))
    reordered["legs"] = [_leg("rb2610", "SHFE", "1", "long", 3)]
    assert _call(SHFE_MIXED, reordered).plan_sha256 == plan.plan_sha256


def test_g17_independent_canonical_serializer_matches_static_oracle():
    payload = {
        "schema_version": "ctp_close_plan.v1",
        "execution_eligible": False,
        "source_binding": {"source_hash": "a" * 64, "request_id": 71},
        "limits": {"legs_max": 3, "actions_max": 6},
    }
    encoded = canonical_ctp_close_plan_json(payload)
    assert (
        encoded
        == b'{"execution_eligible":false,"limits":{"actions_max":6,"legs_max":3},"schema_version":"ctp_close_plan.v1","source_binding":{"request_id":71,"source_hash":"'
        + b"a" * 64
        + b'"}}'
    )


def test_g18_planner_is_structural_only_and_has_no_external_authority():
    plan = _call(
        SHFE_MIXED,
        _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
    )
    assert plan.status == "PLANNED"
    assert plan.planner_capability == "STRUCTURAL_ONLY"
    assert plan.execution_eligible is False
    assert plan.execution_block_reason == "BLOCKED_POLICY_APPLICABILITY"


def _material_digest(value, *excluded):
    payload = deepcopy(value)
    for key in excluded:
        payload.pop(key, None)
    return sha256(canonical_ctp_close_plan_json(payload)).hexdigest()


def _request_material_digest(request):
    payload = deepcopy(request)
    payload.setdefault("priority", "yesterday_first")
    payload.setdefault("generic_age_request", None)
    return _material_digest(payload, "request_sha256", "request_digest")


def _typed_row(raw, *, value_overrides=None):
    value_overrides = value_overrides or {}
    fields = {
        name: {
            "name": name,
            "present": True,
            "state": "value",
            "raw_value": value,
            "value": value_overrides.get(name, value),
        }
        for name, value in raw.items()
        if name
        in {
            "InstrumentID",
            "ExchangeID",
            "PosiDirection",
            "HedgeFlag",
            "PositionDate",
            "TradingDay",
            "Position",
            "TodayPosition",
            "YdPosition",
            "LongFrozen",
            "ShortFrozen",
            "CombLongFrozen",
            "CombShortFrozen",
            "StrikeFrozen",
            "AbandonFrozen",
            "YdStrikeFrozen",
            "CombPosition",
        }
    }
    return {"raw_record": deepcopy(raw), "fields": fields}


def test_cp01_policy_deadline_is_a_paired_minimum_and_never_renewed():
    policy = {**SPLIT_POLICY}
    policy.update(
        expires_at_utc=BASE_COMPLETED + timedelta(seconds=2),
        expires_monotonic_ns=102_000_000_000,
    )
    plan = _call(
        SHFE_MIXED,
        _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        policy,
        now=BASE_COMPLETED + timedelta(seconds=1),
        monotonic=101_000_000_000,
    )
    assert plan.effective_expiry["expires_monotonic_ns"] == 102_000_000_000
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
            policy,
            now=BASE_COMPLETED + timedelta(seconds=3),
            monotonic=103_000_000_000,
        ),
        "O3B_SOURCE_EXPIRED",
    )


def test_cp01_policy_deadline_requires_both_clock_domains():
    policy = {**SPLIT_POLICY, "expires_at_utc": BASE_COMPLETED + timedelta(seconds=2)}
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
            policy,
        ),
        "O3B_POLICY_INVALID_OR_MISMATCH",
    )


def test_cp02_claimed_policy_and_request_digests_must_match_material():
    policy = {**SPLIT_POLICY}
    policy["policy_sha256"] = _material_digest(policy, "policy_sha256", "policy_digest")
    request = _request([_leg("rb2610", "SHFE", "1", "long", 1)])
    request["request_sha256"] = _request_material_digest(request)
    plan = _call(SHFE_MIXED, request, policy)
    assert plan.policy_binding["policy_sha256"] == policy["policy_sha256"]
    assert plan.request_binding["request_sha256"] == request["request_sha256"]

    changed_policy = {**policy, "source_location": ["changed"]}
    _assert_code(
        lambda: _call(SHFE_MIXED, request, changed_policy),
        "O3B_POLICY_INVALID_OR_MISMATCH",
    )
    changed_request = {
        **request,
        "expires_at_utc": BASE_COMPLETED + timedelta(seconds=4),
        "expires_monotonic_ns": 104_000_000_000,
    }
    _assert_code(
        lambda: _call(SHFE_MIXED, changed_request, policy),
        "O3B_REQUEST_INVALID",
    )


def test_cp03_all_account_scope_and_global_raw_identity_are_required():
    evidence = _evidence(SHFE_MIXED[:1])
    evidence.pop("query_scope")
    _assert_code(
        lambda: _call_evidence(
            evidence,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_POSITION_EVIDENCE_INCOMPLETE",
    )

    evidence = _evidence(
        SHFE_MIXED,
        query_envelope={
            "session_scope": {
                "account_fingerprint": "SYNTHETIC-ACCOUNT",
                "connection_generation": 7,
                "trading_day": "20260911",
                "broker_id": "SYNTHB",
                "investor_id": "SYNTHI",
                "read_only_ready": True,
            }
        },
    )
    evidence["records"] = tuple({**row, "InvestorID": "OTHER"} for row in evidence["records"])
    _assert_code(
        lambda: _call_evidence(
            evidence,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_POSITION_ROW_INVALID",
    )


def test_cp03_typed_field_values_must_agree_with_frozen_raw_record():
    evidence = _evidence(
        (
            _typed_row(SHFE_MIXED[0], value_overrides={"Position": 99}),
            _typed_row(SHFE_MIXED[1]),
        )
    )
    _assert_code(
        lambda: _call_evidence(
            evidence,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
        ),
        "O3B_POSITION_ROW_INVALID",
    )


@pytest.mark.parametrize(
    "read_only_ready, expected_error",
    [(True, None), (False, "O3B_POSITION_EVIDENCE_INCOMPLETE")],
    ids=["same-scope-ready-true", "same-scope-ready-false"],
)
def test_cp03_source_readiness_is_consistent_before_scope_proof(read_only_ready, expected_error):
    evidence = _evidence(
        SHFE_MIXED,
        query_envelope={
            "session_scope": {
                "account_fingerprint": "SYNTHETIC-ACCOUNT",
                "connection_generation": 7,
                "trading_day": "20260911",
                "broker_id": "SYNTHB",
                "investor_id": "SYNTHI",
                "read_only_ready": read_only_ready,
            }
        },
    )
    evidence.pop("query_scope")

    def call():
        return _call_evidence(
            evidence,
            _request([_leg("rb2610", "SHFE", "1", "long", 3)]),
        )

    if expected_error is None:
        plan = call()
        assert [(action["offset"], action["quantity"]) for action in plan.actions] == [
            ("close_yesterday", 2),
            ("close_today", 1),
        ]
    else:
        _assert_code(call, expected_error)


@pytest.mark.parametrize(
    "change",
    [
        lambda policy: policy["today_row"].update(current_quantity="YdPosition"),
        lambda policy: policy.pop("missing_age_row"),
        lambda policy: policy.update(document_version="UNKNOWN"),
    ],
    ids=["today-position-field", "missing-age-rule", "source-document-version"],
)
def test_cp04_profile_semantics_are_explicit_and_registered(change):
    policy = deepcopy(SPLIT_POLICY)
    change(policy)
    _assert_code(
        lambda: _call(
            SHFE_MIXED,
            _request([_leg("rb2610", "SHFE", "1", "long", 1)]),
            policy,
        ),
        "O3B_POLICY_INVALID_OR_MISMATCH",
    )


def test_cp05_unrelated_account_rows_do_not_veto_target_close():
    plan = _call(
        (*SHFE_MIXED, *CZCE_MIXED),
        _request([_leg("rb2610", "SHFE", "1", "long", 3)]),
    )
    assert [(action["offset"], action["quantity"]) for action in plan.actions] == [
        ("close_yesterday", 2),
        ("close_today", 1),
    ]

    unrelated_frozen = {
        **SHFE_MIXED[0],
        "InstrumentID": "rb2611",
        "ShortFrozen": 1,
    }
    plan = _call(
        (*SHFE_MIXED, unrelated_frozen),
        _request([_leg("rb2610", "SHFE", "1", "long", 3)]),
    )
    assert len(plan.actions) == 2
