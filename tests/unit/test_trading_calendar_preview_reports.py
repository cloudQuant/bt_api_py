"""Local temporary-file tests; no account, network, mail or publication."""

import json
import multiprocessing
import os
import stat
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bt_api_py import trading_calendar_preview_reports as retention
from bt_api_py.trading_calendar_preview import Attempt, PreviewReport


def report():
    return PreviewReport(
        "INCOMPLETE",
        "SHFE_NOT_REQUESTED",
        10,
        input_status="CHECKED",
        input_code="SUPPLEMENT_UNVERIFIED",
        supplement_status="CANDIDATE",
    )


def json_report(value):
    return json.loads(json.dumps(value.to_dict()))


def retain(directory, value=None, **kwargs):
    return retention.retain_preview_report(
        report() if value is None else value,
        directory=directory,
        filesystem=kwargs.pop("filesystem", retention.TRUSTED_LOCAL_POSIX),
        **kwargs,
    )


def test_valid_report_is_stored_once_without_paths_or_sensitive_data(tmp_path):
    directory = tmp_path.resolve()
    original = report()
    result = retain(directory, original)
    assert (result.status, result.visibility, result.durability) == (
        "COMMITTED",
        "VISIBLE",
        "UNCONFIRMED",
    )
    assert (result.verification, result.publication) == ("UNVERIFIED", "NOT_AUTHORIZED")
    names = list(directory.iterdir())
    assert len(names) == 1 and names[0].name == f"preview-{result.run_id}.json"
    assert stat.S_IMODE(names[0].stat().st_mode) == 0o400
    raw = names[0].read_bytes()
    stored = json.loads(raw)
    assert stored == {
        "schema": "bt_api.calendar-preview-report/1",
        "run_id": result.run_id,
        "report": json_report(original),
    }
    assert original.completeness == "INCOMPLETE" and original.verification == "UNVERIFIED"
    assert str(directory).encode() not in raw and b"secret" not in raw


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "COMPLETE"},
        {"reason": "private /secret/config"},
        {"exit_code": True},
        {"reason": "SHFE_NOT_REQUESTED", "exit_code": 2},
        {"completeness": "COMPLETE"},
        {"verification": "VERIFIED"},
        {"publication": "PUBLISHED"},
        {"input_code": "/secret/config"},
        {"source_status": "CANDIDATE"},
        {"attempts": (Attempt(1, "CANDIDATE", "CANDIDATE", "UNVERIFIED", 0, "CANDIDATE"),)},
        {"holiday_closed_count": 2},
        {"merge_hour_count": 1},
        {"normalization_scope": "private"},
    ],
)
def test_corrupt_or_sensitive_report_is_rejected_not_replaced(tmp_path, changes):
    directory = tmp_path.resolve()
    result = retain(directory, replace(report(), **changes))
    assert (result.status, result.reason) == ("NOT_COMMITTED", "INVALID_REPORT")
    assert "secret" not in repr(result) and not list(directory.iterdir())


def test_protocol_fallback_is_not_silently_written(tmp_path, monkeypatch):
    directory = tmp_path.resolve()
    monkeypatch.setattr(
        retention,
        "_parse_worker_result",
        lambda *a: PreviewReport("ERROR", "WORKER_PROTOCOL_ERROR", 22),
    )
    result = retain(directory)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "INVALID_REPORT")
    assert not list(directory.iterdir())


def test_post_validation_object_mutation_cannot_change_stored_bytes(tmp_path, monkeypatch):
    value = report()
    expected = json_report(value)
    validated_bytes = retention._validated_report(value)
    original_uuid = retention.uuid.uuid4

    def mutate_after_validation():
        object.__setattr__(value, "input_code", "/private/secret/config")
        return original_uuid()

    monkeypatch.setattr(retention.uuid, "uuid4", mutate_after_validation)
    result = retain(tmp_path.resolve(), value)
    assert result.status == "COMMITTED"
    payload = (tmp_path / f"preview-{result.run_id}.json").read_bytes()
    assert json.loads(payload)["report"] == expected
    assert b'"report":' + validated_bytes + b"}" in payload
    assert b"secret" not in payload


def test_valid_supervisor_error_is_preserved_without_replacement(tmp_path):
    value = PreviewReport("ERROR", "WORKER_PROTOCOL_ERROR", 22)
    result = retain(tmp_path.resolve(), value)
    assert result.status == "COMMITTED"
    stored = json.loads((tmp_path / f"preview-{result.run_id}.json").read_bytes())
    assert stored["report"] == json_report(value)


def test_explicit_local_assumption_and_existing_no_symlink_directory(tmp_path):
    directory = tmp_path.resolve()
    assert retain(directory, filesystem="NETWORK").reason == "UNSUPPORTED_FILESYSTEM"
    assert retain(directory / "missing").reason == "INVALID_DIRECTORY"
    assert retain(directory / ".." / "escape").reason == "INVALID_DIRECTORY"
    assert retain("relative").reason == "INVALID_DIRECTORY"
    link = directory / "link"
    actual = directory / "actual"
    actual.mkdir()
    link.symlink_to(actual, target_is_directory=True)
    assert retain(link).reason == "INVALID_DIRECTORY"
    assert not list(actual.iterdir())


def test_existing_final_file_and_symlink_cannot_be_overwritten(tmp_path, monkeypatch):
    directory = tmp_path.resolve()
    fixed = "a" * 32
    monkeypatch.setattr(retention.uuid, "uuid4", lambda: SimpleNamespace(hex=fixed))
    target = directory / f"preview-{fixed}.json"
    target.write_bytes(b"existing")
    result = retain(directory)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "RUN_ID_EXISTS")
    assert target.read_bytes() == b"existing" and len(list(directory.iterdir())) == 1
    target.unlink()
    external = directory / "other"
    external.write_bytes(b"protected")
    target.symlink_to(external)
    assert retain(directory).reason == "RUN_ID_EXISTS"
    assert target.is_symlink() and external.read_bytes() == b"protected"


def test_write_failure_before_commit_does_not_create_final(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("secret path")

    monkeypatch.setattr(retention.os, "write", fail)
    result = retain(tmp_path.resolve())
    assert (result.status, result.visibility) == ("NOT_COMMITTED", "NOT_OBSERVED")
    assert "secret" not in repr(result) and not list(tmp_path.iterdir())


def test_link_exception_after_success_reports_unknown_and_preserves_final(tmp_path, monkeypatch):
    original = retention.os.link

    def fail_after_link(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("injected after link")

    monkeypatch.setattr(retention.os, "link", fail_after_link)
    monkeypatch.setattr(retention, "_supported", lambda: True)
    result = retain(tmp_path.resolve())
    assert (result.status, result.visibility, result.durability) == (
        "COMMIT_UNKNOWN",
        "NOT_OBSERVED",
        "UNCONFIRMED",
    )
    assert json.loads((tmp_path / f"preview-{result.run_id}.json").read_bytes())[
        "report"
    ] == json_report(report())


def test_file_exists_exception_after_success_is_still_unknown(tmp_path, monkeypatch):
    original = retention.os.link

    def fail_after_link(*args, **kwargs):
        original(*args, **kwargs)
        raise FileExistsError("injected after link")

    monkeypatch.setattr(retention.os, "link", fail_after_link)
    monkeypatch.setattr(retention, "_supported", lambda: True)
    result = retain(tmp_path.resolve())
    assert (result.status, result.visibility) == ("COMMIT_UNKNOWN", "NOT_OBSERVED")
    assert (tmp_path / f"preview-{result.run_id}.json").is_file()


def test_directory_fsync_failure_after_verified_readback_is_visible_but_unknown(
    tmp_path, monkeypatch
):
    original = retention.os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected directory fsync failure")
        return original(fd)

    monkeypatch.setattr(retention.os, "fsync", fail_directory)
    result = retain(tmp_path.resolve())
    assert (result.status, result.visibility, result.durability) == (
        "COMMIT_UNKNOWN",
        "VISIBLE",
        "UNCONFIRMED",
    )
    assert (tmp_path / f"preview-{result.run_id}.json").is_file()


def test_random_failure_is_finite_and_does_not_create_files(tmp_path, monkeypatch):
    def fail():
        raise RuntimeError("sensitive")

    monkeypatch.setattr(retention.uuid, "uuid4", fail)
    result = retain(tmp_path.resolve())
    assert result.reason == "RANDOM_UNAVAILABLE" and not list(tmp_path.iterdir())
    assert "sensitive" not in repr(result)


def _parallel_writer(directory, output):
    output.put(retain(directory))


def test_concurrent_processes_write_distinct_immutable_names(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    output = ctx.Queue()
    processes = [
        ctx.Process(target=_parallel_writer, args=(tmp_path.resolve(), output)) for _ in range(4)
    ]
    for process in processes:
        process.start()
    results = [output.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert all(result.status == "COMMITTED" for result in results)
    assert len({result.run_id for result in results}) == 4
    assert len(list(tmp_path.iterdir())) == 4
    assert all(
        json.loads(path.read_bytes())["report"] == json_report(report())
        for path in tmp_path.iterdir()
    )


def _crash_after_link(directory):
    original = retention.os.link

    def link_then_die(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(73)

    retention.os.link = link_then_die
    retention._supported = lambda: True
    retain(directory)


def test_real_process_exit_after_link_leaves_readable_report_and_no_claimed_outcome(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=_crash_after_link, args=(tmp_path.resolve(),))
    process.start()
    process.join(15)
    assert process.exitcode == 73
    committed = list(tmp_path.glob("preview-*.json"))
    assert len(committed) == 1
    assert json.loads(committed[0].read_bytes())["report"] == json_report(report())
    # A crash can leave an orphan temporary name; this is not automatic cleanup.
    assert len(list(tmp_path.glob(".preview-*.tmp"))) == 1
