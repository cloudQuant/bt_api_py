"""Validate configured account request labels without asserting account identity."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class AccountRequestLabelError(ValueError):
    """A configured request label is unavailable or does not match."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def configured_account_request_label(configured: Any, route: Any) -> str:
    """Select an explicitly configured request label for a route."""
    if not isinstance(configured, Mapping):
        raise AccountRequestLabelError("account_labels_missing")
    account_id = configured.get(route)
    if type(account_id) is not str or not account_id or account_id != account_id.strip():
        raise AccountRequestLabelError("account_label_missing")
    return account_id


def validate_supplied_account_request_label(account_id: str, supplied: Any) -> str:
    """Require an optional requested label to match the configured label."""
    if supplied not in (None, ""):
        supplied_id = str(supplied).strip().casefold()
        if supplied_id != account_id.casefold():
            raise AccountRequestLabelError("account_label_mismatch")
    return account_id


__all__ = (
    "AccountRequestLabelError",
    "configured_account_request_label",
    "validate_supplied_account_request_label",
)
