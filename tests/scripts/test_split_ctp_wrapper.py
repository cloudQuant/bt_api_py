"""Offline output contracts for the CTP wrapper split generators."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "split_ctp_wrapper.py"


def _load_wrapper_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("isolated_split_ctp_wrapper", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_class_module_preserves_content_order_and_imports(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_wrapper_script()
    class_alpha = "class CThostFtdcAlpha(_ctp.Structure):\n    pass\n"
    class_beta = (
        "class CThostFtdcBeta(_ctp.Structure):\n"
        "    __repr__ = _swig_repr\n"
        "    ref = weakref.ref\n"
        "    output = stderr\n"
    )
    writes: list[tuple[str, str, bool]] = []

    def capture_write(filepath: str, content: str, dry_run: bool = False) -> None:
        writes.append((filepath, content, dry_run))

    monkeypatch.setattr(module, "_write_file", capture_write)

    module.generate_class_module(
        "ctp_structs_common",
        ["CThostFtdcBeta", "CThostFtdcAlpha"],
        {"CThostFtdcAlpha": class_alpha, "CThostFtdcBeta": class_beta},
        str(tmp_path),
        dry_run=True,
    )

    expected = (
        module._AUTO_GEN_HEADER
        + '"""CTP 通用结构 (登录/认证/用户/经纪商/结算等)"""\n\n'
        + "from ._ctp_base import _ctp, _swig_repr, weakref, stderr\n\n"
        + class_beta
        + class_alpha
        + "\n__all__ = [\n"
        + '    "CThostFtdcBeta",\n'
        + '    "CThostFtdcAlpha",\n'
        + "]\n"
    )
    assert writes == [(str(tmp_path / "ctp_structs_common.py"), expected, True)]


def test_generate_compat_ctp_py_preserves_import_and_module_order(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_wrapper_script()
    writes: list[tuple[str, str, bool]] = []

    def capture_write(filepath: str, content: str, dry_run: bool = False) -> None:
        writes.append((filepath, content, dry_run))

    monkeypatch.setattr(module, "_write_file", capture_write)

    module.generate_compat_ctp_py(["ctp_trader_api", "ctp_md_api"], str(tmp_path), dry_run=True)

    expected = (
        module._AUTO_GEN_HEADER
        + "\n"
        + '"""\n'
        + "CTP Python wrapper — 向后兼容垫片\n\n"
        + "原始文件由 SWIG 自动生成，现已被 split_ctp_wrapper.py 拆分为子模块。\n"
        + "本文件从各子模块重新导入全部符号，保持 ``from .ctp import *`` 的兼容性。\n"
        + '"""\n\n'
        + "from ._ctp_base import *  # noqa: F401,F403  — SWIG infrastructure\n"
        + "from .ctp_trader_api import *  # noqa: F401,F403\n"
        + "from .ctp_md_api import *  # noqa: F401,F403\n"
    )
    assert writes == [(str(tmp_path / "ctp.py"), expected, True)]
