"""Cross-process atomicity checks for the MIG03 migration marker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bt_api_py import service_migration_gate as gate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_EPOCH = "source-epoch"
_DESTINATION_EPOCH = "destination-epoch"


def _enable_destination(state: Path) -> None:
    marker = gate.read_migration_marker(state)
    assert marker is not None and marker.status == gate.STATUS_COMMITTED
    gate.enable_migration_cutover(
        state,
        migration_id=marker.migration_id,
        deployment_epoch=marker.destination_epoch,
    )


def _python_process(code: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", code, *args],
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_file(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(
                f"child exited before signaling (code={process.returncode}, "
                f"stdout={stdout!r}, stderr={stderr!r})"
            )
        time.sleep(0.01)
    process.kill()
    stdout, stderr = process.communicate()
    pytest.fail(f"child did not signal (stdout={stdout!r}, stderr={stderr!r})")


def _finish(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        return process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(f"child timed out (stdout={stdout!r}, stderr={stderr!r})")


def test_late_rollback_rereads_sealed_marker_before_removing_it(tmp_path: Path) -> None:
    state = tmp_path / "registry.sqlite3"
    ready = tmp_path / "rollback-read-committed"
    resume = tmp_path / "resume-rollback"
    marker = gate.arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    gate.enable_migration_cutover(
        state,
        migration_id=marker.migration_id,
        deployment_epoch=_DESTINATION_EPOCH,
    )

    rollback_code = r"""
import json, sys, time
from contextlib import contextmanager
from pathlib import Path
from bt_api_py import service_migration_gate as gate

state, ready, resume = map(Path, sys.argv[1:])
real_lock = gate._marker_state_lock
@contextmanager
def paused_lock(path):
    ready.write_text('pause before acquiring lock for ENABLED marker', encoding='utf-8')
    deadline = time.monotonic() + 10
    while not resume.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    if not resume.exists():
        raise RuntimeError('resume signal timed out')
    with real_lock(path):
        yield
gate._marker_state_lock = paused_lock
try:
    gate.rollback_migration_cutover(state, deployment_epoch='source-epoch')
    print(json.dumps({'result': 'removed'}))
except gate.MigrationGateError as exc:
    print(json.dumps({'result': 'error', 'code': exc.code}))
"""
    rollback = _python_process(rollback_code, str(state), str(ready), str(resume))
    try:
        _wait_for_file(ready, rollback)
        # This process seals after rollback is poised to acquire the shared
        # transition lock. The rollback must reread ENABLED/SEALED while
        # holding that same lock and must never remove the marker.
        assert gate.record_migration_write_attempt(
            state,
            deployment_epoch=_DESTINATION_EPOCH,
            fact_kind="dispatch",
        ) is True
        resume.write_text("continue", encoding="utf-8")
        stdout, stderr = _finish(rollback)
    finally:
        if rollback.poll() is None:
            resume.touch()
            rollback.kill()
            rollback.communicate()

    assert stderr == ""
    assert json.loads(stdout) == {
        "result": "error",
        "code": gate.REFUSAL_REVERSE_MIGRATION_REFUSED,
    }
    marker = gate.read_migration_marker(state)
    assert marker is not None and marker.sealed
    assert marker.first_write_fact_kind == "dispatch"


def test_concurrent_different_epoch_arms_have_exactly_one_winner(tmp_path: Path) -> None:
    state = tmp_path / "registry.sqlite3"
    arm_locked = tmp_path / "arm-a-locked-before-publish"
    release_arm = tmp_path / "release-arm-a"
    contender_timed_out = tmp_path / "arm-b-timeout-result.json"
    retry_arm = tmp_path / "retry-arm-b"
    arm_a_code = r"""
import json, sys, time
from pathlib import Path
from bt_api_py import service_migration_gate as gate

state, ready, release = map(Path, sys.argv[1:])
real_write = gate._write_marker
def delayed_write(path, marker):
    ready.write_text('arm has read no marker and still owns the lock', encoding='utf-8')
    deadline = time.monotonic() + 10
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    if not release.exists():
        raise RuntimeError('release signal timed out')
    return real_write(path, marker)
gate._write_marker = delayed_write
try:
    marker = gate.arm_migration_cutover(state, source_epoch='source-a', new_epoch='destination-a')
    print(json.dumps({'result': 'ok', 'source': marker.source_epoch, 'destination': marker.destination_epoch}))
except gate.MigrationGateError as exc:
    print(json.dumps({'result': 'error', 'code': exc.code}))
"""
    arm_b_code = r"""
import json, sys, time
from pathlib import Path
from bt_api_py import service_migration_gate as gate

state, first_result_path, retry = map(Path, sys.argv[1:])
gate._MIGRATION_LOCK_TIMEOUT_SECONDS = 0.1
try:
    gate.arm_migration_cutover(state, source_epoch='source-b', new_epoch='destination-b')
    first = {'result': 'unexpected-success'}
except gate.MigrationGateError as exc:
    first = {'result': 'error', 'code': exc.code}
first_result_path.write_text(json.dumps(first, sort_keys=True), encoding='utf-8')
deadline = time.monotonic() + 10
while not retry.exists() and time.monotonic() < deadline:
    time.sleep(0.005)
if not retry.exists():
    raise RuntimeError('retry signal timed out')
gate._MIGRATION_LOCK_TIMEOUT_SECONDS = 5.0
try:
    gate.arm_migration_cutover(state, source_epoch='source-b', new_epoch='destination-b')
    second = {'result': 'unexpected-success'}
except gate.MigrationGateError as exc:
    second = {'result': 'error', 'code': exc.code}
print(json.dumps({'first': first, 'retry': second}, sort_keys=True))
"""
    arm_a = _python_process(arm_a_code, str(state), str(arm_locked), str(release_arm))
    arm_b = None
    try:
        _wait_for_file(arm_locked, arm_a)
        # A has passed its absent-marker read and paused before atomic publish
        # while retaining the real interprocess marker lock.
        assert gate.read_migration_marker(state) is None
        arm_b = _python_process(
            arm_b_code, str(state), str(contender_timed_out), str(retry_arm)
        )
        _wait_for_file(contender_timed_out, arm_b)
        first_attempt = json.loads(contender_timed_out.read_text(encoding="utf-8"))
        assert first_attempt == {
            "result": "error",
            "code": gate.CODE_MIGRATION_GATE_LOCK_TIMEOUT,
        }
        assert gate.read_migration_marker(state) is None

        release_arm.touch()
        arm_a_out, arm_a_err = _finish(arm_a)
        assert arm_a_err == ""
        winner = json.loads(arm_a_out)
        assert winner == {
            "result": "ok",
            "source": "source-a",
            "destination": "destination-a",
        }
        marker = gate.read_migration_marker(state)
        assert marker is not None
        assert (marker.source_epoch, marker.destination_epoch) == (
            "source-a",
            "destination-a",
        )

        retry_arm.touch()
        arm_b_out, arm_b_err = _finish(arm_b)
    finally:
        release_arm.touch()
        retry_arm.touch()
        for process in (arm_a, arm_b):
            if process is None:
                continue
            if process.poll() is None:
                process.kill()
                process.communicate()

    assert arm_b_err == ""
    retry_result = json.loads(arm_b_out)
    assert retry_result == {
        "first": first_attempt,
        "retry": {"result": "error", "code": gate.CODE_CUTOVER_MARKER_CONFLICT},
    }
    marker = gate.read_migration_marker(state)
    assert marker is not None
    assert (marker.source_epoch, marker.destination_epoch) == (
        "source-a",
        "destination-a",
    )


def test_lock_wait_is_bounded_and_released_after_contention(tmp_path: Path) -> None:
    state = tmp_path / "registry.sqlite3"
    ready = tmp_path / "lock-held"
    release = tmp_path / "release-lock"
    gate.arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    _enable_destination(state)
    holder_code = r"""
import sys, time
from pathlib import Path
from bt_api_py import service_migration_gate as gate

state, ready, release = map(Path, sys.argv[1:])
with gate._marker_state_lock(state):
    ready.write_text('held', encoding='utf-8')
    deadline = time.monotonic() + 10
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
"""
    contender_code = r"""
import json, sys
from bt_api_py import service_migration_gate as gate

gate._MIGRATION_LOCK_TIMEOUT_SECONDS = 0.1
try:
    gate.record_migration_first_write(sys.argv[1], fact_kind='dispatch')
    print(json.dumps({'result': 'unexpected-success'}))
except gate.MigrationGateError as exc:
    print(json.dumps({'result': 'error', 'code': exc.code}))
"""
    holder = _python_process(holder_code, str(state), str(ready), str(release))
    try:
        _wait_for_file(ready, holder)
        contender = _python_process(contender_code, str(state))
        stdout, stderr = _finish(contender)
    finally:
        release.touch()
        holder_stdout, holder_stderr = _finish(holder)

    assert stderr == ""
    assert json.loads(stdout) == {
        "result": "error",
        "code": gate.CODE_MIGRATION_GATE_LOCK_TIMEOUT,
    }
    assert holder_stderr == ""
    assert gate.record_migration_first_write(state, fact_kind="dispatch") is True


def test_first_arm_waits_for_live_start_guard_and_retries_after_release(
    tmp_path: Path,
) -> None:
    state = tmp_path / "registry.sqlite3"
    start_guard_ready = tmp_path / "start-guard-held"
    release_start = tmp_path / "release-start-guard"
    arm_timeout_result = tmp_path / "arm-timeout-result.json"
    retry_arm = tmp_path / "retry-arm-after-start"
    start_code = r"""
import json, sys, time
from pathlib import Path
from bt_api_py import service_migration_gate as gate

state, ready, release = map(Path, sys.argv[1:])
with gate.migration_start_guard(state, deployment_epoch='source-epoch'):
    ready.write_text('startup holds the persistent marker lock', encoding='utf-8')
    deadline = time.monotonic() + 10
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    if not release.exists():
        raise RuntimeError('start guard release timed out')
print(json.dumps({'start_guard': 'released'}))
"""
    arm_code = r"""
import json, sys, time
from pathlib import Path
from bt_api_py import service_migration_gate as gate

state, first_result_path, retry = map(Path, sys.argv[1:])
gate._MIGRATION_LOCK_TIMEOUT_SECONDS = 0.1
try:
    gate.arm_migration_cutover(
        state, source_epoch='source-epoch', new_epoch='destination-epoch'
    )
    first = {'result': 'unexpected-success'}
except gate.MigrationGateError as exc:
    first = {'result': 'error', 'code': exc.code}
first_result_path.write_text(json.dumps(first, sort_keys=True), encoding='utf-8')
deadline = time.monotonic() + 10
while not retry.exists() and time.monotonic() < deadline:
    time.sleep(0.005)
if not retry.exists():
    raise RuntimeError('arm retry signal timed out')
gate._MIGRATION_LOCK_TIMEOUT_SECONDS = 5.0
try:
    marker = gate.arm_migration_cutover(
        state, source_epoch='source-epoch', new_epoch='destination-epoch'
    )
    result = {
        'result': 'armed',
        'status': marker.status,
        'source': marker.source_epoch,
        'destination': marker.destination_epoch,
    }
except gate.MigrationGateError as exc:
    result = {'result': 'error', 'code': exc.code}
print(json.dumps({'first': first, 'retry': result}, sort_keys=True))
"""

    def marker_is_absent() -> bool:
        try:
            gate.marker_path(state).stat()
        except FileNotFoundError:
            return True
        return False

    startup = _python_process(
        start_code, str(state), str(start_guard_ready), str(release_start)
    )
    arm = None
    try:
        _wait_for_file(start_guard_ready, startup)
        assert marker_is_absent()
        arm = _python_process(
            arm_code, str(state), str(arm_timeout_result), str(retry_arm)
        )
        _wait_for_file(arm_timeout_result, arm)
        assert json.loads(arm_timeout_result.read_text(encoding="utf-8")) == {
            "result": "error",
            "code": gate.CODE_MIGRATION_GATE_LOCK_TIMEOUT,
        }
        # The failed contender cannot publish while the real start guard owns
        # the cross-process lock. Releasing startup permits the same operation
        # to arm normally, proving this is bounded serialization, not a veto.
        assert marker_is_absent()
        release_start.write_text("continue", encoding="utf-8")
        startup_stdout, startup_stderr = _finish(startup)
        retry_arm.touch()
        arm_stdout, arm_stderr = _finish(arm)
    finally:
        release_start.touch()
        if startup.poll() is None:
            startup.kill()
            startup.communicate()
        if arm is not None and arm.poll() is None:
            retry_arm.touch()
            arm.kill()
            arm.communicate()

    assert startup_stderr == ""
    assert json.loads(startup_stdout) == {"start_guard": "released"}
    assert arm_stderr == ""
    assert json.loads(arm_stdout) == {
        "first": {
            "result": "error",
            "code": gate.CODE_MIGRATION_GATE_LOCK_TIMEOUT,
        },
        "retry": {
            "result": "armed",
            "status": gate.STATUS_COMMITTED,
            "source": "source-epoch",
            "destination": "destination-epoch",
        },
    }
    marker = gate.read_migration_marker(state)
    assert marker is not None
    assert (marker.source_epoch, marker.destination_epoch) == (
        "source-epoch",
        "destination-epoch",
    )


def test_pre_replace_write_failure_preserves_old_file_and_allows_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "registry.sqlite3"
    gate.arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    _enable_destination(state)
    path = gate.marker_path(state)
    old_bytes = path.read_bytes()
    real_replace = os.replace

    def fail_replace(source, destination):
        raise OSError("injected atomic-publish failure")

    monkeypatch.setattr(gate.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected atomic-publish failure"):
        gate.record_migration_first_write(state, fact_kind="dispatch")
    assert path.read_bytes() == old_bytes
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []

    monkeypatch.setattr(gate.os, "replace", real_replace)
    assert gate.record_migration_first_write(state, fact_kind="dispatch") is True
    marker = gate.read_migration_marker(state)
    assert marker is not None and marker.sealed


def test_atomic_file_readers_observe_only_complete_marker_documents(tmp_path: Path) -> None:
    state = tmp_path / "registry.sqlite3"
    writer = r"""
import sys, time
from pathlib import Path
from bt_api_py.service_migration_gate import (
    arm_migration_cutover,
    enable_migration_cutover,
    record_migration_first_write,
    rollback_migration_cutover,
)

state, ready, first_committed, reader_ack = map(Path, sys.argv[1:])
ready.write_text('writing', encoding='utf-8')
marker = arm_migration_cutover(
    state,
    source_epoch='cycle-source',
    new_epoch='cycle-destination',
    migration_id='cycle-migration',
)
first_committed.write_text('published', encoding='utf-8')
deadline = time.monotonic() + 10
while not reader_ack.exists() and time.monotonic() < deadline:
    time.sleep(0.005)
if not reader_ack.exists():
    raise RuntimeError('reader acknowledgement timed out')
for _ in range(100):
    arm_migration_cutover(
        state,
        source_epoch='cycle-source',
        new_epoch='cycle-destination',
        migration_id='cycle-migration',
    )
    time.sleep(0.001)
    rollback_migration_cutover(state, deployment_epoch='cycle-source')
    time.sleep(0.001)
marker = arm_migration_cutover(
    state,
    source_epoch='cycle-source',
    new_epoch='cycle-destination',
    migration_id='cycle-migration',
)
enable_migration_cutover(
    state,
    migration_id=marker.migration_id,
    deployment_epoch='cycle-destination',
)
record_migration_first_write(state, fact_kind='dispatch')
"""
    process = _python_process(
        writer,
        str(state),
        str(tmp_path / "writer-ready"),
        str(tmp_path / "first-committed"),
        str(tmp_path / "reader-ack"),
    )
    _wait_for_file(tmp_path / "writer-ready", process)
    seen = set()
    try:
        _wait_for_file(tmp_path / "first-committed", process)
        first_marker = gate.read_migration_marker(state)
        assert first_marker is not None
        assert first_marker.status == gate.STATUS_COMMITTED
        assert first_marker.migration_id == "cycle-migration"
        seen.add(first_marker.status)
        (tmp_path / "reader-ack").touch()
        while process.poll() is None:
            marker = gate.read_migration_marker(state)
            if marker is None:
                seen.add(None)
            else:
                assert marker.migration_id == "cycle-migration"
                assert marker.source_epoch == "cycle-source"
                assert marker.destination_epoch == "cycle-destination"
                assert (marker.status, marker.first_write_fact_kind) in {
                    (gate.STATUS_COMMITTED, None),
                    (gate.STATUS_ENABLED, None),
                    (gate.STATUS_SEALED, "dispatch"),
                }
                seen.add(marker.status)
            time.sleep(0.0005)
        stdout, stderr = _finish(process)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert stdout == ""
    assert stderr == ""
    assert gate.STATUS_COMMITTED in seen
    final_marker = gate.read_migration_marker(state)
    assert final_marker is not None and final_marker.sealed
    assert final_marker.first_write_fact_kind == "dispatch"


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_schema_version_requires_an_exact_integer(tmp_path: Path, schema_version) -> None:
    state = tmp_path / "registry.sqlite3"
    gate.arm_migration_cutover(
        state, source_epoch=_SOURCE_EPOCH, new_epoch=_DESTINATION_EPOCH
    )
    path = gate.marker_path(state)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = schema_version
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(gate.MigrationGateError) as unreadable:
        gate.read_migration_marker(state)
    assert unreadable.value.code == gate.CODE_CUTOVER_MARKER_UNREADABLE
