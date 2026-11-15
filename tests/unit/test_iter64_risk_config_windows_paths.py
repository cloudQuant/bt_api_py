"""Windows path syntax checks; native integration runs only on Windows."""

import os
from pathlib import Path

import pytest

from bt_api_py.risk_service_config import (
    RiskServiceConfigError,
    _resolve_windows_path,
    resolve_path,
)

BASE = r"C:\Risk Config"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"state\risk.sqlite", r"C:\Risk Config\state\risk.sqlite"),
        (r"..\state with spaces", r"C:\state with spaces"),
        (r"D:\state\risk.sqlite", r"D:\state\risk.sqlite"),
        (r"\\server\share\policy.yaml", r"\\server\share\policy.yaml"),
    ],
)
def test_windows_path_lexical_resolution(raw, expected):
    assert _resolve_windows_path(raw, BASE) == expected


@pytest.mark.parametrize("raw", [r"C:state", r"D:state", "C:", r"\state", r"/state"])
def test_windows_ambiguous_drive_and_root_relative_paths_are_refused(raw):
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_INVALID$"):
        _resolve_windows_path(raw, BASE)


@pytest.mark.parametrize("raw", [r"%ITER64_UNSET%\state", r"$ITER64_UNSET\state", r"${ITER64_UNSET}\state"])
def test_windows_unset_environment_variables_are_refused(monkeypatch, raw):
    monkeypatch.delenv("ITER64_UNSET", raising=False)
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_UNRESOLVED$"):
        _resolve_windows_path(raw, BASE)


@pytest.mark.parametrize("raw", [r"%ITER64_ROOT%\state", r"$ITER64_ROOT\state", r"${ITER64_ROOT}\state"])
def test_windows_environment_variable_expansion(monkeypatch, raw):
    monkeypatch.setenv("ITER64_ROOT", r"D:\Risk Data")
    assert _resolve_windows_path(raw, BASE) == r"D:\Risk Data\state"


def test_windows_home_expansion_uses_userprofile(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Risk User")
    assert _resolve_windows_path(r"~\state", BASE) == r"C:\Users\Risk User\state"


def test_windows_relative_path_requires_absolute_config_directory():
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_INVALID$"):
        _resolve_windows_path(r"state", r"D:config")


@pytest.mark.skipif(os.name != "nt", reason="native Windows Path behavior requires Windows")
def test_native_windows_resolve_path(monkeypatch):
    monkeypatch.setenv("ITER64_ROOT", r"D:\Risk Data")
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Risk User")
    monkeypatch.delenv("ITER64_UNSET", raising=False)
    assert resolve_path(r"%ITER64_ROOT%\state", Path(BASE)) == Path(r"D:\Risk Data\state")
    assert resolve_path(r"~\state", Path(BASE)) == Path(r"C:\Users\Risk User\state")
    assert resolve_path(r"state", Path(BASE)) == Path(r"C:\Risk Config\state")
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_INVALID$"):
        resolve_path(r"D:state", Path(BASE))
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_UNRESOLVED$"):
        resolve_path(r"%ITER64_UNSET%\state", Path(BASE))


@pytest.mark.skipif(os.name == "nt", reason="POSIX regression")
def test_posix_percent_delimiters_remain_literal():
    assert resolve_path("%ITER64_UNSET%/state", Path("/risk")) == Path("/risk/%ITER64_UNSET%/state")
