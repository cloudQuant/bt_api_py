"""Offline contracts for the scripts/analysis compatibility wrappers."""

from __future__ import annotations

import importlib
import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_STEMS = (
    "analyze_code_lines",
    "analyze_code_quality",
    "analyze_coverage",
    "analyze_slow_tests",
    "check_code_quality",
    "check_core_isolation",
    "extract_capabilities",
    "extract_capabilities_v2",
)


def _wrapper_path(stem: str) -> Path:
    return ROOT / "scripts" / "analysis" / f"{stem}.py"


def _load_module(script_path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    return module


@pytest.mark.parametrize("stem", SCRIPT_STEMS)
def test_import_wrapper_lazily_forwards_canonical_contract(
    stem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    canonical_name = f"scripts.{stem}"
    canonical = ModuleType(canonical_name)
    marker = object()
    exported_names = ["contract_marker", "main"]
    canonical.__dict__.update(
        {
            "contract_marker": marker,
            "main": lambda: pytest.fail("importing a wrapper must not call main"),
            "__all__": exported_names,
        }
    )
    imported_names: list[str] = []
    original_import_module = importlib.import_module

    def import_fake_canonical(name: str, package: str | None = None) -> ModuleType:
        if name == canonical_name:
            imported_names.append(name)
            return canonical
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", import_fake_canonical)
    wrapper = _load_module(_wrapper_path(stem), f"isolated_analysis_wrapper_{stem}")

    assert imported_names == []
    assert wrapper.__all__ is exported_names
    assert imported_names == [canonical_name]
    assert wrapper.contract_marker is marker
    assert "contract_marker" in dir(wrapper)
    assert imported_names == [canonical_name]

    for attribute in (
        "local_new_attribute",
        "_wrapper_private_attribute",
        "__wrapper_dunder_attribute__",
    ):
        setattr(wrapper, attribute, marker)
        assert wrapper.__dict__[attribute] is marker
        assert not hasattr(canonical, attribute)
        delattr(wrapper, attribute)
        assert attribute not in wrapper.__dict__


@pytest.mark.parametrize("stem", SCRIPT_STEMS)
def test_cli_wrapper_dispatches_canonical_path_and_preserves_argv_and_exit(
    stem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper_path = _wrapper_path(stem)
    arguments = ["--offline-argument", "value with spaces"]
    canonical_path = ROOT / "scripts" / f"{stem}.py"
    dispatch: dict[str, object] = {}
    initial_argv = [str(wrapper_path), *arguments]

    def fake_run_path(path: str, *, run_name: str) -> None:
        dispatch["path"] = path
        dispatch["run_name"] = run_name
        dispatch["argv0_before"] = sys.argv[0]
        sys.argv[0] = path
        try:
            dispatch["argv_tail"] = tuple(sys.argv[1:])
            dispatch["canonical_file"] = path
            raise SystemExit(23)
        finally:
            sys.argv[0] = initial_argv[0]

    monkeypatch.setattr(runpy, "run_path", fake_run_path)
    monkeypatch.setattr(sys, "argv", initial_argv.copy())

    with pytest.raises(SystemExit) as exc_info:
        _load_module(wrapper_path, "__main__")

    assert exc_info.value.code == 23
    assert dispatch == {
        "path": str(canonical_path.resolve()),
        "run_name": "__main__",
        "argv0_before": str(wrapper_path),
        "argv_tail": tuple(arguments),
        "canonical_file": str(canonical_path.resolve()),
    }
    assert sys.argv == initial_argv


@pytest.mark.parametrize(
    "script_path",
    (
        ROOT / "scripts" / "analyze_code_lines.py",
        ROOT / "scripts" / "analysis" / "analyze_code_lines.py",
    ),
    ids=("canonical", "analysis-wrapper"),
)
def test_analyze_code_lines_preserves_path_filter_and_counts(
    script_path: Path, tmp_path: Path
) -> None:
    module = _load_module(script_path, f"analyze_code_lines_{script_path.parent.name}")
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n\n  \n", encoding="utf-8")

    assert module.count_lines(source) == (3, 1, 2)
    assert module.should_skip(Path("src/.private/sample.py")) is True
    assert module.should_skip(Path("src/public/sample.py")) is False


@pytest.mark.parametrize(
    "script_path",
    (
        ROOT / "scripts" / "check_core_isolation.py",
        ROOT / "scripts" / "analysis" / "check_core_isolation.py",
    ),
    ids=("canonical", "analysis-wrapper"),
)
@pytest.mark.parametrize(
    ("source_text", "expected_code", "expected_output"),
    (
        ("from bt_api_py import core\n", 0, "core-plugin-isolation OK\n"),
        (
            "from bt_api_okx.client import Client\n",
            1,
            "Core package must not import plugin packages:\n",
        ),
    ),
    ids=("allowed-import", "forbidden-import"),
)
def test_check_core_isolation_preserves_scoped_scan_behavior(
    script_path: Path,
    source_text: str,
    expected_code: int,
    expected_output: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_module(script_path, f"check_core_isolation_{script_path.parent.name}")
    source = tmp_path / "sample.py"
    source.write_text(source_text, encoding="utf-8")
    monkeypatch.setattr(module, "CORE_DIR", tmp_path)

    assert module.main() == expected_code
    captured = capsys.readouterr()
    assert captured.out.startswith(expected_output)
    if expected_code:
        assert f"{source}:1: from bt_api_okx" in captured.out


def test_check_core_isolation_wrapper_assignment_reaches_canonical_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wrapper = _load_module(
        _wrapper_path("check_core_isolation"), "isolated_core_isolation_assignment"
    )
    source = tmp_path / "module.py"
    source.write_text("from bt_api_okx.client import Client\n", encoding="utf-8")

    monkeypatch.setattr(wrapper, "CORE_DIR", tmp_path)

    assert wrapper.main.__globals__["CORE_DIR"] == tmp_path
    assert wrapper.main() == 1
    assert f"{source}:1: from bt_api_okx" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("stem", "attribute"),
    (("check_core_isolation", "CORE_DIR"), ("analyze_code_lines", "Path")),
    ids=("core-config", "public-path-import"),
)
def test_patch_object_restores_canonical_wrapper_attributes(stem: str, attribute: str) -> None:
    canonical = importlib.import_module(f"scripts.{stem}")
    wrapper = _load_module(_wrapper_path(stem), f"patch_object_analysis_wrapper_{stem}")
    original = getattr(canonical, attribute)
    replacement = object()

    assert getattr(wrapper, attribute) is original
    with patch.object(wrapper, attribute, replacement):
        assert getattr(wrapper, attribute) is replacement
        assert getattr(canonical, attribute) is replacement

    assert getattr(wrapper, attribute) is original
    assert getattr(canonical, attribute) is original
