"""Behavior contracts for safe submodule install ordering."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import install_bt_api_submodules as installer


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
