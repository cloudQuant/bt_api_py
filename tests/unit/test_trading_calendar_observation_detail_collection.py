"""Opt-in observation-to-collection CLI with offline synthetic providers."""

import json
import subprocess

import pytest

from bt_api_py import trading_calendar_detail_collection as collection
from bt_api_py import trading_calendar_observation_run as runs
from tests.unit.test_trading_calendar_observation_run import cli
from tests.unit.test_trading_calendar_preview_catalog import setup_preview
from tests.unit.test_trading_calendar_preview_network_navigation import inject


def options():
    return [
        "--retain-candidate",
        "--fetch-product-navigation",
        "--collect-product-details",
        "--detail-budget-seconds",
        "20",
        "--detail-per-request-seconds",
        "2",
    ]


def test_cli_v3_to_v6_to_two_batches_and_show(tmp_path):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    env["NAV_MODE"] = "large"
    site = tmp_path / "inject-network-nav" / "sitecustomize.py"
    site.write_text(site.read_text().replace("range(80)", "range(9)"))
    with site.open("a") as file:
        file.write("""
if "--collect-product-details" in sys.argv:
    from bt_api_py import trading_calendar_detail_collection as detail_collection
    saved_collection = detail_collection.collect_once
    def spy_collection(**kwargs):
        Path(os.environ["DETAIL_MARKER"] + ".budget").write_text(str(kwargs["budget_seconds"]) + " " + str(kwargs["per_request_seconds"]))
        return saved_collection(**kwargs)
    detail_collection.collect_once = spy_collection
""")
    result = subprocess.run(
        cli(root, config, supplement, *options()),
        env=env,
        capture_output=True,
        text=True,
        timeout=50,
    )
    assert result.returncode == 10, (result.stdout, result.stderr)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(lines) == 4 and lines[0]["event"] == "RUN_ID_ALLOCATED"
    assert lines[1]["record_installation"] == lines[1]["candidate_state"] == "COMMITTED"
    assert (
        lines[2]["event"] == "COLLECTION_ID_ALLOCATED"
        and lines[3]["event"] == "DETAIL_COLLECTION_RESULT"
    )
    assert lines[3]["status"] == "OBSERVED_UNVERIFIED" and lines[3]["coverage"] == "NOT_PROVEN"
    assert lines[0]["run_id"] == lines[2]["run_id"] == lines[3]["run_id"]
    assert lines[0]["candidate_id"] == lines[2]["candidate_id"] == lines[3]["candidate_id"]
    assert lines[2]["collection_id"] == lines[3]["collection_id"]
    run_observation = runs.show_once(
        root=str(root), run_id=lines[0]["run_id"], filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert run_observation["observation"]["venues"]
    assert all(
        venue["state"] == "NOT_ATTEMPTED" for venue in run_observation["observation"]["venues"]
    )
    shown = collection.show(
        root=str(root), collection_id=lines[2]["collection_id"], filesystem="TRUSTED_LOCAL_POSIX"
    )
    assert shown["status"] == "OBSERVED_UNVERIFIED" and len(shown["paths"]) == 11
    assert len((tmp_path / "detail-called").read_text().splitlines()) == 11
    assert (tmp_path / "detail-called.budget").read_text() == "20.0 2.0"
    assert not (root / "CURRENT.json").exists() and "NOT_UPDATED" in result.stdout
    assert all(str(p) not in result.stdout + result.stderr for p in (root, config, supplement))


@pytest.mark.parametrize(
    "extra,omit_trust",
    [
        (["--collect-product-details", "--detail-budget-seconds", "10"], False),
        (
            ["--retain-candidate", "--collect-product-details", "--detail-budget-seconds", "10"],
            False,
        ),
        (["--retain-candidate", "--fetch-product-navigation", "--collect-product-details"], False),
        (options() + ["--detail-budget-seconds", "nan"], False),
        (options() + ["--detail-budget-seconds", "0"], False),
        (options() + ["--detail-per-request-seconds", "61"], False),
        (["--detail-budget-seconds", "5"], False),
        (["--detail-per-request-seconds", "5"], False),
        (options(), True),
    ],
)
def test_invalid_options_reject_before_ids_writes_and_network(tmp_path, extra, omit_trust):
    root, _, config, supplement = setup_preview(tmp_path)
    env = inject(tmp_path)
    cmd = cli(root, config, supplement, *extra)
    if omit_trust:
        cmd.remove("--trusted-local-posix")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2 and json.loads(result.stdout) == {
        "status": "ERROR",
        "reason": "INVALID_ARGUMENTS",
    }
    assert not (root / "observation-runs").exists() and not (root / "detail-collections").exists()
    assert not (tmp_path / "nav-called").exists() and not (tmp_path / "detail-called").exists()


def main_args(root, config, supplement):
    return [
        "run",
        "--calendar-root",
        str(root),
        "--config",
        str(config),
        "--supplement",
        str(supplement),
        "--budget-seconds",
        "15",
        "--trusted-local-posix",
        *options(),
    ]


def committed():
    return {
        "reason": "NONE",
        "intent_installation": "COMMITTED",
        "record_installation": "COMMITTED",
        "candidate_state": "COMMITTED",
    }


@pytest.mark.parametrize(
    "field,value,reason,code",
    [
        ("candidate_state", "COMMIT_UNKNOWN", "NONE", 25),
        ("record_installation", "COMMIT_UNKNOWN", "NONE", 25),
        ("record_installation", "NOT_COMMITTED", "NONE", 24),
        ("candidate_state", "NOT_COMMITTED", "NONE", 24),
        ("candidate_state", "COMMIT_UNKNOWN", "PREVIEW_INTERRUPTED_CLEANUP_UNKNOWN", 23),
    ],
)
def test_ineligible_preview_never_calls_collection(
    tmp_path, monkeypatch, capsys, field, value, reason, code
):
    root, _, config, supplement = setup_preview(tmp_path)
    result = committed()
    result[field], result["reason"] = value, reason
    monkeypatch.setattr(
        runs,
        "run_once",
        lambda **kwargs: {
            **result,
            "run_id": kwargs["run_id"],
            "candidate_id": kwargs["candidate_id"],
        },
    )
    monkeypatch.setattr(
        collection, "collect_once", lambda **kwargs: pytest.fail("unexpected collection")
    )
    assert runs.main(main_args(root, config, supplement)) == code
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 3 and lines[-1]["event"] == "DETAIL_COLLECTION_NOT_ATTEMPTED"
    assert "collection_id" not in lines[-1]


@pytest.mark.parametrize(
    "mode,code",
    [
        ("cleanup", 23),
        ("unknown", 25),
        ("partial", 10),
        ("claim", 25),
        ("precondition", 25),
        ("raise_before", 25),
        ("raise_after", 25),
        ("interrupt", 23),
    ],
)
def test_detail_outcome_and_exception_preserve_actual_ids(
    tmp_path, monkeypatch, capsys, mode, code
):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(
        runs,
        "run_once",
        lambda **kwargs: {
            **committed(),
            "run_id": kwargs["run_id"],
            "candidate_id": kwargs["candidate_id"],
        },
    )

    def fake(**kwargs):
        rid, cid, collect_id = kwargs["run_id"], "", kwargs["collection_id"]
        # Candidate ID is captured from the earlier run allocation.
        captured = capsys.readouterr().out
        prior = [json.loads(line) for line in captured.splitlines()]
        cid = prior[0]["candidate_id"]
        # Restore captured lines for the caller to inspect alongside later output.
        print(captured, end="")
        if mode == "precondition":
            return collection._event(collect_id, rid, cid, "ERROR", "RUN_NOT_ELIGIBLE")
        if mode == "raise_before":
            raise RuntimeError(str(root))
        kwargs["emit"](
            {
                "event": "COLLECTION_ID_ALLOCATED",
                "run_id": rid,
                "candidate_id": cid,
                "collection_id": collect_id,
            }
        )
        if mode in {"raise_after", "interrupt"}:
            exc = KeyboardInterrupt() if mode == "interrupt" else RuntimeError(str(root))
            if mode == "interrupt":
                exc.child_cleanup = "WORKER_CLEANUP_UNCERTAIN"
            raise exc
        status, reason = {
            "cleanup": ("UNKNOWN", "BATCH_CLEANUP_UNCERTAIN"),
            "unknown": ("UNKNOWN", "BATCH_UNKNOWN"),
            "partial": ("PARTIAL", "BATCH_STOPPED"),
            "claim": ("UNKNOWN", "RUN_ALREADY_CLAIMED"),
        }[mode]
        result = collection._event(collect_id, rid, cid, status, reason)
        if mode == "cleanup":
            result.update(cleanup_status="UNCERTAIN", batch_reason="WORKER_CLEANUP_UNCERTAIN")
        if mode == "claim":
            result["observed_claimed_collection_id"] = "d" * 32
        return result

    monkeypatch.setattr(collection, "collect_once", fake)
    assert runs.main(main_args(root, config, supplement)) == code
    output = capsys.readouterr().out
    lines = [json.loads(line) for line in output.splitlines()]
    assert str(root) not in output
    assert (
        lines[-1]["run_id"] == lines[0]["run_id"]
        and lines[-1]["candidate_id"] == lines[0]["candidate_id"]
    )
    if mode in {"precondition", "raise_before"}:
        assert (
            lines[-1]["event"] == "DETAIL_COLLECTION_NOT_ATTEMPTED"
            and "collection_id" not in lines[-1]
        )
    else:
        assert (
            lines[2]["event"] == "COLLECTION_ID_ALLOCATED"
            and lines[-1]["collection_id"] == lines[2]["collection_id"]
        )
    if mode in {"cleanup", "interrupt"}:
        assert lines[-1]["cleanup_status"] == "UNCERTAIN"


def test_run_exception_preserves_run_ids_without_detail(tmp_path, monkeypatch, capsys):
    root, _, config, supplement = setup_preview(tmp_path)

    def fail(**kwargs):
        raise RuntimeError(str(root))

    monkeypatch.setattr(runs, "run_once", fail)
    monkeypatch.setattr(collection, "collect_once", lambda **kwargs: pytest.fail("network"))
    assert runs.main(main_args(root, config, supplement)) == 25
    text = capsys.readouterr().out
    lines = [json.loads(line) for line in text.splitlines()]
    assert len(lines) == 2 and lines[1]["event"] == "DETAIL_COLLECTION_NOT_ATTEMPTED"
    assert lines[1]["run_id"] == lines[0]["run_id"] and str(root) not in text


def test_forged_success_with_cleanup_unknown_cannot_report_success(tmp_path, monkeypatch, capsys):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(
        runs,
        "run_once",
        lambda **kw: {**committed(), "run_id": kw["run_id"], "candidate_id": kw["candidate_id"]},
    )

    def fake(**kwargs):
        prior = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        for line in prior:
            print(json.dumps(line))
        rid, cid, col = kwargs["run_id"], prior[0]["candidate_id"], kwargs["collection_id"]
        kwargs["emit"](
            {
                "event": "COLLECTION_ID_ALLOCATED",
                "run_id": rid,
                "candidate_id": cid,
                "collection_id": col,
            }
        )
        result = collection._event(col, rid, cid, "OBSERVED_UNVERIFIED", "ALL_OBSERVED_UNVERIFIED")
        result.update(cleanup_status="UNCERTAIN", batch_reason="WORKER_CLEANUP_UNCERTAIN")
        return result

    monkeypatch.setattr(collection, "collect_once", fake)
    assert runs.main(main_args(root, config, supplement)) == 23
    final = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert final["event"] == "DETAIL_COLLECTION_UNKNOWN" and final["cleanup_status"] == "UNCERTAIN"


def test_malformed_cleanup_report_remains_cleanup_unknown(tmp_path, monkeypatch, capsys):
    root, _, config, supplement = setup_preview(tmp_path)
    monkeypatch.setattr(
        runs,
        "run_once",
        lambda **kw: {**committed(), "run_id": kw["run_id"], "candidate_id": kw["candidate_id"]},
    )

    def fake(**kwargs):
        prior = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        for line in prior:
            print(json.dumps(line))
        kwargs["emit"](
            {
                "event": "COLLECTION_ID_ALLOCATED",
                "run_id": kwargs["run_id"],
                "candidate_id": prior[0]["candidate_id"],
                "collection_id": kwargs["collection_id"],
            }
        )
        return {"status": "UNKNOWN", "reason": "BATCH_CLEANUP_UNCERTAIN"}

    monkeypatch.setattr(collection, "collect_once", fake)
    assert runs.main(main_args(root, config, supplement)) == 23
    final = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert final["event"] == "DETAIL_COLLECTION_UNKNOWN" and final["cleanup_status"] == "UNCERTAIN"
