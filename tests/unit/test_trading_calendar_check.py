"""Read-only checks against local synthetic files; never real account data."""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bt_api_py.trading_calendar_check import check_calendar_inputs

CONDA = "/Users/yunjinqi/opt/anaconda3/bin/conda"
VALID = """sources:
  - id: synthetic
    description: 合成公告 非真实来源
trading_days:
  - venue: SHFE
    day: '2026-11-12'
    status: open
    source_ids: [synthetic]
trading_hours: []
exceptions: []
""".encode()


@pytest.fixture
def files(tmp_path):
    base = tmp_path.resolve()
    root = base / "日历 数据"
    root.mkdir()
    config = base / "配置 文件.yaml"
    config.write_text(
        "calendars:\n  root: '日历 数据'\nsecret: synthetic-secret-token\n", encoding="utf-8"
    )
    supplement = root / "补充 数据.yaml"
    supplement.write_bytes(b"\xef\xbb\xbf" + VALID)
    return config, root, supplement


def check(files, **kwargs):
    config, root, supplement = files
    values = {"config_file": config, "calendar_root": root, "supplement_file": supplement}
    values.update(kwargs)
    return check_calendar_inputs(**values)


def test_valid_utf8_and_bom_report_counts_without_claims_or_sensitive_content(files):
    config, root, supplement = files
    current = root / "CURRENT.json"
    current.write_bytes(b"sentinel-not-a-calendar")
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    config_before = config.read_bytes()
    result = check(files)
    assert (result.status, result.code, result.exit_code) == ("CHECKED", "SUPPLEMENT_UNVERIFIED", 0)
    assert (
        result.config_status,
        result.root_kind,
        result.review_mode,
        result.review_mode_source,
    ) == (
        "CONFIGURED",
        "directory",
        "auto",
        "default",
    )
    assert (result.supplement_status, result.source_verification, result.venues) == (
        "CANDIDATE",
        "UNVERIFIED",
        ("SHFE",),
    )
    assert (
        result.source_count,
        result.trading_day_count,
        result.trading_hours_count,
        result.exception_count,
    ) == (1, 1, 0, 0)
    assert (
        result.directory_policy,
        result.input_stability,
        result.calendar_completeness,
        result.review,
        result.publication,
    ) == ("UNVERIFIED", "UNVERIFIED", "NOT_ASSESSED", "NOT_PERFORMED", "NOT_ATTEMPTED")
    encoded = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "synthetic-secret-token" not in encoded and "合成公告" not in encoded
    assert (
        str(config) not in encoded and str(root) not in encoded and str(supplement) not in encoded
    )
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    assert config.read_bytes() == config_before


def test_manual_mode_and_config_utf8_bom_are_reported(files):
    config, _, _ = files
    config.write_bytes(
        b"\xef\xbb\xbf" + "calendars:\n  root: '日历 数据'\n  review_mode: manual\n".encode()
    )
    result = check(files, gateway_enabled=True)
    assert (result.exit_code, result.review_mode, result.review_mode_source) == (
        0,
        "manual",
        "explicit",
    )


@pytest.mark.parametrize(
    "body,expected",
    [
        (b"sources: [", "SUPPLEMENT_INVALID_CONTENT"),
        (b"\xff\xfe", "SUPPLEMENT_INVALID_UTF8"),
        (b"", "SUPPLEMENT_INVALID_CONTENT"),
        (
            b"sources: []\nsources: []\ntrading_days: []\ntrading_hours: []\nexceptions: []\n",
            "SUPPLEMENT_INVALID_CONTENT",
        ),
    ],
)
def test_bad_supplement_is_not_source_verification(files, body, expected):
    files[2].write_bytes(body)
    result = check(files)
    assert (result.exit_code, result.code, result.publication) == (4, expected, "NOT_ATTEMPTED")
    assert result.source_verification == "NOT_ASSESSED"


def test_missing_supplement_is_explicit(files):
    files[2].unlink()
    result = check(files)
    assert (result.exit_code, result.code, result.supplement_status) == (
        3,
        "SUPPLEMENT_MISSING",
        "MISSING",
    )


def test_supplement_symlink_is_rejected(files):
    _, root, supplement = files
    real = root / "real.yaml"
    supplement.rename(real)
    supplement.symlink_to(real)
    result = check(files)
    assert (result.exit_code, result.code) == (4, "SUPPLEMENT_SYMLINK")


@pytest.mark.parametrize(
    "data,code",
    [
        (b"calendars: [", "CONFIG_YAML_INVALID"),
        (b"\xff", "CONFIG_ENCODING_INVALID"),
        (b"calendars: {}\ncalendars: {}\n", "CONFIG_DUPLICATE_KEY"),
        (b"[]", "CONFIG_ROOT_INVALID"),
        (b"calendars:\n  root: package_seed\n", "EXTERNAL_ROOT_REQUIRED"),
        (
            "calendars:\n  root: '日历 数据'\n  review_mode: invalid\n".encode(),
            "calendar_review_mode_invalid",
        ),
        (b"other: 1\n", "CALENDAR_ROOT_UNCONFIGURED"),
    ],
)
def test_configuration_errors_are_finite_and_do_not_check_supplement(files, data, code):
    files[0].write_bytes(data)
    result = check(files)
    assert (result.exit_code, result.code, result.supplement_status) == (2, code, "NOT_CHECKED")
    assert result.publication == "NOT_ATTEMPTED"


def test_missing_or_symlink_configuration_is_rejected(files):
    config, _, _ = files
    real = config.with_name("real.yaml")
    config.rename(real)
    assert check(files).code == "CONFIG_READ_FAILED"
    config.symlink_to(real)
    assert check(files).code == "CONFIG_READ_FAILED"


def test_explicit_root_mismatch_unavailable_and_bad_path(files):
    config, root, supplement = files
    other = root.parent / "other"
    other.mkdir()
    assert check(files, calendar_root=other).code == "CALENDAR_ROOT_MISMATCH"
    supplement.unlink()
    root.rmdir()
    assert check(files).code == "CALENDAR_ROOT_UNAVAILABLE"
    assert (
        check_calendar_inputs(
            config_file="relative.yaml", calendar_root=root, supplement_file=supplement
        ).code
        == "INVALID_ABSOLUTE_PATH"
    )
    assert (
        check_calendar_inputs(
            config_file=config, calendar_root=root, supplement_file=root / ".." / "input.yaml"
        ).code
        == "INVALID_ABSOLUTE_PATH"
    )


def test_explicit_supplement_is_required_and_not_inferred(files):
    config, root, _ = files
    with pytest.raises(TypeError):
        check_calendar_inputs(config_file=config, calendar_root=root)


def test_reader_failures_are_mapped_without_echoing_exception(files, monkeypatch):
    from bt_api_ctp import calendar_supplement_reader as reader

    def fail(path):
        raise RuntimeError("secret path and raw source")

    monkeypatch.setattr(reader, "read_supplement", fail)
    result = check(files)
    assert (result.exit_code, result.code) == (5, "SUPPLEMENT_READER_ERROR")
    assert "secret path" not in json.dumps(result.to_dict())


def test_unexpected_reader_venue_is_not_echoed(files, monkeypatch):
    from bt_api_ctp import calendar_supplement_reader as reader

    candidate = SimpleNamespace(
        verification="UNVERIFIED",
        unused_source_ids=(),
        content={
            "sources": [],
            "trading_days": [{"venue": "sensitive-value"}],
            "trading_hours": [],
            "exceptions": [],
        },
    )
    monkeypatch.setattr(
        reader,
        "read_supplement",
        lambda path: SimpleNamespace(
            status="CANDIDATE", verification="UNVERIFIED", candidate=candidate
        ),
    )
    result = check(files)
    assert (result.exit_code, result.code) == (5, "SUPPLEMENT_READER_ERROR")
    assert "sensitive-value" not in json.dumps(result.to_dict())


def test_cli_real_module_exit_codes_and_json_are_reproducible(files):
    config, root, supplement = files
    sdk_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(sdk_root), str(sdk_root / "bt_api/bt_api_ctp/src")))
    command = [
        CONDA,
        "run",
        "-n",
        "base",
        "python",
        "-m",
        "bt_api_py.trading_calendar_check",
        "--config",
        str(config),
        "--calendar-root",
        str(root),
        "--supplement",
        str(supplement),
    ]
    first = subprocess.run(
        command, cwd=sdk_root, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    second = subprocess.run(
        command, cwd=sdk_root, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    assert first.returncode == second.returncode == 0
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert json.loads(first.stdout)["source_verification"] == "UNVERIFIED"
    assert str(config) not in first.stdout and "synthetic-secret-token" not in first.stdout
    supplement.unlink()
    missing = subprocess.run(
        command, cwd=sdk_root, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    assert missing.returncode == 3 and json.loads(missing.stdout)["code"] == "SUPPLEMENT_MISSING"
    malformed = subprocess.run(
        command[:-2], cwd=sdk_root, env=env, capture_output=True, text=True, timeout=30, check=False
    )
    assert malformed.returncode == 2 and json.loads(malformed.stdout)["code"] == "INVALID_ARGUMENTS"


def test_cli_missing_ctp_or_config_yaml_dependency_is_explicit(files, monkeypatch, capsys):
    import builtins

    from bt_api_py.trading_calendar_check import main

    config, root, supplement = files
    argv = ["--config", str(config), "--calendar-root", str(root), "--supplement", str(supplement)]
    original_import = builtins.__import__

    def missing_ctp(name, *args, **kwargs):
        if name == "bt_api_ctp.calendar_supplement_reader":
            raise ModuleNotFoundError("simulated private package detail")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", missing_ctp)
        assert main(argv) == 5
        output = capsys.readouterr().out
        assert json.loads(output)["code"] == "CTP_READER_UNAVAILABLE"
        assert "private package detail" not in output

    def missing_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("simulated yaml package detail")
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", missing_yaml)
        assert main(argv) == 5
        output = capsys.readouterr().out
        assert json.loads(output)["code"] == "CONFIG_DEPENDENCY_UNAVAILABLE"
        assert "yaml package detail" not in output


def test_supplement_parser_dependency_unavailable_is_explicit(files, monkeypatch):
    from bt_api_ctp import calendar_supplement_reader as reader
    from bt_api_ctp.calendar_supplement import SupplementError

    def missing_parser(data):
        raise SupplementError("optional PyYAML dependency unavailable")

    monkeypatch.setattr(reader, "parse_supplement", missing_parser)
    result = check(files)
    assert (result.exit_code, result.code) == (5, "SUPPLEMENT_DEPENDENCY_UNAVAILABLE")
    assert result.supplement_status == "DEPENDENCY_UNAVAILABLE"
