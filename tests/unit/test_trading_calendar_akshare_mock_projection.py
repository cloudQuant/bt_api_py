"""Offline integration with the observation sidecar and fictional inputs."""

from copy import deepcopy
from datetime import date

import pytest
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract

from bt_api_py import trading_calendar_akshare_mock_projection as projection
from bt_api_py import trading_calendar_akshare_observation as obs

OID = "a" * 32
RID = "b" * 32


def project(root, *, fallback=None, observation_id=OID):
    return projection.project(
        root=str(root),
        observation_id=observation_id,
        run_id=RID,
        filesystem=obs.TRUSTED_LOCAL_POSIX,
        fallback=fallback,
    )


def capture(root, rows=None, *, status="OK", transport="CHILD_OK", observation_id=OID):
    def collector(**kwargs):
        return collect_akshare_contract(
            **kwargs, _runner=lambda *_: (transport, {"status": status, "rows": rows or []})
        )

    written = obs.observe(
        root=str(root),
        observation_id=observation_id,
        run_id=RID,
        venue="SHFE",
        contract="RB2701",
        requested_date=date(2026, 11, 13),
        timeout=1,
        filesystem=obs.TRUSTED_LOCAL_POSIX,
        _collector=collector,
    )
    assert written["record_state"] == "COMMITTED"
    return obs.show(
        root=str(root),
        observation_id=observation_id,
        run_id=RID,
        filesystem=obs.TRUSTED_LOCAL_POSIX,
        include_content=True,
    )


def mock(*, mode="WHEN_NO_AKSHARE", start="13:30", end="15:00", scope="DAY", offset=0):
    return {
        "data_origin": "MOCK",
        "environment": "SIMULATION_ONLY",
        "mode": mode,
        "requested_venue": "SHFE",
        "requested_contract": "RB2701",
        "mock_contract": "mock_shfe_future_contract",
        "windows": [
            {
                "id": "fictional",
                "scope": scope,
                "start": start,
                "end": end,
                "end_day_offset": offset,
                "phase": "continuous",
            }
        ],
    }


def test_syntax_candidates_replay_into_windows_without_mutation(tmp_path):
    shown = capture(tmp_path, [["交易时间", "日盘 09:00-10:15,10:30-11:30"]])
    original = deepcopy(shown)
    fallback = mock(mode="ADD_EXPLICIT")
    original_fallback = deepcopy(fallback)
    result = project(tmp_path, fallback=fallback)
    sem = result["semantic"]
    assert [(w["start"], w["end"]) for w in sem["windows"]] == [
        ("09:00", "10:15"),
        ("10:30", "11:30"),
        ("13:30", "15:00"),
    ]
    assert all(
        w["source_type"] == "AKSHARE_SYNTAX_CANDIDATE"
        and w["verification"] == "UNVERIFIED"
        and w["phase"] == "UNKNOWN"
        and w["use"] == "REVIEW_ONLY"
        for w in sem["windows"][:2]
    )
    assert sem["windows"][2]["source_type"] == "MOCK"
    assert sem["windows"][2]["verification"] == "SIMULATION_ONLY"
    assert sem["simulation_mapping"] == {
        "requested_venue": "SHFE",
        "requested_contract": "RB2701",
        "mock_contract": "mock_shfe_future_contract",
        "mapping_status": "SIMULATION_ONLY",
        "identity": "UNKNOWN",
    }
    assert sem["source_status"] == "SUCCESS"
    assert sem["request"]["scope_status"] == "DECLARED"
    assert all(
        sem[key] == "UNKNOWN" for key in ("identity", "effective_period", "trading_day", "coverage")
    )
    assert sem["publication"] == sem["account_use"] == "NOT_AUTHORIZED"
    assert sem["business_snapshot"] == "NOT_A_BUSINESS_SNAPSHOT"
    assert shown == original and fallback == original_fallback


@pytest.mark.parametrize("text", ["09:00-10:15", "日盘 09:00-10:15 其他", "夜盘 21:00-01:00"])
def test_unlabeled_partial_and_inferred_rollover_only_diagnostic(tmp_path, text):
    capture(tmp_path, [["交易时间", text]])
    sem = project(tmp_path, fallback=mock())["semantic"]
    assert sem["source_status"] == "SUCCESS"
    assert sem["diagnostic"]["parse_status"] == "NEEDS_REVIEW"
    assert all(w["source_type"] == "MOCK" for w in sem["windows"])
    assert all(item["use"] == "DIAGNOSTIC_ONLY" for item in sem["diagnostic"]["ranges"])


def test_explicit_midnight_is_unverified_syntax_only(tmp_path):
    capture(tmp_path, [["交易时间", "夜盘 21:00-次日01:00"]])
    sem = project(tmp_path)["semantic"]
    assert len(sem["windows"]) == 1
    assert sem["windows"][0]["day_transition"] == "EXPLICIT_NEXT_DAY"
    assert sem["windows"][0]["source_type"] == "AKSHARE_SYNTAX_CANDIDATE"
    assert sem["trading_day"] == "UNKNOWN"


@pytest.mark.parametrize(
    ("rows", "child_status", "transport", "status"),
    [
        ([], "OK", "CHILD_OK", "EMPTY"),
        ([["名称", "x"]], "OK", "CHILD_OK", "MISSING_TRADING_TIME"),
        ([], "DEPENDENCY_UNAVAILABLE", "CHILD_OK", "DEPENDENCY_UNAVAILABLE"),
        ([], "FETCH_ERROR", "CHILD_OK", "FETCH_ERROR"),
        ([], "OK", "CHILD_TIMEOUT", "FETCH_TIMEOUT"),
        ([], "OK", "CHILD_CLEANUP_UNCERTAIN", "UNKNOWN"),
    ],
)
def test_failed_source_and_explicit_mock_do_not_promote_success(
    tmp_path, rows, child_status, transport, status
):
    capture(tmp_path, rows, status=child_status, transport=transport)
    sem = project(tmp_path, fallback=mock())["semantic"]
    assert sem["source_status"] == status
    assert len(sem["windows"]) == 1 and sem["windows"][0]["source_type"] == "MOCK"
    assert sem["simulation_mapping"]["mapping_status"] == "SIMULATION_ONLY"
    assert sem["publication"] == "NOT_AUTHORIZED"


def test_absent_fallback_never_invents_mock_identity(tmp_path):
    capture(tmp_path, [])
    sem = project(tmp_path)["semantic"]
    assert sem["windows"] == [] and sem["simulation_mapping"] is None
    assert sem["source_status"] == "EMPTY"


def test_mock_only_when_no_akshare_or_explicit_addition(tmp_path):
    capture(tmp_path, [["交易时间", "日盘 09:00-10:00"]])
    assert len(project(tmp_path, fallback=mock())["semantic"]["windows"]) == 1
    assert len(project(tmp_path, fallback=mock(mode="ADD_EXPLICIT"))["semantic"]["windows"]) == 2
    conflict = project(tmp_path, fallback=mock(mode="ADD_EXPLICIT", start="09:30", end="10:30"))[
        "semantic"
    ]
    assert conflict["simulation_status"] == "NEEDS_REVIEW"
    assert all(item["use"] == "CONFLICT_REVIEW" for item in conflict["windows"])


def test_stable_semantics_and_distinct_trace(tmp_path):
    rows = [["交易时间", "日盘 09:00-10:00"]]
    capture(tmp_path, rows, observation_id=OID)
    capture(tmp_path, rows, observation_id="c" * 32)
    a = project(tmp_path)
    b = project(tmp_path, observation_id="c" * 32)
    assert a["semantic"] == b["semantic"]
    assert a["trace"]["observation_id"] != b["trace"]["observation_id"]
    assert a["trace"]["capture_id"] != b["trace"]["capture_id"]
    assert a["trace"]["response_digest"] == b["trace"]["response_digest"]
    assert (
        a["semantic"]["windows"][0]["source_id"] == "akshare.futures_contract_detail.v1.SHFE.RB2701"
    )


def test_product_entry_rejects_forged_show_and_binds_original_id(tmp_path):
    first = capture(tmp_path, [["交易时间", "日盘 09:00-10:00"]])
    second = capture(tmp_path, [["交易时间", "日盘 10:00-11:00"]], observation_id="c" * 32)
    mixed = deepcopy(first)
    for key in ("content", "content_sha256", "response_digest", "capture_id"):
        mixed[key] = second[key]
    with pytest.raises(TypeError):
        projection.project(shown=mixed, fallback=mock())
    a = project(tmp_path)
    b = project(tmp_path, observation_id="c" * 32)
    assert a["semantic"]["windows"][0]["start"] == "09:00"
    assert b["semantic"]["windows"][0]["start"] == "10:00"
    assert a["trace"]["observation_id"] == OID
    assert a["trace"]["response_digest"] == first["response_digest"]
    assert b["trace"]["observation_id"] == "c" * 32
    assert b["trace"]["response_digest"] == second["response_digest"]


def test_rehashed_range_and_status_conflicts_are_replayed(tmp_path):
    from hashlib import sha256

    from bt_api_py import trading_calendar_detail_collection as storage

    capture(tmp_path, [["交易时间", "日盘 09:00-10:00"]])
    path = tmp_path / "akshare-observations" / f"result-{OID}.json"
    original = path.read_bytes()
    body, _ = storage._unwrap(original)
    body["content"]["time"]["ranges"][0]["end"] = "11:00"
    body["content_sha256"] = sha256(storage._json(body["content"])).hexdigest()
    path.write_bytes(storage._envelope(body))
    with pytest.raises(ValueError):
        project(tmp_path)
    body, _ = storage._unwrap(original)
    body["status"] = "MISSING_TRADING_TIME"
    body["time_role"] = "DIAGNOSTIC"
    path.write_bytes(storage._envelope(body))
    with pytest.raises(ValueError):
        project(tmp_path)


def test_upstream_structure_error_never_promotes_syntax(tmp_path):
    capture(tmp_path, [["交易时间", "日盘 09:00-10:00"], ["名称", "a"], ["名称", "b"]])
    sem = project(tmp_path, fallback=mock())["semantic"]
    assert sem["source_status"] == "STRUCTURE_CHANGED"
    assert sem["diagnostic"]["parse_status"] == "SYNTAX_CANDIDATE"
    assert sem["diagnostic"]["ranges"][0]["use"] == "DIAGNOSTIC_ONLY"
    assert sem["windows"][0]["source_type"] == "MOCK"


def test_mock_requires_explicit_fictional_mapping(tmp_path):
    capture(tmp_path, [])
    for change in (
        {"mock_contract": "RB2701"},
        {"data_origin": "AKSHARE"},
        {"environment": "LIVE"},
        {"mock_contract": None},
        {"requested_contract": "RB2702"},
    ):
        scenario = mock()
        scenario.update(change)
        with pytest.raises(ValueError):
            project(tmp_path, fallback=scenario)


def test_fictional_windows_from_existing_mock_builder(tmp_path):
    from bt_api_py.trading_calendar_mock import build

    mock_data, _ = build(2026)
    template = mock_data["shards"][0]["layers"]["trading_hours"][0]
    scenario = {
        "data_origin": "MOCK",
        "environment": "SIMULATION_ONLY",
        "mode": "WHEN_NO_AKSHARE",
        "requested_venue": "SHFE",
        "requested_contract": "RB2701",
        "mock_contract": template["contracts"][0],
        "windows": [{**item, "scope": "DAY"} for item in template["day_windows"]],
    }
    capture(tmp_path, [])
    sem = project(tmp_path, fallback=scenario)["semantic"]
    assert len(sem["windows"]) == 3
    assert all(window["source_type"] == "MOCK" for window in sem["windows"])
    assert sem["simulation_mapping"]["mock_contract"] == template["contracts"][0]


def test_intent_without_result_uses_explicit_mock_only(tmp_path):
    def broken(**_):
        raise RuntimeError("secret")

    result = obs.observe(
        root=str(tmp_path),
        observation_id=OID,
        run_id=RID,
        venue="SHFE",
        contract="RB2701",
        requested_date=date(2026, 11, 13),
        timeout=1,
        filesystem=obs.TRUSTED_LOCAL_POSIX,
        _collector=broken,
    )
    assert result["status"] == "ATTEMPT_UNKNOWN"
    shown = obs.show(
        root=str(tmp_path),
        observation_id=OID,
        run_id=RID,
        filesystem=obs.TRUSTED_LOCAL_POSIX,
        include_content=True,
    )
    assert shown["status"] == "ATTEMPT_UNKNOWN"
    sem = project(tmp_path, fallback=mock())["semantic"]
    assert sem["observation_state"] == "ATTEMPT_UNKNOWN" and sem["source_status"] == "UNKNOWN"
    assert sem["windows"][0]["source_type"] == "MOCK"


def test_omitted_content_is_unknown_with_mock(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "_LIMIT", 5000)
    rows = [["交易时间", "日盘 09:00-10:00"]] + [[f"f{i}", "x" * 500] for i in range(10)]
    shown = capture(tmp_path, rows)
    assert shown["data_state"] == "DATA_OMITTED"
    sem = project(tmp_path, fallback=mock())["semantic"]
    assert sem["source_status"] == "UNKNOWN" and sem["observation_state"] == "CAPTURE_TOO_LARGE"
    assert all(w["source_type"] == "MOCK" for w in sem["windows"])


def test_explicit_missing_scope_retains_mock_night_only(tmp_path):
    capture(tmp_path, [["交易时间", "日盘 09:00-10:00"]])
    scenario = mock(mode="MISSING_SCOPE")
    scenario["windows"].append(
        {
            "id": "night",
            "scope": "NIGHT",
            "start": "21:00",
            "end": "01:00",
            "end_day_offset": 1,
            "phase": "continuous",
        }
    )
    result = project(tmp_path, fallback=scenario)["semantic"]
    assert [(item["scope"], item["source_type"]) for item in result["windows"]] == [
        ("DAY", "AKSHARE_SYNTAX_CANDIDATE"),
        ("NIGHT", "MOCK"),
    ]
    assert result["windows"][1]["verification"] == "SIMULATION_ONLY"
    assert result["coverage"] == "UNKNOWN" and result["trading_day"] == "UNKNOWN"


def test_midnight_overlap_without_date_alignment_is_review(tmp_path):
    capture(tmp_path, [["交易时间", "夜盘 21:00-次日01:00"]])
    scenario = mock(mode="ADD_EXPLICIT", start="00:30", end="02:00", scope="NIGHT")
    result = project(tmp_path, fallback=scenario)["semantic"]
    assert result["simulation_status"] == "NEEDS_REVIEW"
    assert result["date_alignment"] == "UNKNOWN" and result["interval_convention"] == "HALF_OPEN"
    assert result["conflicts"] == [
        {"left": 0, "right": 1, "status": "POTENTIAL_DATE_ALIGNMENT_UNKNOWN"}
    ]
    assert all(window["use"] == "CONFLICT_REVIEW" for window in result["windows"])


def test_cross_scope_overlap_and_half_open_boundary(tmp_path):
    capture(tmp_path, [["交易时间", "日盘 09:00-10:00"]])
    overlap = mock(mode="ADD_EXPLICIT", start="09:30", end="10:30", scope="NIGHT")
    result = project(tmp_path, fallback=overlap)["semantic"]
    assert result["simulation_status"] == "NEEDS_REVIEW"
    assert len(result["conflicts"]) == 1
    boundary = mock(mode="ADD_EXPLICIT", start="10:00", end="11:00", scope="NIGHT")
    adjacent = project(tmp_path, fallback=boundary)["semantic"]
    assert adjacent["conflicts"] == []
    assert adjacent["date_alignment"] == "UNKNOWN"
    assert adjacent["simulation_status"] == "DATE_ALIGNMENT_UNRESOLVED"
