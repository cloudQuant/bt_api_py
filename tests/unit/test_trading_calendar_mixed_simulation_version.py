"""Offline simulation version evidence: no network or account access."""

from copy import deepcopy
from datetime import date

import pytest
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract

from bt_api_py import trading_calendar_akshare_observation as obs
from bt_api_py import trading_calendar_mixed_simulation_version as mixed

FS = obs.TRUSTED_LOCAL_POSIX


def capture(
    root, oid, rid, text, *, extra_rows=None, requested_date=date(2026, 11, 13), status="OK"
):
    def collector(**kwargs):
        return collect_akshare_contract(
            **kwargs,
            _runner=lambda *_: (
                "CHILD_OK",
                {
                    "status": status,
                    "rows": ([["交易时间", text]] if text is not None else []) + (extra_rows or []),
                },
            ),
        )

    result = obs.observe(
        root=str(root),
        observation_id=oid,
        run_id=rid,
        venue="SHFE",
        contract="RB2701",
        requested_date=requested_date,
        timeout=1,
        filesystem=FS,
        _collector=collector,
    )
    assert result["record_state"] == "COMMITTED"


def assumptions():
    return {
        "DAY": {
            "phase": "continuous",
            "timezone": "Asia/Shanghai",
            "local_date": "2026-11-13",
            "trading_day": "2026-11-13",
            "night_ownership": "UNKNOWN",
        },
        "NIGHT": {
            "phase": "UNKNOWN",
            "timezone": "Asia/Shanghai",
            "local_date": "UNKNOWN",
            "trading_day": "UNKNOWN",
            "night_ownership": "NEXT_TRADING_DAY",
        },
    }


def fallback(mode="MISSING_SCOPE"):
    return {
        "data_origin": "MOCK",
        "environment": "SIMULATION_ONLY",
        "mode": mode,
        "requested_venue": "SHFE",
        "requested_contract": "RB2701",
        "mock_contract": "mock_shfe_future_contract",
        "windows": [
            {
                "id": "day",
                "scope": "DAY",
                "start": "13:00",
                "end": "15:00",
                "end_day_offset": 0,
                "phase": "continuous",
            },
            {
                "id": "night",
                "scope": "NIGHT",
                "start": "21:00",
                "end": "01:00",
                "end_day_offset": 1,
                "phase": "continuous",
            },
        ],
    }


def build(root, oid="a" * 32, rid="b" * 32, *, a=None, f=None):
    return mixed.build(
        root=str(root),
        observation_id=oid,
        run_id=rid,
        filesystem=FS,
        assumptions=assumptions() if a is None else a,
        fallback=f,
    )


def digest(record, root):
    return mixed.semantic_digest(record, root=str(root), filesystem=FS)


def compare(a, b, root):
    return mixed.diff(a, b, before_root=str(root), after_root=str(root), filesystem=FS)


def test_stable_content_and_bound_trace(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    capture(tmp_path, "c" * 32, "d" * 32, "日盘 09:00-10:00")
    one = build(tmp_path, f=fallback())
    two = build(tmp_path, "c" * 32, "d" * 32, f=fallback())
    assert one["semantic"] == two["semantic"]
    assert one["trace"] != two["trace"]
    assert digest(one, tmp_path) == digest(two, tmp_path)
    assert mixed.evidence_digest(one, root=str(tmp_path), filesystem=FS) != mixed.evidence_digest(
        two, root=str(tmp_path), filesystem=FS
    )
    assert compare(one, two, tmp_path) == []
    assert mixed.validate(one, root=str(tmp_path), filesystem=FS)


def test_missing_scope_and_attribution(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    result = build(tmp_path, f=fallback())["semantic"]
    day, night = result["windows"]
    assert [w["source_type"] for w in result["windows"]] == ["AKSHARE", "MOCK"]
    assert day["fields"]["start"] == {"value": "09:00", "basis": "AKSHARE_SYNTAX_CANDIDATE"}
    assert day["simulation"]["phase"] == {"value": "continuous", "basis": "MOCK_ASSUMPTION"}
    assert night["fields"]["day_transition"] == {"value": 1, "basis": "MOCK_ASSUMPTION"}
    assert night["simulation"]["local_date"]["basis"] == "UNKNOWN"
    assert night["simulation"]["night_ownership"]["basis"] == "MOCK_ASSUMPTION"
    assert all(set(w["observation"].values()) == {"UNKNOWN"} for w in (day, night))
    assert result["environment"] == "SIMULATION_ONLY"
    assert result["source_authentication"] == "NOT_PROVEN"
    assert result["projection"]["coverage"] == "UNKNOWN"
    assert result["projection"]["date_alignment"] == "UNKNOWN"


def test_changed_window_and_assumptions_are_diffed(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    capture(tmp_path, "c" * 32, "d" * 32, "日盘 09:00-10:30")
    one = build(tmp_path, f=fallback())
    two = build(tmp_path, "c" * 32, "d" * 32, f=fallback())
    assert digest(one, tmp_path) != digest(two, tmp_path)
    assert any("end" in row["path"] for row in compare(one, two, tmp_path))
    changed = assumptions()
    changed["DAY"]["phase"] = "auction"
    changed["NIGHT"]["local_date"] = "2026-11-12"
    three = build(tmp_path, a=changed, f=fallback())
    paths = {row["path"] for row in compare(one, three, tmp_path)}
    assert "/windows/0/simulation/phase/value" in paths
    assert "/windows/1/simulation/local_date/value" in paths


def test_diagnostic_and_conflicts_not_promoted(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "夜盘 21:00-01:00")
    result = build(tmp_path, f=fallback())["semantic"]
    assert result["projection"]["diagnostic"]["parse_status"] == "NEEDS_REVIEW"
    assert all(w["source_type"] == "MOCK" for w in result["windows"])
    capture(tmp_path, "c" * 32, "d" * 32, "日盘 09:00-10:00")
    collision = fallback("ADD_EXPLICIT")
    collision["windows"][0].update(start="09:30", end="10:30")
    result = build(tmp_path, "c" * 32, "d" * 32, f=collision)["semantic"]
    assert result["projection"]["simulation_status"] == "NEEDS_REVIEW"
    assert result["projection"]["conflicts"]
    assert result["windows"][0]["use"] == "CONFLICT_REVIEW"


def test_tampering_and_old_schema_rejected(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    record = build(tmp_path, f=fallback())
    for edit in (
        lambda r: r["semantic"]["windows"][0]["fields"]["start"].update(value="08:00"),
        lambda r: r["trace"].update(response_digest="0" * 64),
        lambda r: r["semantic"].update(schema="bt_api.akshare-mock-projection/1"),
        lambda r: r["trace"]["assumptions"]["DAY"].update(phase="auction"),
    ):
        forged = deepcopy(record)
        edit(forged)
        with pytest.raises(ValueError):
            mixed.validate(forged, root=str(tmp_path), filesystem=FS)
        with pytest.raises(ValueError):
            digest(forged, tmp_path)
    with pytest.raises(ValueError):
        mixed.validate(
            {"semantic": record["semantic"]["projection"], "trace": record["trace"]},
            root=str(tmp_path),
            filesystem=FS,
        )


def test_input_constraints_and_unknown_explicit(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    for change in (
        {"phase": "OPEN"},
        {"timezone": "Not/AZone"},
        {"local_date": "20261113"},
        {"trading_day": None},
        {"night_ownership": "NEXT_TRADING_DAY"},
    ):
        a = assumptions()
        a["DAY"].update(change)
        with pytest.raises(ValueError):
            build(tmp_path, a=a)
    a = assumptions()
    a["DAY"].update(
        phase="UNKNOWN", timezone="UNKNOWN", local_date="UNKNOWN", trading_day="UNKNOWN"
    )
    result = build(tmp_path, a=a)["semantic"]["windows"][0]
    assert all(
        result["simulation"][key] == {"value": "UNKNOWN", "basis": "UNKNOWN"}
        for key in ("phase", "timezone", "local_date", "trading_day")
    )
    with pytest.raises(ValueError):
        build(tmp_path, oid="f" * 32)


def test_source_and_conflict_changes_are_reported(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    capture(tmp_path, "c" * 32, "d" * 32, "无可识别时间")
    scenario = fallback()
    scenario["windows"][0].update(start="09:00", end="10:00")
    observed = build(tmp_path, f=scenario)
    simulated = build(tmp_path, "c" * 32, "d" * 32, f=scenario)
    paths = {entry["path"] for entry in compare(observed, simulated, tmp_path)}
    assert "/windows/0/source_type" in paths
    assert digest(observed, tmp_path) != digest(simulated, tmp_path)
    overlapping = fallback("ADD_EXPLICIT")
    overlapping["windows"][0].update(start="09:30", end="10:30")
    conflicted = build(tmp_path, f=overlapping)
    assert any("conflicts" in entry["path"] for entry in compare(observed, conflicted, tmp_path))


def test_rehashed_sidecar_tamper_is_rejected(tmp_path):
    from hashlib import sha256

    from bt_api_py import trading_calendar_detail_collection as storage

    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    record = build(tmp_path)
    path = tmp_path / "akshare-observations" / f"result-{'a' * 32}.json"
    body, _ = storage._unwrap(path.read_bytes())
    body["content"]["time"]["ranges"][0]["end"] = "11:00"
    body["content_sha256"] = sha256(storage._json(body["content"])).hexdigest()
    path.write_bytes(storage._envelope(body))
    with pytest.raises(ValueError):
        mixed.validate(record, root=str(tmp_path), filesystem=FS)


def test_irrelevant_response_content_only_changes_evidence(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00", extra_rows=[["名称", "first"]])
    capture(tmp_path, "c" * 32, "d" * 32, "日盘 09:00-10:00", extra_rows=[["名称", "second"]])
    one = build(tmp_path)
    two = build(tmp_path, "c" * 32, "d" * 32)
    assert one["trace"]["response_digest"] != two["trace"]["response_digest"]
    assert digest(one, tmp_path) == digest(two, tmp_path)
    assert compare(one, two, tmp_path) == []
    assert mixed.evidence_digest(one, root=str(tmp_path), filesystem=FS) != mixed.evidence_digest(
        two, root=str(tmp_path), filesystem=FS
    )


def test_request_dates_are_bound_only_in_trace(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    capture(tmp_path, "c" * 32, "d" * 32, "日盘 09:00-10:00", requested_date=date(2026, 11, 14))
    one = build(tmp_path, f=fallback())
    two = build(tmp_path, "c" * 32, "d" * 32, f=fallback())
    assert one["trace"]["requested_date"] == "2026-11-13"
    assert two["trace"]["requested_date"] == "2026-11-14"
    assert "requested_date" not in one["semantic"]["projection"]["request"]
    assert digest(one, tmp_path) == digest(two, tmp_path)
    assert compare(one, two, tmp_path) == []
    assert mixed.evidence_digest(one, root=str(tmp_path), filesystem=FS) != mixed.evidence_digest(
        two, root=str(tmp_path), filesystem=FS
    )
    forged = deepcopy(one)
    forged["trace"]["requested_date"] = "2026-11-14"
    with pytest.raises(ValueError):
        mixed.validate(forged, root=str(tmp_path), filesystem=FS)


def test_attempt_status_changes_remain_in_trace(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, None)
    capture(tmp_path, "c" * 32, "d" * 32, None, status="FETCH_ERROR")
    one = build(tmp_path, f=fallback())
    two = build(tmp_path, "c" * 32, "d" * 32, f=fallback())
    assert one["trace"]["source_status"] == "EMPTY"
    assert two["trace"]["source_status"] == "FETCH_ERROR"
    assert (
        one["trace"]["observation_state"]
        == two["trace"]["observation_state"]
        == "VISIBLE_UNAUTHENTICATED"
    )
    assert "source_status" not in one["semantic"]["projection"]
    assert "observation_state" not in one["semantic"]["projection"]
    assert digest(one, tmp_path) == digest(two, tmp_path)
    assert compare(one, two, tmp_path) == []
    forged = deepcopy(one)
    forged["trace"]["source_status"] = "FETCH_ERROR"
    with pytest.raises(ValueError):
        mixed.validate(forged, root=str(tmp_path), filesystem=FS)

    def broken(**_):
        raise RuntimeError("collector failure")

    attempted = obs.observe(
        root=str(tmp_path),
        observation_id="e" * 32,
        run_id="f" * 32,
        venue="SHFE",
        contract="RB2701",
        requested_date=date(2026, 11, 13),
        timeout=1,
        filesystem=FS,
        _collector=broken,
    )
    assert attempted["status"] == "ATTEMPT_UNKNOWN"
    unknown = build(tmp_path, "e" * 32, "f" * 32, f=fallback())
    assert unknown["trace"]["observation_state"] == "ATTEMPT_UNKNOWN"
    assert compare(one, unknown, tmp_path) == []


@pytest.mark.parametrize(
    ("scope", "local", "trading", "ownership", "valid"),
    [
        ("DAY", "2026-11-13", "2026-11-13", "UNKNOWN", True),
        ("DAY", "2026-11-13", "2026-11-16", "UNKNOWN", False),
        ("NIGHT", "2026-11-13", "2026-11-16", "NEXT_TRADING_DAY", True),
        ("NIGHT", "2026-11-13", "2026-11-13", "NEXT_TRADING_DAY", False),
        ("NIGHT", "2026-11-13", "2026-11-12", "NEXT_TRADING_DAY", False),
        ("NIGHT", "2026-11-13", "2026-11-13", "SAME_TRADING_DAY", True),
        ("NIGHT", "2026-11-13", "2026-11-16", "SAME_TRADING_DAY", False),
        ("NIGHT", "2026-11-13", "UNKNOWN", "NEXT_TRADING_DAY", True),
        ("NIGHT", "UNKNOWN", "2026-11-16", "NEXT_TRADING_DAY", True),
    ],
)
def test_date_assumptions_remain_explicit(tmp_path, scope, local, trading, ownership, valid):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    a = assumptions()
    a[scope].update(local_date=local, trading_day=trading, night_ownership=ownership)
    if not valid:
        with pytest.raises(ValueError):
            build(tmp_path, a=a)
        return
    result = build(tmp_path, a=a)
    assert result["trace"]["assumptions"][scope]["local_date"] == local
    assert result["trace"]["assumptions"][scope]["trading_day"] == trading


def test_unselected_mock_is_evidence_only_and_fixed_digest_detects_change(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    initial = fallback()
    one = build(tmp_path, f=initial)
    changed = deepcopy(initial)
    changed["windows"][0].update(start="12:30", end="14:30")
    two = build(tmp_path, f=changed)
    assert one["trace"]["fallback"] == initial
    assert two["trace"]["fallback"] == changed
    assert digest(one, tmp_path) == digest(two, tmp_path)
    assert compare(one, two, tmp_path) == []
    fixed = mixed.evidence_digest(one, root=str(tmp_path), filesystem=FS)
    assert mixed.verify_evidence_digest(
        one, root=str(tmp_path), filesystem=FS, expected_evidence_sha256=fixed
    )
    assert mixed.validate(two, root=str(tmp_path), filesystem=FS)
    with pytest.raises(ValueError, match="digest mismatch"):
        mixed.verify_evidence_digest(
            two, root=str(tmp_path), filesystem=FS, expected_evidence_sha256=fixed
        )
    selected = deepcopy(initial)
    selected["windows"][1].update(start="22:00")
    three = build(tmp_path, f=selected)
    assert digest(one, tmp_path) != digest(three, tmp_path)
    assert compare(one, three, tmp_path)


def test_consistently_changed_assumption_passes_replay_but_not_fixed_digest(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    one = build(tmp_path)
    fixed = mixed.evidence_digest(one, root=str(tmp_path), filesystem=FS)
    a = assumptions()
    a["DAY"]["phase"] = "auction"
    changed = build(tmp_path, a=a)
    assert mixed.validate(changed, root=str(tmp_path), filesystem=FS)
    with pytest.raises(ValueError, match="digest mismatch"):
        mixed.verify_evidence_digest(
            changed, root=str(tmp_path), filesystem=FS, expected_evidence_sha256=fixed
        )
    for invalid in (None, "0" * 63, "G" * 64):
        with pytest.raises(ValueError):
            mixed.verify_evidence_digest(
                one, root=str(tmp_path), filesystem=FS, expected_evidence_sha256=invalid
            )


def test_unused_assumptions_affect_trace_not_semantics(tmp_path):
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    initial = assumptions()
    changed = deepcopy(initial)
    changed["NIGHT"]["phase"] = "auction"
    one = build(tmp_path, a=initial, f=fallback())
    two = build(tmp_path, a=changed, f=fallback())
    assert one["semantic"]["windows"][1]["simulation"]["phase"]["value"] == "continuous"
    assert digest(one, tmp_path) == digest(two, tmp_path)
    assert compare(one, two, tmp_path) == []
    fixed = mixed.evidence_digest(one, root=str(tmp_path), filesystem=FS)
    assert mixed.validate(two, root=str(tmp_path), filesystem=FS)
    with pytest.raises(ValueError, match="digest mismatch"):
        mixed.verify_evidence_digest(
            two, root=str(tmp_path), filesystem=FS, expected_evidence_sha256=fixed
        )

    capture(tmp_path, "c" * 32, "d" * 32, None)
    none = build(tmp_path, "c" * 32, "d" * 32, a=initial)
    changed["DAY"].update(phase="auction", local_date="UNKNOWN")
    none_changed = build(tmp_path, "c" * 32, "d" * 32, a=changed)
    assert none["semantic"]["windows"] == []
    assert digest(none, tmp_path) == digest(none_changed, tmp_path)
    assert compare(none, none_changed, tmp_path) == []


def template_fixture():
    from bt_api_py import trading_calendar_mock as mock
    from tests.unit.test_trading_calendar_review_bundle import sample

    value = sample(days=5)
    value["metadata"] = deepcopy(mock.LABEL)
    value["source_texts"] = {"MOCK.simulation_only": "MOCK fictional simulation data"}
    dates = ["2026-12-31", "2027-01-01", "2027-01-02", "2027-01-03", "2027-01-04"]
    for shard in value["shards"]:
        venue = shard["venue"]
        shard["coverage_start"], shard["coverage_end"] = dates[0], dates[-1]
        layers = shard["layers"]
        layers["sources"] = [deepcopy(mock.SOURCE)]
        for index, row in enumerate(layers["trading_days"]):
            row.update(
                day=dates[index],
                status="open" if index in (1, 4) else "closed",
                source_ids=["MOCK.simulation_only"],
            )
        for row, requirement in zip(layers["trading_hours"], shard["requirements"], strict=True):
            contract = f"mock_{venue.lower()}_{row['kind']}_contract"
            row.update(
                contracts=[contract],
                valid_from=dates[0],
                valid_to=dates[-1],
                source_ids=["MOCK.simulation_only"],
                night_windows=[
                    {
                        "id": "night",
                        "phase": "continuous",
                        "start": "21:00",
                        "end": "23:00",
                        "end_day_offset": 0,
                    }
                ],
                night_mappings=[
                    {
                        "trading_day": dates[1],
                        "start_date": None,
                        "source_ids": ["MOCK.simulation_only"],
                    },
                    {
                        "trading_day": dates[4],
                        "start_date": dates[1],
                        "source_ids": ["MOCK.simulation_only"],
                    },
                ],
            )
            requirement.update(contract=contract, start_day=dates[0], end_day=dates[-1])
    return value


def save_candidate(root, cid, value):
    from bt_api_py import trading_calendar_review_bundle as review

    result = review.build_once(
        root=str(root), candidate_id=cid, input_data=value, filesystem=FS, emit=lambda _: None
    )
    assert result["status"] == "COMMITTED", result
    assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED", result
    return review.show(root=str(root), candidate_id=cid, filesystem=FS)


def template(root, cid="1" * 32, oid="a" * 32, rid="b" * 32, *, target=None, a=None):
    target = target or {
        "venue": "SHFE",
        "kind": "future",
        "product": "pfuture",
        "mock_contract": "mock_shfe_future_contract",
        "valid_from": "2026-12-31",
        "valid_to": "2027-01-04",
    }
    a = a or {
        "DAY": {"phase": "continuous", "timezone": "Asia/Shanghai"},
        "NIGHT": {"phase": "UNKNOWN", "timezone": "Asia/Shanghai"},
    }
    return mixed.build_template(
        root=str(root),
        candidate_root=str(root),
        candidate_id=cid,
        observation_id=oid,
        run_id=rid,
        filesystem=FS,
        target=target,
        assumptions=a,
    )


def test_template_persisted_scope_dates_and_stability(tmp_path):
    """The fixture spans five days across years, not a full two-year run."""
    value = template_fixture()
    save_candidate(tmp_path, "1" * 32, value)
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    capture(tmp_path, "c" * 32, "d" * 32, "日盘 09:00-10:00", requested_date=date(2026, 11, 14))
    one = template(tmp_path)
    two = template(tmp_path, oid="c" * 32, rid="d" * 32)
    assert one["semantic"]["status"] == "NEEDS_REVIEW"
    assert "DATE_ALIGNMENT_UNKNOWN" in one["semantic"]["issues"]
    assert one["semantic"]["scope"]["valid_to"] == "2027-01-04"
    assert one["semantic"]["identity_binding"] == {
        "observation_contract_identity": "UNKNOWN",
        "observation_product_identity": "UNKNOWN",
        "target_product_to_observation_contract": "MOCK_ASSUMPTION",
    }
    assert one["semantic"]["night_mappings"][0]["start_date"] is None
    assert one["semantic"]["night_mappings"][1]["start_date"] == "2027-01-01"
    assert [w["source_type"] for w in one["semantic"]["windows"]] == ["AKSHARE", "MOCK"]
    args = {"root": str(tmp_path), "candidate_root": str(tmp_path), "filesystem": FS}
    assert mixed.validate_template(one, **args)
    assert mixed.diff_templates(one, two, **args) == []
    assert mixed.template_digest(one, **args) == mixed.template_digest(two, **args)
    assert mixed.template_digest(one, **args, include_trace=True) != mixed.template_digest(
        two, **args, include_trace=True
    )
    with pytest.raises(ValueError):
        mixed.validate(one, root=str(tmp_path), filesystem=FS)
    with pytest.raises(ValueError):
        mixed.validate_template(build(tmp_path), **args)


def test_template_selected_and_unselected_candidate_changes(tmp_path):
    value = template_fixture()
    save_candidate(tmp_path, "1" * 32, value)
    other = deepcopy(value)
    other["shards"][1]["layers"]["trading_hours"][0]["day_windows"][0]["end"] = "10:10"
    save_candidate(tmp_path, "2" * 32, other)
    changed = deepcopy(value)
    changed["shards"][0]["layers"]["trading_hours"][0]["night_windows"][0]["end"] = "23:10"
    save_candidate(tmp_path, "3" * 32, changed)
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    a, b, c = (template(tmp_path, cid=str(i) * 32) for i in (1, 2, 3))
    kwargs = {"root": str(tmp_path), "candidate_root": str(tmp_path), "filesystem": FS}
    assert mixed.compare_templates(a, b, **kwargs)["status"] == "SAME"
    assert mixed.compare_templates(a, c, **kwargs)["status"] == "CHANGED"
    assert mixed.diff_templates(a, c, **kwargs)
    fixed = mixed.template_digest(a, **kwargs, include_trace=True)
    assert mixed.verify_template_evidence_digest(a, **kwargs, expected_evidence_sha256=fixed)
    with pytest.raises(ValueError):
        mixed.verify_template_evidence_digest(b, **kwargs, expected_evidence_sha256=fixed)


def test_template_rejects_scope_and_detects_tampering(tmp_path):
    value = template_fixture()
    save_candidate(tmp_path, "1" * 32, value)
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    record = template(tmp_path)
    args = {"root": str(tmp_path), "candidate_root": str(tmp_path), "filesystem": FS}
    for changes in (
        {"valid_from": "2026-12-30"},
        {"product": "wrong"},
        {"mock_contract": "mock_missing"},
        {"venue": "DCE"},
    ):
        target = deepcopy(record["trace"]["target"])
        target.update(changes)
        with pytest.raises(ValueError):
            template(tmp_path, target=target)
    forged = deepcopy(record)
    forged["trace"]["candidate_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        mixed.validate_template(forged, **args)
    path = tmp_path / "review-bundles" / f"candidate-{'1' * 32}.json"
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        mixed.validate_template(record, **args)


def test_template_rejects_futures_observation_for_option(tmp_path):
    save_candidate(tmp_path, "1" * 32, template_fixture())
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    option = {
        "venue": "SHFE",
        "kind": "option",
        "product": "poption",
        "mock_contract": "mock_shfe_option_contract",
        "valid_from": "2026-12-31",
        "valid_to": "2027-01-04",
    }
    with pytest.raises(ValueError, match="futures observation"):
        template(tmp_path, target=option)


def test_template_derived_supplement_and_exception_mismatch(tmp_path):
    import json

    from bt_api_py import trading_calendar_mock as mock
    from bt_api_py import trading_calendar_review_bundle as review

    value = template_fixture()
    exception = {
        "venue": "SHFE",
        "kind": "future",
        "product": "pfuture",
        "contract": "mock_shfe_future_contract",
        "trading_day": "2027-01-04",
        "action": "cancel_window",
        "window_id": "day",
        "source_ids": ["MOCK.simulation_only"],
    }
    support = {
        "schema": "bt_api.calendar-review-support/1",
        "night_ownership": [],
        "night_windows": [],
        "exceptions": [{k: v for k, v in exception.items() if k != "source_ids"}],
    }
    value["source_texts"]["MOCK.simulation_only"] = json.dumps(support)
    parent = save_candidate(tmp_path, "1" * 32, value)
    supplement = {
        "sources": [deepcopy(mock.SOURCE)],
        "trading_days": [],
        "trading_hours": [],
        "exceptions": [exception],
    }
    result = review.build_derived_once(
        root=str(tmp_path),
        candidate_id="4" * 32,
        parent_id="1" * 32,
        parent_sha256=parent["payload_sha256"],
        supplement=supplement,
        source_texts={},
        decisions=[],
        filesystem=FS,
        emit=lambda _: None,
    )
    assert result["status"] == "COMMITTED", result
    shown = review.show(root=str(tmp_path), candidate_id="4" * 32, filesystem=FS)
    assert shown["status"] == "VISIBLE_UNAUTHENTICATED", shown
    assert shown["bundle"]["schema"].endswith("/4")
    assert shown["bundle"]["report"]["status"] == "STRUCTURALLY_READY_UNVERIFIED"
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    result = template(tmp_path, cid="4" * 32)
    assert result["semantic"]["exceptions"] == [exception]
    assert result["semantic"]["status"] == "NEEDS_REVIEW"
    assert "EXCEPTION_WINDOW_UNMATCHED" in result["semantic"]["issues"]


def test_template_unready_missing_mapping_and_nonmock_rejected(tmp_path):
    from bt_api_py import trading_calendar_review_bundle as review

    missing = template_fixture()
    missing["shards"][0]["layers"]["trading_hours"][0]["night_mappings"].pop()
    result = review.build_once(
        root=str(tmp_path),
        candidate_id="5" * 32,
        input_data=missing,
        filesystem=FS,
        emit=lambda _: None,
    )
    assert (
        result["status"] == "COMMITTED"
        and result["review_status"] != "STRUCTURALLY_READY_UNVERIFIED"
    )
    nonmock = template_fixture()
    nonmock["metadata"]["data_origin"] = "OTHER"
    save_candidate(tmp_path, "6" * 32, nonmock)
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    for cid in ("5" * 32, "6" * 32):
        with pytest.raises(ValueError):
            template(tmp_path, cid=cid)


def test_template_unused_assumptions_and_selected_source_changes(tmp_path):
    value = template_fixture()
    save_candidate(tmp_path, "1" * 32, value)
    changed = deepcopy(value)
    for shard in changed["shards"]:
        for source in shard["layers"]["sources"]:
            source["description"] = "MOCK alternate simulation description"
    save_candidate(tmp_path, "2" * 32, changed)
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    a = {
        "DAY": {"phase": "continuous", "timezone": "Asia/Shanghai"},
        "NIGHT": {"phase": "UNKNOWN", "timezone": "Asia/Shanghai"},
    }
    b = deepcopy(a)
    b["NIGHT"]["phase"] = "auction"
    first = template(tmp_path, a=a)
    unused = template(tmp_path, a=b)
    second_source = template(tmp_path, cid="2" * 32, a=a)
    kwargs = {"root": str(tmp_path), "candidate_root": str(tmp_path), "filesystem": FS}
    assert mixed.diff_templates(first, unused, **kwargs) == []
    assert mixed.diff_templates(first, second_source, **kwargs)


def test_template_unused_mock_window_and_conflict(tmp_path):
    value = template_fixture()
    save_candidate(tmp_path, "1" * 32, value)
    changed = deepcopy(value)
    changed["shards"][0]["layers"]["trading_hours"][0]["day_windows"][0]["end"] = "10:10"
    save_candidate(tmp_path, "2" * 32, changed)
    capture(tmp_path, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    first, second = template(tmp_path), template(tmp_path, cid="2" * 32)
    kwargs = {"root": str(tmp_path), "candidate_root": str(tmp_path), "filesystem": FS}
    assert mixed.diff_templates(first, second, **kwargs) == []
    capture(tmp_path, "c" * 32, "d" * 32, "日盘 21:30-22:30")
    conflict = template(tmp_path, oid="c" * 32, rid="d" * 32)
    assert conflict["semantic"]["status"] == "NEEDS_REVIEW"
    assert conflict["semantic"]["projection"]["conflicts"]
    assert "OBSERVATION_REVIEW_REQUIRED" in conflict["semantic"]["issues"]
