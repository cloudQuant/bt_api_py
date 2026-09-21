"""Behavior contracts for safe submodule install ordering."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import install_bt_api_submodules as installer


def test_load_submodule_packages_reads_non_installable_metadata(monkeypatch, tmp_path):
    gitmodules = tmp_path / ".gitmodules"
    child = tmp_path / "bt_api" / "bt_api_execution"
    child.mkdir(parents=True)
    gitmodules.write_text(
        """[submodule \"bt_api/bt_api_execution\"]
path = bt_api/bt_api_execution
url = https://example.invalid/bt_api_execution.git
installable = false
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer, "GITMODULES", gitmodules)

    specs = installer.load_submodule_packages()

    assert specs == [
        installer.PackageSpec(
            "bt_api_execution",
            "bt_api_execution",
            child,
            "https://example.invalid/bt_api_execution.git",
            installable=False,
        )
    ]


def test_not_packaged_submodule_is_initialized_but_never_installed(monkeypatch, tmp_path):
    child = tmp_path / "bt_api" / "bt_api_execution"
    spec = installer.PackageSpec(
        "bt_api_execution",
        "bt_api_execution",
        child,
        "unused",
        installable=False,
    )
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    commands = []
    monkeypatch.setattr(installer, "resolve_executable", lambda _value: "/tool/git")
    monkeypatch.setattr(
        installer,
        "run_command",
        lambda command, **kwargs: commands.append((command, kwargs)),
    )

    installer.ensure_submodules([spec], jobs=2, skip_update=False, dry_run=True)

    assert [command for command, _kwargs in commands] == [
        ["/tool/git", "submodule", "sync", "--recursive"],
        [
            "/tool/git",
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--jobs",
            "2",
            str(child.relative_to(tmp_path)),
        ],
    ]

    calls = []
    args = SimpleNamespace(strategy="source-first", upgrade=True, python="/unused/python")
    monkeypatch.setattr(installer, "installed_version", lambda _name: calls.append("version"))
    monkeypatch.setattr(
        installer,
        "pip_install_source",
        lambda *_args, **_kwargs: calls.append("source") or False,
    )
    monkeypatch.setattr(
        installer,
        "pip_install_pypi",
        lambda *_args, **_kwargs: calls.append("pypi") or False,
    )

    result = installer.install_one(spec, args)

    assert result.status == "not-packaged"
    assert "installable=false" in result.detail
    assert calls == []


def test_packaged_submodule_keeps_source_then_pypi_fallback(monkeypatch, tmp_path):
    spec = installer.PackageSpec(
        "bt_api_example",
        "bt_api_example",
        tmp_path / "missing",
        "unused",
    )
    args = SimpleNamespace(
        strategy="source-first",
        upgrade=True,
        python="/unused/python",
        editable=False,
        dry_run=False,
    )
    calls = []
    monkeypatch.setattr(
        installer, "pip_install_source", lambda *_args, **_kwargs: calls.append("source") or False
    )
    monkeypatch.setattr(
        installer, "pip_install_pypi", lambda *_args, **_kwargs: calls.append("pypi") or True
    )

    result = installer.install_one(spec, args)

    assert result.status == "pypi"
    assert calls == ["source", "pypi"]


def test_main_reports_not_packaged_by_default_and_strict_mode_fails(monkeypatch, tmp_path):
    spec = installer.PackageSpec(
        "bt_api_execution",
        "bt_api_execution",
        tmp_path / "bt_api_execution",
        "unused",
        installable=False,
    )
    summaries = []

    def run(strict):
        args = SimpleNamespace(
            packages=["execution"],
            strategy="source-first",
            with_root=False,
            jobs=1,
            skip_submodule_update=False,
            dry_run=False,
            python="/unused/python",
            editable=False,
            upgrade=False,
            editable_root=False,
            strict=strict,
        )
        monkeypatch.setattr(installer, "parse_args", lambda: args)
        monkeypatch.setattr(installer, "load_submodule_packages", lambda: [spec])
        monkeypatch.setattr(installer, "ensure_submodules", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            installer,
            "install_one",
            lambda *_args: installer.InstallResult(
                "bt_api_execution", "not-packaged", "declared installable=false"
            ),
        )
        monkeypatch.setattr(
            installer,
            "print_summary",
            lambda results: summaries.append([(item.name, item.status) for item in results]),
        )
        return installer.main()

    assert run(strict=False) == 0
    assert run(strict=True) == 1
    assert summaries == [
        [("bt_api_execution", "not-packaged")],
        [("bt_api_execution", "not-packaged")],
    ]


def test_main_keeps_base_root_and_remaining_package_order_offline(monkeypatch):
    """The dependency package precedes root, then remaining packages in manifest order."""
    specs = [
        installer.PackageSpec("bt_api_binance", "bt-api-binance", Path("binance"), "unused"),
        installer.PackageSpec("bt_api_base", "bt-api-base", Path("base"), "unused"),
        installer.PackageSpec("bt_api_okx", "bt-api-okx", Path("okx"), "unused"),
    ]
    args = SimpleNamespace(
        packages=[],
        strategy="source-first",
        with_root=True,
        jobs=3,
        skip_submodule_update=False,
        dry_run=False,
        python="/unused/python",
        editable=False,
        upgrade=False,
        editable_root=False,
        strict=False,
    )
    events = []
    summaries = []

    def fake_parse_args():
        events.append("parse_args")
        return args

    def fake_load_packages():
        events.append("load_packages")
        return specs

    def fake_ensure_submodules(actual_specs, **kwargs):
        events.append("ensure_submodules")
        assert actual_specs == specs
        assert kwargs == {
            "jobs": 3,
            "skip_update": False,
            "dry_run": False,
        }

    def fake_install_one(spec, actual_args):
        assert actual_args is args
        events.append(f"install:{spec.name}")
        results = {
            "bt_api_base": installer.InstallResult("bt_api_base", "source"),
            "bt_api_binance": installer.InstallResult("bt_api_binance", "checked", "1.2.3"),
            "bt_api_okx": installer.InstallResult("bt_api_okx", "source"),
        }
        return results[spec.name]

    def fake_install_root(actual_args):
        events.append("install:bt_api_py")
        assert actual_args is args
        return True

    def fake_print_summary(results):
        events.append("print_summary")
        summaries.append([(result.name, result.status, result.detail) for result in results])

    monkeypatch.setattr(installer, "parse_args", fake_parse_args)
    monkeypatch.setattr(installer, "load_submodule_packages", fake_load_packages)
    monkeypatch.setattr(installer, "ensure_submodules", fake_ensure_submodules)
    monkeypatch.setattr(installer, "install_one", fake_install_one)
    monkeypatch.setattr(installer, "install_root", fake_install_root)
    monkeypatch.setattr(installer, "print_summary", fake_print_summary)

    exit_code = installer.main()

    assert exit_code == 0
    assert events == [
        "parse_args",
        "load_packages",
        "ensure_submodules",
        "install:bt_api_base",
        "install:bt_api_py",
        "install:bt_api_binance",
        "install:bt_api_okx",
        "print_summary",
    ]
    assert summaries == [
        [
            ("bt_api_base", "source", ""),
            ("bt_api_py", "source", str(installer.ROOT)),
            ("bt_api_binance", "checked", "1.2.3"),
            ("bt_api_okx", "source", ""),
        ]
    ]
