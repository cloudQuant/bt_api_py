"""Independent, synthetic samples against injected simulation ports only."""

import json
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
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding
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
