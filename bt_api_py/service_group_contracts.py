"""Public Backtrader-facing access to neutral service-group contracts.

The canonical immutable group request and snapshot contracts are owned by the
SDK Execution component.  Host frameworks import them through this module so
they do not depend on ``bt_api_execution`` module paths.  Importing this public
module alone is lightweight; the optional Execution component is loaded only
when a contract type or parser is selected.
"""

from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Any


class ServiceGroupContractFacadeError(RuntimeError):
    """A finite contract-loading or payload-validation failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_LOAD_LOCK = RLock()
_GROUP_CONTRACTS: Any = None


def _valid_code(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and all(
            character.isascii()
            and (character.isalnum() or character in "_.:-")
            for character in value
        )
    )


def _load_group_contracts() -> Any:
    """Load and validate the optional canonical execution contract module."""

    global _GROUP_CONTRACTS
    with _LOAD_LOCK:
        if _GROUP_CONTRACTS is not None:
            return _GROUP_CONTRACTS
        try:
            contracts = import_module("bt_api_execution.group_order_contracts")
        except ModuleNotFoundError as error:
            missing = error.name or ""
            code = (
                "SERVICE_GROUP_CONTRACTS_UNAVAILABLE"
                if missing == "bt_api_execution" or missing.startswith("bt_api_execution.")
                else "SERVICE_GROUP_CONTRACT_DEPENDENCY_UNAVAILABLE"
            )
            raise ServiceGroupContractFacadeError(code) from None
        except ImportError:
            raise ServiceGroupContractFacadeError(
                "SERVICE_GROUP_CONTRACTS_INCOMPATIBLE"
            ) from None
        except Exception:
            raise ServiceGroupContractFacadeError(
                "SERVICE_GROUP_CONTRACTS_INITIALIZATION_FAILED"
            ) from None

        try:
            intent_type = contracts.GroupOrderIntent
            snapshot_type = contracts.GroupOrderSnapshot
            intent_parser = intent_type.from_payload
            snapshot_parser = snapshot_type.from_payload
        except AttributeError:
            raise ServiceGroupContractFacadeError(
                "SERVICE_GROUP_CONTRACTS_INCOMPATIBLE"
            ) from None
        if (
            not isinstance(intent_type, type)
            or not isinstance(snapshot_type, type)
            or not callable(intent_parser)
            or not callable(snapshot_parser)
        ):
            raise ServiceGroupContractFacadeError(
                "SERVICE_GROUP_CONTRACTS_INCOMPATIBLE"
            )
        _GROUP_CONTRACTS = contracts
        return contracts


def parse_group_order_intent(payload: object) -> object:
    """Parse one payload into the canonical immutable SDK group intent."""

    contracts = _load_group_contracts()
    try:
        intent = contracts.GroupOrderIntent.from_payload(payload)
    except Exception as error:
        code = getattr(error, "code", None)
        raise ServiceGroupContractFacadeError(
            code if _valid_code(code) else "INVALID_GROUP_ORDER_INTENT"
        ) from None
    if type(intent) is not contracts.GroupOrderIntent:
        raise ServiceGroupContractFacadeError("INVALID_GROUP_ORDER_INTENT")
    return intent


def parse_group_order_snapshot(payload: object) -> object:
    """Parse one payload into the canonical immutable SDK group snapshot."""

    contracts = _load_group_contracts()
    try:
        snapshot = contracts.GroupOrderSnapshot.from_payload(payload)
    except Exception as error:
        code = getattr(error, "code", None)
        raise ServiceGroupContractFacadeError(
            code if _valid_code(code) else "INVALID_GROUP_ORDER_SNAPSHOT"
        ) from None
    if type(snapshot) is not contracts.GroupOrderSnapshot:
        raise ServiceGroupContractFacadeError("INVALID_GROUP_ORDER_SNAPSHOT")
    return snapshot


def __getattr__(name: str) -> Any:
    """Lazily re-export the two neutral contract types through this facade."""

    if name not in {"GroupOrderIntent", "GroupOrderSnapshot"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    contracts = _load_group_contracts()
    value = getattr(contracts, name)
    globals()[name] = value
    return value


__all__ = [
    "GroupOrderIntent",
    "GroupOrderSnapshot",
    "ServiceGroupContractFacadeError",
    "parse_group_order_intent",
    "parse_group_order_snapshot",
]
