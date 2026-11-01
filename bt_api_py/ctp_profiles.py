"""Lazy SDK access to the CTP provider's SimNow profile contract."""

from importlib import import_module

__all__ = [
    "official_simnow_fronts",
    "official_simnow_profile_for_td_front",
    "SimNowProfileError",
    "validate_official_simnow_store_profile",
    "verify_official_simnow_profile",
]


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(import_module("bt_api_ctp.simnow_store_profile"), name)
