"""Pure fakes for the synthetic Gateway fixture's local cleanup evidence.

These tests do not start a service, worker thread, transport or account.
"""

from types import SimpleNamespace

import pytest

from tests.integration.test_iter64_risk_service_process import (
    _gateway,
    _gateway_fixture_cleanup,
    _gateway_fixture_closed,
    _gateway_fixture_message,
    _write_key,
)

PRIVATE_DETAIL = "synthetic-private-detail-must-not-escape"


class FakeMessages:
    def __init__(self, *, fail_for=()):
        self.items = []
        self.fail_for = set(fail_for)

    def put(self, message):
        if message[0] in self.fail_for:
            raise RuntimeError(PRIVATE_DETAIL)
        self.items.append(message)


class FakeHost:
    def __init__(self, *, state="started", close_result="closed", summary=None):
        self.state = state
        self.close_result = close_result
        self.close_calls = 0
        self.summary_override = summary

    def close(self):
        self.close_calls += 1
        if self.close_result == "raise":
            raise RuntimeError(PRIVATE_DETAIL)
        self.state = self.close_result

    @property
    def summary(self):
        if self.summary_override == "raise":
            raise RuntimeError(PRIVATE_DETAIL)
        if self.summary_override is not None:
            return self.summary_override
        return {"state": self.state, "cleanup_failures": ()}


class FakeOwner:
    def __init__(self, *, close_result=True, closed=True, status=None):
        self.close_result = close_result
        self.closed = closed
        self.close_calls = 0
        self.status_calls = 0
        self.result_status = status or SimpleNamespace(
            thread_alive=False,
            running=False,
            accepting=False,
            queue_depth=0,
            service_queue_depth=0,
            failure_category=None,
        )

    def close(self, timeout_seconds):
        assert timeout_seconds == 5
        self.close_calls += 1
        if self.close_result == "raise":
            raise RuntimeError(PRIVATE_DETAIL)
        return self.close_result

    def status(self):
        self.status_calls += 1
        if self.result_status == "raise":
            raise RuntimeError(PRIVATE_DETAIL)
        return self.result_status


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def _assert_uncertain(messages, reason):
    assert messages.items == [("cleanup_uncertain", reason)]
    assert PRIVATE_DETAIL not in repr(messages.items)


def test_confirmed_local_cleanup_is_the_only_success_marker():
    host, owner, messages = FakeHost(), FakeOwner(), FakeMessages()
    assert _gateway_fixture_cleanup(host, owner, messages, run_failed=False) is True
    assert host.close_calls == owner.close_calls == owner.status_calls == 1
    assert len(messages.items) == 1
    assert messages.items[0][0] == "closed"
    assert type(messages.items[0][1]) is int


def test_already_closed_host_still_requires_owner_confirmation():
    host, owner, messages = FakeHost(state="closed"), FakeOwner(), FakeMessages()
    assert _gateway_fixture_cleanup(host, owner, messages, run_failed=False) is True
    assert host.close_calls == 0
    assert owner.close_calls == 1
    assert messages.items[0][0] == "closed"


@pytest.mark.parametrize("state", ["created", "failed", "close_failed", "unknown"])
def test_unverified_host_is_retained_without_closing_owner(state):
    host, owner, messages = FakeHost(state=state), FakeOwner(), FakeMessages()
    assert _gateway_fixture_cleanup(host, owner, messages, run_failed=False) is False
    assert host.close_calls == owner.close_calls == 0
    _assert_uncertain(messages, "HOST_UNVERIFIED")


@pytest.mark.parametrize("host,owner", [(None, None), (None, FakeOwner()), (FakeHost(), None)])
def test_missing_resource_is_unverified_without_closing_either(host, owner):
    messages = FakeMessages()
    assert _gateway_fixture_cleanup(host, owner, messages, run_failed=False) is False
    if host is not None:
        assert host.close_calls == 0
    if owner is not None:
        assert owner.close_calls == 0
    _assert_uncertain(messages, "RESOURCE_UNVERIFIED")


@pytest.mark.parametrize(
    "host,reason",
    [
        (FakeHost(close_result="raise"), "CLEANUP_EXCEPTION"),
        (FakeHost(close_result="close_failed"), "HOST_UNVERIFIED"),
        (FakeHost(summary="raise"), "CLEANUP_EXCEPTION"),
        (FakeHost(summary={"state": "closed", "cleanup_failures": ("listener",)}), "HOST_UNVERIFIED"),
        (FakeHost(summary={"state": "closed", "cleanup_failures": []}), "HOST_UNVERIFIED"),
        (FakeHost(summary={"state": "started", "cleanup_failures": ()}), "HOST_UNVERIFIED"),
    ],
)
def test_host_failure_or_uncertain_evidence_never_closes_owner(host, reason):
    owner, messages = FakeOwner(), FakeMessages()
    assert _gateway_fixture_cleanup(host, owner, messages, run_failed=False) is False
    assert owner.close_calls == 0
    _assert_uncertain(messages, reason)


@pytest.mark.parametrize("result", [False, None, 1, "raise"])
def test_owner_close_requires_exact_true_and_no_exception(result):
    host, owner, messages = FakeHost(), FakeOwner(close_result=result), FakeMessages()
    assert _gateway_fixture_cleanup(host, owner, messages, run_failed=False) is False
    assert host.close_calls == owner.close_calls == 1
    assert owner.status_calls == 0
    _assert_uncertain(messages, "CLEANUP_EXCEPTION" if result == "raise" else "OWNER_UNVERIFIED")


def test_owner_closed_flag_must_be_true():
    host, owner, messages = FakeHost(), FakeOwner(closed=False), FakeMessages()
    assert _gateway_fixture_cleanup(host, owner, messages, run_failed=False) is False
    assert owner.close_calls == 1 and owner.status_calls == 0
    _assert_uncertain(messages, "OWNER_UNVERIFIED")


@pytest.mark.parametrize(
    "field,value",
    [
        ("thread_alive", True), ("thread_alive", None), ("running", True), ("accepting", True),
        ("queue_depth", 1), ("queue_depth", False), ("service_queue_depth", 1),
        ("service_queue_depth", False), ("failure_category", "cleanup_failed"),
    ],
)
def test_owner_unresolved_status_is_not_reported_closed(field, value):
    owner = FakeOwner()
    setattr(owner.result_status, field, value)
    messages = FakeMessages()
    clock = FakeClock()
    assert _gateway_fixture_cleanup(FakeHost(), owner, messages, run_failed=False,
                                    _clock=clock.monotonic, _sleep=clock.sleep) is False
    assert owner.close_calls == 1
    if field == "thread_alive" and value is True:
        assert 1 < owner.status_calls <= 27
        assert clock.value == pytest.approx(0.25)
    else:
        assert owner.status_calls == 1
    _assert_uncertain(messages, "OWNER_UNVERIFIED")


def test_owner_thread_is_given_bounded_time_to_finish_naturally():
    owner, messages, clock = FakeOwner(), FakeMessages(), FakeClock()
    statuses = [
        SimpleNamespace(thread_alive=True),
        SimpleNamespace(thread_alive=True),
        owner.result_status,
    ]

    def status():
        owner.status_calls += 1
        return statuses.pop(0)

    owner.status = status
    assert _gateway_fixture_cleanup(FakeHost(), owner, messages, run_failed=False,
                                    _clock=clock.monotonic, _sleep=clock.sleep) is True
    assert owner.close_calls == 1 and owner.status_calls == 3
    assert clock.sleeps == [0.01, 0.01]
    assert messages.items[0][0] == "closed"


def test_owner_status_failure_is_sanitized():
    owner, messages = FakeOwner(status="raise"), FakeMessages()
    assert _gateway_fixture_cleanup(FakeHost(), owner, messages, run_failed=False) is False
    _assert_uncertain(messages, "CLEANUP_EXCEPTION")


def test_prior_error_retains_both_resources():
    host, owner, messages = FakeHost(), FakeOwner(), FakeMessages()
    assert _gateway_fixture_cleanup(host, owner, messages, run_failed=True) is False
    assert host.close_calls == owner.close_calls == 0
    _assert_uncertain(messages, "RUN_FAILED")


def test_success_marker_delivery_failure_is_uncertain_without_secret():
    messages = FakeMessages(fail_for={"closed"})
    assert _gateway_fixture_cleanup(FakeHost(), FakeOwner(), messages, run_failed=False) is False
    _assert_uncertain(messages, "MESSAGE_UNVERIFIED")


def test_uncertain_message_delivery_failure_does_not_raise_or_report_success():
    messages = FakeMessages(fail_for={"cleanup_uncertain"})
    assert _gateway_fixture_cleanup(None, None, messages, run_failed=False) is False
    assert messages.items == []
    assert _gateway_fixture_message(messages, ("cleanup_uncertain", "FIXED")) is False


def test_gateway_import_failure_does_not_report_exception_or_cleanup_success(monkeypatch):
    import builtins
    import sys

    original_import = builtins.__import__

    def fail_fixture_import(name, *args, **kwargs):
        if name == "bt_api_base.direct_cancel_protocol":
            raise RuntimeError(PRIVATE_DETAIL)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_fixture_import)
    monkeypatch.setattr(sys, "path", list(sys.path))
    messages = FakeMessages()
    _gateway("unused", "unused", None, None, messages, None)
    assert messages.items == [
        ("error", "SYNTHETIC_GATEWAY_ERROR"),
        ("cleanup_uncertain", "RUN_FAILED"),
    ]
    assert PRIVATE_DETAIL not in repr(messages.items)


def test_gateway_error_message_failure_does_not_emit_success(monkeypatch):
    import builtins
    import sys

    original_import = builtins.__import__

    def fail_fixture_import(name, *args, **kwargs):
        if name == "bt_api_base.direct_cancel_protocol":
            raise RuntimeError(PRIVATE_DETAIL)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_fixture_import)
    monkeypatch.setattr(sys, "path", list(sys.path))
    messages = FakeMessages(fail_for={"error"})
    _gateway("unused", "unused", None, None, messages, None)
    assert messages.items == [("cleanup_uncertain", "RUN_FAILED")]


@pytest.mark.parametrize(
    "items,expected_pid",
    [
        ([], 123),
        ([("cleanup_uncertain", "HOST_UNVERIFIED")], 123),
        ([("error", "SYNTHETIC_GATEWAY_ERROR")], 123),
        ([("closed", 456)], 123),
        ([("closed", True)], 1),
        ([("closed", 123), ("cleanup_uncertain", "MESSAGE_UNVERIFIED")], 123),
        ([("closed", 123), ("closed", 123)], 123),
        ([("closed", 123)], 0),
    ],
)
def test_parent_never_accepts_missing_mismatched_or_uncertain_markers(items, expected_pid):
    import queue

    messages = queue.Queue()
    for item in items:
        messages.put(item)
    assert _gateway_fixture_closed(messages, expected_pid, timeout_seconds=0.001,
                                   drain_timeout_seconds=0.001) is False


def test_parent_accepts_only_one_matching_confirmed_close_marker():
    import queue

    messages = queue.Queue()
    messages.put(("closed", 123))
    assert _gateway_fixture_closed(messages, 123, timeout_seconds=0.001,
                                   drain_timeout_seconds=0.001) is True


def test_parent_message_failure_is_sanitized():
    class BrokenQueue:
        def get(self, *, timeout):
            raise RuntimeError(PRIVATE_DETAIL)

    assert _gateway_fixture_closed(BrokenQueue(), 123) is False


def test_secret_key_is_created_exclusively_with_private_mode(tmp_path, monkeypatch):
    import os
    import stat

    original_open, original_write = os.open, os.write
    observed = []

    def observe_open(path, flags, mode=0o777):
        observed.append(("open", flags, mode))
        return original_open(path, flags, mode)

    def observe_write(fd, content):
        observed.append(("mode_at_write", stat.S_IMODE(os.fstat(fd).st_mode)))
        return original_write(fd, content)

    path = tmp_path / "synthetic.secret"
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", observe_open)
        patch.setattr(os, "write", observe_write)
        assert _write_key(path, b"synthetic", secret=True) == str(path)
    assert observed[0][0] == "open"
    assert observed[0][1] & os.O_EXCL and observed[0][1] & os.O_CREAT
    assert observed[0][2] == 0o600
    assert observed[1][0] == "mode_at_write"
    assert observed[1][1] & ~0o600 == 0
    assert path.read_bytes() == b"synthetic\n"


def test_secret_key_existing_path_is_never_overwritten(tmp_path):
    import stat

    path = tmp_path / "synthetic.secret"
    path.write_bytes(b"existing synthetic data")
    before = path.stat()
    with pytest.raises(FileExistsError, match="^SYNTHETIC_SECRET_EXISTS$"):
        _write_key(path, b"replacement", secret=True)
    assert path.read_bytes() == b"existing synthetic data"
    assert path.stat().st_ino == before.st_ino
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(before.st_mode)


def test_secret_symlink_is_not_followed(tmp_path):
    target, link = tmp_path / "target", tmp_path / "synthetic.secret"
    target.write_bytes(b"existing synthetic data")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("NOT_RUN: symbolic links unavailable")
    with pytest.raises(FileExistsError, match="^SYNTHETIC_SECRET_EXISTS$"):
        _write_key(link, b"replacement", secret=True)
    assert target.read_bytes() == b"existing synthetic data"


def test_secret_key_handles_short_writes_and_closes_descriptor(tmp_path, monkeypatch):
    import os

    original_write = os.write
    descriptors = []

    def short_write(fd, content):
        descriptors.append(fd)
        return original_write(fd, content[:2])

    path = tmp_path / "synthetic.secret"
    with monkeypatch.context() as patch:
        patch.setattr(os, "write", short_write)
        _write_key(path, b"synthetic", secret=True)
    assert len(descriptors) > 1 and len(set(descriptors)) == 1
    assert path.read_bytes() == b"synthetic\n"
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_secret_write_failure_keeps_private_partial_file_and_sanitizes(tmp_path, monkeypatch):
    import os
    import stat

    original_write = os.write
    descriptors = []

    def fail_after_short_write(fd, content):
        descriptors.append(fd)
        if len(descriptors) == 1:
            return original_write(fd, content[:2])
        raise RuntimeError(PRIVATE_DETAIL)

    path = tmp_path / "synthetic.secret"
    with monkeypatch.context() as patch:
        patch.setattr(os, "write", fail_after_short_write)
        with pytest.raises(OSError, match="^SYNTHETIC_SECRET_WRITE_FAILED$") as error:
            _write_key(path, b"synthetic", secret=True)
    assert PRIVATE_DETAIL not in str(error.value)
    assert path.read_bytes() == b"sy"
    assert stat.S_IMODE(path.stat().st_mode) & ~0o600 == 0
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.parametrize("written", [0, -1, True, 999])
def test_secret_invalid_write_count_is_a_fixed_failure(tmp_path, monkeypatch, written):
    import os

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", lambda _fd, _data: written)
        with pytest.raises(OSError, match="^SYNTHETIC_SECRET_WRITE_FAILED$"):
            _write_key(tmp_path / "synthetic.secret", b"x", secret=True)


def test_secret_creation_exception_is_sanitized(tmp_path, monkeypatch):
    import os

    def fail_open(*_args, **_kwargs):
        raise RuntimeError(PRIVATE_DETAIL)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", fail_open)
        with pytest.raises(OSError, match="^SYNTHETIC_SECRET_CREATE_FAILED$") as error:
            _write_key(tmp_path / "synthetic.secret", b"x", secret=True)
    assert PRIVATE_DETAIL not in str(error.value)


def test_secret_key_rejects_non_bytes_without_echo(tmp_path):
    with pytest.raises(TypeError, match="^SYNTHETIC_SECRET_INVALID$") as error:
        _write_key(tmp_path / "synthetic.secret", PRIVATE_DETAIL, secret=True)
    assert PRIVATE_DETAIL not in str(error.value)


def test_public_key_behavior_is_unchanged(tmp_path):
    import stat

    path = tmp_path / "synthetic.public"
    path.write_bytes(b"old")
    assert _write_key(path, b"new", secret=False) == str(path)
    assert path.read_bytes() == b"new\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
