"""Offline multi-scope updates against a real persistent simulation store."""

import json
from datetime import date

import pytest

from bt_api_py.trading_calendar_mock import SOURCE
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_mixed_simulation_version import template_fixture
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_fresh_observation import FAKE, confirm, flags
from tests.unit.test_trading_calendar_mock_persistent import operation, run_args, settings
from tests.unit.test_trading_calendar_observation_run import env_without_network


def selected(venue):
    result = list(flags())
    for key, value in (
        ("--mixed-fetch-contract", "RB2701" if venue == "SHFE" else "M2701"),
        ("--mixed-venue", venue),
        ("--mixed-contract", f"mock_{venue.lower()}_future_contract"),
    ):
        result[result.index(key) + 1] = value
    return result


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_two_scopes_survive_updates_and_can_be_queried(tmp_path, mode):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, FAKE)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, rows, err = command(env, *run_args(root, config))
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    original_run = next(row["run_id"] for row in rows if row.get("candidate_state") == "COMMITTED")
    if mode == "manual":
        assert confirm(env, root, rows[-1])["status"] == "COMMITTED"
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    (root / "supplement.yaml").write_text(json.dumps(supplement))

    def update(venue):
        return command(env, *operation(root, original_run, "mock-update", *selected(venue)))

    rc, first, err = update("SHFE")
    assert rc == (0 if mode == "auto" else 10), (first, err)
    if mode == "manual":
        assert confirm(env, root, first[-1])["status"] == "COMMITTED"
    store = TradingCalendarStore(root)
    first_version = store.read_current()
    assert first_version.simulation_provenance["target"]["venue"] == "SHFE"
    rc, second, err = update("DCE")
    assert rc == (0 if mode == "auto" else 10), (second, err)
    if mode == "manual":
        assert confirm(env, root, second[-1])["status"] == "COMMITTED"
    current = store.read_current()
    assert current.simulation_provenance["schema"] == "bt_api.calendar-mixed-scopes-provenance/1"
    assert {item["target"]["venue"] for item in current.simulation_provenance["scopes"]} == {
        "SHFE",
        "DCE",
    }
    candidate_id = second[-1]["candidate_id"]
    publication_id = second[-1]["publication_id"]
    from bt_api_py import trading_calendar_mixed_publication as mixed_publication

    history = mixed_publication.read_history(
        root=str(root),
        filesystem="TRUSTED_LOCAL_POSIX",
        candidate_id=candidate_id,
        publication_id=publication_id,
    )
    assert history["status"] == "VISIBLE_UNAUTHENTICATED", history
    assert history["publication_consistency"] == "RECEIPT_CHAIN_BOUND_UNAUTHENTICATED"
    assert history["simulation_provenance"] == current.simulation_provenance
    show_args = operation(
        root,
        original_run,
        "mock-show",
        "--candidate-id",
        candidate_id,
        "--publication-id",
        publication_id,
    )
    rc, shown, err = command(env, *show_args)
    assert rc == 0 and shown[-1]["candidate_history"]["status"] == "VISIBLE_UNAUTHENTICATED", (
        shown,
        err,
    )
    assert shown[-1]["proposal"]["observation"] == "CURRENT_REPREPARE"
    assert shown[-1]["proposal"]["original_publication"] == "NOT_INFERRED"
    assert shown[-1]["publication_visibility"]["status"] == "VISIBLE"
    if mode == "auto":
        evidence_path = root / "review-bundles" / f"mock-mixed-{candidate_id}.json"
        original = evidence_path.read_bytes()
        evidence_path.write_bytes(b"corrupted")
        rc, damaged, err = command(env, *show_args)
        assert rc != 0 and damaged[-1]["candidate_history"]["status"] == "UNKNOWN", (damaged, err)
        assert damaged[-1]["publication_visibility"]["status"] == "VISIBLE"
        evidence_path.unlink()
        rc, missing, err = command(env, *show_args)
        assert rc != 0 and missing[-1]["candidate_history"]["status"] == "UNKNOWN", (missing, err)
        evidence_path.write_bytes(original)
        evidence_path.chmod(0o600)
    if mode == "auto":
        import importlib.util
        import shutil
        import subprocess
        import sys

        git = shutil.which("git")
        assert git
        old_source = subprocess.check_output(
            [git, "show", "e2cbe0e6:bt_api_py/trading_calendar_store.py"], text=True
        )
        old_path = tmp_path / "old_calendar_store.py"
        old_path.write_text(old_source)
        spec = importlib.util.spec_from_file_location("prior_scope_store", old_path)
        prior = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = prior
        try:
            spec.loader.exec_module(prior)
            old_event = json.loads(
                (root / "publications" / f"{first_version.publication_id}.json").read_text()
            )
            old_bytes = (root / "versions" / f"{first_version.version_id}.json").read_bytes()
            assert (
                prior._decode_version(old_event, old_bytes).simulation_provenance["target"]["venue"]
                == "SHFE"
            )
            new_event = json.loads(
                (root / "publications" / f"{current.publication_id}.json").read_text()
            )
            new_bytes = (root / "versions" / f"{current.version_id}.json").read_bytes()
            with pytest.raises(prior.CalendarStoreError):
                prior._decode_version(new_event, new_bytes)
        finally:
            del sys.modules[spec.name]
    operator = MockCalendarPublication(root=str(root), run_id=original_run)
    bound = operator.binding(trading_day=date(2027, 1, 4))
    for venue, observation_id in (
        ("SHFE", first[-1]["observation"]["observation_id"]),
        ("DCE", second[-1]["observation"]["observation_id"]),
    ):
        result = bound.query(
            venue=venue,
            kind="future",
            product="pfuture",
            contract=f"mock_{venue.lower()}_future_contract",
            trading_day=date(2027, 1, 4),
        )["query"]
        assert result.result.status == "COMPLETE"
        assert result.simulation_provenance["evidence_locator"]["observation_id"] == observation_id
        assert {window["source_type"] for window in result.simulation_provenance["windows"]} == {
            "AKSHARE",
            "MOCK",
        }
        from bt_api_py.trading_calendar_runtime_status import TradingCalendarStatusReader

        reader = TradingCalendarStatusReader()
        reader.configure({"MOCK.route": str(root)})
        live = reader.query(
            "MOCK.route",
            mode="SIMULATION_ONLY",
            venue=venue,
            kind="future",
            product="pfuture",
            contract=f"mock_{venue.lower()}_future_contract",
            trading_day=date(2027, 1, 4),
        )
        assert (
            live.status == "COMPLETE"
            and live.simulation_provenance["evidence_locator"]["observation_id"] == observation_id
        )
    before = (root / "CURRENT.json").read_bytes()
    rc, unchanged, err = update("DCE")
    assert rc == 10 and unchanged[-1]["status"] == "SAME", (unchanged, err)
    assert (root / "CURRENT.json").read_bytes() == before
    env["FAKE_AK_END"] = "10:30"
    rc, changed, err = update("SHFE")
    assert rc == (0 if mode == "auto" else 10), (changed, err)
    if mode == "manual":
        assert confirm(env, root, changed[-1])["status"] == "COMMITTED"
    updated = store.read_current()
    assert updated.publication_id != current.publication_id
    old_dce = next(
        scope
        for scope in current.simulation_provenance["scopes"]
        if scope["target"]["venue"] == "DCE"
    )
    new_dce = next(
        scope
        for scope in updated.simulation_provenance["scopes"]
        if scope["target"]["venue"] == "DCE"
    )
    assert old_dce == new_dce
    assert len(updated.simulation_provenance["scopes"]) == 2
    historical = mixed_publication.read_history(
        root=str(root),
        filesystem="TRUSTED_LOCAL_POSIX",
        candidate_id=candidate_id,
        publication_id=publication_id,
    )
    assert historical["status"] == "VISIBLE_UNAUTHENTICATED", historical
    assert historical["publication_consistency"] == "RECEIPT_CHAIN_BOUND_UNAUTHENTICATED"
    rc, historical_show, err = command(env, *show_args)
    assert rc != 0 and historical_show[-1]["publication_visibility"]["status"] == "UNKNOWN", (
        historical_show,
        err,
    )
    assert historical_show[-1]["candidate_history"]["status"] == "VISIBLE_UNAUTHENTICATED"
    before_failure = (root / "CURRENT.json").read_bytes()
    env["FAKE_AK_STATUS"] = "ERROR"
    rc, failed, err = update("DCE")
    assert rc != 0 and failed[-1]["observation"]["observation_status"] == "FETCH_ERROR", (
        failed,
        err,
    )
    assert (root / "CURRENT.json").read_bytes() == before_failure
    if mode == "auto":
        from bt_api_py import trading_calendar_full_diff as full_diff
        from bt_api_py import trading_calendar_review_bundle as review
        from bt_api_py.trading_calendar_store import (
            CurrentPrecondition,
            PolicyPublication,
            _canonical,
        )

        # A = P's original baseline, Q = the later SHFE update, R = an
        # otherwise legal Store publication of P's original version atop Q.
        # A is an ancestor of R but is not R's direct publication baseline.
        candidate = review.show(
            root=str(root), candidate_id=candidate_id, filesystem="TRUSTED_LOCAL_POSIX"
        )
        composite = candidate["bundle"]["report"]["composite"]
        comparison = full_diff.compare_full(
            root=str(root),
            candidate_id=candidate_id,
            filesystem="TRUSTED_LOCAL_POSIX",
            simulation_provenance=current.simulation_provenance,
        )
        assert comparison["status"] == "CHANGED", comparison
        policy = store.read_review_policy()
        current_q = store.read_current()
        evidence = PolicyPublication(
            policy.directory_id,
            policy.revision,
            policy.mode,
            comparison["difference"]["after"]["store_snapshot_sha256"],
            comparison["difference"],
            comparison["difference_sha256"],
        )
        replay = store.publish(
            composite["snapshot"],
            publication_id="MOCK_replay_on_q",
            version_id="MOCK_replay_on_q_version",
            coverage_manifest_bytes=_canonical(composite["manifest"]),
            coverage_manifest_sha256=composite["manifest_sha256"],
            binding_bytes=_canonical(composite["binding"]),
            binding_sha256=composite["binding_sha256"],
            simulation_provenance=current.simulation_provenance,
            expected_current=CurrentPrecondition.matching(
                current_q.publication_id, current_q.publication_sha256
            ),
            policy_publication=evidence,
        )
        assert replay.visibility == "COMMITTED", replay
        mismatched = mixed_publication.read_history(
            root=str(root),
            filesystem="TRUSTED_LOCAL_POSIX",
            candidate_id=candidate_id,
            publication_id="MOCK_replay_on_q",
        )
        assert mismatched["status"] == "UNKNOWN", mismatched


def test_reverse_scope_order_has_identical_semantics(tmp_path):
    semantics = []
    for index, order in enumerate((("SHFE", "DCE"), ("DCE", "SHFE"))):
        base = tmp_path / str(index)
        base.mkdir()
        root, config = settings(base)
        env = env_without_network(base, FAKE)
        env["FAKE_CALLS"] = str(base / "calls")
        rc, rows, err = command(env, *run_args(root, config))
        assert rc == 0, (rows, err)
        run_id = next(row["run_id"] for row in rows if row.get("candidate_state") == "COMMITTED")
        (root / "supplement.yaml").write_text(
            json.dumps(
                {
                    "sources": [SOURCE],
                    "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
                    "trading_hours": [],
                    "exceptions": [],
                }
            )
        )
        for venue in order:
            rc, rows, err = command(env, *operation(root, run_id, "mock-update", *selected(venue)))
            assert rc == 0, (rows, err)
        provenance = TradingCalendarStore(root).read_current().simulation_provenance
        semantics.append(
            (
                provenance["semantic_sha256"],
                [(scope["target"], scope["semantic_sha256"]) for scope in provenance["scopes"]],
            )
        )
    assert semantics[0] == semantics[1]


def test_manual_candidate_stales_after_other_process_changes_current(tmp_path):
    root, config = settings(tmp_path, "manual")
    env = env_without_network(tmp_path, FAKE)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, rows, err = command(env, *run_args(root, config))
    assert rc == 10, (rows, err)
    run_id = next(row["run_id"] for row in rows if row.get("candidate_state") == "COMMITTED")
    assert confirm(env, root, rows[-1])["status"] == "COMMITTED"
    (root / "supplement.yaml").write_text(
        json.dumps(
            {
                "sources": [SOURCE],
                "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
                "trading_hours": [],
                "exceptions": [],
            }
        )
    )

    def update(venue):
        return command(env, *operation(root, run_id, "mock-update", *selected(venue)))

    rc, first, err = update("SHFE")
    assert rc == 10, (first, err)
    assert confirm(env, root, first[-1])["status"] == "COMMITTED"
    rc, pending, err = update("DCE")
    assert rc == 10 and pending[-1]["status"] == "PENDING_REVIEW", (pending, err)
    old_review = pending[-1]
    env["FAKE_AK_END"] = "10:30"
    rc, changed, err = update("SHFE")
    assert rc == 10 and confirm(env, root, changed[-1])["status"] == "COMMITTED", (changed, err)
    baseline = (root / "CURRENT.json").read_bytes()
    args = operation(
        root,
        old_review["run_id"],
        "mock-confirm",
        "--review-id",
        old_review["review_id"],
        "--review-sha256",
        old_review["review_payload_sha256"],
        "--reviewer",
        "MOCK_reviewer",
        "--confirmed-at",
        "2026-11-13T00:00:00.000000Z",
    )
    rc, rejected, err = command(env, *args)
    assert rc != 0 and rejected[-1]["reason"] == "MIXED_BASELINE_CHANGED", (rejected, err)
    assert (root / "CURRENT.json").read_bytes() == baseline
    rc, repeated, err = command(env, *args)
    assert rc != 0 and repeated[-1]["reason"] == "REVIEW_STALE", (repeated, err)
    rc, replacement, err = update("DCE")
    assert rc == 10 and confirm(env, root, replacement[-1])["status"] == "COMMITTED", (
        replacement,
        err,
    )


@pytest.mark.parametrize("case", ["exception", "trading_day", "source_text", "unrelated"])
def test_retained_scope_fact_changes_are_not_silently_reused(tmp_path, case):
    root, config = settings(tmp_path)
    env = env_without_network(
        tmp_path,
        FAKE
        + """
_previous_build = mock.build
def altered_build(year):
    data, supplement = _previous_build(year)
    if os.environ.get('CHANGE_MOCK_SOURCE_TEXT'):
        data['source_texts'][mock.SOURCE['id']] = 'different fictional MOCK material'
    if os.environ.get('CHANGE_SHFE_DAY'):
        shard = next(s for s in data['shards'] if s['venue'] == 'SHFE')
        next(day for day in shard['layers']['trading_days'] if day['day'] == '2027-01-04')['status'] = 'closed'
        for row in shard['layers']['trading_hours']:
            row['night_mappings'] = [m for m in row['night_mappings'] if m['trading_day'] != '2027-01-04']
    return data, supplement
mock.build = altered_build
""",
    )
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, rows, err = command(env, *run_args(root, config))
    assert rc == 0, (rows, err)
    run_id = next(row["run_id"] for row in rows if row.get("candidate_state") == "COMMITTED")
    supplement = {
        "sources": [SOURCE],
        "trading_days": [template_fixture()["shards"][0]["layers"]["trading_days"][0]],
        "trading_hours": [],
        "exceptions": [],
    }
    path = root / "supplement.yaml"
    path.write_text(json.dumps(supplement))
    rc, first, err = command(env, *operation(root, run_id, "mock-update", *selected("SHFE")))
    assert rc == 0, (first, err)
    before = (root / "CURRENT.json").read_bytes()
    if case == "trading_day":
        env["CHANGE_SHFE_DAY"] = "1"
    elif case == "source_text":
        env["CHANGE_MOCK_SOURCE_TEXT"] = "1"
    else:
        unrelated = case == "unrelated"
        supplement["exceptions"] = [
            {
                "venue": "GFEX" if unrelated else "SHFE",
                "kind": "option" if unrelated else "future",
                "product": "poption" if unrelated else "pfuture",
                "contract": "mock_gfex_option_contract"
                if unrelated
                else "mock_shfe_future_contract",
                "trading_day": "2027-01-04",
                "action": "close_day" if unrelated else "cancel_window",
                "window_id": None if unrelated else "night",
                "source_ids": [SOURCE["id"]],
            }
        ]
        path.write_text(json.dumps(supplement))
    rc, second, err = command(env, *operation(root, run_id, "mock-update", *selected("DCE")))
    if case == "unrelated":
        assert rc == 0 and second[-1]["status"] == "COMMITTED", (second, err)
        assert (root / "CURRENT.json").read_bytes() != before
    else:
        assert rc != 0 and second[-1]["reason"] == "RETAINED_SCOPE_CHANGED", (second, err)
        assert (root / "CURRENT.json").read_bytes() == before
