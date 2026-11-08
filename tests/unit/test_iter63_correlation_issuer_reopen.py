"""Explicit existing issuer reopening without initializing a replacement store."""

from __future__ import annotations

import os
import select
import signal
import sqlite3
import threading

import pytest

from bt_api_py.order_correlation_issuer import (
    DurableOrderCorrelationIssuer,
    OrderCorrelationIssuerError,
)


def test_missing_store_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(OrderCorrelationIssuerError):
        DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id="a" * 64)
    assert not path.exists()


def test_empty_and_bad_schema_are_not_initialized(tmp_path):
    path = tmp_path / "empty.sqlite"
    path.touch()
    with pytest.raises(OrderCorrelationIssuerError):
        DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id="a" * 64)
    assert path.stat().st_size == 0
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(OrderCorrelationIssuerError):
        DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id="a" * 64)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("unrelated",)
        ]


def test_reopen_requires_exact_issuer_id_and_preserves_observation_scope(tmp_path):
    path = tmp_path / "issuer.sqlite"
    with DurableOrderCorrelationIssuer(path) as first:
        issuer_id = first.issuer_id
        order = first.issue_order_observation_id(
            "ctp", "paper", "account-a", "day=20261108;order=1"
        )
    with pytest.raises(OrderCorrelationIssuerError):
        DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id="a" * 64)
    with DurableOrderCorrelationIssuer.open_existing(
        path, expected_issuer_id=issuer_id
    ) as reopened:
        assert reopened.issuer_id == issuer_id
        assert (
            reopened.issue_order_observation_id("ctp", "paper", "account-a", "day=20261108;order=1")
            == order
        )
        assert (
            reopened.issue_order_observation_id("ctp", "paper", "account-b", "day=20261108;order=1")
            != order
        )
        assert (
            reopened.issue_order_observation_id("ctp", "paper", "account-a", "day=20261109;order=1")
            != order
        )
    with DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id=issuer_id) as another:
        assert (
            another.issue_order_observation_id("ctp", "paper", "account-a", "day=20261108;order=1")
            == order
        )
    with pytest.raises(OrderCorrelationIssuerError):
        _ = reopened.issuer_id
    with pytest.raises(AttributeError):
        another.issuer_id = "different"


def test_missing_extension_table_is_never_repaired(tmp_path):
    path = tmp_path / "issuer.sqlite"
    with DurableOrderCorrelationIssuer(path) as first:
        issuer_id = first.issuer_id
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE sdk_order_provider_references")
    with pytest.raises(OrderCorrelationIssuerError):
        DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id=issuer_id)
    with sqlite3.connect(path) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='sdk_order_provider_references'"
            ).fetchone()[0]
            == 0
        )


def test_missing_metadata_is_never_replaced_or_rekeyed(tmp_path, monkeypatch):
    path = tmp_path / "issuer.sqlite"
    with DurableOrderCorrelationIssuer(path) as first:
        issuer_id = first.issuer_id
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER sdk_order_correlation_issuer_meta_no_delete")
        db.execute("DELETE FROM sdk_order_correlation_issuer_meta")
        db.execute(
            "CREATE TRIGGER sdk_order_correlation_issuer_meta_no_delete "
            "BEFORE DELETE ON sdk_order_correlation_issuer_meta "
            "BEGIN SELECT RAISE(ABORT, 'order correlation issuer metadata is immutable'); END"
        )

    def never_generate(*_args):
        raise AssertionError("must not create a new issuer identity or secret")

    monkeypatch.setattr("bt_api_py.order_correlation_issuer.secrets.token_hex", never_generate)
    monkeypatch.setattr("bt_api_py.order_correlation_issuer.secrets.token_bytes", never_generate)
    with pytest.raises(OrderCorrelationIssuerError):
        DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id=issuer_id)
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM sdk_order_correlation_issuer_meta").fetchone()[0] == 0
        )


def test_expected_id_and_capacity_are_strict(tmp_path):
    path = tmp_path / "issuer.sqlite"
    with DurableOrderCorrelationIssuer(path, max_reservations=17) as issuer:
        issuer_id = issuer.issuer_id
    for wrong in (None, "", "a" * 63, "A" * 64, 7):
        with pytest.raises(OrderCorrelationIssuerError):
            DurableOrderCorrelationIssuer.open_existing(
                path, expected_issuer_id=wrong, max_reservations=17
            )
    with pytest.raises(OrderCorrelationIssuerError):
        DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id=issuer_id)
    with DurableOrderCorrelationIssuer.open_existing(
        path, expected_issuer_id=issuer_id, max_reservations=17
    ) as reopened:
        assert reopened.issuer_id == issuer_id


def test_reopen_escapes_sqlite_uri_path_characters(tmp_path):
    path = tmp_path / "issuer # question?.sqlite"
    with DurableOrderCorrelationIssuer(path) as issuer:
        expected = issuer.issuer_id
    with DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id=expected) as issuer:
        assert issuer.issuer_id == expected
    assert sorted(item.name for item in tmp_path.iterdir()) == [path.name]


@pytest.mark.parametrize(
    "failure", [sqlite3.OperationalError, OSError, RuntimeError, KeyboardInterrupt, SystemExit]
)
def test_close_failure_latches_business_and_retries_real_close(tmp_path, failure):
    issuer = DurableOrderCorrelationIssuer(tmp_path / "issuer.sqlite")
    original = issuer._connection

    class CloseOnceFails:
        attempts = 0

        def close(self):
            self.attempts += 1
            if self.attempts == 1:
                raise failure("synthetic close failure")
            original.close()

    connection = CloseOnceFails()
    issuer._connection = connection
    expected_error = (
        failure
        if issubclass(failure, (KeyboardInterrupt, SystemExit))
        else OrderCorrelationIssuerError
    )
    with pytest.raises(expected_error) as error:
        issuer.close()
    if expected_error is OrderCorrelationIssuerError:
        assert error.value.code == "order_correlation_store_close_failed"
    assert issuer._connection is connection
    with pytest.raises(OrderCorrelationIssuerError, match="order_correlation_issuer_unavailable"):
        _ = issuer.issuer_id
    with pytest.raises(OrderCorrelationIssuerError, match="order_correlation_issuer_unavailable"):
        issuer.issue_order_observation_id("ctp", "paper", "account-a", "order-1")
    issuer.close()
    assert connection.attempts == 2
    with pytest.raises(sqlite3.ProgrammingError):
        original.execute("SELECT 1")
    issuer.close()
    assert connection.attempts == 2


def test_close_failure_between_precheck_and_transaction_rejects_write(tmp_path):
    issuer = DurableOrderCorrelationIssuer(tmp_path / "issuer.sqlite")
    original_connection = issuer._connection
    passed_precheck = threading.Event()
    resume = threading.Event()
    original_precheck = issuer._ensure_open
    failures = []

    def paused_precheck():
        original_precheck()
        passed_precheck.set()
        assert resume.wait(4)

    class Connection:
        attempts = 0

        def __getattr__(self, name):
            return getattr(original_connection, name)

        def close(self):
            self.attempts += 1
            if self.attempts == 1:
                raise sqlite3.OperationalError("synthetic close failure")
            original_connection.close()

    connection = Connection()
    issuer._connection = connection
    issuer._ensure_open = paused_precheck

    def write():
        try:
            issuer.issue_order_correlation("ctp", "SA701")
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=write)
    thread.start()
    assert passed_precheck.wait(2)
    try:
        with pytest.raises(
            OrderCorrelationIssuerError, match="order_correlation_store_close_failed"
        ):
            issuer.close()
    finally:
        resume.set()
        thread.join(4)
    assert not thread.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], OrderCorrelationIssuerError)
    assert failures[0].code == "order_correlation_issuer_unavailable"
    assert (
        original_connection.execute(
            "SELECT COUNT(*) FROM sdk_order_correlation_reservations"
        ).fetchone()[0]
        == 0
    )
    issuer.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork unavailable")
@pytest.mark.parametrize("operation", ["issuer_id", "cleanup_close"])
def test_new_entrypoints_reject_fork_before_inherited_lock(tmp_path, operation):
    from bt_api_py.order_correlation_issuer import OrderCorrelationIssuerCleanupError

    issuer = DurableOrderCorrelationIssuer(tmp_path / "issuer.sqlite")
    cleanup_error = OrderCorrelationIssuerCleanupError(issuer)
    acquired = threading.Event()
    release = threading.Event()

    def hold():
        with issuer._lock:
            acquired.set()
            release.wait(4)

    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(2)
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        signal.alarm(2)
        os.close(read_fd)
        try:
            if operation == "issuer_id":
                _ = issuer.issuer_id
            else:
                cleanup_error.close()
        except OrderCorrelationIssuerError:
            os.write(write_fd, b"ok")
            os._exit(0)
        except BaseException:
            os._exit(1)
        os._exit(1)
    os.close(write_fd)
    try:
        ready, _, _ = select.select([read_fd], [], [], 3)
        assert ready and os.read(read_fd, 2) == b"ok"
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        os.close(read_fd)
        release.set()
        thread.join(4)
        issuer.close()


@pytest.mark.parametrize("initial_failure", ["mismatch", "interrupt"])
@pytest.mark.parametrize("entry", ["existing", "constructor"])
@pytest.mark.parametrize(
    "cleanup_failure",
    [sqlite3.OperationalError, OSError, RuntimeError, KeyboardInterrupt, SystemExit],
)
def test_initialization_cleanup_failure_retains_exact_connection(
    tmp_path, monkeypatch, initial_failure, cleanup_failure, entry
):
    import bt_api_py.order_correlation_issuer as issuer_module
    from bt_api_py.order_correlation_issuer import OrderCorrelationIssuerCleanupError

    path = tmp_path / "issuer.sqlite"
    with DurableOrderCorrelationIssuer(path) as existing:
        expected = existing.issuer_id
    original_connect = sqlite3.connect
    wrappers = []

    class CloseOnceFails:
        def __init__(self, raw):
            object.__setattr__(self, "raw", raw)
            object.__setattr__(self, "attempts", 0)

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def __setattr__(self, name, value):
            if name in {"raw", "attempts"}:
                object.__setattr__(self, name, value)
            else:
                setattr(self.raw, name, value)

        def close(self):
            self.attempts += 1
            if self.attempts == 1:
                raise cleanup_failure("private test material")
            self.raw.close()

    def connect(*args, **kwargs):
        wrapper = CloseOnceFails(original_connect(*args, **kwargs))
        wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(issuer_module.sqlite3, "connect", connect)
    if initial_failure == "interrupt":

        def interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt("private test material")

        monkeypatch.setattr(DurableOrderCorrelationIssuer, "_initialize_schema", interrupt)
    elif entry == "constructor":

        def fail(*_args, **_kwargs):
            raise OrderCorrelationIssuerError("synthetic_initialization_failure")

        monkeypatch.setattr(DurableOrderCorrelationIssuer, "_initialize_schema", fail)
    else:
        expected = "a" * 64 if expected != "a" * 64 else "b" * 64
    with pytest.raises(OrderCorrelationIssuerCleanupError) as error:
        if entry == "constructor":
            DurableOrderCorrelationIssuer(path)
        else:
            DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id=expected)
    assert error.value.code == "order_correlation_initialization_cleanup_failed"
    assert "private test material" not in str(error.value) + repr(error.value)
    retained = error.value._issuer
    assert retained._connection is wrappers[0] and not retained._closed
    with pytest.raises(OrderCorrelationIssuerError, match="order_correlation_issuer_unavailable"):
        _ = retained.issuer_id
    with pytest.raises(OrderCorrelationIssuerError, match="order_correlation_issuer_unavailable"):
        retained.issue_order_observation_id("ctp", "paper", "account-a", "order-1")
    assert wrappers[0].raw.execute("SELECT 1").fetchone()[0] == 1
    error.value.close()
    assert wrappers[0].attempts == 2
    with pytest.raises(sqlite3.ProgrammingError):
        wrappers[0].raw.execute("SELECT 1")
    error.value.close()
    assert wrappers[0].attempts == 2


@pytest.mark.parametrize("initial_failure", ["mismatch", "interrupt"])
@pytest.mark.parametrize("entry", ["existing", "constructor"])
def test_initialization_error_survives_successful_cleanup(
    tmp_path, monkeypatch, initial_failure, entry
):
    path = tmp_path / "issuer.sqlite"
    with DurableOrderCorrelationIssuer(path) as existing:
        expected = existing.issuer_id
    if initial_failure == "interrupt":

        def interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(DurableOrderCorrelationIssuer, "_initialize_schema", interrupt)
        error_type = KeyboardInterrupt
    elif entry == "constructor":

        def fail(*_args, **_kwargs):
            raise OrderCorrelationIssuerError("synthetic_initialization_failure")

        monkeypatch.setattr(DurableOrderCorrelationIssuer, "_initialize_schema", fail)
        error_type = OrderCorrelationIssuerError
    else:
        expected = "a" * 64 if expected != "a" * 64 else "b" * 64
        error_type = OrderCorrelationIssuerError
    with pytest.raises(error_type):
        if entry == "constructor":
            DurableOrderCorrelationIssuer(path)
        else:
            DurableOrderCorrelationIssuer.open_existing(path, expected_issuer_id=expected)
