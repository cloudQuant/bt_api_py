from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from bt_api_risk_observer import service as observer


def _config(tmp_path: Path, storage_directory: Path, *, suffix: str = ".json") -> Path:
    path = tmp_path / f"risk-service{suffix}"
    document = {
        "schema": "bt_api_risk.service.v1",
        "storage": {"directory": str(storage_directory)},
        # These deliberately incomplete paths must never be loaded by the observer.
        "service": {
            "secret_key_file": str(tmp_path / "must-not-read.key"),
            "public_key_file": str(tmp_path / "must-not-read.pub"),
        },
        "account": {"account_scope": "ignored-by-observer"},
    }
    if suffix == ".json":
        path.write_text(json.dumps(document), encoding="utf-8")
    else:
        path.write_text(
            "schema: bt_api_risk.service.v1\n"
            "storage:\n"
            f"  directory: {json.dumps(str(storage_directory))}\n"
            "service:\n"
            f"  secret_key_file: {json.dumps(str(tmp_path / 'must-not-read.key'))}\n",
            encoding="utf-8",
        )
    return path


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.parent.chmod(0o700)
    else:
        try:
            observer._windows_set_owner_only_acl(path.parent)
        except observer.RiskObservationError as error:
            pytest.skip(f"Windows ACL primitive unavailable: {error.code}")
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE pending_actions (state TEXT, detail TEXT)")
        connection.executemany(
            "INSERT INTO pending_actions VALUES (?, ?)",
            (("PENDING", "keep"), ("UNKNOWN", "also keep")),
        )
        connection.commit()
    finally:
        connection.close()
    if os.name == "posix":
        path.chmod(0o600)
    else:
        try:
            observer._windows_set_owner_only_acl(path)
        except observer.RiskObservationError as error:
            pytest.skip(f"Windows ACL primitive unavailable: {error.code}")


def _marker(path: Path) -> Path:
    with path.open("xb") as marker:
        marker.write(observer._STOP_MARKER_INITIAL)
    if os.name == "posix":
        path.chmod(0o600)
    else:
        try:
            observer._windows_set_owner_only_acl(path)
        except observer.RiskObservationError as error:
            pytest.skip(f"Windows ACL primitive unavailable: {error.code}")
    return path


def _rows(path: Path) -> tuple[tuple[str, str], ...]:
    connection = sqlite3.connect(path)
    try:
        return tuple(connection.execute("SELECT state, detail FROM pending_actions ORDER BY state"))
    finally:
        connection.close()


def test_resolves_only_public_storage_directory_without_reading_key_files(tmp_path):
    storage = tmp_path / "storage"
    config_path = _config(tmp_path, storage)

    assert observer.risk_database_path(config_path) == storage / "risk.sqlite"
    assert not storage.exists()
    assert not (tmp_path / "must-not-read.key").exists()
    assert not (tmp_path / "must-not-read.pub").exists()


def test_strict_finite_yaml_storage_subset_is_supported(tmp_path):
    storage = tmp_path / "relative-storage"
    config = tmp_path / "risk-service.yaml"
    config.write_text(
        "schema: bt_api_risk.service.v1\nstorage:\n  directory: relative-storage\n",
        encoding="utf-8",
    )
    assert observer.risk_database_path(config) == storage / "risk.sqlite"


@pytest.mark.parametrize(
    "document",
    [
        '{"schema":"bt_api_risk.service.v1","storage":{"directory":"x","directory":"y"}}',
        "schema: bt_api_risk.service.v1\nstorage:\n  directory: x\n  directory: y\n",
        "schema: bt_api_risk.service.v1\nstorage:\n  directory: x\nother: 1.5\n",
    ],
)
def test_config_duplicate_keys_and_floats_fail_closed(tmp_path, document):
    path = tmp_path / "bad-config"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(observer.RiskObservationError):
        observer.risk_database_path(path)


def test_created_owner_stop_marker_is_private_and_empty_is_stop_signal(tmp_path):
    marker = observer.create_owner_stop_marker(tmp_path)

    assert marker.name.startswith("risk-") and marker.name.endswith(".stop")
    assert len(marker.stem.removeprefix("risk-")) == 32
    assert observer._read_protected_marker(marker)[0] == observer._STOP_MARKER_INITIAL


def test_observation_is_read_only_and_preserves_pending_unknown_rows(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    _database(database)
    config_path = _config(tmp_path, storage)
    stop_file = _marker(tmp_path / "risk-test.stop")
    before = _rows(database)
    statements: list[str] = []
    connect_calls: list[tuple[str, bool]] = []
    real_connect = sqlite3.connect

    def connect(database_uri, *args, **kwargs):
        connect_calls.append((database_uri, kwargs.get("uri", False)))
        connection = real_connect(database_uri, *args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(observer.sqlite3, "connect", connect)
    host = observer.RiskObservationServiceHost(config_path, stop_file=stop_file)
    result = host.observe_once()

    assert connect_calls and connect_calls[0][1] is True
    assert connect_calls[0][0].endswith("?mode=ro")
    assert any(statement == "PRAGMA query_only = ON" for statement in statements)
    assert any("sqlite_schema" in statement for statement in statements)
    assert not any("pending_actions" in statement for statement in statements)
    assert not any(
        statement.lstrip()
        .upper()
        .startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"))
        for statement in statements
    )
    assert _rows(database) == before
    assert result.mode == "OBSERVE_ONLY"
    assert result.storage_health == "SQLITE_SCHEMA_READABLE"
    assert result.risk_readiness == "NOT_ASSESSED"
    assert result.account_readiness == "NOT_ASSESSED"
    assert result.market_readiness == "NOT_ASSESSED"
    assert result.risk_schema_validation == "NOT_VALIDATED"


def test_missing_database_fails_without_creating_it(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )

    with pytest.raises(observer.RiskObservationError):
        host.observe_once()
    assert not (storage / "risk.sqlite").exists()


def test_owner_stop_marker_empty_requests_stop_and_missing_fails_closed(tmp_path):
    storage = tmp_path / "storage"
    _database(storage / "risk.sqlite")
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )

    with host.stop_file.open("wb"):
        pass
    assert host.stop_requested() is True

    host.stop_file.unlink()
    with pytest.raises(observer.RiskObservationError):
        host.stop_requested()


def test_owner_stop_marker_replacement_fails_on_identity_mismatch(tmp_path):
    marker = _marker(tmp_path / "risk-test.stop")
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, tmp_path / "storage"), stop_file=marker
    )
    replacement = _marker(tmp_path / "replacement.stop")
    marker.unlink()
    replacement.replace(marker)

    with pytest.raises(observer.RiskObservationError) as error:
        host.stop_requested()
    assert error.value.code == "RISK_OBSERVER_STOP_MARKER_IDENTITY_CHANGED"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink check")
def test_owner_stop_marker_symlink_fails_closed(tmp_path):
    target = _marker(tmp_path / "risk-target.stop")
    link = tmp_path / "risk-link.stop"
    link.symlink_to(target)

    with pytest.raises(observer.RiskObservationError):
        observer.RiskObservationServiceHost(_config(tmp_path, tmp_path / "storage"), stop_file=link)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode-bit check")
def test_owner_stop_marker_with_broad_permissions_fails_closed(tmp_path):
    marker = _marker(tmp_path / "risk-test.stop")
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, tmp_path / "storage"), stop_file=marker
    )
    marker.chmod(0o644)

    with pytest.raises(observer.RiskObservationError) as error:
        host.stop_requested()
    assert error.value.code == "RISK_OBSERVER_FILE_UNPROTECTED"


@pytest.mark.skipif(os.name != "posix", reason="POSIX database permission check")
def test_database_with_broad_permissions_fails_closed(tmp_path):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    _database(database)
    database.chmod(0o640)
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )

    with pytest.raises(observer.RiskObservationError) as error:
        host.observe_once()
    assert error.value.code == "RISK_OBSERVER_FILE_UNPROTECTED"


@pytest.mark.skipif(os.name != "posix", reason="POSIX database symlink check")
def test_database_symlink_fails_closed(tmp_path):
    storage = tmp_path / "storage"
    _database(storage / "target.sqlite")
    (storage / "risk.sqlite").symlink_to(storage / "target.sqlite")
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )

    with pytest.raises(observer.RiskObservationError):
        host.observe_once()


@pytest.mark.skipif(os.name != "posix", reason="POSIX storage directory permission check")
def test_storage_directory_with_broad_permissions_fails_before_sqlite_open(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    _database(database)
    storage.chmod(0o755)
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )
    monkeypatch.setattr(
        observer.sqlite3,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("SQLite must not open an unprotected directory"),
    )

    with pytest.raises(observer.RiskObservationError) as error:
        host.observe_once()
    assert error.value.code == "RISK_OBSERVER_STORAGE_UNPROTECTED"


@pytest.mark.skipif(os.name != "posix", reason="POSIX storage directory symlink check")
def test_storage_directory_symlink_fails_closed(tmp_path):
    storage = tmp_path / "storage"
    _database(storage / "risk.sqlite")
    alias = tmp_path / "storage-alias"
    alias.symlink_to(storage, target_is_directory=True)
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, alias), stop_file=_marker(tmp_path / "risk-test.stop")
    )

    with pytest.raises(observer.RiskObservationError):
        host.observe_once()


@pytest.mark.skipif(
    os.name != "posix" or not Path("/proc/self/fd").is_dir(),
    reason="POSIX proc descriptor URI needed for deterministic ABA reproduction",
)
def test_sqlite_uri_reads_the_pinned_inode_during_replace_restore_aba(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    replacement = storage / "replacement.sqlite"
    moved_original = storage / "original-held.sqlite"
    moved_replacement = storage / "replacement-held.sqlite"
    _database(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE pinned_inode_only (value TEXT)")
        connection.commit()
    finally:
        connection.close()
    _database(replacement)
    connection = sqlite3.connect(replacement)
    try:
        connection.execute("CREATE TABLE replacement_inode_only (value TEXT)")
        connection.commit()
    finally:
        connection.close()

    real_connect = sqlite3.connect
    observed_uris: list[str] = []

    def replace_then_restore(database_uri, *args, **kwargs):
        observed_uris.append(database_uri)
        os.replace(database, moved_original)
        os.replace(replacement, database)
        try:
            return real_connect(database_uri, *args, **kwargs)
        finally:
            os.replace(database, moved_replacement)
            os.replace(moved_original, database)

    monkeypatch.setattr(observer.sqlite3, "connect", replace_then_restore)
    _observation, signature = observer._observe_database(database)

    assert len(observed_uris) == 1
    assert "/proc/self/fd/" in observed_uris[0]
    names = {name for _kind, name in signature[3]}
    assert "pinned_inode_only" in names
    assert "replacement_inode_only" not in names
    assert database.exists()
    assert not moved_original.exists()
    assert not moved_replacement.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows no-delete handle proof")
def test_windows_no_delete_handles_pin_database_and_storage_directory(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    replacement = storage / "replacement.sqlite"
    moved_storage = tmp_path / "storage-moved.sqlite"
    _database(database)
    _database(replacement)
    real_connect = sqlite3.connect
    blocked = {"database": False, "directory": False}

    def attempt_swap_then_connect(database_uri, *args, **kwargs):
        try:
            os.replace(replacement, database)
        except OSError:
            blocked["database"] = True
        else:
            os.replace(database, replacement)
        try:
            os.replace(storage, moved_storage)
        except OSError:
            blocked["directory"] = True
        else:
            os.replace(moved_storage, storage)
        return real_connect(database_uri, *args, **kwargs)

    monkeypatch.setattr(observer.sqlite3, "connect", attempt_swap_then_connect)
    observer._observe_database(database)

    assert blocked == {"database": True, "directory": True}
    assert database.exists() and replacement.exists() and storage.is_dir()


def test_database_size_limit_fails_closed_before_sqlite_open(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    _database(database)
    monkeypatch.setattr(observer, "_DATABASE_LIMIT", database.stat().st_size - 1)
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )

    with pytest.raises(observer.RiskObservationError):
        host.observe_once()


def test_schema_metadata_character_byte_object_and_total_limits():
    valid = observer._bound_schema_metadata([("table", "pending_actions")])
    assert valid == (("table", "pending_actions"),)

    invalid_rows = [
        [("x" * (observer._SCHEMA_TYPE_CHAR_LIMIT + 1), "name")],
        [("😀" * (observer._SCHEMA_TYPE_BYTE_LIMIT // 4 + 1), "name")],
        [("table", "n" * (observer._SCHEMA_NAME_CHAR_LIMIT + 1))],
        [("table", "😀" * (observer._SCHEMA_NAME_BYTE_LIMIT // 4 + 1))],
        [("table", f"name-{index}") for index in range(observer._SCHEMA_OBJECT_LIMIT + 1)],
        [
            ("table", "😀" * (observer._SCHEMA_NAME_BYTE_LIMIT // 4))
            for _ in range(observer._SCHEMA_OBJECT_LIMIT)
        ],
    ]
    for rows in invalid_rows:
        with pytest.raises(observer.RiskObservationError) as error:
            observer._bound_schema_metadata(rows)
        assert error.value.code == "RISK_OBSERVER_SCHEMA_UNAVAILABLE"


def test_schema_change_between_polls_fails_closed(tmp_path):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    _database(database)
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )
    host.observe_once()
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE later_change (value TEXT)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(observer.RiskObservationError) as error:
        host.observe_once()
    assert error.value.code == "RISK_OBSERVER_SCHEMA_CHANGED"


def test_database_replacement_between_polls_fails_closed(tmp_path):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    _database(database)
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )
    host.observe_once()
    replacement = storage / "replacement.sqlite"
    _database(replacement)
    os.replace(replacement, database)

    with pytest.raises(observer.RiskObservationError) as error:
        host.observe_once()
    assert error.value.code == "RISK_OBSERVER_STORAGE_IDENTITY_CHANGED"


def test_run_reports_only_observer_readiness_and_polls_at_half_second(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    _database(storage / "risk.sqlite")
    host = observer.RiskObservationServiceHost(
        _config(tmp_path, storage), stop_file=_marker(tmp_path / "risk-test.stop")
    )
    waits: list[float] = []

    def request_stop(interval):
        waits.append(interval)
        with host.stop_file.open("wb"):
            pass

    monkeypatch.setattr(observer.time, "sleep", request_stop)
    output = io.StringIO()

    assert host.run(output=output) == 0
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert waits == [0.5]
    assert [event["state"] for event in events] == ["RISK_OBSERVER_READY", "RISK_OBSERVER_STOPPED"]
    assert all(event["observer"] == "RISK_STORAGE_OBSERVER" for event in events)
    assert all(event["mode"] == "OBSERVE_ONLY" for event in events)
    assert all(event["risk_readiness"] == "NOT_ASSESSED" for event in events)
    assert all(event["account_readiness"] == "NOT_ASSESSED" for event in events)
    assert all(event["market_readiness"] == "NOT_ASSESSED" for event in events)


def test_main_lifecycle_retains_marker_and_exits_normally_on_owner_stop(
    tmp_path, monkeypatch, capsys
):
    storage = tmp_path / "storage"
    database = storage / "risk.sqlite"
    _database(database)
    before_rows = _rows(database)
    config = _config(tmp_path, storage)
    created_markers = []
    original_create = observer.create_owner_stop_marker

    def create_marker(directory):
        marker = original_create(directory)
        created_markers.append(marker)
        return marker

    monkeypatch.setattr(observer, "create_owner_stop_marker", create_marker)
    waits = []
    lifecycle_output = io.StringIO()
    original_run = observer.RiskObservationServiceHost.run

    def capture_run(host, *, output=sys.stdout):
        return original_run(host, output=lifecycle_output)

    monkeypatch.setattr(observer.RiskObservationServiceHost, "run", capture_run)

    def owner_stop(interval):
        waits.append(interval)
        marker = created_markers[0]
        with marker.open("wb"):
            pass

    monkeypatch.setattr(observer.time, "sleep", owner_stop)

    assert observer.main(["--config", str(config)]) == 0
    captured = capsys.readouterr()
    marker = created_markers[0]
    events = [json.loads(line) for line in lifecycle_output.getvalue().splitlines()]

    assert waits == [0.5]
    assert captured.out.strip() == "RISK_OBSERVER_STOP_FILE " + marker.name
    assert marker.exists()
    assert observer._read_protected_marker(marker)[0] == b""
    assert [event["state"] for event in events] == [
        "RISK_OBSERVER_READY",
        "RISK_OBSERVER_STOPPED",
    ]
    assert all(event["mode"] == "OBSERVE_ONLY" for event in events)
    assert all(event["account_readiness"] == "NOT_ASSESSED" for event in events)
    assert _rows(database) == before_rows


def test_windows_acl_and_reparse_helpers_fail_closed_when_system_checks_fail(tmp_path, monkeypatch):
    marker = tmp_path / "marker.stop"
    marker.write_bytes(observer._STOP_MARKER_INITIAL)
    monkeypatch.setattr(
        observer,
        "_windows_acl_state",
        lambda _path: (_ for _ in ()).throw(observer.RiskObservationError("ACL_UNAVAILABLE")),
    )
    with pytest.raises(observer.RiskObservationError):
        observer._windows_private_file(marker)

    with pytest.raises(observer.RiskObservationError) as error:
        observer._validate_windows_stamp(
            (1, 2, observer._FILE_ATTRIBUTE_REPARSE_POINT, 1, 10, 0),
            directory=False,
            size_limit=100,
        )
    assert error.value.code == "RISK_OBSERVER_REPARSE_REFUSED"


@pytest.mark.parametrize(
    ("target_name", "acl_state"),
    [
        ("risk.sqlite", ("S-1-owner", "S-1-other", False)),
        ("storage", ("S-1-owner", "S-1-owner", True)),
    ],
)
def test_windows_database_permissions_fail_closed_for_owner_or_acl_mismatch(
    tmp_path, monkeypatch, target_name, acl_state
):
    monkeypatch.setattr(observer, "_windows_acl_state", lambda _path: acl_state)
    with pytest.raises(observer.RiskObservationError) as error:
        observer._windows_private_file(tmp_path / target_name)
    assert error.value.code == "RISK_OBSERVER_FILE_UNPROTECTED"


def test_module_entrypoint_import_audit_excludes_sdk_and_risk_packages(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "sitecustomize.py").write_text(
        "import sys\n"
        "def audit(event, args):\n"
        "    if event == 'import' and args:\n"
        "        name = args[0]\n"
        "        if name == 'bt_api_py' or name.startswith('bt_api_py.') or name == 'bt_api_risk' or name.startswith('bt_api_risk.'):\n"
        "            raise RuntimeError('forbidden package import')\n"
        "sys.addaudithook(audit)\n",
        encoding="utf-8",
    )
    repository = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((str(audit_dir), str(repository)))
    result = subprocess.run(
        [sys.executable, "-m", "bt_api_risk_observer", "--help"],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Observe existing Risk SQLite storage only" in result.stdout
    assert "forbidden package import" not in result.stderr
