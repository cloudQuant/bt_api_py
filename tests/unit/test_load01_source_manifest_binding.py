from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest


HELPER_MODULE = "tests.unit._load01_resource_diagnostics"
HELPER_RELATIVE = "tests/unit/_load01_resource_diagnostics.py"
PARENT_COMMIT = "load01-test-parent"
PACKAGE_GITLINKS = {
    "bt_api/bt_api_base": "base-commit",
    "bt_api/bt_api_execution": "execution-commit",
    "bt_api/bt_api_gateway": "gateway-commit",
    "bt_api/bt_api_monitor": "monitor-commit",
    "bt_api/bt_api_risk": "risk-commit",
    "bt_api/bt_api_transport_zmq": "transport-commit",
}


def _module_relative_path(name: str) -> str:
    parts = name.split(".")
    if parts[0] == "bt_api_py":
        return Path("bt_api_py", *parts[1:]).with_suffix(".py").as_posix()
    if parts[:2] == ["tests", "unit"]:
        return Path("tests", "unit", *parts[2:]).with_suffix(".py").as_posix()
    package = parts[0]
    return Path(
        "bt_api",
        package,
        "src",
        package,
        *parts[1:],
    ).with_suffix(".py").as_posix()


def _load_manifest_functions(source_root: Path):
    runner_path = Path(__file__).with_name("test_load01_hundred_strategy_30min.py")
    tree = ast.parse(runner_path.read_text(encoding="utf-8"), filename=str(runner_path))
    wanted = {
        "_check_fixed_source_manifest",
        "_write_source_run_manifest",
    }
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    assert {node.name for node in definitions} == wanted
    namespace: dict[str, Any] = {
        "__file__": str(source_root / "tests/unit/test_load01_hundred_strategy_30min.py"),
        "Path": Path,
        "Any": Any,
        "Mapping": Mapping,
        "os": os,
        "json": json,
        "_FIXED_PARENT": PARENT_COMMIT,
        "_FIXED_PACKAGE_GITLINKS": dict(PACKAGE_GITLINKS),
        "_fixed_source_root": lambda: source_root,
        "_sha256": lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "_verify_fixed_archives": lambda *_args, **_kwargs: {"archive_count": 7},
    }
    module = ast.Module(body=definitions, type_ignores=[])
    exec(compile(module, str(runner_path), "exec"), namespace)
    return namespace, tree


def _make_fixed_source_tree(source_root: Path, module_names: tuple[str, ...]) -> dict[str, Path]:
    module_paths: dict[str, Path] = {}
    for name in module_names:
        relative = _module_relative_path(name)
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"source:{name}\n".encode("utf-8"))
        module_paths[name] = path
    for relative in (
        "tests/unit/test_service_gateway_hundred_strategy_integration.py",
        "tests/unit/test_load01_hundred_strategy_30min.py",
        HELPER_RELATIVE,
    ):
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(f"source:{relative}\n".encode("utf-8"))
    return module_paths


def _write_export_pins(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "parent_commit": PARENT_COMMIT,
                "parent_archive_sha256": "a" * 64,
                "package_gitlinks": [
                    {"path": package, "commit": commit}
                    for package, commit in PACKAGE_GITLINKS.items()
                ],
            }
        ),
        encoding="utf-8",
    )


def test_load01_runtime_manifest_binds_resource_helper_and_rejects_bad_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "source-fixed"
    source_root.mkdir()
    namespace, tree = _load_manifest_functions(source_root)
    checker_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_check_fixed_source_manifest"
    )
    module_assignment = next(
        node
        for node in ast.walk(checker_node)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "module_names"
            for target in node.targets
        )
    )
    module_names = ast.literal_eval(module_assignment.value)
    module_paths = _make_fixed_source_tree(source_root, module_names)

    pins_path = tmp_path / "source-export-pins.json"
    _write_export_pins(pins_path)
    monkeypatch.setenv("BT_API_LOAD01_EXPORT_PINS", str(pins_path))
    manifest_work = tmp_path / "manifest-work"
    manifest_work.mkdir()
    runner_relative = "tests/unit/test_load01_hundred_strategy_30min.py"
    runner_sha = hashlib.sha256(
        (source_root / runner_relative).read_bytes()
    ).hexdigest()
    manifest_path = namespace["_write_source_run_manifest"](
        source_root,
        manifest_work,
        allowed_overlays={runner_relative: runner_sha},
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    helper_sha = hashlib.sha256(
        (source_root / HELPER_RELATIVE).read_bytes()
    ).hexdigest()
    assert manifest["files"].get(HELPER_RELATIVE) == helper_sha

    module_paths[HELPER_MODULE] = source_root / HELPER_RELATIVE
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: SimpleNamespace(__file__=str(module_paths[name])),
    )
    checked = namespace["_check_fixed_source_manifest"](manifest_path)
    assert checked[HELPER_MODULE] == {"path": HELPER_RELATIVE, "sha256": helper_sha}

    manifest["files"].pop(HELPER_RELATIVE)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AssertionError, match="fixed source digest mismatch"):
        namespace["_check_fixed_source_manifest"](manifest_path)

    manifest["files"][HELPER_RELATIVE] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AssertionError, match="fixed source digest mismatch"):
        namespace["_check_fixed_source_manifest"](manifest_path)
