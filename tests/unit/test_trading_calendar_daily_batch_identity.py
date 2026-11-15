"""Explicit daily batch identity tests; all source calls are fake."""

import json
import subprocess
import sys
from datetime import date, datetime

import pytest

from bt_api_py import trading_calendar_akshare_observation as observation
from bt_api_py import trading_calendar_daily_batch as batch
from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_observation_run as runs
from tests.unit.test_trading_calendar_daily_batch import _initialized_batch_root, scope
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation

DAY = date(2026, 11, 13)
ORIGINAL = "0" * 32


def scopes():
    return [scope("DCE"), scope("SHFE")]


def bundle(items=None):
    items = scopes() if items is None else items
    return {
        "original_run_id": ORIGINAL,
        "run_id": "1" * 32,
        "batch_id": "2" * 32,
        "reservation_id": "3" * 32,
        "capture_id": "4" * 32,
        "parent_id": "5" * 32,
        "derived_id": "6" * 32,
        "candidate_id": "7" * 32,
        "review_id": "8" * 32,
        "publication_id": "MOCK_publication",
        "version_id": "MOCK_version",
        "observations": [
            {"observation_id": oid * 32, "scope_sha256": batch.scope_sha256(item)}
            for oid, item in zip(("9", "a"), items, strict=True)
        ],
    }


def setup_fake(tmp_path, monkeypatch, fetch):
    fds, _ = runs._open_runs(str(tmp_path), create=True)
    runs._close(fds)
    (tmp_path / "supplement.yaml").write_text("{}")

    class Operator:
        root = str(tmp_path)
        run_id = ORIGINAL
        _declaration = {"config": {}}
        _audit = {"year": 2026}

        def _guard(self):
            pass

    monkeypatch.setattr(batch, "MockCalendarPublication", lambda **kwargs: Operator())
    monkeypatch.setattr(batch, "_baseline", lambda *_: ({"current": "same"}, {"mode": "auto"}))
    monkeypatch.setattr(mock, "build", lambda *_: ({}, {}))
    monkeypatch.setattr(
        batch.mixed_publication,
        "_scope_rows",
        lambda *_: (
            {"coverage_start": "2026-01-01", "coverage_end": "2027-12-31"},
            {"windows_complete": True, "exceptions_complete": True},
        ),
    )
    monkeypatch.setattr(batch.mixed, "build_template", lambda **kwargs: {})
    monkeypatch.setattr(batch.mixed, "template_digest", lambda *args, **kwargs: "f" * 64)
    monkeypatch.setattr(batch.akshare, "observe_once", fetch)

    def update(operator, *, ids, batch_work, **kwargs):
        return batch_work(
            operator=operator,
            derived_id=ids["candidate_id"],
            binding={"normalized_content_sha256": "e" * 64},
            ids=ids,
            result={},
        )

    monkeypatch.setattr(batch.cli, "_update_with_supplement", update)


def invoke(root, identity=None, items=None, day=DAY):
    return batch.run(
        root=str(root),
        run_id=ORIGINAL,
        scopes=scopes() if items is None else items,
        identity_bundle=bundle() if identity is None else identity,
        requested_date=day,
    )


def test_explicit_date_order_reservation_and_no_repeat(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return {
            "descriptor_state": "COMMITTED",
            "observation_state": "COMMITTED" if len(calls) == 1 else "ATTEMPT_UNKNOWN",
            "observation_status": "SUCCESS" if len(calls) == 1 else "UNKNOWN",
            "projection_state": "VISIBLE_UNAUTHENTICATED" if len(calls) == 1 else "UNKNOWN",
        }

    setup_fake(tmp_path, monkeypatch, fake_fetch)
    monkeypatch.setattr(
        batch.cli, "_beijing_today", lambda: (_ for _ in ()).throw(AssertionError("clock reread"))
    )
    result = invoke(tmp_path)
    assert (
        result["status"] == "UNKNOWN" and result["reason"] == "OBSERVATION_RECONCILE_ORIGINAL_IDS"
    )
    assert result["identity_bundle"] == bundle() and result["requested_date"] == DAY.isoformat()
    assert [entry["request"]["venue"] for entry in calls] == ["DCE", "SHFE"]
    for index, call in enumerate(calls):
        assert call["request"]["requested_date"] == DAY
        assert call["reservation"] == {
            "reservation_id": "3" * 32,
            "batch_id": "2" * 32,
            "scope_sha256": bundle()["observations"][index]["scope_sha256"],
        }
    intent, _ = batch.mixed_publication._batch_intent(str(tmp_path), "2" * 32)
    assert intent["schema"].endswith("/2")
    assert intent["request_date_basis"] == "CALLER_DECLARED_ASIA_SHANGHAI_DATE"
    assert intent["date_role"] == "REQUEST_DATE_NOT_EFFECTIVE_DATE"
    shown = observation.show_reservation(
        root=str(tmp_path), reservation_id="3" * 32, filesystem=batch.FS
    )
    assert shown["status"] == "VISIBLE_UNAUTHENTICATED"
    assert [row["scope_sha256"] for row in shown["reservation"]["requests"]] == [
        row["scope_sha256"] for row in bundle()["observations"]
    ]
    incomplete = batch.show(root=str(tmp_path), run_id=ORIGINAL, batch_id="2" * 32)
    assert (
        incomplete["status"] == "UNKNOWN" and incomplete["reason"] == "BATCH_EVIDENCE_UNAVAILABLE"
    )
    repeated = invoke(tmp_path)
    assert repeated["status"] == "UNKNOWN" and repeated["reason"] == "BATCH_INTENT_RECONCILE_FIRST"
    assert repeated["identity_bundle"] == bundle() and len(calls) == 2


@pytest.mark.parametrize(
    "fault", ["run", "duplicate", "scope", "order", "count", "date", "extra", "mock"]
)
def test_invalid_bundle_rejected_before_any_write(tmp_path, monkeypatch, fault):
    identity, items, day = bundle(), scopes(), DAY
    if fault == "run":
        identity["original_run_id"] = "f" * 32
    if fault == "duplicate":
        identity["capture_id"] = identity["reservation_id"]
    if fault == "scope":
        identity["observations"][0]["scope_sha256"] = "a" * 64
    if fault == "order":
        items.reverse()
    if fault == "count":
        identity["observations"].pop()
    if fault == "date":
        day = datetime(2026, 11, 13)
    if fault == "extra":
        identity["other"] = "x"
    if fault == "mock":
        identity["publication_id"] = "bad"
    monkeypatch.setattr(
        batch, "MockCalendarPublication", lambda **kwargs: pytest.fail("preflight reached")
    )
    result = invoke(tmp_path, identity, items, day)
    assert result["status"] == "NOT_COMMITTED" and result["reason"] == "INVALID_IDENTITY_BUNDLE"
    assert not list(tmp_path.iterdir())


def test_prior_reservation_blocks_entire_group_before_fetch(tmp_path, monkeypatch):
    calls = []
    setup_fake(tmp_path, monkeypatch, lambda **kwargs: calls.append(kwargs))
    item = scopes()[0]
    prior = [
        {
            "observation_id": "9" * 32,
            "scope_sha256": "f" * 64,
            "venue": item["target"]["venue"],
            "contract": item["contract"],
            "requested_date": DAY,
            "timeout": item["timeout"],
        },
        {
            "observation_id": "b" * 32,
            "scope_sha256": "e" * 64,
            "venue": "SHFE",
            "contract": "RB2701",
            "requested_date": DAY,
            "timeout": 2,
        },
    ]
    assert (
        observation.reserve_observations(
            root=str(tmp_path),
            reservation_id="c" * 32,
            batch_id="d" * 32,
            run_id="e" * 32,
            requests=prior,
            filesystem=batch.FS,
        )["status"]
        == "COMMITTED"
    )
    result = invoke(tmp_path)
    assert (
        result["status"] == "UNKNOWN"
        and result["reservation_result"]["reason"] == "ID_ALREADY_USED"
    )
    assert result["identity_bundle"] == bundle() and not calls
    assert (tmp_path / "observation-runs" / f"mock-daily-batch-intent-{'2' * 32}.json").exists()


def test_prior_legacy_observation_blocks_entire_group(tmp_path, monkeypatch):
    calls = []
    setup_fake(tmp_path, monkeypatch, lambda **kwargs: calls.append(kwargs))

    def collector(**kwargs):
        raise RuntimeError("offline unknown")

    prior = observation.observe(
        root=str(tmp_path),
        observation_id="9" * 32,
        run_id="c" * 32,
        venue="DCE",
        contract="M2701",
        requested_date=DAY,
        timeout=1,
        filesystem=batch.FS,
        _collector=collector,
    )
    assert prior["status"] == "ATTEMPT_UNKNOWN"
    result = invoke(tmp_path)
    assert (
        result["status"] == "UNKNOWN"
        and result["reservation_result"]["reason"] == "ID_ALREADY_USED"
    )
    assert result["identity_bundle"] == bundle() and not calls


def test_legacy_call_keeps_v1_intent_and_original_scope_order(tmp_path, monkeypatch):
    setup_fake(tmp_path, monkeypatch, lambda **kwargs: pytest.fail("collector not expected"))
    monkeypatch.setattr(batch.cli, "_beijing_today", lambda: DAY)
    monkeypatch.setattr(
        batch.cli, "_update_with_supplement", lambda *args, **kwargs: {"status": "STOPPED"}
    )
    items = [scope("SHFE"), scope("DCE")]
    result = batch.run(root=str(tmp_path), run_id=ORIGINAL, scopes=items)
    assert result == {"status": "STOPPED"}
    paths = list((tmp_path / "observation-runs").glob("mock-daily-batch-intent-*.json"))
    assert len(paths) == 1
    body, _ = batch.storage._unwrap(paths[0].read_bytes())
    intent, _ = batch.mixed_publication._batch_intent(str(tmp_path), body["batch_id"])
    assert intent["schema"] == "bt_api.calendar-daily-batch-intent/1"
    assert "reservation_id" not in intent and "request_date_basis" not in intent
    assert [row["target"]["venue"] for row in intent["observations"]] == ["SHFE", "DCE"]


def test_intent_write_unknown_never_reserves_or_fetches(tmp_path, monkeypatch):
    calls = []
    setup_fake(tmp_path, monkeypatch, lambda **kwargs: calls.append(kwargs))
    original = batch._write

    def fail_after_write(*args):
        original(*args)
        raise OSError("unknown")

    monkeypatch.setattr(batch, "_write", fail_after_write)
    result = invoke(tmp_path)
    assert result["status"] == "UNKNOWN" and result["reason"] == "BATCH_INTENT_RECONCILE_FIRST"
    assert result["identity_bundle"] == bundle() and not calls
    assert not (tmp_path / "akshare-observations").exists()
    shown = batch.show(root=str(tmp_path), run_id=ORIGINAL, batch_id="2" * 32)
    assert shown["status"] == "UNKNOWN"
    monkeypatch.setattr(batch, "_write", original)
    assert invoke(tmp_path)["reason"] == "BATCH_INTENT_RECONCILE_FIRST" and not calls


def test_reservation_unknown_never_fetches_or_retries(tmp_path, monkeypatch):
    calls = []
    setup_fake(tmp_path, monkeypatch, lambda **kwargs: calls.append(kwargs))
    original = observation.reserve_observations

    def uncertain(**kwargs):
        result = original(**kwargs)
        assert result["status"] == "COMMITTED"
        return {**result, "status": "UNKNOWN"}

    monkeypatch.setattr(observation, "reserve_observations", uncertain)
    result = invoke(tmp_path)
    assert result["status"] == "UNKNOWN" and result["reason"] == "RESERVATION_RECONCILE_ORIGINAL_ID"
    assert result["identity_bundle"] == bundle() and not calls
    monkeypatch.setattr(observation, "reserve_observations", original)
    assert invoke(tmp_path)["reason"] == "BATCH_INTENT_RECONCILE_FIRST" and not calls


def test_reservation_readback_mismatch_stops_before_fetch(tmp_path, monkeypatch):
    calls = []
    setup_fake(tmp_path, monkeypatch, lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        observation,
        "show_reservation",
        lambda **kwargs: {"status": "VISIBLE_UNAUTHENTICATED", "reservation": {"run_id": "wrong"}},
    )
    result = invoke(tmp_path)
    assert result["status"] == "UNKNOWN" and result["reason"] == "RESERVATION_RECONCILE_ORIGINAL_ID"
    assert result["identity_bundle"] == bundle() and not calls


@pytest.mark.parametrize("stage", ["intent_read", "reservation_read", "observe"])
def test_interrupt_keeps_original_bundle_and_never_restarts(tmp_path, monkeypatch, stage):
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        raise KeyboardInterrupt()

    setup_fake(tmp_path, monkeypatch, fetch)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    if stage == "intent_read":
        monkeypatch.setattr(batch.mixed_publication, "_batch_intent", interrupt)
    elif stage == "reservation_read":
        monkeypatch.setattr(observation, "show_reservation", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        invoke(tmp_path)
    assert caught.value.identity_bundle == bundle()
    assert len(calls) == (1 if stage == "observe" else 0)
    assert invoke(tmp_path)["reason"] == "BATCH_INTENT_RECONCILE_FIRST"
    assert len(calls) == (1 if stage == "observe" else 0)


@pytest.mark.parametrize("tamper", ["digest", "identity", "order", "basis", "downgrade"])
def test_v2_intent_rejects_changed_binding_and_schema_downgrade(tmp_path, monkeypatch, tamper):
    setup_fake(
        tmp_path,
        monkeypatch,
        lambda **kwargs: {
            "descriptor_state": "COMMIT_UNKNOWN",
            "observation_state": "UNKNOWN",
            "observation_status": "UNKNOWN",
            "projection_state": "UNKNOWN",
        },
    )
    assert invoke(tmp_path)["status"] == "UNKNOWN"
    path = tmp_path / "observation-runs" / f"mock-daily-batch-intent-{'2' * 32}.json"
    body, _ = batch.storage._unwrap(path.read_bytes())
    if tamper == "digest":
        body["observations"][0]["scope_sha256"] = "f" * 64
    if tamper == "identity":
        body["reservation_id"] = body["capture_id"]
    if tamper == "order":
        body["observations"].reverse()
    if tamper == "basis":
        body["request_date_basis"] = "TRUSTED_CLOCK"
    if tamper == "downgrade":
        body["schema"] = "bt_api.calendar-daily-batch-intent/1"
    path.write_bytes(batch.storage._envelope(body))
    with pytest.raises(ValueError):
        batch.mixed_publication._batch_intent(str(tmp_path), "2" * 32)


def test_real_store_fake_collectors_full_batch_and_original_id_reentry(tmp_path):
    root, env, original = _initialized_batch_root(tmp_path, "auto")
    identity = bundle()
    identity["original_run_id"] = original
    day = date(2026, 11, 14)
    script = r"""
import json,sys
from datetime import date
from bt_api_py import trading_calendar_daily_batch as b
b.cli._beijing_today=lambda: (_ for _ in ()).throw(AssertionError("clock reread"))
value=b.run(root=sys.argv[1],run_id=sys.argv[2],scopes=json.loads(sys.argv[3]),
            identity_bundle=json.loads(sys.argv[4]),requested_date=date.fromisoformat(sys.argv[5]))
print(json.dumps({"final":value},ensure_ascii=True))
"""
    command = [
        sys.executable,
        "-c",
        script,
        str(root),
        original,
        json.dumps(scopes()),
        json.dumps(identity),
        day.isoformat(),
    ]

    def execute():
        proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.splitlines()[-1])["final"]

    result = execute()
    assert result["status"] == "COMMITTED", result
    assert result["identity_bundle"] == identity and result["requested_date"] == day.isoformat()
    assert [row["requested_date"] for row in result["observations"]] == [day.isoformat()] * 2
    assert [row["observation_id"] for row in result["observations"]] == [
        row["observation_id"] for row in identity["observations"]
    ]
    assert len((tmp_path / "calls").read_text().splitlines()) == 2
    repeated = execute()
    assert repeated["status"] == "UNKNOWN" and repeated["reason"] == "BATCH_INTENT_RECONCILE_FIRST"
    assert repeated["identity_bundle"] == identity
    assert len((tmp_path / "calls").read_text().splitlines()) == 2


_DELETE_IN_STORE_GUARD = r"""
from bt_api_py import trading_calendar_akshare_observation as reservation_observation
import inspect
_original_reservation_verify = reservation_observation.verify_reservation_readonly
def verify_at_commit(**kwargs):
    if os.environ.get('DELETE_REGISTRY_IN_GUARD') and any(
            frame.function == 'locked_policy_guard' for frame in inspect.stack()):
        from pathlib import Path
        (Path(kwargs['root']) / 'akshare-observations' / reservation_observation._REGISTRY).unlink()
        Path(os.environ['GUARD_MARK']).write_text('checked')
        os.environ.pop('DELETE_REGISTRY_IN_GUARD', None)
    return _original_reservation_verify(**kwargs)
reservation_observation.verify_reservation_readonly = verify_at_commit
"""


def prepare_manual_batch(tmp_path, *, extra="", partial=False):
    root, env, original = _initialized_batch_root(tmp_path, "manual", extra)
    if partial:
        env["FAIL_DCE"] = "1"
    identity = bundle()
    identity["original_run_id"] = original
    script = r"""
import json,sys
from datetime import date
from bt_api_py import trading_calendar_daily_batch as b
result=b.run(root=sys.argv[1],run_id=sys.argv[2],scopes=json.loads(sys.argv[3]),
             identity_bundle=json.loads(sys.argv[4]),requested_date=date(2026,11,13))
print(json.dumps({"final":result},ensure_ascii=True))
"""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            original,
            json.dumps(scopes()),
            json.dumps(identity),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.splitlines()[-1])["final"]
    assert result["status"] == "PENDING_REVIEW", result
    return root, env, original, identity, result


def confirm_args(root, pending):
    return operation(
        root,
        pending["run_id"],
        "mock-confirm",
        "--review-id",
        pending["review_id"],
        "--review-sha256",
        pending["review_payload_sha256"],
        "--reviewer",
        "MOCK_reviewer",
        "--confirmed-at",
        "2026-11-13T00:00:00.000000Z",
    )


@pytest.mark.parametrize("partial", [False, True])
def test_manual_show_and_confirmation_reject_deleted_reservation_db(tmp_path, partial):
    root, env, original, identity, pending = prepare_manual_batch(tmp_path, partial=partial)
    before = (root / "CURRENT.json").read_bytes()
    evidence = root / "observation-runs" / f"mock-daily-batch-{identity['batch_id']}.json"
    body, _ = batch.storage._unwrap(evidence.read_bytes())
    assert body["schema"] == (
        "bt_api.calendar-daily-batch-evidence/4"
        if partial
        else "bt_api.calendar-daily-batch-evidence/3"
    )
    assert len(body["reservation_payload_sha256"]) == 64
    original_evidence = evidence.read_bytes()
    for fault in ("null_digest", "wrong_digest", "downgrade"):
        altered = dict(body)
        if fault == "null_digest":
            altered["reservation_payload_sha256"] = None
        if fault == "wrong_digest":
            altered["reservation_payload_sha256"] = "f" * 64
        if fault == "downgrade":
            altered["schema"] = (
                "bt_api.calendar-daily-batch-evidence/2"
                if partial
                else "bt_api.calendar-daily-batch-evidence/1"
            )
            altered.pop("reservation_payload_sha256")
        encoded = batch.storage._envelope(altered)
        evidence.write_bytes(encoded)
        with pytest.raises(ValueError):
            batch.mixed_publication._batch_record(
                str(root), identity["batch_id"], batch.storage._unwrap(encoded)[1]
            )
    evidence.write_bytes(original_evidence)
    (root / "akshare-observations" / observation._REGISTRY).unlink()
    rc, rows, err = command(
        env, *operation(root, original, "mock-batch-show", "--batch-id", identity["batch_id"])
    )
    assert rc != 0 and rows[-1]["status"] == "UNKNOWN", (rows, err)
    rc, rows, err = command(env, *confirm_args(root, pending))
    assert rc != 0 and rows[-1]["status"] != "COMMITTED", (rows, err)
    assert (root / "CURRENT.json").read_bytes() == before


def test_manual_confirmation_rechecks_reservation_inside_store_writer_lock(tmp_path):
    root, env, _, _, pending = prepare_manual_batch(tmp_path, extra=_DELETE_IN_STORE_GUARD)
    before = (root / "CURRENT.json").read_bytes()
    marker = tmp_path / "guard-reached"
    env.update(DELETE_REGISTRY_IN_GUARD="1", GUARD_MARK=str(marker))
    rc, rows, err = command(env, *confirm_args(root, pending))
    assert marker.exists(), (rows, err)
    assert rc != 0 and rows[-1]["status"] != "COMMITTED", (rows, err)
    assert (root / "CURRENT.json").read_bytes() == before
