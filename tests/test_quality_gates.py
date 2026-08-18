"""Quality gate regression tests (Task 0.3).

Lock in the real defects that were masking a broken quality baseline:

* ``from bt_api_py.exceptions import *`` must not fail because ``__all__``
  references an undefined export (``InvalidSymbolError``).
* Mixins must declare their host attributes via ``Protocol`` instead of
  relying on a disabled ``attr-defined`` mypy code.
"""

from __future__ import annotations

import importlib


def test_exceptions_star_import_semantics() -> None:
    mod = importlib.import_module("bt_api_py.exceptions")
    imported: dict[str, object] = {}
    for name in mod.__all__:
        # getattr mirrors star-import semantics: an undefined name raises here.
        imported[name] = getattr(mod, name)
    assert "BtApiError" in imported
    assert "InvalidSymbolError" in imported
    assert "PartialDownloadError" in imported


def test_exceptions_all_names_are_defined() -> None:
    mod = importlib.import_module("bt_api_py.exceptions")
    for name in mod.__all__:
        assert hasattr(mod, name), f"{name} in __all__ but not defined on module"


def test_balance_manager_host_protocol_is_structural() -> None:
    from bt_api_py.balance_manager import BalanceManagerMixin
    from bt_api_py.bt_api import BtApi

    assert issubclass(BtApi, BalanceManagerMixin)
    annotations = getattr(BtApi, "__annotations__", {})
    for attr in ("exchange_feeds", "_value_dict", "_cash_dict"):
        assert hasattr(BtApi, attr) or attr in annotations, f"BtApi missing {attr}"


def test_data_downloader_host_protocol_is_structural() -> None:
    from bt_api_py.bt_api import BtApi

    for method in ("_get_feed", "push_bar_data_to_queue", "log"):
        assert hasattr(BtApi, method), f"BtApi missing {method}"
