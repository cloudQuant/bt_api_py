from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "measure_public_api_quality.py"
EXCLUDED_DIRECTORY_NAMES = (
    "testing",
    "tests",
    "examples",
    "build",
    "dist",
    "generated",
    "__pycache__",
)


def _write_module(source_root: Path, relative_path: str, source: str = "") -> None:
    module_path = source_root / relative_path
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(source, encoding="utf-8")


def _run_report(source_root: Path, output_format: str = "json") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            str(source_root),
            "--format",
            output_format,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_report_scores_public_sync_async_methods_properties_and_overloads(tmp_path: Path) -> None:
    source_root = tmp_path / "bt_api_py"
    _write_module(source_root, "__init__.py", '"""Package docstring is not a callable."""\n')
    _write_module(
        source_root,
        "api.py",
        '''from typing import overload


def documented(required: int, optional) -> str:
    """A documented public function."""
    return str(required)


async def async_function(payload: str):
    pass


@overload
def overloaded(value: int) -> int: ...


@overload
def overloaded(value: str) -> str: ...


def overloaded(value):
    """The implementation is counted once; overloads are not counted."""
    return value


def _private_function(secret):
    """Private functions are excluded."""
    return secret


class PublicApi:
    def method(self, value: int, missing):
        """A public instance method."""

    @classmethod
    def class_method(cls, query):
        pass

    async def async_method(self, payload: str):
        pass

    @property
    def label(self) -> str:
        """A property is included, but self and return annotations are not parameters."""
        return "label"

    def _private_method(self, secret):
        """Private methods are excluded."""
        return secret

    @staticmethod
    def static_method(self: int):
        pass

    def keyword_only_method(self, *, cls: int):
        pass

    def repeated_name_method(self, /, cls: int):
        pass

    def __repr__(self):
        """Dunder methods are excluded."""
        return "PublicApi()"


class _PrivateApi:
    def hidden(self, value):
        """Methods of private classes are excluded."""
        return value
''',
    )
    _write_module(source_root, "_private.py", "def hidden(value: int):\n    pass\n")

    result = _run_report(source_root)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == 1
    assert report["metrics"]["docstrings"] == {
        "numerator": 4,
        "denominator": 10,
        "percentage": 40.0,
    }
    assert report["metrics"]["parameter_annotations"] == {
        "numerator": 7,
        "denominator": 11,
        "percentage": 63.64,
    }
    api_file = next(item for item in report["files"] if item["path"] == "api.py")
    assert api_file["public_callables"] == 10
    assert "_private.py" not in {item["path"] for item in report["files"]}
    assert api_file["docstrings"] == report["metrics"]["docstrings"]


def test_report_excludes_private_paths_and_fixed_directories(tmp_path: Path) -> None:
    source_root = tmp_path / "bt_api_py"
    _write_module(source_root, "__init__.py")
    _write_module(source_root, "public.py", "def visible():\n    pass\n")
    _write_module(source_root, "package/__init__.py")
    _write_module(source_root, "_private_package/__init__.py")
    _write_module(source_root, "package/_private_module.py")
    _write_module(source_root, "package/_helpers/nested.py")
    for directory_name in EXCLUDED_DIRECTORY_NAMES:
        _write_module(source_root, f"package/{directory_name}/hidden.py")

    result = _run_report(source_root)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    included_paths = {item["path"] for item in report["files"]}
    assert included_paths == {"__init__.py", "public.py", "package/__init__.py"}

    excluded_paths = {item["path"] for item in report["excluded_files"]}
    assert excluded_paths == {
        "_private_package/__init__.py",
        "package/_private_module.py",
        "package/_helpers/nested.py",
        *(f"package/{directory_name}/hidden.py" for directory_name in EXCLUDED_DIRECTORY_NAMES),
    }
    assert report["exclusion_rules"]["directory_names"] == list(EXCLUDED_DIRECTORY_NAMES)
    assert any(
        "parent directory" in rule["description"] for rule in report["exclusion_rules"]["rules"]
    )
    assert any("__init__.py" in rule["description"] for rule in report["exclusion_rules"]["rules"])


def test_empty_parameter_denominator_is_reported_as_na(tmp_path: Path) -> None:
    source_root = tmp_path / "bt_api_py"
    _write_module(
        source_root,
        "api.py",
        '''def no_parameters():
    """This callable has no parameters."""


class Api:
    @property
    def value(self):
        return 1
''',
    )

    json_result = _run_report(source_root)
    text_result = _run_report(source_root, output_format="text")

    assert json_result.returncode == 0, json_result.stderr
    report = json.loads(json_result.stdout)
    assert report["metrics"]["parameter_annotations"] == {
        "numerator": 0,
        "denominator": 0,
        "percentage": None,
    }
    assert text_result.returncode == 0, text_result.stderr
    assert "Parameter annotations: 0/0 (N/A)" in text_result.stdout
    assert "Excluded files:" in text_result.stdout
    assert "Exclusion rules:" in text_result.stdout


def test_fixed_directory_rules_remain_visible_when_no_paths_match(tmp_path: Path) -> None:
    source_root = tmp_path / "bt_api_py"
    _write_module(source_root, "api.py", "def visible():\n    pass\n")

    result = _run_report(source_root)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["excluded_files"] == []
    assert report["exclusion_rules"]["directory_names"] == list(EXCLUDED_DIRECTORY_NAMES)


def test_external_python_symlink_is_reported_without_reading_target(tmp_path: Path) -> None:
    source_root = tmp_path / "bt_api_py"
    _write_module(source_root, "public.py", "def visible():\n    pass\n")
    external_module = tmp_path / "outside.py"
    external_module.write_text("invalid python !", encoding="utf-8")
    symlink_path = source_root / "external.py"
    try:
        symlink_path.symlink_to(external_module)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    result = _run_report(source_root)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert "external.py" not in {item["path"] for item in report["files"]}
    assert {"path": "external.py", "reasons": ["external_symlink"]} in report["excluded_files"]
