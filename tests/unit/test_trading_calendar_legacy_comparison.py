"""Independent, synthetic samples against injected simulation ports only."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from bt_api_base.trading_session_protocol import (
    TradingPhase,
    TradingSessionState,
    TradingWindow,
    TradingWindowKind,
)
from bt_api_ctp.calendar_snapshot import CalendarSnapshot
from bt_api_ctp.trading_session_schedule import CALENDAR_REF, CtpTradingSessionSchedule

from bt_api_py import trading_calendar_legacy_comparison as comparison
from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py import trading_calendar_store as calendar_store
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
from tests.unit.test_trading_calendar_mixed_simulation_version import (
    capture,
    save_candidate,
    template,
    template_fixture,
)
from tests.unit.test_trading_calendar_runtime_mixed_query import FS
from tests.unit.test_trading_calendar_session_simulation import (
    FIRST,
    OWNER,
    Store,
    dataset,
    store_snapshot,
)


class Gate:
    def verify(self, event, context):
        raise AssertionError("comparison must not activate")


def real_binding(*, changed_phase=False, night_au=False, bound_day=OWNER):
    value = dataset()
    value = json.loads(json.dumps(value).replace('"mock"', '"MOCK.simulation_only"'))
    if changed_phase:
        value["trading_hours"][0]["day_windows"][0]["phase"] = "auction"
    if night_au:
        row = value["trading_hours"][0]
        row["product"] = "au"
        row["contracts"] = ["au2601"]
        row["night_windows"][1]["end"] = "02:30"
        row["night_windows"][1]["end_day_offset"] = 1
        value["exceptions"] = []
    snapshot = store_snapshot(value)
    return SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=Store(snapshot),
        snapshot_factory=CalendarSnapshot,
        gate=Gate(),
        route="fake-route",
        source_generation="fixture-1",
        trading_day=bound_day,
        last_sequence=1,
    )


def args(*, binding=None, start=None, expected=None, contract="rb2601", product="rb"):
    start = start or datetime(2026, 6, 8, 1, tzinfo=UTC)
    expected = expected or ["continuous", "continuous"]
    return {
        "binding": binding or real_binding(),
        "legacy_schedule": CtpTradingSessionSchedule(),
        "mode": "SIMULATION_ONLY",
        "venue": "SHFE",
        "kind": "future",
        "product": product,
        "contract": contract,
        "instrument_id": contract,
        "owner_day": OWNER,
        "start_utc": start,
        "end_utc": start + timedelta(minutes=5 * len(expected)),
        "expected_phases": expected,
        "expected_source_id": "TEST.independent.sample",
        "legacy_source_id": "CTP.legacy.fixture",
        "legacy_calendar_ref": CALENDAR_REF,
    }


def test_real_ctp_and_mock_binding_match_independent_samples():
    value = args()
    result = comparison.compare(**value)
    assert result["status"] == "MATCH", result
    assert result["sample_step_minutes"] == 5 and len(result["samples"]) == 2
    assert all(
        item["modern_vs_expected"]
        == item["legacy_vs_expected"]
        == item["modern_vs_legacy"]
        == "MATCH"
        for item in result["samples"]
    )
    assert result["legacy_41_products"] == "NOT_RUN" and result["trade_authorization"] == "NONE"
    assert result["source_authentication"] == "NOT_PROVEN"
    assert result["calendar_ref"] and len(result["version_sha256"]) == 64


def test_real_ctp_against_deliberately_changed_mock_window():
    result = comparison.compare(**args(binding=real_binding(changed_phase=True)))
    assert result["status"] == "MISMATCH", result
    assert result["samples"][0]["modern_phase"] == "auction"
    assert result["samples"][0]["legacy_phase"] == "continuous"
    assert result["samples"][0]["modern_vs_legacy"] == "MISMATCH"


def test_half_open_boundary_never_infers_modern_closed():
    result = comparison.compare(
        **args(
            start=datetime(2026, 6, 8, 2, 10, tzinfo=UTC),
            expected=["continuous", "closed", "closed"],
        )
    )
    assert result["status"] == "UNKNOWN", result
    assert result["samples"][0]["modern_phase"] == "continuous"
    assert result["samples"][1]["modern_phase"] == "unknown"
    assert result["samples"][1]["modern_phase_basis"] == "UNKNOWN"
    assert result["samples"][1]["modern_window_time_source_id"] is None
    assert result["samples"][1]["legacy_phase"] == "closed"
    assert result["samples"][1]["modern_vs_expected"] == "UNKNOWN"


def test_real_ctp_midnight_crossing_requires_explicit_owner_day():
    start = datetime(2026, 6, 5, 16, tzinfo=UTC)  # Saturday 00:00 Shanghai, Monday owner
    assert FIRST.isoformat() == "2026-06-05"
    value = args(binding=real_binding(night_au=True), start=start, contract="au2601", product="au")
    good = comparison.compare(**value)
    assert good["status"] == "MATCH", good
    assert all(
        item["modern_phase"] == item["legacy_phase"] == "continuous" for item in good["samples"]
    )
    value["owner_day"] = FIRST
    bad = comparison.compare(**value)
    assert bad["status"] == "UNKNOWN"


def fake_ports(*, source=True, changing=False, legacy_owner=OWNER):
    instant = datetime(2026, 6, 8, 1, tzinfo=UTC)
    status = SimpleNamespace(
        mode="SIMULATION_ONLY",
        route="fixture-route",
        source_generation="fixture-generation",
        last_sequence=1,
        coverage_status="DECLARED_UNVERIFIED",
        load_error=None,
        binding_reason="PROCESS_START_OR_RESTART",
        data_origin="UNSPECIFIED",
        declared_coverage=(
            SimpleNamespace(venue="SHFE", start_day=FIRST, end_day=date(2026, 6, 9)),
        ),
        bound_trading_day=OWNER,
        calendar_ref="fixture-v1",
        publication_id="fixture-pub",
        version_sha256="a" * 64,
        content_sha256="b" * 64,
        coverage_manifest_sha256="c" * 64,
        binding_sha256="d" * 64,
    )
    window = SimpleNamespace(
        window_id="declared",
        start_utc=instant,
        end_utc=instant + timedelta(minutes=10),
        phase="continuous",
        trading_day=OWNER,
    )
    result = SimpleNamespace(
        status="COMPLETE",
        sources_verified=False,
        calendar_ref="fixture-v1",
        venue="SHFE",
        kind="future",
        product="rb",
        contract="rb2601",
        trading_day=OWNER,
        source_ids=("MOCK.simulation_only",) if source else (),
        windows=(window,),
    )
    query = SimpleNamespace(
        binding=status,
        result=result,
        declared_mock_provenance={
            "schema": "bt_api.calendar-declared-mock-windows/1",
            "environment": "SIMULATION_ONLY",
            "windows": [
                {
                    "window_id": "declared",
                    "source_type": "MOCK",
                    "source_id": "MOCK.simulation_only",
                    "source_authentication": "NOT_PROVEN",
                }
            ],
        },
        simulation_provenance=None,
    )

    class Binding:
        reads = 0

        def status(self):
            self.reads += 1
            if changing and self.reads > 1:
                return SimpleNamespace(**{**vars(status), "version_sha256": "e" * 64})
            return status

        def query(self, **kwargs):
            return query

    class Legacy:
        def session_state(self, instrument_id, at_utc):
            w = TradingWindow(
                legacy_owner,
                TradingWindowKind.CONTINUOUS,
                instant,
                instant + timedelta(minutes=10),
                "fake",
            )
            return TradingSessionState(
                instrument_id,
                at_utc,
                TradingPhase.CONTINUOUS,
                legacy_owner,
                w,
                None,
                "legacy.fixture",
            )

    return Binding(), Legacy()


def test_fake_ports_need_sources_snapshot_and_exact_owner():
    binding, legacy = fake_ports()
    value = args(binding=binding)
    value.update(legacy_schedule=legacy, legacy_calendar_ref="legacy.fixture")
    assert comparison.compare(**value)["status"] == "MATCH"
    binding, _ = fake_ports(source=False)
    assert comparison.compare(**{**value, "binding": binding})["status"] == "UNKNOWN"
    binding, _ = fake_ports(changing=True)
    changed = comparison.compare(**{**value, "binding": binding})
    assert changed["status"] == "UNKNOWN" and changed["reason"] == "BINDING_CHANGED"
    binding, legacy = fake_ports(legacy_owner=FIRST)
    assert (
        comparison.compare(**{**value, "binding": binding, "legacy_schedule": legacy})["status"]
        == "UNKNOWN"
    )
    assert comparison.compare(**{**value, "legacy_source_id": ""})["status"] == "UNKNOWN"


def test_bounds_invalid_inputs_and_exception_are_sanitized():
    binding, legacy = fake_ports()
    value = args(binding=binding)
    value.update(legacy_schedule=legacy, legacy_calendar_ref="legacy.fixture")
    assert (
        comparison.compare(**{**value, "end_utc": value["start_utc"] + timedelta(days=2)})["status"]
        == "UNKNOWN"
    )
    assert (
        comparison.compare(**{**value, "start_utc": value["start_utc"].replace(tzinfo=None)})[
            "status"
        ]
        == "UNKNOWN"
    )
    assert comparison.compare(**{**value, "expected_phases": ["closed"]})["status"] == "UNKNOWN"
    assert comparison.compare(**{**value, "mode": "LIVE"})["status"] == "UNKNOWN"

    class Broken:
        def status(self):
            raise ValueError("private/path password")

    output = comparison.compare(**{**value, "binding": Broken()})
    assert output["status"] == "UNKNOWN" and "private" not in str(output)


def test_real_local_store_binding_stays_on_pinned_version(tmp_path):
    from bt_api_py.trading_calendar_composite import compose_simulated
    from bt_api_py.trading_calendar_update_simulation import VenueContent
    from tests.calendar_test_store import FixtureCalendarStore

    value = json.loads(json.dumps(dataset()).replace('"mock"', '"MOCK.simulation_only"'))
    bundle = compose_simulated(
        mode="SIMULATION_ONLY",
        venues=(VenueContent.from_layers("SHFE", FIRST, date(2026, 6, 9), value),),
        snapshot_factory=CalendarSnapshot,
    )
    assert bundle.status == "READY_SIMULATED"
    store = FixtureCalendarStore(tmp_path)
    outcome = store.publish(
        json.loads(bundle.snapshot_bytes),
        publication_id="fixture-p1",
        version_id="fixture-v1",
        coverage_manifest_bytes=bundle.manifest_bytes,
        coverage_manifest_sha256=bundle.manifest_sha256,
        binding_bytes=bundle.binding_bytes,
        binding_sha256=bundle.binding_sha256,
    )
    assert outcome.visibility == "COMMITTED"
    bound = SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CalendarSnapshot,
        gate=Gate(),
        route="fake-route",
        source_generation="fixture-1",
        trading_day=OWNER,
        last_sequence=1,
    )
    original_version = bound.status().version_sha256
    value["trading_hours"][0]["day_windows"][0]["end"] = "10:20"
    next_bundle = compose_simulated(
        mode="SIMULATION_ONLY",
        venues=(VenueContent.from_layers("SHFE", FIRST, date(2026, 6, 9), value),),
        snapshot_factory=CalendarSnapshot,
    )
    assert next_bundle.status == "READY_SIMULATED"
    outcome = store.publish(
        json.loads(next_bundle.snapshot_bytes),
        publication_id="fixture-p2",
        version_id="fixture-v2",
        coverage_manifest_bytes=next_bundle.manifest_bytes,
        coverage_manifest_sha256=next_bundle.manifest_sha256,
        binding_bytes=next_bundle.binding_bytes,
        binding_sha256=next_bundle.binding_sha256,
    )
    assert outcome.visibility == "COMMITTED" and store.read_current().version_id == "fixture-v2"
    result = comparison.compare(**args(binding=bound))
    assert result["status"] == "MATCH" and result["calendar_ref"] == "fixture-v1"
    assert result["version_sha256"] == original_version


def test_in_place_binding_metadata_change_fails_closed():
    binding, legacy = fake_ports()
    value = args(binding=binding)

    class MutatingLegacy:
        def session_state(self, instrument_id, at_utc):
            result = legacy.session_state(instrument_id, at_utc)
            binding.status().version_sha256 = "e" * 64
            return result

    value.update(legacy_schedule=MutatingLegacy(), legacy_calendar_ref="legacy.fixture")
    result = comparison.compare(**value)
    assert result["status"] == "UNKNOWN" and result["reason"] == "BINDING_CHANGED"


def test_report_preserves_independent_scope_sources_and_binding_identity():
    binding, legacy = fake_ports()
    value = args(binding=binding)
    value.update(legacy_schedule=legacy, legacy_calendar_ref="legacy.fixture")
    result = comparison.compare(**value)
    assert result["status"] == "MATCH", result
    assert result["scope"] == {
        "venue": "SHFE",
        "kind": "future",
        "product": "rb",
        "contract": "rb2601",
        "instrument_id": "rb2601",
        "owner_day": "2026-06-08",
    }
    assert result["sample_range"] == {
        "start_utc": value["start_utc"].isoformat(),
        "end_utc": value["end_utc"].isoformat(),
        "expected_source_id": "TEST.independent.sample",
        "legacy_source_id": "CTP.legacy.fixture",
    }
    assert result["binding"]["route"] == "fixture-route"
    assert result["binding"]["source_generation"] == "fixture-generation"
    assert result["binding"]["bound_trading_day"] == "2026-06-08"
    assert result["binding"]["coverage_manifest_sha256"] == "c" * 64
    assert result["binding"]["binding_sha256"] == "d" * 64
    assert result["binding"]["declared_coverage"][0]["venue"] == "SHFE"
    assert all(
        item["modern_window_id"] == "declared"
        and item["modern_source_type"] == "MOCK"
        and item["modern_source_id"] == "MOCK.simulation_only"
        and item["modern_window_time_source_id"] == "MOCK.simulation_only"
        and item["modern_source_scope"] == "WINDOW_TIME_FIELDS_ONLY"
        and item["modern_phase_basis"] == "MOCK_SIMULATION_DECLARATION"
        for item in result["samples"]
    )
    assert result["expectation"] == result["identity"] == "CALLER_DECLARED_NOT_PROVEN"
    json.dumps(result)


def test_real_ctp_rejects_mismatched_contract_and_profile():
    value = args()
    for bad in (
        {"instrument_id": "hc2601"},
        {"instrument_id": "RB2601"},
        {"product": "hc"},
        {"venue": "DCE"},
        {"kind": "option"},
    ):
        assert comparison.compare(**{**value, **bad})["status"] == "UNKNOWN"


def test_mock_attribution_cannot_label_akshare():
    binding, legacy = fake_ports()
    value = args(binding=binding)
    value.update(legacy_schedule=legacy, legacy_calendar_ref="legacy.fixture")
    assert comparison.compare(**value)["status"] == "MATCH"
    binding.query().declared_mock_provenance["windows"][0]["source_type"] = "AKSHARE"
    assert comparison.compare(**value)["status"] == "UNKNOWN"


def test_binding_can_query_covered_owner_different_from_bound_day():
    value = args(binding=real_binding(bound_day=FIRST))
    result = comparison.compare(**value)
    assert result["status"] == "MATCH", result
    assert result["binding"]["bound_trading_day"] == FIRST.isoformat()
    assert result["scope"]["owner_day"] == OWNER.isoformat()


def test_fake_mixed_attribution_is_mapped_per_window():
    binding, legacy = fake_ports()
    value = args(binding=binding)
    value.update(legacy_schedule=legacy, legacy_calendar_ref="legacy.fixture")
    query = binding.query()
    start = value["start_utc"]
    query.result.windows = (
        SimpleNamespace(
            window_id="ak",
            start_utc=start,
            end_utc=start + timedelta(minutes=5),
            phase="continuous",
            trading_day=OWNER,
        ),
        SimpleNamespace(
            window_id="mock",
            start_utc=start + timedelta(minutes=5),
            end_utc=start + timedelta(minutes=10),
            phase="continuous",
            trading_day=OWNER,
        ),
    )
    query.result.source_ids = ("MOCK.simulation_only", "AKSHARE.simulation_unverified")

    def fields(start_clock, end_clock, basis, transition):
        return {
            "scope": {"value": "DAY", "basis": basis},
            "start": {"value": start_clock, "basis": basis},
            "end": {"value": end_clock, "basis": basis},
            "day_transition": {"value": transition, "basis": basis},
        }

    query.simulation_provenance = {
        "schema": "bt_api.calendar-mixed-window-provenance/2",
        "environment": "SIMULATION_ONLY",
        "target": {
            "venue": "SHFE",
            "kind": "future",
            "product": "rb",
            "mock_contract": "rb2601",
            "valid_from": "2026-06-05",
            "valid_to": "2026-06-09",
        },
        "evidence_locator": {
            "candidate_id": "a" * 32,
            "parent_candidate_id": "b" * 32,
            "observation_id": "c" * 32,
            "observation_run_id": "d" * 32,
            "capture_id": "akshare.capture." + "e" * 32,
            "response_digest": "f" * 64,
        },
        "windows": [
            {
                "window_id": "ak",
                "scope": "DAY",
                "source_type": "AKSHARE",
                "fields": fields("09:00", "09:05", "AKSHARE_SYNTAX_CANDIDATE", "SAME_CLOCK_DAY"),
                "simulation": {"phase": {"value": "continuous", "basis": "MOCK_ASSUMPTION"}},
            },
            {
                "window_id": "mock",
                "scope": "DAY",
                "source_type": "MOCK",
                "fields": fields("09:05", "09:10", "MOCK_ASSUMPTION", 0),
                "simulation": {"phase": {"value": "continuous", "basis": "MOCK_ASSUMPTION"}},
            },
        ],
    }
    query.simulation_provenance["evidence_locator"]["private_extra"] = "private-secret-not-exported"
    result = comparison.compare(**value)
    assert result["status"] == "MATCH", result
    assert [sample["modern_source_id"] for sample in result["samples"]] == [
        "AKSHARE.simulation_unverified",
        "MOCK.simulation_only",
    ]
    assert result["sample_range"]["expected_source_id"] == "TEST.independent.sample"
    assert [sample["modern_window_time_source_type"] for sample in result["samples"]] == [
        "AKSHARE",
        "MOCK",
    ]
    assert all(
        sample["modern_phase_basis"] == "MOCK_ASSUMPTION"
        and sample["modern_source_scope"] == "WINDOW_TIME_FIELDS_ONLY"
        for sample in result["samples"]
    )
    assert result["mixed_evidence_authentication"] == "NOT_PROVEN"
    assert result["mixed_evidence_locator"] == {
        key: query.simulation_provenance["evidence_locator"][key]
        for key in (
            "candidate_id",
            "parent_candidate_id",
            "observation_id",
            "observation_run_id",
            "capture_id",
            "response_digest",
        )
    }
    assert "private-secret-not-exported" not in json.dumps(result)
    query.simulation_provenance["windows"][0]["source_type"] = "MOCK"
    assert comparison.compare(**value)["status"] == "UNKNOWN"
    query.simulation_provenance["windows"][0]["source_type"] = "AKSHARE"
    query.simulation_provenance["windows"][0]["simulation"]["phase"]["basis"] = (
        "AKSHARE_SYNTAX_CANDIDATE"
    )
    assert comparison.compare(**value)["status"] == "UNKNOWN"


def persisted_mixed_binding(root):
    fixture = template_fixture()
    old_days = ["2026-12-31", "2027-01-01", "2027-01-02", "2027-01-03", "2027-01-04"]
    new_days = ["2026-06-04", "2026-06-05", "2026-06-06", "2026-06-07", "2026-06-08"]
    # Reuse the complete fixture, changing only its simulated date grid.
    serialized = json.dumps(fixture)
    for old, new in zip(old_days, new_days, strict=True):
        serialized = serialized.replace(old, new)
    save_candidate(root, "1" * 32, json.loads(serialized))
    capture(root, "a" * 32, "b" * 32, "日盘 09:00-10:00")
    target = {
        "venue": "SHFE",
        "kind": "future",
        "product": "pfuture",
        "mock_contract": "mock_shfe_future_contract",
        "valid_from": new_days[0],
        "valid_to": new_days[-1],
    }
    record = template(
        root,
        target=target,
        a={
            "DAY": {"phase": "continuous", "timezone": "Asia/Shanghai"},
            "NIGHT": {"phase": "continuous", "timezone": "Asia/Shanghai"},
        },
    )
    digest = mixed.template_digest(
        record, root=str(root), candidate_root=str(root), filesystem=FS, include_trace=True
    )
    materialized = mixed.materialize_template(
        record,
        root=str(root),
        candidate_root=str(root),
        filesystem=FS,
        alignment="REPEAT_OVER_MOCK_OWNER_DAY_MAPPING",
        expected_evidence_sha256=digest,
        candidate_id="2" * 32,
    )
    candidate = save_candidate(root, "2" * 32, materialized["input"])
    composite = candidate["bundle"]["report"]["composite"]
    content = composite["snapshot"]
    artifacts = {
        "coverage_manifest_bytes": mixed._canonical(composite["manifest"]),
        "coverage_manifest_sha256": composite["manifest_sha256"],
        "binding_bytes": mixed._canonical(composite["binding"]),
        "binding_sha256": composite["binding_sha256"],
        "simulation_provenance": materialized["provenance"],
    }
    store = calendar_store.TradingCalendarStore(str(root))
    policy = store.initialize_review_policy(config_read_succeeded=True)
    difference = {}
    claim = calendar_store.PolicyPublication(
        policy.directory_id,
        policy.revision,
        policy.mode,
        calendar_store.calendar_snapshot_digest(content, **artifacts),
        difference,
        calendar_store._sha(calendar_store._canonical(difference)),
    )
    result = store.publish(
        content,
        publication_id="MOCK_pub",
        version_id="MOCK_ver",
        **artifacts,
        expected_current=calendar_store.CurrentPrecondition.absent(),
        policy_publication=claim,
    )
    assert result.visibility == "COMMITTED", result
    return SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=store,
        snapshot_factory=CalendarSnapshot,
        gate=Gate(),
        route="mixed-route",
        source_generation="mixed-generation",
        trading_day=OWNER,
        last_sequence=1,
    )


def mapped_args(binding, start, expected):
    value = args(
        binding=binding,
        start=start,
        expected=expected,
        contract="mock_shfe_future_contract",
        product="pfuture",
    )
    value["instrument_id"] = "rb2601"
    status = binding.status()
    value["simulation_mapping"] = {
        "mode": "SIMULATION_ONLY",
        "modern_venue": "SHFE",
        "modern_kind": "future",
        "modern_product": "pfuture",
        "modern_contract": "mock_shfe_future_contract",
        "legacy_instrument_id": "rb2601",
        "legacy_profile_venue": "SHFE",
        "legacy_profile_product": "rb",
        "owner_day": OWNER.isoformat(),
        "calendar_ref": status.calendar_ref,
        "version_sha256": status.version_sha256,
        "legacy_calendar_ref": CALENDAR_REF,
    }
    return value


def test_persisted_mixed_with_real_ctp_schedule_and_declared_mapping(tmp_path):
    binding = persisted_mixed_binding(tmp_path)
    # Independent hand-declared samples in Shanghai time: day 09:00/09:55/10:00;
    # Friday night 21:00/22:55/23:00, owned by Monday June 8.
    for start, expected, statuses, source in (
        (datetime(2026, 6, 8, 1, 0, tzinfo=UTC), ["continuous"], ["MATCH"], "AKSHARE"),
        (
            datetime(2026, 6, 8, 1, 55, tzinfo=UTC),
            ["continuous", "continuous"],
            ["MATCH", "UNKNOWN"],
            "AKSHARE",
        ),
        (datetime(2026, 6, 5, 13, 0, tzinfo=UTC), ["continuous"], ["MATCH"], "MOCK"),
        (
            datetime(2026, 6, 5, 14, 55, tzinfo=UTC),
            ["continuous", "closed"],
            ["MATCH", "UNKNOWN"],
            "MOCK",
        ),
    ):
        value = mapped_args(binding, start, expected)
        result = comparison.compare(**value)
        assert [s["modern_vs_legacy"] for s in result["samples"]] == statuses, result
        assert result["status"] == ("UNKNOWN" if "UNKNOWN" in statuses else "MATCH")
        assert result["samples"][0]["modern_source_type"] == source
        assert result["simulation_mapping"] == value["simulation_mapping"]
        assert result["mapping_identity_authentication"] == "NOT_PROVEN"
        assert (
            result["match_meaning"]
            == "DECLARED_SAMPLE_PHASES_ONLY_NO_IDENTITY_OR_TRADE_AUTHORIZATION"
        )
        assert result["mixed_evidence_locator"]["observation_id"] == "a" * 32
        assert result["binding"]["version_sha256"] == value["simulation_mapping"]["version_sha256"]
        json.dumps(result)


def test_mapping_rejects_missing_changed_scope_version_and_evidence(tmp_path):
    binding = persisted_mixed_binding(tmp_path)
    value = mapped_args(binding, datetime(2026, 6, 8, 1, tzinfo=UTC), ["continuous"])
    assert comparison.compare(**value)["status"] == "MATCH"
    assert comparison.compare(**{**value, "simulation_mapping": None})["status"] == "UNKNOWN"
    for key in value["simulation_mapping"]:
        missing = deepcopy(value["simulation_mapping"])
        del missing[key]
        assert (
            comparison.compare(**{**value, "simulation_mapping": missing})["status"] == "UNKNOWN"
        ), key
        changed = deepcopy(value["simulation_mapping"])
        changed[key] = "0" * 64 if key == "version_sha256" else "wrong"
        assert (
            comparison.compare(**{**value, "simulation_mapping": changed})["status"] == "UNKNOWN"
        ), key
    for key, bad in (
        ("venue", "DCE"),
        ("product", "other"),
        ("contract", "mock_other"),
        ("instrument_id", "hc2601"),
        ("owner_day", FIRST),
        ("kind", "option"),
    ):
        assert comparison.compare(**{**value, key: bad})["status"] == "UNKNOWN", key
    # Consistently forged caller declarations must still match the snapshot
    # and the provider's independently selected static profile.
    for argument, mapping_key, bad in (
        ("venue", "modern_venue", "DCE"),
        ("product", "modern_product", "other"),
        ("contract", "modern_contract", "mock_other"),
        ("instrument_id", "legacy_instrument_id", "hc2601"),
    ):
        changed = deepcopy(value["simulation_mapping"])
        changed[mapping_key] = bad
        assert (
            comparison.compare(**{**value, argument: bad, "simulation_mapping": changed})["status"]
            == "UNKNOWN"
        )
    assert comparison.compare(**{**value, "expected_phases": ["auction"]})["status"] == "MISMATCH"
    version = tmp_path / "versions" / "MOCK_ver.json"
    original = version.read_bytes()
    changed = original.replace(b'"source_type":"AKSHARE"', b'"source_type":"MOCK"', 1)
    assert changed != original
    version.write_bytes(changed)
    # A newly loaded binding must reject altered persisted evidence.
    broken = SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=calendar_store.TradingCalendarStore(str(tmp_path)),
        snapshot_factory=CalendarSnapshot,
        gate=Gate(),
        route="mixed-route",
        source_generation="mixed-generation",
        trading_day=OWNER,
        last_sequence=1,
    )
    assert comparison.compare(**{**value, "binding": broken})["status"] == "UNKNOWN"


def test_mapping_rejects_different_venues_even_if_each_scope_matches():
    binding, legacy = fake_ports()
    status = binding.status()
    status.declared_coverage = (SimpleNamespace(venue="DCE", start_day=FIRST, end_day=OWNER),)
    snapshot = binding.query().result
    snapshot.venue = "DCE"
    snapshot.product = "pfuture"
    snapshot.contract = "mock_dce_future_contract"
    value = args(binding=binding, contract=snapshot.contract, product=snapshot.product)
    value.update(
        venue="DCE",
        instrument_id="rb2601",
        legacy_schedule=legacy,
        legacy_calendar_ref="legacy.fixture",
    )
    value["simulation_mapping"] = {
        "mode": "SIMULATION_ONLY",
        "modern_venue": "DCE",
        "modern_kind": "future",
        "modern_product": "pfuture",
        "modern_contract": snapshot.contract,
        "legacy_instrument_id": "rb2601",
        "legacy_profile_venue": "SHFE",
        "legacy_profile_product": "rb",
        "owner_day": OWNER.isoformat(),
        "calendar_ref": status.calendar_ref,
        "version_sha256": status.version_sha256,
        "legacy_calendar_ref": "legacy.fixture",
    }
    assert comparison.compare(**value)["status"] == "UNKNOWN"


def test_mapping_declaration_is_snapshotted_before_binding_callback(tmp_path):
    binding = persisted_mixed_binding(tmp_path)
    value = mapped_args(binding, datetime(2026, 6, 8, 1, tzinfo=UTC), ["continuous"])
    declaration = value["simulation_mapping"]
    original = declaration.copy()

    class MutatingBinding:
        def status(self):
            return binding.status()

        def query(self, **scope):
            declaration["legacy_profile_product"] = "other"
            return binding.query(**scope)

    result = comparison.compare(**{**value, "binding": MutatingBinding()})
    assert declaration["legacy_profile_product"] == "other"
    assert result["status"] == "MATCH", result
    assert result["simulation_mapping"] == original
    assert result["mapping_declaration_time"] == "CALL_TIME_NOT_AUTHENTICATED"
    assert result["mapping_identity_authentication"] == "NOT_PROVEN"

    class StringSubclass(str):
        pass

    for invalid in (
        {**original, "modern_venue": StringSubclass("SHFE")},
        {**original, StringSubclass("extra"): "value"},
    ):
        assert comparison.compare(**{**value, "simulation_mapping": invalid})["status"] == "UNKNOWN"


def test_expected_samples_are_snapshotted_before_binding_callback(tmp_path):
    binding = persisted_mixed_binding(tmp_path)
    for replacement in ([], ["auction"], ["invalid", "invalid", "invalid"]):
        value = mapped_args(
            binding, datetime(2026, 6, 8, 1, tzinfo=UTC), ["continuous", "continuous"]
        )
        expected = value["expected_phases"]

        class MutatingBinding:
            def __init__(self, samples, changed):
                self.samples = samples
                self.changed = changed

            def status(self):
                return binding.status()

            def query(self, **scope):
                self.samples[:] = self.changed
                return binding.query(**scope)

        result = comparison.compare(**{**value, "binding": MutatingBinding(expected, replacement)})
        assert expected == replacement
        assert result["status"] == "MATCH", result
        assert [sample["expected_phase"] for sample in result["samples"]] == [
            "continuous",
            "continuous",
        ]
