"""Retirement contract for the superseded runtime_plugins gateway writer."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from setuptools import find_packages

import bt_api_py.runtime_plugins as runtime_plugins
from bt_api_py.runtime_plugins import RuntimePluginError

_RETIRED_CODE = "LEGACY_GATEWAY_RUNTIME_RETIRED"
_MIGRATION_TEXT = "ServiceExecutionSession with ServiceHost and service.order_entry"


def _assert_retired(operation) -> None:
    with pytest.raises(RuntimePluginError) as raised:
        operation()
    assert raised.value.code == _RETIRED_CODE
    assert _MIGRATION_TEXT in str(raised.value)


def test_runtime_plugins_import_does_not_load_the_retired_gateway_module():
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    source_roots = [
        repository,
        repository / "bt_api" / "bt_api_base" / "src",
    ]
    inherited_paths = environment.get("PYTHONPATH", "").split(os.pathsep)
    python_paths = []
    seen_paths = set()
    for path in [*(str(root) for root in source_roots), *inherited_paths]:
        if not path:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = repository / candidate
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized not in seen_paths:
            python_paths.append(str(candidate))
            seen_paths.add(normalized)

    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    environment["BT_API_PY_LIGHT_IMPORT"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "import bt_api_base",
            "import bt_api_py",
            "import bt_api_py.runtime_plugins as runtime_plugins",
            f"expected_root = Path({str(repository)!r}).resolve()",
            "assert Path(bt_api_base.__file__).resolve() == expected_root / 'bt_api' / 'bt_api_base' / 'src' / 'bt_api_base' / '__init__.py'",
            "assert Path(bt_api_py.__file__).resolve() == expected_root / 'bt_api_py' / '__init__.py'",
            "assert Path(runtime_plugins.__file__).resolve() == expected_root / 'bt_api_py' / 'runtime_plugins' / '__init__.py'",
            "assert 'bt_api_py.runtime_plugins.gateway_dispatch' not in sys.modules",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_package_gateway_factories_reject_before_capability_or_path_access(tmp_path):
    state_directory = tmp_path / "must-not-be-created"
    untouched_capabilities = object()

    _assert_retired(
        lambda: runtime_plugins.compose_gateway_managed_client(
            untouched_capabilities,
            state_directory=state_directory,
            provider="fixture",
            environment="production",
            account_ref="account",
            strategy_id="strategy",
            writer_id="writer",
            client=object(),
        )
    )
    _assert_retired(
        lambda: runtime_plugins.compose_gateway_execution_authority(
            untouched_capabilities,
            state_directory=state_directory,
            provider="fixture",
            environment="production",
            account_ref="account",
            strategy_id="strategy",
            writer_id="writer",
            policy_id="policy",
            max_increase_notional=100,
            max_increase_count=1,
            provider_dispatch=lambda _intent: None,
        )
    )

    assert not state_directory.exists()


def test_package_gateway_runtime_constructors_and_command_id_reject():
    for name in (
        "GatewayManagedDispatcher",
        "GatewayManagedExecutionRuntime",
        "GatewayExecutionAuthority",
    ):
        constructor = getattr(runtime_plugins, name)
        _assert_retired(lambda constructor=constructor: constructor())

    _assert_retired(lambda: runtime_plugins.gateway_command_id(object()))


def test_direct_gateway_submodule_exports_only_fail_closed_entrypoints():
    gateway_dispatch = importlib.import_module(
        "bt_api_py.runtime_plugins.gateway_dispatch"
    )

    for name in (
        "GatewayManagedDispatcher",
        "GatewayManagedExecutionRuntime",
        "GatewayExecutionAuthority",
    ):
        constructor = getattr(gateway_dispatch, name)
        _assert_retired(lambda constructor=constructor: constructor())

    _assert_retired(
        lambda: gateway_dispatch.compose_gateway_managed_client(object())
    )
    _assert_retired(
        lambda: gateway_dispatch.compose_gateway_execution_authority(object())
    )
    _assert_retired(lambda: gateway_dispatch.gateway_command_id(object()))


def test_retired_api_module_stays_inside_the_discoverable_runtime_package():
    repository = Path(__file__).resolve().parents[1]
    packages = find_packages(
        where=str(repository),
        include=["bt_api_py", "bt_api_py.*"],
        exclude=["tests"],
    )

    assert "bt_api_py.runtime_plugins" in packages
    assert (repository / "bt_api_py" / "runtime_plugins" / "gateway_dispatch.py").is_file()
