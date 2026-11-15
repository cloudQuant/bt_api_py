"""Initial explicit simulation runs with a maintained local supplement."""

import json

import pytest

from bt_api_py.trading_calendar_store import TradingCalendarStore
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_persistent import operation, run_args, settings
from tests.unit.test_trading_calendar_mock_supplement_update import (
    OPEN_MOCK,
    confirm_args,
    supplemental,
)
from tests.unit.test_trading_calendar_observation_run import env_without_network


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_default_input_used_by_initial_publication(tmp_path, mode):
    root, config = settings(tmp_path, mode)
    path = root / "supplement.yaml"
    payload = json.dumps(supplemental())
    path.write_text(payload)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == (0 if mode == "auto" else 10), (lines, err)
    captured = next(row for row in lines if row.get("candidate_state") == "COMMITTED")
    assert captured["supplement_input"] == "USER_SUPPLEMENT_CAPTURED"
    assert captured["supplement_status"] == "CAPTURED_UNVERIFIED"
    assert str(path) not in json.dumps(lines)
    assert path.read_text() == payload
    if mode == "manual":
        pending = lines[-1]
        assert pending["status"] == "PENDING_REVIEW"
        assert pending["proposal"]["difference"]["changes"]["exceptions"]
        rc, confirmed, err = command(env, *confirm_args(root, pending))
        assert rc == 0, (confirmed, err)
    current = TradingCalendarStore(root).read_current()
    assert current.content["exceptions"] == supplemental()["exceptions"]
    rc, shown, err = command(env, *operation(root, captured["run_id"], "mock-show"))
    assert rc == 0 and shown[-1]["current"]["status"] == "VISIBLE_UNAUTHENTICATED", (shown, err)


def test_explicit_initial_path_and_unchanged_file(tmp_path):
    root, config = settings(tmp_path)
    path = root.parent / "maintained-input.yaml"
    payload = json.dumps(supplemental())
    path.write_text(payload)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config, "--supplement", str(path)))
    assert rc == 0, (lines, err)
    assert str(path) not in json.dumps(lines) and path.read_text() == payload
    assert (
        TradingCalendarStore(root).read_current().content["exceptions"]
        == supplemental()["exceptions"]
    )


def test_synthetic_initial_stops_if_default_appears_before_initialize(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(
        tmp_path,
        OPEN_MOCK
        + f"""
from bt_api_py import trading_calendar_mock_cli as cli
old_initial = cli.initial
def injected_initial(**kwargs):
    from pathlib import Path
    (Path(kwargs['root']) / 'supplement.yaml').write_text({json.dumps(json.dumps(supplemental()))})
    return old_initial(**kwargs)
cli.initial = injected_initial
""",
    )
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 24 and lines[-1]["reason"] == "INPUT_CHANGED", (lines, err)
    assert any(row.get("supplement_input") == "SYNTHETIC_MOCK_NOT_USER_YAML" for row in lines)
    assert not (root / "CURRENT.json").exists()
    assert not (root / "REVIEW_POLICY.json").exists()


def test_synthetic_initial_stops_if_default_appears_before_publish(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(
        tmp_path,
        OPEN_MOCK
        + f"""
from bt_api_py import trading_calendar_mock_cli as cli
old_act = cli._act
def injected_act(operator, ids, proposal, **kwargs):
    from pathlib import Path
    (Path(operator.root) / 'supplement.yaml').write_text({json.dumps(json.dumps(supplemental()))})
    return old_act(operator, ids, proposal, **kwargs)
cli._act = injected_act
""",
    )
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 24 and lines[-1]["reason"] == "INPUT_CHANGED", (lines, err)
    assert (root / "REVIEW_POLICY.json").exists()
    assert not (root / "CURRENT.json").exists()
    assert not list((root / "observation-runs").glob("mock-publication-*.json"))


@pytest.mark.parametrize("placement", ["outside", "inside"])
def test_explicit_input_overrides_unselected_invalid_root_default(tmp_path, placement):
    root, config = settings(tmp_path)
    default = root / "supplement.yaml"
    selected = (root.parent if placement == "outside" else root) / "selected.yaml"
    default.write_bytes(b"invalid unselected user file\x00")
    selected.write_text(json.dumps(supplemental()))
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config, "--supplement", str(selected)))
    assert rc == 0, (lines, err)
    assert default.read_bytes() == b"invalid unselected user file\x00"
    assert (
        TradingCalendarStore(root).read_current().content["exceptions"]
        == supplemental()["exceptions"]
    )
    assert str(selected) not in json.dumps(lines)


def test_nested_explicit_input_with_unselected_default(tmp_path):
    root, config = settings(tmp_path)
    default = root / "supplement.yaml"
    default.write_bytes(b"untouched invalid default\x00")
    directory = root / "nested" / "more"
    directory.mkdir(parents=True)
    selected = directory / "selected.yaml"
    selected.write_text(json.dumps(supplemental()))
    before = selected.read_bytes()
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config, "--supplement", str(selected)))
    assert rc == 0, (lines, err)
    assert default.read_bytes() == b"untouched invalid default\x00"
    assert selected.read_bytes() == before and str(selected) not in json.dumps(lines)
    assert (
        TradingCalendarStore(root).read_current().content["exceptions"]
        == supplemental()["exceptions"]
    )


@pytest.mark.parametrize(
    "unsafe", ["linked_dir", "linked_file", "extra_root", "extra_nested", "reserved", "missing"]
)
def test_nested_invalid_tree_is_rejected_before_marker(tmp_path, unsafe):
    root, config = settings(tmp_path)
    outside = root.parent / "outside"
    outside.mkdir()
    external = outside / "selected.yaml"
    external.write_text(json.dumps(supplemental()))
    if unsafe == "linked_dir":
        (root / "nested").symlink_to(outside, target_is_directory=True)
        selected = root / "nested" / "selected.yaml"
    else:
        top = root / ("versions" if unsafe == "reserved" else "nested")
        directory = top / "more"
        directory.mkdir(parents=True)
        selected = directory / "selected.yaml"
        if unsafe == "linked_file":
            selected.symlink_to(external)
        elif unsafe != "missing":
            selected.write_text(json.dumps(supplemental()))
        if unsafe == "extra_root":
            (root / "unrelated.txt").write_text("retain")
        elif unsafe == "extra_nested":
            (directory / "unrelated.txt").write_text("retain")
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config, "--supplement", str(selected)))
    assert rc == 24, (lines, err)
    assert lines[-1]["state"] == "NOT_COMMITTED"
    assert lines[-1]["reason"] in {
        "ROOT_INPUT_REJECTED",
        "SUPPLEMENT_MISSING",
        "INVALID_SUPPLEMENT_PATH",
    }
    assert not (root / "MOCK-SIMULATION_ONLY.json").exists()
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "reserved",
    [
        "POLICY_CHANGE_PENDING.json",
        "POLICY_AUDIT_INITIALIZED.json",
        "policy_changes",
        "association-review-candidates",
        "detail-collections",
        "hours-review-candidates",
        "hours-evidence-candidates",
        "holiday-evidence-candidates",
        "inbox",
        "akshare-observations",
        "source-evidence",
        ".CURRENT-abc.tmp",
        ".REVIEW_POLICY-abc.tmp",
        ".preview-abc.tmp",
        "preview-abc.json",
        "MOCK-config-abc.json",
    ],
)
def test_reserved_components_rejected_without_marker(tmp_path, monkeypatch, reserved, nested):
    from bt_api_py import trading_calendar_mock as mock
    from bt_api_py import trading_calendar_observation_run as runs

    root, config = settings(tmp_path)
    parent = root / "safe" if nested else root
    parent.mkdir(exist_ok=True)
    selected = parent / reserved
    selected.write_text("{}")
    monkeypatch.setattr(mock, "build", lambda year: ({}, {}))
    result = runs.run_once(
        root=str(root),
        config=str(config),
        supplement=str(selected),
        budget_seconds=20,
        enable_shfe=False,
        filesystem=runs.TRUSTED_LOCAL_POSIX,
        run_id="1" * 32,
        capture_id="2" * 32,
        parent_id="3" * 32,
        derived_id="4" * 32,
        mock_year=2026,
        emit=lambda _: None,
    )
    assert result["state"] == "NOT_COMMITTED" and result["reason"] == "INVALID_SUPPLEMENT_PATH"
    assert selected.read_text() == "{}"
    assert not (root / "MOCK-SIMULATION_ONLY.json").exists()
    assert not (root / "CURRENT.json").exists()


def test_regular_input_without_extension_is_supported(tmp_path):
    root, config = settings(tmp_path)
    selected = root / "calendar"
    selected.write_text(json.dumps(supplemental()))
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config, "--supplement", str(selected)))
    assert rc == 0, (lines, err)
    assert (
        TradingCalendarStore(root).read_current().content["exceptions"]
        == supplemental()["exceptions"]
    )


@pytest.mark.parametrize(
    "reserved",
    [
        "POLICY_CHANGE_PENDING.json",
        "POLICY_AUDIT_INITIALIZED.json",
        "source-evidence",
        ".CURRENT-example.tmp",
    ],
)
def test_reserved_intermediate_directory_is_rejected(tmp_path, monkeypatch, reserved):
    from bt_api_py import trading_calendar_mock as mock
    from bt_api_py import trading_calendar_observation_run as runs

    root, config = settings(tmp_path)
    directory = root / reserved
    directory.mkdir()
    selected = directory / "calendar"
    selected.write_text("{}")
    monkeypatch.setattr(mock, "build", lambda year: ({}, {}))
    result = runs.run_once(
        root=str(root),
        config=str(config),
        supplement=str(selected),
        budget_seconds=20,
        enable_shfe=False,
        filesystem=runs.TRUSTED_LOCAL_POSIX,
        run_id="1" * 32,
        capture_id="2" * 32,
        parent_id="3" * 32,
        derived_id="4" * 32,
        mock_year=2026,
        emit=lambda _: None,
    )
    assert result["state"] == "NOT_COMMITTED" and result["reason"] == "INVALID_SUPPLEMENT_PATH"
    assert selected.read_text() == "{}"
    assert not (root / "MOCK-SIMULATION_ONLY.json").exists()
    assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("unsafe", ["symlink", "extra", "lock", "declaration", "current"])
def test_explicit_input_still_rejects_unsafe_root_entries(tmp_path, unsafe):
    root, config = settings(tmp_path)
    selected = root.parent / "selected.yaml"
    selected.write_text(json.dumps(supplemental()))
    if unsafe == "symlink":
        (root / "supplement.yaml").symlink_to(selected)
    elif unsafe == "extra":
        (root / "unselected.yaml").write_text("unrelated")
    else:
        selected = (
            root
            / {
                "lock": ".lock",
                "declaration": "MOCK-SIMULATION_ONLY.json",
                "current": "CURRENT.json",
            }[unsafe]
        )
        selected.write_text(json.dumps(supplemental()))
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config, "--supplement", str(selected)))
    assert rc != 0, (lines, err)
    if unsafe == "current":
        assert selected.read_text() == json.dumps(supplemental())
    else:
        assert not (root / "CURRENT.json").exists()


@pytest.mark.parametrize("case", ["missing", "invalid", "conflict", "nonmock", "symlink"])
def test_invalid_initial_input_does_not_publish(tmp_path, case):
    root, config = settings(tmp_path)
    path = root / "supplement.yaml"
    extra = ()
    if case == "missing":
        extra = ("--supplement", str(root.parent / "absent.yaml"))
    elif case == "invalid":
        path.write_text("invalid: [yaml")
    elif case == "conflict":
        path.write_text(json.dumps(supplemental(conflict=True)))
    elif case == "nonmock":
        value = supplemental()
        value["sources"][0] = {"id": "external", "description": "external"}
        value["exceptions"][0]["source_ids"] = ["external"]
        path.write_text(json.dumps(value))
    else:
        other = root.parent / "source.yaml"
        other.write_text(json.dumps(supplemental()))
        path.symlink_to(other)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config, *extra))
    assert rc != 0, (lines, err)
    assert not (root / "CURRENT.json").exists()
    assert lines[-1]["supplement_input"] == "USER_SUPPLEMENT_REQUESTED"
    if case == "conflict":
        assert lines[-1]["reason"] == "SUPPLEMENT_CONFLICT"
    elif case == "missing":
        assert lines[-1]["reason"] == "SUPPLEMENT_MISSING"
    elif case == "symlink":
        assert lines[-1]["reason"] == "SUPPLEMENT_NOT_REGULAR"
    elif case == "invalid":
        assert lines[-1]["supplement_status"] == "INVALID_CONTENT"
    else:
        assert lines[-1]["reason"] == "SUPPLEMENT_NOT_MOCK"


def test_initial_input_changed_during_protected_read_fails(tmp_path):
    root, config = settings(tmp_path)
    path = root / "supplement.yaml"
    path.write_text(json.dumps(supplemental()))
    env = env_without_network(
        tmp_path,
        OPEN_MOCK
        + """
from bt_api_ctp import calendar_supplement_reader as reader
original_read = reader.read_supplement
def changed_during_read(path):
    result = original_read(path)
    from pathlib import Path
    with Path(path).open('a') as stream: stream.write(' ')
    return result
reader.read_supplement = changed_during_read
""",
    )
    rc, lines, err = command(env, *run_args(root, config))
    assert rc != 0 and lines[-1]["supplement_status"] == "CHANGED", (lines, err)
    assert not (root / "CURRENT.json").exists()


def test_initial_pending_changed_file_is_stale(tmp_path):
    root, config = settings(tmp_path, "manual")
    path = root / "supplement.yaml"
    path.write_text(json.dumps(supplemental()))
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 10, (lines, err)
    pending = lines[-1]
    path.write_text(json.dumps(supplemental(conflict=True)))
    rc, result, err = command(env, *confirm_args(root, pending))
    assert rc == 24 and result[-1]["reason"] == "REVIEW_STALE", (result, err)
    path.write_text(json.dumps(supplemental()))
    rc, result, err = command(env, *confirm_args(root, pending))
    assert rc == 24 and result[-1]["reason"] == "REVIEW_STALE", (result, err)
    assert not (root / "CURRENT.json").exists()


def unknown_final_capture_for(caller):
    return (
        OPEN_MOCK
        + f"""
from bt_api_py import trading_calendar_supplement_capture as capture
import sys
original_read_capture = capture.read_capture
def unknown_final(**kwargs):
    if sys._getframe(1).f_code.co_name == {caller!r}:
        return {{'status': 'UNKNOWN', 'capture_id': kwargs['capture_id']}}
    return original_read_capture(**kwargs)
capture.read_capture = unknown_final
"""
    )


def test_initial_final_capture_unknown_preserves_committed_derived(tmp_path):
    root, config = settings(tmp_path)
    (root / "supplement.yaml").write_text(json.dumps(supplemental()))
    env = env_without_network(tmp_path, unknown_final_capture_for("run_mock"))
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 25, (lines, err)
    result = lines[-1]
    assert result["reason"] == "CAPTURE_RECONCILE_FIRST"
    assert (
        result["candidate_state"]
        == result["derived_state"]
        == result["capture_state"]
        == "COMMITTED"
    )
    assert not (root / "CURRENT.json").exists()
    rc, shown, err = command(
        env,
        "show",
        "--calendar-root",
        str(root),
        "--run-id",
        result["run_id"],
        "--trusted-local-posix",
    )
    assert shown[-1]["derived_association"]["state"] == "VISIBLE_UNAUTHENTICATED", (shown, err)
    assert shown[-1]["derived_association"]["capture_id"] == result["capture_id"]


def test_update_final_capture_unknown_preserves_committed_derived(tmp_path):
    root, config = settings(tmp_path)
    env = env_without_network(tmp_path, OPEN_MOCK)
    rc, lines, err = command(env, *run_args(root, config))
    assert rc == 0, (lines, err)
    initial = next(row for row in lines if row.get("candidate_state") == "COMMITTED")
    before = (root / "CURRENT.json").read_bytes()
    (root / "supplement.yaml").write_text(json.dumps(supplemental()))
    folder = tmp_path / "unknown-capture"
    folder.mkdir()
    injected = env_without_network(folder, unknown_final_capture_for("_update_with_supplement"))
    rc, lines, err = command(injected, *operation(root, initial["run_id"], "mock-update"))
    assert rc == 25, (lines, err)
    result = lines[-1]
    assert result["reason"] == "CAPTURE_RECONCILE_FIRST"
    assert (
        result["candidate_state"]
        == result["derived_state"]
        == result["capture_state"]
        == "COMMITTED"
    )
    assert (root / "CURRENT.json").read_bytes() == before
    rc, shown, err = command(
        env,
        "show",
        "--calendar-root",
        str(root),
        "--run-id",
        result["run_id"],
        "--trusted-local-posix",
    )
    assert shown[-1]["derived_association"]["state"] == "VISIBLE_UNAUTHENTICATED", (shown, err)
    assert shown[-1]["derived_association"]["capture_id"] == result["capture_id"]
