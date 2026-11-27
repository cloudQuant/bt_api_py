"""Process entry of the independent Risk service (iteration 64, 64.09): ``bt-risk-service``.

One process per account (D64-15). Start-up order, every step failing closed with
a finite ``RISK_SERVICE_*`` code:

1. load the service configuration (``bt_api_risk.service.v1``);
2. open the Risk SQLite file (``RiskOrderJournal``; pre-trade ledger and
   delegate journal share it) and hold the account writer fence for the
   process lifetime; refuse when another process holds it;
3. build the engine and bootstrap the policy file (first version, or a pure
   tightening of the effective one; a loosening must be published by an
   operator with a reason, D64-12);
4. start the Gateway runtimes and check that the Gateway granted what this
   configuration needs (observer, control, and market/account snapshot when
   configured, freeze 6.3); delegate grants are per (strategy, live run) and can
   only be checked once that run exists, on first use;
5. ``engine.recover()``, replay the observation stream, start the ServiceHost;
6. loop: ``serve_once``, observation poll, market/account refresh,
   ``engine.maintain()``; SIGTERM/SIGINT or an enabled protected stop marker stop the loop.

Commands: ``run --config <file>``, ``validate-policy <file>`` (offline, no
account) and ``status --config <file>`` (local ledger summary, no Gateway).
Output never contains keys, account numbers or file paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import signal
import sqlite3
import stat
import sys
import threading
import time
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from typing import Any

from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_base.risk_service_protocol import (
    RISK_ADMIN_OPERATIONS,
    RISK_ADMIN_PORT,
    RISK_ORDER_OPERATIONS,
    RISK_SERVICE_PORT,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._owner_stop_file import marker_requested, new_marker
from bt_api_py.risk_service_config import (
    RiskAccountFactsObservation,
    RiskPretradeUpgrade,
    RiskR04Selection,
    RiskServiceConfig,
    RiskServiceConfigError,
    ensure_private_directory,
    load_risk_service_config,
    resolve_path,
    restrict_file,
)
from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed

POLICY_LABELS = {
    "scope_version": "risk-scope-v1",
    "security_ref": "risk-curve",
    "security_version": "risk-curve-v1",
    "registration_policy_ref": "risk-order-policy-v1",
    "registration_policy_version": "risk-order-policy-v1",
    "service_requirements_ref": "risk-order-requirements-v1",
    "service_requirements_version": "risk-order-requirements-v1",
}
OBSERVER_OPERATIONS = frozenset(
    {"account.events.read_after", "account.events.progress", "account.events.ack"}
)
ACCOUNT_FACTS_OBSERVATION_OPERATIONS = frozenset({'account.facts.read_after.v2', 'account.facts.ack'})
from bt_api_py.gateway_account_cancel_attempts import OPERATIONS as ACCOUNT_CANCEL_ATTEMPT_OPERATIONS
from bt_api_py.gateway_order_cut_codec import OPERATIONS as ACCOUNT_ORDER_CUT_OPERATIONS
DELEGATE_OPERATIONS = frozenset(
    {
        "direct.delegated_reserve",
        "direct.delegated_dispatch",
        "direct.delegate_actor_status",
        "direct.delegate_actor_cancel",
        "direct.delegate_actor_cancel_status",
        "direct.delegate_actor_release",
        "direct.delegate_actor_release_status",
    }
)
_GATEWAY_SECURITY = "gateway-curve"
_LOG = logging.getLogger(__name__)
_RETAINED_HOSTS: set[Any] = set()
# A provisional strong root is installed before invoking a storage initializer.
# It retains the *same* ExitStack that owns the entered fence, even if setup or
# callback registration is interrupted. It is removed only after successful
# ownership transfer or confirmed cleanup; uncertain entries remain rooted.
_STORAGE_INIT_ROOTS: dict[int, tuple[Any, ...]] = {}
# Once the handoff succeeds, keep the exact owner and stack reachable until
# confirmed cleanup. This is a retention mechanism, not an authorization or a
# barrier against code explicitly calling ExitStack.close().
_STORAGE_ACTIVE_ROOTS: dict[int, tuple[Any, ...]] = {}
_PROBE_GUARD = threading.Lock()
_PROBE_STATE: dict[str, Any] = {"active": False, "uncertain": []}
_CLEANUP_DIAGNOSTIC_REASONS = frozenset({
    "CONCURRENT_CLEANUP_UNKNOWN", "LIFECYCLE_STATE_CHANGED",
    "RISK_RUNTIME_CLEANUP_UNKNOWN", "RUNTIME_CLOSE_UNCONFIRMED",
    "RUNTIME_CLOSE_EXCEPTION", "STORAGE_ENTRY_UNKNOWN", "STORAGE_INIT_UNKNOWN",
    "DELEGATE_INIT_UNKNOWN", "STORAGE_CLEANUP_FAILED", "DELEGATE_UNRESOLVED",
    "DELEGATE_FACTORY_STATE_CHANGED", "OBSERVER_FACTORY_STATE_CHANGED",
    "PREVIOUS_CLEANUP_UNCONFIRMED", "PORT_CLEANUP_UNCONFIRMED",
    "SERVICE_STATE_UNKNOWN", "SERVICE_START_UNKNOWN", "SERVICE_CLOSE_UNCONFIRMED",
    "PORT_CLOSE_UNCONFIRMED", "STORAGE_STACK_UNCONFIRMED", "STORAGE_OWNER_UNCONFIRMED",
    "STORAGE_CLOSE_UNCONFIRMED", "CLEANUP_EXCEPTION", "HANDLE_CLOSE_EXCEPTION",
    "HANDLE_CLOSE_UNCONFIRMED", "SLOT_REGISTRATION_UNCONFIRMED",
    'ACCOUNT_FACTS_STORAGE_UNKNOWN',
})
_ENTRY_PROOFS_GUARD = threading.Lock()
_ENTRY_PROOFS: weakref.WeakKeyDictionary[Any, tuple[Any, Any, Any, Any]] = weakref.WeakKeyDictionary()


class RiskServiceStartError(RuntimeError):
    """Finite refusal to start or keep running; carries no value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _FencedJournalEntryError(RiskServiceStartError):
    """Finite SDK refusal retaining the original entry error and manager."""

    def __init__(self, code: str, original: BaseException, manager: Any) -> None:
        super().__init__(code)
        self.original = original
        self.manager = manager


def _consume_fence_entry_result(outer_manager: Any, error: BaseException) -> Any:
    """Consume the SDK record of this entry's already-consumed Risk proof."""
    if type(error) is not _FencedJournalEntryError:
        return None
    with _ENTRY_PROOFS_GUARD:
        proof = _ENTRY_PROOFS.pop(error, None)
    if (proof is None or proof[0]() is not outer_manager
            or proof[1]() is not error.manager or proof[2]() is not error.original):
        return None
    return proof[3] if _fence_entry_result_code(proof[3]) is not None else None


def _risk_fence_entry_result(manager: Any, original: BaseException) -> Any:
    try:
        from bt_api_risk.risk_order_journal import (
            RiskFenceEntryComplete,
            RiskFenceEntryError,
            consume_fence_entry_complete,
        )

        if type(original) is not RiskFenceEntryError:
            return None
        result = consume_fence_entry_complete(manager, original)
        return (result if type(result) is RiskFenceEntryComplete
                and result in (RiskFenceEntryComplete.HELD, RiskFenceEntryComplete.OTHER)
                else None)
    except Exception:
        return None


def _risk_fence_entry_is_held(result: Any) -> bool:
    try:
        from bt_api_risk.risk_order_journal import RiskFenceEntryComplete

        return result is RiskFenceEntryComplete.HELD
    except Exception:
        return False


def _fence_entry_result_code(result: Any) -> str | None:
    try:
        from bt_api_risk.risk_order_journal import RiskFenceEntryComplete

        if type(result) is RiskFenceEntryComplete:
            if result is RiskFenceEntryComplete.HELD:
                return "RISK_SERVICE_FENCE_HELD"
            if result is RiskFenceEntryComplete.OTHER:
                return "RISK_SERVICE_STORAGE_UNAVAILABLE"
    except Exception:
        return None
    return None


def _fail(code: str) -> None:
    raise RiskServiceStartError(code)


def _pretrade_selection(config: RiskServiceConfig) -> tuple[int, RiskPretradeUpgrade | None]:
    version = getattr(config, 'pretrade_schema_version', 3)
    upgrade = getattr(config, 'pretrade_upgrade_from_v3', None)
    if type(version) is not int or version not in (3, 4):
        _fail('RISK_SERVICE_CONFIG_PRETRADE_INVALID')
    if upgrade is not None:
        def valid(value):
            return (type(value) is tuple and len(value) == 2
                    and all(type(part) is int and part >= 0 for part in value) and value[1] > 0)
        if (type(upgrade) is not RiskPretradeUpgrade or version != 4
                or upgrade.maintenance_window is not True
                or not valid(upgrade.expected_database_identity) or not valid(upgrade.expected_lock_identity)
                or upgrade.expected_database_identity == upgrade.expected_lock_identity):
            _fail('RISK_SERVICE_CONFIG_PRETRADE_INVALID')
    return version, upgrade


def _pretrade_existing(config: RiskServiceConfig, scope: Any, *, version: int,
                       expected: tuple[tuple[int, int], tuple[int, int]] | None = None):
    """Observe pre-existing v3/v4 storage without creating files or granting authority."""
    from bt_api_risk.pretrade.ledger import PretradeLedger, _source_unverified
    try:
        if os.name != "nt" and not hasattr(os, 'geteuid'):
            raise ValueError
        path = config.risk_database
        digest = hashlib.sha256(scope.key.encode('utf-8')).hexdigest()
        lock_path = path.with_name(path.name + '.' + digest + '.writer.lock')
        def observe():
            result = []
            for value in (path, lock_path):
                if os.name == "nt":
                    # Windows parity of st_uid == geteuid(): the file owner
                    # must be the process token user. The POSIX gate here only
                    # compares the uid (no mode bits), so skip the DACL walk.
                    # inspect_owner fails closed on any API/reparse failure.
                    from bt_api_py._win32_security import inspect_owner
                    info = os.stat(value, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError
                    owner = inspect_owner(value, others_may_write_check=False)
                    if not owner.is_current_user:
                        raise ValueError
                    result.append((info.st_dev, info.st_ino))
                else:
                    info = os.stat(value, follow_symlinks=False)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_uid != os.geteuid()):
                        raise ValueError
                    result.append((info.st_dev, info.st_ino))
            return tuple(result)
        identities = observe()
        if identities[0] == identities[1] or (expected is not None and identities != expected):
            raise ValueError
        # The URI explicitly prevents sqlite from creating a missing database.
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True)) as connection:
            PretradeLedger._check_schema(connection, version=version)
            if _source_unverified(connection) is not True:
                raise ValueError
        if observe() != identities:
            raise ValueError
        return identities
    except Exception:
        raise RiskServiceStartError('RISK_SERVICE_STORAGE_SCHEMA_INVALID') from None


def _require_supported_r04(config: RiskServiceConfig) -> None:
    """Validate a v2 R-04 selection statically, before any resource is acquired.

    Decision B-2: an explicit ``rules.r04`` selection may name one v2 rule set.
    The selection must be an exact :class:`RiskR04Selection` whose implementation
    is listed in the same trusted ``rules.trusted_implementations`` allowlist with
    exactly its declared implementation version; both contract versions are
    already pinned to the frozen ones by the config parser. This function never
    imports the plugin or touches the host, so a forged or unlisted selection is
    refused with a finite code and no side effect. The class itself is verified
    when the artifact mapping is built (:func:`_r04_artifacts`).
    """
    selection = getattr(config, "r04_selection", None)
    if selection is None:
        return
    if type(selection) is not RiskR04Selection:
        _fail("RISK_SERVICE_R04_SELECTION_INVALID")
    trusted = getattr(config, "trusted_rules", None)
    if not isinstance(trusted, Mapping):
        _fail("RISK_SERVICE_R04_NOT_ALLOWED")
    expected = None
    for name, version in trusted.items():
        if type(name) is not str:
            _fail("RISK_SERVICE_R04_NOT_ALLOWED")
        if name == selection.implementation:
            expected = version
    if type(expected) is not int or expected != selection.implementation_version:
        _fail("RISK_SERVICE_R04_NOT_ALLOWED")


def _r04_artifacts(config: RiskServiceConfig) -> dict[str, Any] | None:
    """Build the host's exact v2 artifact mapping for the selected rule set.

    This is the only place the host imports the selected implementation: the
    name comes from trusted service configuration, is re-checked against the
    allowlist, and must resolve to an exact class that subclasses both frozen
    runner bases. Declared versions and the two contract versions are checked
    again by ``compose_r04_v2`` at activation, and the artifact reference is a
    consistency claim, not a signature. Any failure is a finite refusal.
    """
    selection = getattr(config, "r04_selection", None)
    if selection is None:
        return None
    _require_supported_r04(config)
    from bt_api_risk.pretrade.r04_v2_contract import R04RuleV2
    from bt_api_risk.pretrade.r04_v2_observation_contract import R04ObservationRuleV2
    from bt_api_risk.pretrade.r04_v2_registry_candidate import R04ArtifactBinding

    module_name, _, class_name = selection.implementation.partition(":")
    try:
        module = __import__(module_name, fromlist=[class_name])
        rule_class = getattr(module, class_name)
    except Exception:
        _fail("RISK_SERVICE_R04_UNAVAILABLE")
    if (
        type(rule_class) is not type
        or R04RuleV2 not in rule_class.__mro__
        or R04ObservationRuleV2 not in rule_class.__mro__
    ):
        # A class must implement both runners; one contract alone cannot serve
        # the claim and observation paths with one instance.
        _fail("RISK_SERVICE_R04_UNAVAILABLE")
    try:
        binding = R04ArtifactBinding(
            selection.implementation,
            selection.implementation_version,
            selection.claim_contract_version,
            selection.observation_contract_version,
            rule_class,
            selection.implementation,
        )
    except Exception:
        _fail("RISK_SERVICE_R04_UNAVAILABLE")
    return {selection.implementation: binding}


def _finite_poll_value(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (ArithmeticError, TypeError, ValueError):
        return False


# ----------------------------------------------------------------------------- fence probe


@contextmanager
def _held_fence(journal: Any, scope: Any) -> Iterator[Any]:
    """The journal's writer fence taken with one non-blocking attempt.

    ``RISK_SERVICE_FENCE_HELD`` when another thread or process holds it; any
    other storage failure is ``RISK_SERVICE_STORAGE_UNAVAILABLE``. The lock file
    lies in the owner-only storage directory.
    """
    # A process-wide latch avoids treating a new journal/scope object as proof
    # that an earlier uncertain probe belongs to a different resource.
    with _PROBE_GUARD:
        if _PROBE_STATE["active"] or _PROBE_STATE["uncertain"]:
            _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
        _PROBE_STATE["active"] = True
    stack = ExitStack()
    manager = None
    try:
        try:
            manager = journal.writer_fence(scope, blocking=False)
            value = stack.enter_context(manager)
            with _PROBE_GUARD:
                if _PROBE_STATE["uncertain"]:
                    _PROBE_STATE["uncertain"].append((journal, scope, manager, stack))
                    _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
        except BaseException as error:
            try:
                result = _risk_fence_entry_result(manager, error)
                code = ("RISK_SERVICE_FENCE_HELD" if _risk_fence_entry_is_held(result)
                        else "RISK_SERVICE_STORAGE_UNAVAILABLE")
            except BaseException as classification_error:
                with _PROBE_GUARD:
                    _PROBE_STATE["uncertain"].append(
                        (journal, scope, manager, stack, error, classification_error)
                    )
                raise
            if result is None:
                with _PROBE_GUARD:
                    _PROBE_STATE["uncertain"].append((journal, scope, manager, stack, error))
            if not isinstance(error, Exception):
                raise
            raise RiskServiceStartError(code) from None
        try:
            yield value
        except BaseException as body_error:
            with _PROBE_GUARD:
                if _PROBE_STATE["uncertain"]:
                    _PROBE_STATE["uncertain"].append((journal, scope, manager, stack, body_error))
                    raise
            try:
                suppressed = stack.__exit__(type(body_error), body_error, body_error.__traceback__)
            except BaseException as error:
                with _PROBE_GUARD:
                    _PROBE_STATE["uncertain"].append((journal, scope, manager, stack, body_error, error))
                if not isinstance(error, Exception):
                    raise
                raise RiskServiceStartError("RISK_SERVICE_STORAGE_UNAVAILABLE") from None
            with _PROBE_GUARD:
                if _PROBE_STATE["uncertain"]:
                    _PROBE_STATE["uncertain"].append((journal, scope, manager, stack, body_error))
                    raise
            if not suppressed:
                raise
        else:
            with _PROBE_GUARD:
                if _PROBE_STATE["uncertain"]:
                    _PROBE_STATE["uncertain"].append((journal, scope, manager, stack))
                    _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
            try:
                stack.close()
            except BaseException as error:
                with _PROBE_GUARD:
                    _PROBE_STATE["uncertain"].append((journal, scope, manager, stack, error))
                if not isinstance(error, Exception):
                    raise
                raise RiskServiceStartError("RISK_SERVICE_STORAGE_UNAVAILABLE") from None
            with _PROBE_GUARD:
                if _PROBE_STATE["uncertain"]:
                    _PROBE_STATE["uncertain"].append((journal, scope, manager, stack))
                    _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
    finally:
        with _PROBE_GUARD:
            _PROBE_STATE["active"] = False


def probe_writer_fence(journal: Any, scope: Any) -> None:
    """Refuse at once with ``RISK_SERVICE_FENCE_HELD`` when the fence is held elsewhere.

    A point-in-time check only: it takes and drops the fence without waiting,
    which advances the account's fencing token when it succeeds. The host
    itself never probes before acquiring: it takes the fence with one
    non-blocking attempt (``RiskOrderJournal.writer_fence(..., blocking=False)``)
    and holds it for the process lifetime.
    """
    with _held_fence(journal, scope):
        pass


class _FencedJournalManager:
    """Bind the SDK entry proof to this exact outer context manager."""

    def __init__(self, path: Path, scope: Any) -> None:
        self._path = path
        self._scope = scope
        self._entry_attempted = False
        self._inner_manager = None
        self._binding = None
        self._exit_attempted = False
        self._close_result = None

    def __enter__(self) -> tuple[Any, Any]:
        if self._entry_attempted:
            raise RiskServiceStartError("RISK_SERVICE_STORAGE_UNAVAILABLE")
        self._entry_attempted = True
        return _open_fenced_journal_context(self, self._path, self._scope)

    def __exit__(self, *args: Any) -> Any:
        if self._exit_attempted:
            raise RiskServiceStartError("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
        # An interrupted attempt must not cause a second exit of the owner.
        self._exit_attempted = True
        self._close_fence_once(*args)
        return False

    def _close_fence_once(self, *args: Any) -> None:
        from bt_api_risk.risk_order_journal import RiskFenceExitCleanup

        binding = self._binding
        if type(binding) is not tuple or len(binding) != 3:
            _fail("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
        manager, journal, fence = binding
        if manager is not self._inner_manager:
            _fail("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
        journal.prepare_borrowed_fence_close(manager, fence)
        manager.__exit__(*args)
        receipt = journal.consume_borrowed_fence_close(manager, fence)
        if receipt is not RiskFenceExitCleanup.COMPLETE:
            _fail("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
        # This records the delivered receipt for this exact binding.  If the
        # receipt was consumed but not delivered, this remains unconfirmed.
        self._close_result = receipt


def _fenced_journal_close_confirmed(manager: Any, journal: Any, fence: Any) -> bool:
    from bt_api_risk.risk_order_journal import RiskFenceExitCleanup

    if type(manager) is not _FencedJournalManager:
        return False
    binding = manager._binding
    if type(binding) is not tuple or len(binding) != 3:
        return False
    if journal is None and fence is None:
        # The entry succeeded but unpacking its result failed before the host
        # held the journal/fence. The manager's own binding is what it yielded
        # and what it closed, so it remains the comparison target.
        journal, fence = binding[1], binding[2]
    return (binding[0] is manager._inner_manager and binding[1] is journal
            and binding[2] is fence and manager._exit_attempted is True
            and manager._close_result is RiskFenceExitCleanup.COMPLETE)


def _only_exit_callback(stack: Any, owner: Any, exit_method: Any = None) -> bool:
    """Do not unwind an altered stack or unknown callbacks ahead of a fence.

    ``ExitStack.enter_context`` records ``type(owner).__exit__``, so the default
    check is exactly the function that was registered for this owner: one
    synchronous callback whose bound instance is ``owner``.
    """
    if type(stack) is not ExitStack or len(stack._exit_callbacks) != 1:
        return False
    if exit_method is None:
        # A non-context-manager owner has no registered exit to match; treat
        # that as no proof instead of letting the attribute lookup escape.
        exit_method = getattr(type(owner), "__exit__", None)
        if exit_method is None:
            return False
    is_sync, callback = stack._exit_callbacks[0]
    return (is_sync is True and getattr(callback, "__self__", None) is owner
            and getattr(callback, "__func__", None) is exit_method)


def _open_fenced_journal(path: Path, scope: Any) -> _FencedJournalManager:
    return _FencedJournalManager(path, scope)


def _open_fenced_journal_context(outer_manager: _FencedJournalManager, path: Path,
                                 scope: Any) -> tuple[Any, Any]:
    """Acquire the account lock before initialization, without waiting on SQLite."""
    from bt_api_risk.risk_order_journal import RiskOrderJournal, RiskOrderJournalError

    manager = None
    try:
        manager = RiskOrderJournal.open_fenced(path, writer_id="risk-service", scope=scope, blocking=False)
        # Keep the original inner manager reachable before its entry begins.
        outer_manager._inner_manager = manager
        result = manager.__enter__()
        if type(result) is not tuple or len(result) != 2:
            _fail("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
        journal, fence = result
        outer_manager._binding = (manager, journal, fence)
    except RiskOrderJournalError as error:
        result = _risk_fence_entry_result(manager, error)
        code = ("RISK_SERVICE_FENCE_HELD" if _risk_fence_entry_is_held(result)
                else "RISK_SERVICE_STORAGE_UNAVAILABLE")
        refusal = _FencedJournalEntryError(code, error, manager)
        if result is not None:
            try:
                proof = (weakref.ref(outer_manager), weakref.ref(manager), weakref.ref(error), result)
                with _ENTRY_PROOFS_GUARD:
                    _ENTRY_PROOFS[refusal] = proof
            except (TypeError, ValueError):
                refusal = _FencedJournalEntryError("RISK_SERVICE_STORAGE_UNAVAILABLE", error, manager)
        raise refusal from None
    except sqlite3.OperationalError as error:
        code = getattr(error, "sqlite_errorcode", None)
        mapped = ("RISK_SERVICE_STORAGE_BUSY" if type(code) is int and (code & 0xff) in
                  (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "RISK_SERVICE_STORAGE_UNAVAILABLE")
        raise _FencedJournalEntryError(mapped, error, manager) from None
    except Exception as error:
        raise _FencedJournalEntryError("RISK_SERVICE_STORAGE_UNAVAILABLE", error, manager) from None
    return result


# ----------------------------------------------------------------------------- test-only faults

# TEST ONLY (64.09, plan 64 section 4.3 crash table). When this variable is
# absent the host composes exactly what it composes without this code. When it
# is set to one of FAULT_POINTS the process exits at once (``os._exit``, no
# cleanup, no reply) at that point of the first order through it; any other
# value, including the empty string, refuses to start.
FAULT_POINT_ENVIRONMENT = "BT_RISK_FAULT_POINT"
FAULT_POINTS = frozenset(
    {"after_local_reserve", "after_gateway_reserve", "after_claim_commit", "after_dispatch_sent"}
)
FAULT_EXIT_CODE = 86


def fault_point_from_environment(environ: Mapping[str, str]) -> str | None:
    """The configured test-only fault point, None when unset; refuse unknown values."""
    if FAULT_POINT_ENVIRONMENT not in environ:
        return None
    value = environ[FAULT_POINT_ENVIRONMENT]
    if value not in FAULT_POINTS:
        _fail("RISK_SERVICE_FAULT_POINT_INVALID")
    return value


class _FaultInjectingOrderPort:
    """TEST ONLY: the Gateway order port with one process-exit point (section 4.3).

    ``after_local_reserve``: Risk committed the permit (RESERVING) and its
    occupancy; the delegate reserve has not left. ``after_gateway_reserve``: the
    Gateway answered the delegate reserve; Risk recorded nothing of it.
    ``after_claim_commit``: Risk committed the claim (CLAIMED); the delegate
    dispatch has not left. ``after_dispatch_sent``: the Gateway answered the
    delegate dispatch; Risk recorded nothing of it.
    """

    def __init__(self, port: Any, point: str, exit_now: Callable[[int], Any] = os._exit) -> None:
        if point not in FAULT_POINTS:
            raise ValueError("RISK_SERVICE_FAULT_POINT_INVALID")
        self._port, self._point, self._exit_now = port, point, exit_now

    def _fire(self, point: str) -> None:
        if point == self._point:
            os.write(2, ("RISK_SERVICE_FAULT_INJECTED " + point + "\n").encode("ascii"))
            self._exit_now(FAULT_EXIT_CODE)

    def reserve(self, **kwargs: Any) -> Any:
        self._fire("after_local_reserve")
        reply = self._port.reserve(**kwargs)
        self._fire("after_gateway_reserve")
        return reply

    def dispatch(self, **kwargs: Any) -> Any:
        self._fire("after_claim_commit")
        reply = self._port.dispatch(**kwargs)
        self._fire("after_dispatch_sent")
        return reply

    def __getattr__(self, name: str) -> Any:
        return getattr(self._port, name)


# ----------------------------------------------------------------------------- configs


def service_config_document(config: RiskServiceConfig) -> dict:
    """The Risk ServiceHost manifest (no keys; keys go to the trusted authority)."""
    _require_supported_r04(config)
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": config.service_id,
        "scope_ref": config.scope_ref,
        "transport_ref": "risk-server",
        "registration_policy_ref": POLICY_LABELS["registration_policy_ref"],
        "service_requirements_ref": POLICY_LABELS["service_requirements_ref"],
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": config.instance_id,
                    "bind_ports": [RISK_SERVICE_PORT, RISK_ADMIN_PORT],
                    "options": {"role": "order_service"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "risk-server": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": config.rpc_bind},
                    "security_profile_ref": POLICY_LABELS["security_ref"],
                }
            },
        },
    }


def _gateway_request_timeout_ms(config: RiskServiceConfig) -> int:
    """Propagate call_timeout_seconds to ZMQ profiles without shrinking the 2s floor."""
    timeout_s = getattr(config, "call_timeout_seconds", 2.0)
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
        return 2000
    return max(2000, min(120_000, int(math.ceil(timeout_s * 1000))))


def _gateway_client_config(config: RiskServiceConfig, mount: str, *, market: bool = False):
    """A SERVICE-role client manifest for one Gateway mount of the Risk principal."""
    _require_supported_r04(config)
    from bt_api_py.service_config import parse_service_config

    raw = service_config_document(config)
    gateway = config.gateway
    binding = {
        "mount": mount,
        "service_id": gateway.service_id,
        "instance_id": gateway.instance_id,
        "scope_ref": gateway.scope_ref,
        "transport_ref": "gateway-client",
    }
    if not market:
        binding["account_ref"] = config.account_scope
    endpoints = {"rpc_connect": gateway.rpc_endpoint}
    if market:
        endpoints["market_connect"] = gateway.market_endpoint
    timeout_ms = _gateway_request_timeout_ms(config)
    raw["clients"] = {
        "gateway": {
            "config_schema_version": 1,
            "settings": {
                "bindings": [binding],
                "options": {"request_timeout_ms": timeout_ms},
            },
        }
    }
    raw["communication"]["profiles"]["gateway-client"] = {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": endpoints,
        "security_profile_ref": _GATEWAY_SECURITY,
        "options": {"request_timeout_ms": timeout_ms},
    }
    return parse_service_config(raw)


def _control_client_config(config: RiskServiceConfig, mounts: Sequence[str]):
    _require_supported_r04(config)
    from bt_api_py.service_config import parse_client_config

    gateway = config.gateway
    timeout_ms = _gateway_request_timeout_ms(config)
    bindings = [
        {
            "mount": mount,
            "service_id": gateway.service_id,
            "instance_id": gateway.instance_id,
            "scope_ref": gateway.scope_ref,
            "account_ref": config.account_scope,
            "transport_ref": "gateway",
        }
        for mount in mounts
    ]
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": gateway.control.actor_ref},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": bindings,
                        "options": {"request_timeout_ms": timeout_ms},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "gateway": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": gateway.rpc_endpoint},
                        "security_profile_ref": _GATEWAY_SECURITY,
                        "options": {"request_timeout_ms": timeout_ms},
                    }
                },
            },
        }
    )


def build_authority(config: RiskServiceConfig):
    """Operator-installed principal table -> ServiceHost authority (never from requests)."""
    _require_supported_r04(config)
    from bt_api_py.service_host import TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    domain, account = config.execution_domain_ref, config.account_scope
    grants: dict[str, tuple] = {}
    keys: dict[bytes, str] = {}
    for principal in config.principals.values():
        keys[principal.public_key] = principal.principal_ref
        if principal.role == "operator":
            identity = WireIdentity(
                WireRole.APPLICATION_CLIENT, principal.principal_ref, principal.actor_ref
            )
            resources = (
                ResourceGrant(
                    config.instance_id,
                    RISK_ADMIN_PORT,
                    config.scope_ref,
                    WireScope(domain, account_ref=account),
                    frozenset(principal.operations & RISK_ADMIN_OPERATIONS),
                ),
            )
        else:
            role = WireRole.STRATEGY_CLIENT if principal.role == "strategy" else WireRole.SERVICE
            identity = WireIdentity(role, principal.principal_ref, principal.actor_ref)
            resources = tuple(
                ResourceGrant(
                    config.instance_id,
                    RISK_SERVICE_PORT,
                    config.scope_ref,
                    WireScope(domain, account_ref=account, strategy_ref=strategy),
                    frozenset(principal.operations & RISK_ORDER_OPERATIONS),
                )
                for strategy in sorted(principal.strategy_refs)
            )
        grants[principal.principal_ref] = (PrincipalGrant(identity, resources),)
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, config.service_principal_ref, config.service_id),
        scope=WireScope(domain),
        scope_ref=config.scope_ref,
        scope_version=POLICY_LABELS["scope_version"],
        security_ref=POLICY_LABELS["security_ref"],
        security_version=POLICY_LABELS["security_version"],
        registration_policy_ref=POLICY_LABELS["registration_policy_ref"],
        registration_policy_version=POLICY_LABELS["registration_policy_version"],
        service_requirements_ref=POLICY_LABELS["service_requirements_ref"],
        service_requirements_version=POLICY_LABELS["service_requirements_version"],
        curve_public_key=config.server_public_key,
        curve_secret_key=config.server_secret_key,
        principals=keys,
        grants=grants,
    )


# ----------------------------------------------------------------------------- policy


def read_policy_files(policy_file: Path) -> tuple[str, str | None]:
    """Policy text and the trusted exchange-rules text the policy names (if any)."""
    from bt_api_risk.pretrade.policy import load_document

    try:
        text = policy_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _fail("RISK_SERVICE_POLICY_UNAVAILABLE")
    try:
        document = load_document(text)
    except Exception:
        _fail("RISK_SERVICE_POLICY_INVALID")
    rules_text = None
    named = document.get("exchange_rules") if isinstance(document, Mapping) else None
    if isinstance(named, Mapping) and named.get("file") is not None:
        try:
            rules_path = resolve_path(named["file"], policy_file.resolve().parent)
            rules_text = rules_path.read_text(encoding="utf-8")
        except (RiskServiceConfigError, OSError, UnicodeDecodeError):
            _fail("RISK_SERVICE_EXCHANGE_RULES_UNAVAILABLE")
    return text, rules_text


def bootstrap_policy(engine: Any, ledger: Any, config: RiskServiceConfig, registry: Any) -> str:
    """Publish the policy file at start when the ledger has none, or when it only tightens."""
    _require_supported_r04(config)
    from bt_api_risk.pretrade import CallerIdentity, RiskAdminError, RiskPolicyError
    from bt_api_risk.pretrade.policy import classify_change, validate_policy_document

    text, rules_text = read_policy_files(config.policy_file)
    try:
        policy = validate_policy_document(
            text, exchange_rules_text=rules_text, rule_allowlist=registry.allowlist
        )
    except RiskPolicyError:
        _fail("RISK_SERVICE_POLICY_INVALID")
    if policy.route != config.route or policy.account_scope != config.account_scope:
        _fail("RISK_SERVICE_POLICY_ACCOUNT_MISMATCH")
    summary = engine.status_summary()
    if summary["policy_load_error"] == "RULE_R04_BINDING_UNAVAILABLE":
        # A stored v2 policy binds a rule this host did not select or cannot
        # compose; the service must not run without the policy it was given.
        _fail("RISK_SERVICE_R04_UNAVAILABLE")
    if summary["policy_digest"] == policy.digest:
        return "unchanged"
    with ledger.transaction() as txn:
        row = txn.latest_policy()
    if row is not None:
        if summary["policy_version"] is None:
            _fail("RISK_SERVICE_POLICY_STORE_UNREADABLE")
        old = validate_policy_document(
            row["document_json"],
            exchange_rules_text=row["exchange_rules_text"],
            rule_allowlist=registry.allowlist,
        )
        if classify_change(old, policy)["loosened"]:
            # D64-12: loosening needs an authorized operator, a reason and no latch.
            _fail("RISK_SERVICE_POLICY_FILE_LOOSENS")
    operator = CallerIdentity(
        principal_ref="risk-host:policy-file",
        actor_ref=config.service_id,
        role="operator",
        strategy_refs=frozenset(),
        run_id=None,
        operations=frozenset({"risk.policy.publish"}),
    )
    try:
        engine.publish_policy(
            operator,
            text,
            summary["policy_version"] or 0,
            # The first version has no predecessor to compare with, so it counts as a
            # loosening; the operator's file is the decision. Later files only tighten.
            "first policy version from the service policy file" if row is None else None,
            exchange_rules_text=rules_text,
        )
    except RiskAdminError as error:
        if getattr(error, "code", None) == "policy_r04_binding_unavailable":
            # The file binds an R-04 implementation that the selection did not
            # provide (or whose version/contract does not match): refused whole.
            _fail("RISK_SERVICE_R04_UNAVAILABLE")
        _fail("RISK_SERVICE_POLICY_PUBLISH_REFUSED")
    return "bootstrapped" if row is None else "tightened"


# ----------------------------------------------------------------------------- Gateway composition


_INVALID_CLEANUP_VALUE = object()


class _GuardedCleanupSnapshot:
    """Inspect a runtime snapshot only while the shared cleanup state permits it."""

    def __init__(self, owner: Any, runtime: Any, snapshot: Any) -> None:
        self._owner, self._runtime, self._snapshot = owner, runtime, snapshot

    def __getattr__(self, name: str) -> Any:
        if self._owner._retain_if_global_unknown(self._runtime) or self._owner._cleanup_uncertain:
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        try:
            value = getattr(self._snapshot, name)
        except BaseException:
            self._owner._retain_if_global_unknown(self._runtime)
            raise
        if self._owner._retain_if_global_unknown(self._runtime) or self._owner._cleanup_uncertain:
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        if name == "state" and type(value) is not str:
            return _INVALID_CLEANUP_VALUE
        if name == "error_code" and value is not None and type(value) is not str:
            return _INVALID_CLEANUP_VALUE
        return value


class _DelegateHandle:
    def __init__(self, runtime: Any, client: Any, host: Any,
                 cleanup_guard: Callable[[Any], bool] | None = None,
                 snapshot_guard: Callable[[Any, Any], Any] | None = None) -> None:
        self.runtime, self.client, self.host = runtime, client, host
        self._cleanup_guard = cleanup_guard
        self._snapshot_guard = snapshot_guard

    def close(self) -> Any:
        if self._cleanup_guard is not None and self._cleanup_guard(self.runtime):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        try:
            result = self.runtime.close(timeout_seconds=5.0)
        except BaseException:
            if self._cleanup_guard is not None:
                self._cleanup_guard(self.runtime)
            raise
        if self._cleanup_guard is not None and self._cleanup_guard(self.runtime):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        return self._snapshot_guard(self.runtime, result) if self._snapshot_guard is not None else result


class _MarketOwnerExtension:
    """Owner-thread market clients of the Risk principal on ``upstream.market``."""

    def __init__(self, settings: Any, *, stream_scope: WireScope, security_profiles: Mapping,
                 cleanup_guard: Callable[[Any], bool] | None = None,
                 runtime_cleanup_unknown: Callable[[], bool] | None = None,
                 on_cleanup_sticky: Callable[[Any], None] | None = None) -> None:
        self._settings, self._scope, self._profiles = settings, stream_scope, security_profiles
        self._market: Any = None
        self._cleanup_guard = cleanup_guard
        self._runtime_cleanup_unknown = runtime_cleanup_unknown
        self._on_cleanup_sticky = on_cleanup_sticky

    def start(self, runtime: Any) -> None:
        from bt_api_py.gateway_strategy_market import GatewayMarketOwner

        if self._cleanup_guard is not None and self._cleanup_guard(self):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        strict_options = {}
        if self._runtime_cleanup_unknown is not None and self._on_cleanup_sticky is not None:
            strict_options = {
                "strict_cleanup": True,
                "cleanup_guard": self._runtime_cleanup_unknown,
                "cleanup_uncertain_callback": lambda: self._on_cleanup_sticky(self),
            }
        try:
            self._market = GatewayMarketOwner(
                self._settings,
                stream_scope=self._scope,
                security_profiles=self._profiles,
                mount="upstream.market",
                **strict_options,
            )
        except BaseException:
            if self._cleanup_guard is not None:
                self._cleanup_guard(self)
            raise
        if self._cleanup_guard is not None and self._cleanup_guard(self):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        try:
            self._market.start(runtime)
        except BaseException:
            if self._cleanup_guard is not None:
                self._cleanup_guard(self)
            raise
        if self._cleanup_guard is not None and self._cleanup_guard(self):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")

    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> Any:
        from bt_api_py.service_runtime_thread import OwnerOperationError

        if self._cleanup_guard is not None and self._cleanup_guard(self):
            raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")
        if self._market is None or not operation.startswith("market."):
            raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")
        try:
            result = self._market.invoke(operation.removeprefix("market."), arguments)
        except BaseException:
            if self._cleanup_guard is not None:
                self._cleanup_guard(self)
            raise
        if self._cleanup_guard is not None and self._cleanup_guard(self):
            raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")
        return result

    @property
    def cleanup_uncertainties(self) -> int:
        if self._cleanup_guard is not None and self._cleanup_guard(self):
            return 1
        return 0 if self._market is None else self._market.cleanup_uncertainties

    def close(self) -> None:
        if self._market is not None:
            if self._cleanup_guard is not None and self._cleanup_guard(self):
                _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
            try:
                self._market.close()
            except BaseException:
                if self._cleanup_guard is not None:
                    self._cleanup_guard(self)
                raise
            if self._cleanup_guard is not None and self._cleanup_guard(self):
                _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")


class _AccountOwnerExtension:
    def __init__(self, settings: Any, *, account_ref: str, scope_ref: str,
                 cleanup_guard: Callable[[Any], bool] | None = None,
                 runtime_cleanup_unknown: Callable[[], bool] | None = None,
                 on_cleanup_sticky: Callable[[Any], None] | None = None) -> None:
        self._settings, self._account, self._scope_ref = settings, account_ref, scope_ref
        self._owner: Any = None
        self._cleanup_guard = cleanup_guard
        self._runtime_cleanup_unknown = runtime_cleanup_unknown
        self._on_cleanup_sticky = on_cleanup_sticky

    def start(self, runtime: Any) -> None:
        from bt_api_py.gateway_strategy_account import GatewayAccountOwner

        if self._cleanup_guard is not None and self._cleanup_guard(self):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        strict_options = {}
        if self._runtime_cleanup_unknown is not None and self._on_cleanup_sticky is not None:
            strict_options = {
                "strict_cleanup": True,
                "cleanup_guard": self._runtime_cleanup_unknown,
                "cleanup_uncertain_callback": lambda: self._on_cleanup_sticky(self),
            }
        try:
            self._owner = GatewayAccountOwner(
                self._settings,
                account_ref=self._account,
                scope_ref=self._scope_ref,
                mount="application.account",
                **strict_options,
            )
        except BaseException:
            if self._cleanup_guard is not None:
                self._cleanup_guard(self)
            raise
        if self._cleanup_guard is not None and self._cleanup_guard(self):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        try:
            self._owner.start(runtime)
        except BaseException:
            if self._cleanup_guard is not None:
                self._cleanup_guard(self)
            raise
        if self._cleanup_guard is not None and self._cleanup_guard(self):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")

    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> Any:
        from bt_api_py.service_runtime_thread import OwnerOperationError

        if self._cleanup_guard is not None and self._cleanup_guard(self):
            raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")
        if self._owner is None or not operation.startswith("account."):
            raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")
        try:
            result = self._owner.invoke(operation.removeprefix("account."), dict(arguments))
        except BaseException:
            if self._cleanup_guard is not None:
                self._cleanup_guard(self)
            raise
        if self._cleanup_guard is not None and self._cleanup_guard(self):
            raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")
        return result

    @property
    def cleanup_uncertainties(self) -> int:
        if self._cleanup_guard is not None and self._cleanup_guard(self):
            return 1
        if self._owner is None or self._runtime_cleanup_unknown is None:
            return 0
        return self._owner.cleanup_uncertainties

    def close(self) -> None:
        if self._owner is not None:
            if self._cleanup_guard is not None and self._cleanup_guard(self):
                _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
            try:
                self._owner.close()
            except BaseException:
                if self._cleanup_guard is not None:
                    self._cleanup_guard(self)
                raise
            if self._cleanup_guard is not None and self._cleanup_guard(self):
                _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")


def _granted(snapshot: Any, mount: str, operations: frozenset[str]) -> bool:
    return (
        snapshot.state == "running"
        and snapshot.healthy is True
        and any(
            b.active is True
            and b.mount == mount
            and type(b.operations) is frozenset
            and operations <= b.operations
            for b in snapshot.bindings
        )
    )


def _r05_cut_has_current_coverage(progress: Any, cut: Any, coverage_verified: Any) -> bool:
    """Check the local R-05 applied/cursor/ACK proof without inventing a Gateway epoch."""
    if not isinstance(progress, Mapping) or not isinstance(cut, Mapping) or coverage_verified is not True:
        return False
    cursor = progress.get("cursor")
    acknowledged = progress.get("ack_observed")
    watermark = cut.get("fact_watermark")
    sequence = cut.get("seq")
    observed_at_ns = cut.get("observed_at_ns")
    max_age_ns = cut.get("max_age_ns")
    trading_day = cut.get("current_session_trading_day")
    return (
        progress.get("pending") is None
        and type(sequence) is int
        and sequence > 0
        and type(cursor) is int
        and type(acknowledged) is int
        and cursor == acknowledged
        and type(watermark) is int
        and watermark >= 0
        and type(observed_at_ns) is int
        and observed_at_ns > 0
        and type(max_age_ns) is int
        and max_age_ns > 0
        and type(trading_day) is str
        and re.fullmatch(r"\d{8}", trading_day) is not None
        and cursor >= watermark
        and acknowledged >= watermark
        and type(progress.get("epoch")) is str
        and bool(progress.get("epoch"))
        and progress.get("epoch") == cut.get("fact_epoch")
        and type(progress.get("gateway_journal_id")) is str
        and bool(progress.get("gateway_journal_id"))
        and progress.get("gateway_journal_id") == cut.get("journal_id")
        and cut.get("query_complete") is True
        and cut.get("receipt_complete") is True
        and cut.get("query_scope") == "ctp.futures.account_orders.v1"
        and cut.get("source_history_has_gap") is False
        and type(cut.get("owner_generation")) is str
        and bool(cut.get("owner_generation"))
        and type(cut.get("cut_ref")) is str
        and bool(cut.get("cut_ref"))
        and type(cut.get("query_manifest_digest")) is str
        and bool(cut.get("query_manifest_digest"))
        and type(cut.get("source_binding_ref")) is str
        and bool(cut.get("source_binding_ref"))
    )


def gateway_server_key_fingerprint(public_key: bytes) -> str:
    """Return canonical sha256 digest of a Gateway CURVE server public key."""
    if type(public_key) is not bytes or not public_key:
        raise ValueError("GATEWAY_SERVER_KEY_INVALID")
    return "sha256:" + hashlib.sha256(public_key).hexdigest()


def gateway_generation_reference(
    *,
    journal_id: str,
    owner_generation: str,
    service_id: str,
    instance_id: str,
    server_key_fingerprint: str,
) -> str:
    """Canonical sha256 digest binding a verified Gateway generation and peer identity."""
    fields = (journal_id, owner_generation, service_id, instance_id, server_key_fingerprint)
    if any(type(item) is not str or not item for item in fields):
        raise ValueError("GATEWAY_GENERATION_REFERENCE_INVALID")
    payload = json.dumps(
        {
            "schema": "bt_api_py.gateway_generation_reference/v1",
            "journal_id": journal_id,
            "owner_generation": owner_generation,
            "service_id": service_id,
            "instance_id": instance_id,
            "server_key_fingerprint": server_key_fingerprint,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _r05_observation_reference(
    *,
    gateway_reference: str,
    cut_ref: str,
    fact_epoch: str,
    fact_watermark: int,
    service_epoch: str,
    registration_id: str,
    verified_stream_epoch: str,
    query_manifest_digest: str,
    source_binding_ref: str,
) -> str:
    payload = json.dumps(
        {
            "schema": "bt_api_py.r05_observation_reference/v1",
            "gateway_reference": gateway_reference,
            "cut_ref": cut_ref,
            "fact_epoch": fact_epoch,
            "fact_watermark": fact_watermark,
            "service_epoch": service_epoch,
            "registration_id": registration_id,
            "verified_stream_epoch": verified_stream_epoch,
            "query_manifest_digest": query_manifest_digest,
            "source_binding_ref": source_binding_ref,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _format_portfolio_decimal(value: Any, *, ratio: bool = False) -> str | None:
    """Format an exact finite Decimal as a canonical string without floats or -0."""
    from decimal import Decimal, ROUND_HALF_UP

    if value is None or type(value) is not Decimal or not value.is_finite():
        return None
    if ratio:
        value = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def build_risk_portfolio_summary(
    facts: Any,
    *,
    instruments: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a bounded, exact-Decimal read-only portfolio exposure summary from Risk facts.

    Uses the exact same AccountSnapshot, InstrumentFacts (multiplier), and PriceFact
    objects consumed by pre-trade controls. Any missing multiplier, missing/unverified
    price, incomplete snapshot, or non-positive equity fails closed to ``None`` with
    explicit ``missing_reasons`` rather than guessing zero.
    """
    from decimal import Decimal

    unavailable = {
        "valuation_status": "UNAVAILABLE",
        "snapshot_complete": False,
        "currency": None,
        "as_of_ns": None,
        "equity": None,
        "balance": None,
        "available": None,
        "margin": None,
        "margin_ratio": None,
        "gross_notional": None,
        "priced_gross_notional": None,
        "net_notional": None,
        "long_notional": None,
        "short_notional": None,
        "gross_leverage": None,
        "net_leverage": None,
        "missing_reasons": ["ACCOUNT_SNAPSHOT_UNAVAILABLE"],
        "positions": [],
    }
    if facts is None or not callable(getattr(facts, "account_snapshot", None)):
        return unavailable
    snapshot = facts.account_snapshot()
    if snapshot is None:
        return unavailable

    reasons: list[str] = []

    def add_reason(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    snapshot_complete = bool(getattr(snapshot, "complete", False))
    if not snapshot_complete:
        add_reason("ACCOUNT_SNAPSHOT_INCOMPLETE")

    raw_positions = getattr(snapshot, "positions", None) or {}
    positions_out: list[dict[str, Any]] = []
    priced_gross = Decimal(0)
    priced_long = Decimal(0)
    priced_short = Decimal(0)
    all_valued = True
    all_fully_priced = True

    for (instrument_id, side), (today_qty, yesterday_qty) in sorted(raw_positions.items()):
        if len(positions_out) >= 256:
            add_reason("POSITIONS_TRUNCATED")
            all_valued = False
            all_fully_priced = False
            break
        total_qty = today_qty + yesterday_qty
        if total_qty == 0:
            continue
        binding = instruments.get(instrument_id)
        inst_key = getattr(binding, "key", None) if binding is not None else None
        inst_facts = (
            facts.instrument_facts(inst_key)
            if inst_key is not None and callable(getattr(facts, "instrument_facts", None))
            else None
        )
        multiplier = (
            getattr(inst_facts, "multiplier", None)
            if inst_facts is not None
            else (getattr(binding, "multiplier", None) if binding is not None else None)
        )
        price_fact = (
            facts.reference_price(inst_key)
            if inst_key is not None and callable(getattr(facts, "reference_price", None))
            else None
        )
        last_price = getattr(price_fact, "last_price", None) if price_fact is not None else None
        price_quality_ok = (
            getattr(price_fact, "quality_ok", None) if price_fact is not None else None
        )
        has_attested_ref_fn = getattr(facts, "has_attested_market_ref", None)
        has_attested_ref = bool(
            inst_key is not None
            and callable(has_attested_ref_fn)
            and has_attested_ref_fn(inst_key) is True
        )

        notional: Decimal | None = None
        if type(multiplier) is not Decimal or not multiplier.is_finite() or multiplier <= 0:
            pos_status = "NO_MULTIPLIER"
            multiplier = None
            all_valued = False
            all_fully_priced = False
            add_reason("MULTIPLIER_MISSING")
        elif type(last_price) is not Decimal or not last_price.is_finite() or last_price <= 0:
            pos_status = "NO_PRICE"
            last_price = None
            all_valued = False
            all_fully_priced = False
            add_reason("PRICE_MISSING")
        elif price_quality_ok is not True:
            all_fully_priced = False
            add_reason("PRICE_QUALITY_UNVERIFIED")
            if price_quality_ok is False and has_attested_ref:
                # Gateway attested this live PUB tick on Risk's own subscription
                # (e.g. CTP native MD lacks quality_contract_ref); expose exact
                # indicative mark-to-market while keeping valuation_status=PARTIAL
                # and pre-trade PriceFact.quality_ok strictly False.
                pos_status = "INDICATIVE"
                notional = abs(total_qty) * multiplier * last_price
                priced_gross += notional
                if side == "LONG":
                    priced_long += notional
                else:
                    priced_short += notional
            else:
                pos_status = "STALE_OR_BAD_PRICE"
                all_valued = False
        else:
            pos_status = "PRICED"
            notional = abs(total_qty) * multiplier * last_price
            priced_gross += notional
            if side == "LONG":
                priced_long += notional
            else:
                priced_short += notional

        positions_out.append(
            {
                "instrument_id": instrument_id,
                "side": side,
                "quantity": _format_portfolio_decimal(total_qty) or "0",
                "today_quantity": _format_portfolio_decimal(today_qty) or "0",
                "yesterday_quantity": _format_portfolio_decimal(yesterday_qty) or "0",
                "multiplier": _format_portfolio_decimal(multiplier),
                "last_price": _format_portfolio_decimal(last_price),
                "price_quality_ok": price_quality_ok if type(price_quality_ok) is bool else None,
                "notional": _format_portfolio_decimal(notional),
                "valuation_status": pos_status,
            }
        )

    gross_notional: Decimal | None = priced_gross if all_valued else None
    long_notional: Decimal | None = priced_long if all_valued else None
    short_notional: Decimal | None = priced_short if all_valued else None
    net_notional: Decimal | None = (priced_long - priced_short) if all_valued else None

    equity = getattr(snapshot, "equity", None)
    balance = getattr(snapshot, "balance", None)
    available = getattr(snapshot, "available", None)
    margin = getattr(snapshot, "margin", None)
    currency = getattr(snapshot, "currency", None)
    as_of_ns = getattr(snapshot, "as_of_ns", None)

    equity_positive = type(equity) is Decimal and equity.is_finite() and equity > 0
    if not equity_positive:
        add_reason("EQUITY_UNAVAILABLE")

    gross_leverage: Decimal | None = None
    net_leverage: Decimal | None = None
    margin_ratio: Decimal | None = None
    if equity_positive:
        if gross_notional is not None:
            gross_leverage = gross_notional / equity
        if net_notional is not None:
            net_leverage = net_notional / equity
        if type(margin) is Decimal and margin.is_finite() and margin >= 0:
            margin_ratio = margin / equity
        else:
            add_reason("MARGIN_UNAVAILABLE")
    elif margin is None:
        add_reason("MARGIN_UNAVAILABLE")

    valuation_status = (
        "COMPLETE"
        if (snapshot_complete and equity_positive and all_fully_priced)
        else "PARTIAL"
    )
    return {
        "valuation_status": valuation_status,
        "snapshot_complete": snapshot_complete,
        "currency": currency if type(currency) is str and currency else None,
        "as_of_ns": as_of_ns if type(as_of_ns) is int and as_of_ns >= 0 else None,
        "equity": _format_portfolio_decimal(equity),
        "balance": _format_portfolio_decimal(balance),
        "available": _format_portfolio_decimal(available),
        "margin": _format_portfolio_decimal(margin),
        "margin_ratio": _format_portfolio_decimal(margin_ratio, ratio=True),
        "gross_notional": _format_portfolio_decimal(gross_notional),
        "priced_gross_notional": _format_portfolio_decimal(priced_gross),
        "net_notional": _format_portfolio_decimal(net_notional),
        "long_notional": _format_portfolio_decimal(long_notional),
        "short_notional": _format_portfolio_decimal(short_notional),
        "gross_leverage": _format_portfolio_decimal(gross_leverage, ratio=True),
        "net_leverage": _format_portfolio_decimal(net_leverage, ratio=True),
        "missing_reasons": reasons[:32],
        "positions": positions_out,
    }


def _r05_cut_matches_current_gateway(
    progress: Any,
    cut: Any,
    current_page: Any,
    coverage_verified: Any,
    *,
    registration_id: Any,
    service_epoch: Any,
    observer_service_epoch: Any,
    verified_stream_epoch: Any,
) -> bool:
    """Bind persisted R-05 coverage to the current Gateway registration and cut.

    These are distinct domains: service_epoch binds the active Registry session,
    verified_stream_epoch is ObservationPump's account.events cursor proof, and
    owner_generation is the Gateway journal owner recorded in the order cut.
    Equality is only checked within the matching domain.
    """
    if not _r05_cut_has_current_coverage(progress, cut, coverage_verified):
        return False
    if not isinstance(current_page, Mapping):
        return False
    header = current_page.get("header")
    if not isinstance(header, Mapping):
        return False
    if (
        type(registration_id) is not str
        or not registration_id
        or type(service_epoch) is not str
        or not service_epoch
        or type(observer_service_epoch) is not str
        or not observer_service_epoch
        or observer_service_epoch != service_epoch
        or type(verified_stream_epoch) is not str
        or not verified_stream_epoch
        or type(header.get("fact_watermark")) is not int
        or header.get("fact_watermark") < 0
    ):
        return False
    return (
        current_page.get("schema") == "sdk.gateway.order_cut.current/1"
        and current_page.get("registration_id") == registration_id
        and current_page.get("service_epoch") == service_epoch
        and current_page.get("cut_ref") == cut.get("cut_ref")
        and current_page.get("query_manifest_digest") == cut.get("query_manifest_digest")
        # Compare every Gateway-header field also stored in the local Risk cut.
        # Risk seq is a local ledger sequence, not a Gateway owner generation.
        and all(
            header.get(name) == cut.get(name)
            for name in (
                "journal_id",
                "owner_generation",
                "fact_epoch",
                "fact_watermark",
                "current_session_trading_day",
                "base_sequence",
                "begin_sequence",
                "end_sequence",
                "source_prefix",
                "query_digest",
                "query_scope",
                "query_row_count",
                "cut_record_digest",
                "query_fact_digests_digest",
                "source_binding_ref",
            )
        )
        and header.get("source_history_has_gap") is False
        and cut.get("source_history_has_gap") is False
        and progress.get("gateway_journal_id") == header.get("journal_id")
        and progress.get("epoch") == header.get("fact_epoch")
        and progress.get("cursor") >= header.get("fact_watermark")
        and progress.get("ack_observed") >= header.get("fact_watermark")
    )


class RiskServiceHost:
    """Owns storage, engine, Gateway runtimes and the Risk ServiceHost on one thread."""

    def __init__(
        self,
        config: RiskServiceConfig,
        *,
        now_ns: Callable[[], int] = time.time_ns,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(config) is not RiskServiceConfig:
            raise TypeError("RISK_SERVICE_CONFIG_REQUIRED")
        if type(config.strict_admin_cancel) is not bool:
            raise RiskServiceStartError("RISK_SERVICE_CONFIG_STRICT_ADMIN_CANCEL_INVALID")
        version, upgrade = _pretrade_selection(config)
        observation = getattr(config, 'account_facts_observation', None)
        if observation is not None:
            cut_enabled = getattr(observation, 'order_cut_enabled', False)
            cut_max_age = getattr(observation, 'order_cut_max_age_seconds', None)
            cut_timeout = getattr(observation, 'order_cut_timeout_seconds', None)
            cut_page_size = getattr(observation, 'order_cut_page_size', 256)
            cut_invalid = (
                type(cut_enabled) is not bool
                or (
                    not cut_enabled
                    and (cut_max_age is not None or cut_timeout is not None or cut_page_size != 256)
                )
                or (
                    cut_enabled
                    and (
                        type(cut_max_age) not in (int, float)
                        or not math.isfinite(cut_max_age)
                        or not 0 < cut_max_age <= 86400
                        or (
                            cut_timeout is not None
                            and (
                                type(cut_timeout) not in (int, float)
                                or not math.isfinite(cut_timeout)
                                or not 0.001 <= cut_timeout <= 5.0
                            )
                        )
                        or type(cut_page_size) is not int
                        or not 1 <= cut_page_size <= 256
                    )
                )
            )
            cancel_enabled = getattr(observation, 'cancel_attempts_enabled', False)
            cancel_max_age = getattr(observation, 'cancel_attempts_max_age_seconds', None)
            cancel_timeout = getattr(observation, 'cancel_attempts_timeout_seconds', None)
            cancel_page_size = getattr(observation, 'cancel_attempts_page_size', 256)
            cancel_invalid = (
                type(cancel_enabled) is not bool
                or (cancel_enabled and not cut_enabled)
                or (
                    not cancel_enabled
                    and (cancel_max_age is not None or cancel_timeout is not None or cancel_page_size != 256)
                )
                or (
                    cancel_enabled
                    and (
                        type(cancel_max_age) not in (int, float)
                        or not math.isfinite(cancel_max_age)
                        or not 0 < cancel_max_age <= 86400
                        or (
                            cancel_timeout is not None
                            and (
                                type(cancel_timeout) not in (int, float)
                                or not math.isfinite(cancel_timeout)
                                or not 0.001 <= cancel_timeout <= 5.0
                            )
                        )
                        or type(cancel_page_size) is not int
                        or not 1 <= cancel_page_size <= 256
                    )
                )
            )
            if (
                type(observation) is not RiskAccountFactsObservation
                or version != 4 or upgrade is not None
                or type(observation.poll_interval_seconds) not in (int, float)
                or not math.isfinite(observation.poll_interval_seconds)
                or not 1 <= observation.poll_interval_seconds <= 3600
                or type(observation.timeout_seconds) not in (int, float)
                or not math.isfinite(observation.timeout_seconds)
                or not 0.001 <= observation.timeout_seconds <= 5.0
                or type(observation.page_size) is not int or not 1 <= observation.page_size <= 256
                or cut_invalid
                or cancel_invalid
            ):
                raise RiskServiceStartError('RISK_SERVICE_CONFIG_ACCOUNT_FACTS_INVALID')
        _require_supported_r04(config)
        self.config = config
        self._creator_pid = os.getpid()
        self._now_ns, self._monotonic = now_ns, monotonic
        self._stack = ExitStack()
        self._storage_owner: threading.Thread | None = None
        self._resource_owner: threading.Thread | None = None
        self._storage_init_uncertain = False
        self._storage_entry_in_flight = False
        self._storage_init_in_flight = False
        self._delegate_init_in_flight = False
        self._storage_handoff_in_flight = False
        self._uncertain_storage: tuple[Any, Any] | None = None
        self._storage_cleanup_uncertain = False
        self._cleanup_state = "ready"
        self._cleanup_uncertain = False
        self._cleanup_in_progress = False
        self._in_control_wait_pump = False
        self._lifecycle_in_progress = 0
        self._delegate_connect_in_progress = False
        self._cleanup_uncertain_resources: list[Any] = []
        self._started = False
        self.engine: Any = None
        self.ledger: Any = None
        self.service_host: Any = None
        self._service_host_start_attempted = False
        self.order_port: Any = None
        self.control: Any = None
        self.facts: Any = None
        self.pump: Any = None
        self.market: Any = None
        self.account: Any = None
        self.financial: Any = None
        self.component: Any = None
        self._runtimes: list[Any] = []
        self._observer_runtime: Any = None
        self._observer_route: Any = None
        self._observer_retry_at = 0.0
        self._market_runtime: Any = None
        self._market_retry_at = 0.0
        self._control_retry_at = 0.0
        self._account_started = False
        self._financial_runtime: Any = None
        self._financial_retry_at = 0.0
        self._financial_started = False
        self._account_facts_runtime: Any = None
        self._account_facts_coordinator: Any = None
        self._account_facts_retry_at = 0.0
        self.account_facts_status = 'DISABLED'
        self.order_cut_status = 'DISABLED'
        self.cancel_attempts_status = 'DISABLED'
        self._delegate_cleanup_unresolved: list[Any] = []
        self._next: dict[str, float] = {}
        self.stop_event = threading.Event()
        self._fault_point: str | None = None  # test only, read by start()

    # ------------------------------------------------------------------ start

    def _check_owner_identity(self) -> None:
        if os.getpid() != self._creator_pid:
            _fail("RISK_SERVICE_HOST_PROCESS_MISMATCH")
        if self._storage_owner is not None and threading.current_thread() is not self._storage_owner:
            _fail("RISK_SERVICE_HOST_THREAD_MISMATCH")
        if self._resource_owner is not None and threading.current_thread() is not self._resource_owner:
            _fail("RISK_SERVICE_HOST_THREAD_MISMATCH")

    def _check_owner(self) -> None:
        self._check_owner_identity()
        self._check_cleanup_ready()

    def _claim_resource_owner(self) -> None:
        _require_supported_r04(self.config)
        self._check_owner()
        if self._lifecycle_in_progress:
            _fail("RISK_SERVICE_HOST_LIFECYCLE_IN_PROGRESS")
        if self._resource_owner is None:
            self._resource_owner = threading.current_thread()

    def _check_cleanup_ready(self) -> None:
        # A retained storage uncertainty keeps its original identity: reporting
        # the generic finalized code would hide which commit state is unproven
        # and invite an operator to look for a different cause.
        if self._storage_init_uncertain:
            _fail("RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN")
        if self._storage_cleanup_uncertain:
            _fail("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
        if self._cleanup_uncertain or self._cleanup_in_progress or self._cleanup_state != "ready":
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        with _PROBE_GUARD:
            global_unknown = bool(_PROBE_STATE["uncertain"])
        if global_unknown:
            # Another owner's storage state is unproven in this process, so no
            # new acquisition may start here. Refusing does not implicate this
            # host: it holds no storage, so it is not retained or recorded as
            # uncertain. A separate process still reports
            # RISK_SERVICE_FENCE_HELD from the OS lock.
            _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")

    def _record_cleanup_uncertain(self, resource: Any, _reason: str) -> None:
        with _PROBE_GUARD:
            self._record_cleanup_uncertain_locked(resource, _reason)

    def _record_cleanup_uncertain_locked(self, resource: Any, reason: str) -> None:
        self._cleanup_uncertain = True
        self._cleanup_uncertain_resources.append(resource)
        _RETAINED_HOSTS.add(self)
        _PROBE_STATE["uncertain"].append((self, resource, reason))

    def _retain_if_global_unknown(self, resource: Any) -> bool:
        with _PROBE_GUARD:
            if not _PROBE_STATE["uncertain"]:
                return False
            if not self._cleanup_uncertain:
                self._record_cleanup_uncertain_locked(resource, "CONCURRENT_CLEANUP_UNKNOWN")
            elif resource is not self and not any(item is resource for item in self._cleanup_uncertain_resources):
                self._cleanup_uncertain_resources.append(resource)
            return True

    def _check_global_cleanup_ready(self, resource: Any) -> None:
        if self._retain_if_global_unknown(resource):
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        if self._cleanup_state != "ready" and resource is not self:
            self._record_cleanup_uncertain(resource, "LIFECYCLE_STATE_CHANGED")
        self._check_cleanup_ready()

    def _port_cleanup_uncertain(self) -> bool:
        resource = self.order_port if self.order_port is not None else self
        return self._cleanup_uncertain or self._retain_if_global_unknown(resource)

    def _runtime_cleanup_unknown(self) -> bool:
        with _PROBE_GUARD:
            return self._cleanup_uncertain or bool(_PROBE_STATE["uncertain"])

    def _runtime_cleanup_sticky(self, runtime: Any) -> None:
        self._record_cleanup_uncertain(runtime, "RISK_RUNTIME_CLEANUP_UNKNOWN")

    def _construct_risk_raw_runtime(self, factory: Callable[..., Any], *args: Any,
                                    **kwargs: Any) -> Any:
        _require_supported_r04(self.config)
        # The callback is registered during construction, so retain a holder
        # even if cleanup becomes uncertain before the raw object is returned.
        holder: dict[str, Any] = {"reference": None, "early_unknown": False}

        def sticky() -> None:
            reference = holder["reference"]
            raw = reference() if reference is not None else None
            if raw is None:
                holder["early_unknown"] = True
                self._runtime_cleanup_sticky(holder)
            else:
                self._runtime_cleanup_sticky(raw)

        if self._runtime_cleanup_unknown():
            self._runtime_cleanup_sticky(holder)
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        try:
            runtime = factory(*args, cleanup_unknown=self._runtime_cleanup_unknown,
                              on_cleanup_sticky=sticky, **kwargs)
        except BaseException as error:
            self._runtime_cleanup_sticky((holder, error))
            raise
        try:
            holder["reference"] = weakref.ref(runtime)
        except TypeError:
            self._runtime_cleanup_sticky(runtime)
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        if holder["early_unknown"] or self._runtime_cleanup_unknown():
            self._runtime_cleanup_sticky(runtime)
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        return runtime

    def _known_empty_for_cleanup(self) -> bool:
        return (
            type(self) is RiskServiceHost
            and self._storage_owner is None and self._resource_owner is None
            and not self._started and not self._storage_init_uncertain
            and not self._delegate_init_in_flight
            and not self._storage_cleanup_uncertain and self._uncertain_storage is None
            and type(self._stack) is ExitStack and not self._stack._exit_callbacks
            and not self._runtimes and not self._delegate_cleanup_unresolved
            and self._observer_runtime is None and self._observer_route is None
            and self._market_runtime is None and self._financial_runtime is None
            and all(value is None for value in (
                self.engine, self.ledger, self.service_host, self.order_port,
                self.control, self.facts, self.pump, self.market, self.account,
                self.financial, self.component,
            ))
        )

    def _check_storage_acquisition_ready(self) -> None:
        with _PROBE_GUARD:
            uncertain = bool(_PROBE_STATE["uncertain"])
        if uncertain and not self._cleanup_uncertain:
            self._record_cleanup_uncertain(self, "CONCURRENT_CLEANUP_UNKNOWN")
        self._check_cleanup_ready()

    def _call_lifecycle(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        self._check_global_cleanup_ready(self)
        self._lifecycle_in_progress += 1
        try:
            try:
                result = function(*args, **kwargs)
            except BaseException:
                try:
                    resource = getattr(function, "__self__", None)
                except BaseException:
                    resource = self
                self._retain_if_global_unknown(resource if resource is not None else self)
                raise
        finally:
            self._lifecycle_in_progress -= 1
        resource = self if type(result) in (type(None), bool, int, float, str, bytes) else result
        self._retain_if_global_unknown(resource)
        return result

    def _runtime_close_timeout(self) -> float:
        """Bound runtime.close() above one in-flight ZMQ RPC plus strict unregister."""
        cfg = getattr(self, "config", None)
        call_s = getattr(cfg, "call_timeout_seconds", None)
        if type(call_s) not in (int, float) or not math.isfinite(call_s) or call_s <= 0:
            return 5.0
        req_s = _gateway_request_timeout_ms(cfg) / 1000.0
        return max(5.0, float(call_s), req_s * 2.0 + 2.0)

    def _close_runtime_once(self, runtime: Any, timeout: float) -> bool:
        if self._retain_if_global_unknown(runtime):
            return False
        self._check_cleanup_ready()
        self._cleanup_in_progress = True
        try:
            try:
                result = runtime.close(timeout_seconds=timeout)
                if self._retain_if_global_unknown(runtime):
                    return False
                confirmed = runtime_cleanup_confirmed(_GuardedCleanupSnapshot(self, runtime, result)) is True
                if self._retain_if_global_unknown(runtime):
                    return False
                if self._cleanup_uncertain or not confirmed:
                    self._record_cleanup_uncertain(runtime, "RUNTIME_CLOSE_UNCONFIRMED")
                    return False
                return True
            except BaseException as error:
                _LOG.warning(
                    "Risk runtime close raised %s (code=%s)",
                    type(error).__name__,
                    getattr(error, "code", None),
                )
                self._record_cleanup_uncertain(runtime, "RUNTIME_CLOSE_EXCEPTION")
                if not isinstance(error, Exception):
                    raise
                return False
        finally:
            self._cleanup_in_progress = False

    def open_storage(self) -> None:
        _require_supported_r04(self.config)
        self._check_owner()
        if self._lifecycle_in_progress:
            _fail("RISK_SERVICE_HOST_LIFECYCLE_IN_PROGRESS")
        with _PROBE_GUARD:
            if _PROBE_STATE["active"] or _PROBE_STATE["uncertain"]:
                _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
            _PROBE_STATE["active"] = True
        self._lifecycle_in_progress = True
        try:
            self._open_storage_owned()
        finally:
            self._lifecycle_in_progress = False
            with _PROBE_GUARD:
                _PROBE_STATE["active"] = False

    def _open_storage_owned(self) -> None:
        _require_supported_r04(self.config)
        if self._cleanup_state != "ready":
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        from bt_api_risk.core.admission import AccountScope
        from bt_api_risk.gateway_delegate_journal import RiskGatewayDelegateJournal
        from bt_api_risk.pretrade import PolicyStore, PretradeLedger, RuleRegistry
        from bt_api_risk.risk_order_journal import RiskOrderJournalError
        if self._storage_init_uncertain:
            _fail("RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN")
        if self._storage_cleanup_uncertain:
            _fail("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
        config = self.config
        pretrade_version, pretrade_upgrade = _pretrade_selection(config)
        existing_identities = None
        if pretrade_version == 4:
            scope = AccountScope(config.provider, config.account_scope, config.environment)
            expected = None if pretrade_upgrade is None else (
                pretrade_upgrade.expected_database_identity, pretrade_upgrade.expected_lock_identity)
            existing_identities = _pretrade_existing(config, scope, version=3 if pretrade_upgrade else 4,
                                                     expected=expected)
        ensure_private_directory(config.storage_directory)
        self._check_storage_acquisition_ready()
        if pretrade_version == 3:
            scope = AccountScope(config.provider, config.account_scope, config.environment)
        self._check_storage_acquisition_ready()
        # Hold the same account lock across schema initialization and ownership;
        # ordinary setup failures release it, but an uncertain commit retains it.
        acquiring = ExitStack()
        journal = fence = None
        result = None
        manager = None
        entered = False
        prior_storage_owner = self._storage_owner
        try:
            try:
                manager = _open_fenced_journal(config.risk_database, scope)
            except BaseException as error:
                self._storage_cleanup_uncertain = True
                self._storage_owner = threading.current_thread()
                self._uncertain_storage = (manager, error)
                self._record_cleanup_uncertain((manager, error), "STORAGE_ENTRY_UNKNOWN")
                if (isinstance(error, RiskServiceStartError)
                        and error.code == "RISK_SERVICE_FENCE_HELD"):
                    raise RiskServiceStartError("RISK_SERVICE_STORAGE_UNAVAILABLE") from None
                raise
            try:
                self._check_storage_acquisition_ready()
            except BaseException:
                if self._cleanup_uncertain:
                    self._cleanup_uncertain_resources.append(manager)
                raise
            # Root the *exact* stack and manager before calling __enter__.
            # The entry may complete and register an exit callback before
            # Python can assign its return value or set a local flag.
            self._storage_entry_in_flight = True
            _STORAGE_INIT_ROOTS[id(self)] = (self, acquiring, manager)
            try:
                result = acquiring.enter_context(manager)
                entered = True
                journal, fence = result
                _STORAGE_INIT_ROOTS[id(self)] = (self, acquiring, manager, result, journal, fence)
                self._storage_entry_in_flight = False
            except BaseException as error:
                # A registered callback bound to this exact manager is the
                # same complete-entry proof the local flag records: the
                # interrupt can be delivered after enter_context returned but
                # before "entered = True" executes. Only the result handoff
                # did not finish, so never classify that as a failed entry and
                # never unwind the acquiring stack here. An interruption keeps
                # the in-flight mark: unwinding while the process is being
                # torn down cannot be shown to complete. The fenced manager can
                # also be interrupted inside its own entry, after the inner
                # manager was acquired but before the result/binding exists;
                # that window is equally unproven for an interruption.
                if (entered or _only_exit_callback(acquiring, manager)
                        or (type(manager) is _FencedJournalManager
                            and not isinstance(error, Exception))):
                    if isinstance(error, Exception):
                        self._storage_entry_in_flight = False
                    raise
                try:
                    entry_result = _consume_fence_entry_result(manager, error)
                    code = _fence_entry_result_code(entry_result)
                except BaseException as classification_error:
                    # The entry attempt already failed, so ExitStack registered
                    # no callback for it; what remains unproven is the manager's
                    # own provisional resources. Retain the manager together
                    # with both errors, stop classifying, and clear the
                    # in-flight mark: this attempt is now fully recorded, the
                    # rooted acquiring stack is never unwound, and the original
                    # interruption keeps propagating.
                    self._storage_cleanup_uncertain = True
                    self._storage_owner = threading.current_thread()
                    self._uncertain_storage = (manager, error)
                    self._storage_entry_in_flight = False
                    self._record_cleanup_uncertain(
                        (manager, error, classification_error), "STORAGE_ENTRY_UNKNOWN"
                    )
                    raise
                if code is None:
                    # Without an exactly consumed receipt the SDK's controlled
                    # refusal for this manager is not proven. Retain the manager
                    # and the original entry error; nothing here is unentered
                    # state that may be unwound.
                    self._storage_cleanup_uncertain = True
                    self._storage_owner = threading.current_thread()
                    self._uncertain_storage = (manager, error)
                    self._storage_entry_in_flight = False
                    self._record_cleanup_uncertain((manager, error), "STORAGE_ENTRY_UNKNOWN")
                    if (isinstance(error, RiskServiceStartError)
                            and error.code == "RISK_SERVICE_FENCE_HELD"):
                        raise RiskServiceStartError("RISK_SERVICE_STORAGE_UNAVAILABLE") from None
                else:
                    self._storage_entry_in_flight = False
                    _STORAGE_INIT_ROOTS.pop(id(self), None)
                    raise _FencedJournalEntryError(code, error.original, error.manager) from None
                raise
            self._check_storage_acquisition_ready()
            if pretrade_version == 4:
                _pretrade_existing(config, scope, version=3 if pretrade_upgrade else 4,
                                   expected=existing_identities)
            restrict_file(config.risk_database)
            self._check_storage_acquisition_ready()
            self._storage_owner = threading.current_thread()
            self._storage_init_in_flight = True
            _STORAGE_INIT_ROOTS[id(self)] = (self, acquiring, manager, result, journal, fence)
            try:
                if pretrade_upgrade is not None:
                    from bt_api_risk.pretrade.ledger import migrate_v3_to_v4
                    if (type(manager) is not _FencedJournalManager or type(manager._binding) is not tuple
                            or len(manager._binding) != 3 or manager._binding[0] is not manager._inner_manager
                            or manager._binding[1] is not journal or manager._binding[2] is not fence):
                        _fail('RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN')
                    migrate_v3_to_v4(journal, fence, fence_manager=manager._inner_manager,
                        expected_database_identity=pretrade_upgrade.expected_database_identity,
                        expected_lock_identity=pretrade_upgrade.expected_lock_identity,
                        maintenance_window=pretrade_upgrade.maintenance_window)
                    _pretrade_existing(config, scope, version=4, expected=existing_identities)
                if pretrade_version == 4:
                    self.ledger = PretradeLedger(journal, fence, schema_version=4)
                else:
                    self.ledger = PretradeLedger(journal, fence)
                if getattr(config, 'account_facts_observation', None) is not None:
                    try:
                        self.ledger.attach_existing_r05_observations_v2()
                    except RiskOrderJournalError as error:
                        if error.code == 'R05_OBSERVATION_COMMIT_UNCERTAIN':
                            raise
                        _fail('RISK_SERVICE_ACCOUNT_FACTS_V2_REQUIRED')
                    if getattr(config.account_facts_observation, 'cancel_attempts_enabled', False):
                        try:
                            self.ledger.enable_r04_coverage()
                        except RiskOrderJournalError as error:
                            if error.code == 'R05_OBSERVATION_COMMIT_UNCERTAIN':
                                raise
                            _fail('RISK_SERVICE_R04_COVERAGE_REQUIRED')
                    if getattr(config, 'bound_control_intents_enabled', False):
                        try:
                            self.ledger.attach_existing_bound_intents()
                        except RiskOrderJournalError as error:
                            if error.code == 'R05_OBSERVATION_COMMIT_UNCERTAIN':
                                raise
                            _fail('RISK_SERVICE_BOUND_INTENTS_REQUIRED')
            except RiskOrderJournalError as error:
                if error.code in ("PRETRADE_INIT_COMMIT_UNCERTAIN", "PRETRADE_MIGRATION_COMMIT_UNCERTAIN",
                                  'R05_OBSERVATION_COMMIT_UNCERTAIN'):
                    # Preserve the original acquiring stack itself. Moving its
                    # callbacks with pop_all() introduces an unrooted handoff.
                    self._uncertain_storage = (journal, fence)
                    self._storage_init_uncertain = True
                    self._record_cleanup_uncertain(
                        (acquiring, manager, journal, fence), "STORAGE_INIT_UNKNOWN"
                    )
                    _fail("RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN")
                self._storage_init_in_flight = False
                _fail("RISK_SERVICE_STORAGE_SCHEMA_INVALID")
            except RiskServiceStartError:
                if pretrade_upgrade is not None:
                    self._storage_init_uncertain = True
                    self._uncertain_storage = (journal, fence)
                    self._record_cleanup_uncertain((acquiring, manager, journal, fence), "STORAGE_INIT_UNKNOWN")
                    _fail("RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN")
                self._storage_init_in_flight = False
                raise
            except Exception:
                if pretrade_upgrade is not None:
                    self._storage_init_uncertain = True
                    self._uncertain_storage = (journal, fence)
                    self._record_cleanup_uncertain((acquiring, manager, journal, fence), "STORAGE_INIT_UNKNOWN")
                    _fail("RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN")
                # PretradeLedger.__init__ is a proof-producing constructor. It
                # wraps every unproven COMMIT/ROLLBACK/connection-close outcome
                # as RiskOrderJournalError("PRETRADE_INIT_COMMIT_UNCERTAIN") and
                # re-raises the untouched body error only after the transaction
                # context's ROLLBACK and close both returned normally (ledger
                # write start is included in the wrap decision). Any ordinary
                # exception escaping that constructor therefore proves no
                # unproven state was left behind, so the exact acquiring stack
                # is released below like a finite schema failure. BaseException
                # (KeyboardInterrupt/SystemExit) is deliberately excluded: it
                # can replace the constructor's own wrap while being raised, so
                # it remains UNKNOWN through the in-flight mark.
                self._storage_init_in_flight = False
                _fail("RISK_SERVICE_STORAGE_SCHEMA_INVALID")
            self._storage_init_in_flight = False
            self._check_storage_acquisition_ready()
            # Delegate schema initialization uses its own write transaction.
            # Its constructor does not expose a commit/rollback-and-close
            # receipt, so any interrupted or exceptional entry is UNKNOWN.
            self._delegate_init_in_flight = True
            try:
                self.delegate_storage = RiskGatewayDelegateJournal(journal, fence)
            except BaseException as error:
                self._storage_init_uncertain = True
                self._uncertain_storage = (journal, fence)
                self._record_cleanup_uncertain(
                    (acquiring, manager, journal, fence, error), "DELEGATE_INIT_UNKNOWN"
                )
                if not isinstance(error, Exception):
                    raise
                _fail("RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN")
            self._delegate_init_in_flight = False
            self._check_storage_acquisition_ready()
            self.policy_store = PolicyStore(self.ledger)
            self._check_storage_acquisition_ready()
            try:
                self.rules = RuleRegistry(
                    dict(config.trusted_rules), r04_artifacts=_r04_artifacts(config)
                )
            except RiskServiceStartError:
                raise
            except Exception:
                _fail("RISK_SERVICE_RULES_INVALID")
            self._check_storage_acquisition_ready()
            self._storage_owner = threading.current_thread()
            # Register the original stack, not a detached temporary stack. If
            # registration is interrupted its exact callbacks remain rooted.
            self._storage_handoff_in_flight = True
            self._stack.enter_context(acquiring)
            # Publication of another host's UNKNOWN and registration of this
            # active owner have one shared-lock ordering. No external callback
            # or close executes while this guard is held.
            with _PROBE_GUARD:
                if self._cleanup_uncertain or _PROBE_STATE["uncertain"]:
                    if not self._cleanup_uncertain:
                        self._record_cleanup_uncertain_locked(
                            (acquiring, manager, journal, fence), "CONCURRENT_CLEANUP_UNKNOWN"
                        )
                    raise RiskServiceStartError("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
                _STORAGE_ACTIVE_ROOTS[id(self)] = (self, acquiring, manager, journal, fence)
                _STORAGE_INIT_ROOTS.pop(id(self), None)
                self._storage_handoff_in_flight = False
        except BaseException as body_error:
            if not entered:
                # An interruption can occur after __enter__ registered the
                # callback but before the local `entered` assignment. Without
                # a complete-entry proof do not unwind or discard this stack.
                if self._storage_entry_in_flight:
                    self._storage_cleanup_uncertain = True
                    self._storage_owner = threading.current_thread()
                    self._uncertain_storage = (manager, body_error)
                    if not self._cleanup_uncertain:
                        self._record_cleanup_uncertain(
                            (acquiring, manager, result, body_error), "STORAGE_ENTRY_UNKNOWN"
                        )
                    raise RiskServiceStartError("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN") from None
                raise
            # A callback may have published another host's UNKNOWN and raised
            # before our next normal acquisition check. Preserve this exact
            # stack before attempting any cleanup in that case.
            with _PROBE_GUARD:
                retain = (self._cleanup_uncertain or bool(_PROBE_STATE["uncertain"])
                          or self._storage_entry_in_flight or self._storage_init_in_flight
                          or self._delegate_init_in_flight
                          or self._storage_handoff_in_flight)
                if retain:
                    resources = (acquiring, manager, journal, fence, result, body_error)
                    self._cleanup_uncertain = True
                    self._cleanup_uncertain_resources.append(resources)
                    _RETAINED_HOSTS.add(self)
                    _PROBE_STATE["uncertain"].append((self, resources, "STORAGE_CLEANUP_BLOCKED"))
            if retain:
                self._storage_owner = threading.current_thread()
                self._uncertain_storage = (journal, fence)
                if self._storage_entry_in_flight:
                    self._storage_cleanup_uncertain = True
                if (self._storage_init_in_flight or self._delegate_init_in_flight
                        or self._storage_handoff_in_flight):
                    self._storage_init_uncertain = True
            else:
                try:
                    if not _only_exit_callback(acquiring, manager):
                        raise RiskServiceStartError("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
                    acquiring.close()
                    if not _fenced_journal_close_confirmed(manager, journal, fence):
                        raise RiskServiceStartError("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
                    with _PROBE_GUARD:
                        if self._cleanup_uncertain or _PROBE_STATE["uncertain"]:
                            raise RiskServiceStartError("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN")
                        _STORAGE_INIT_ROOTS.pop(id(self), None)
                        _STORAGE_ACTIVE_ROOTS.pop(id(self), None)
                        self._storage_owner = prior_storage_owner
                except BaseException as exit_error:
                    self._storage_cleanup_uncertain = True
                    self._uncertain_storage = (journal, fence)
                    self._record_cleanup_uncertain((acquiring, journal, fence), "STORAGE_CLEANUP_FAILED")
                    self._cleanup_uncertain_resources.append((manager, result, body_error, exit_error))
                    raise RiskServiceStartError("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN") from None
            if self._storage_entry_in_flight:
                raise RiskServiceStartError("RISK_SERVICE_STORAGE_CLEANUP_UNCERTAIN") from None
            if (self._storage_init_in_flight or self._delegate_init_in_flight
                    or self._storage_handoff_in_flight):
                raise RiskServiceStartError("RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN") from None
            raise

    def _curve(self, identity: Any) -> Any:
        _require_supported_r04(self.config)
        from bt_api_py.service_runtime import ClientCurveKeys

        return ClientCurveKeys(
            self.config.gateway.server_public_key, identity.public_key, identity.secret_key
        )

    def _peer(self) -> WireIdentity:
        _require_supported_r04(self.config)
        gateway = self.config.gateway
        return WireIdentity(WireRole.SERVICE, gateway.principal_ref, gateway.service_id)

    def _pump_control_during_wait(self) -> None:
        """Serve only ServiceRegistry control RPCs while waiting on a Gateway runtime."""
        if (
            not self._started
            or self.stop_event.is_set()
            or self._in_control_wait_pump
            or self._cleanup_uncertain
            or self._cleanup_state != "ready"
            or self.service_host is None
            or threading.current_thread() is not self._resource_owner
            or self._runtime_cleanup_unknown()
        ):
            return
        from bt_api_py.service_host import ServiceHost

        if type(self.service_host) is not ServiceHost or self.service_host.state != "started":
            return
        self._in_control_wait_pump = True
        try:
            self.service_host.serve_control_burst(0, max_requests=16)
        except Exception:
            pass
        finally:
            self._in_control_wait_pump = False

    def _service_runtime(self, mount: str, scope: WireScope, operations: frozenset[str], *, run_id: str | None = None, extension: Callable[[], Any] | None = None, market: bool = False, strict_connection: bool = False) -> Any:
        _require_supported_r04(self.config)
        from bt_api_py.service_runtime import ServiceClientRuntime, ZmqTransportFactory
        from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

        delegate = self.config.gateway.delegate
        client_config = _gateway_client_config(self.config, mount, market=market)
        self._check_cleanup_ready()
        identity = WireIdentity(WireRole.SERVICE, delegate.principal_ref, delegate.actor_ref)
        self._check_cleanup_ready()
        curve = self._curve(delegate)
        self._check_cleanup_ready()
        factory = (ZmqTransportFactory({_GATEWAY_SECURITY: curve}, strict_single_connection=True)
                   if strict_connection else ZmqTransportFactory({_GATEWAY_SECURITY: curve}))
        self._check_cleanup_ready()
        peer = self._peer()
        self._check_cleanup_ready()
        requested = {mount: operations}
        return ThreadedServiceClientRuntime(
            lambda: self._construct_risk_raw_runtime(ServiceClientRuntime,
                client_config,
                identity=identity,
                scope_resolver=lambda _ref: scope,
                peer_resolver=lambda _service: peer,
                transport_factory=factory,
                run_id=run_id,
                requested_operations=requested,
                require_unregister_ack=True,
            ),
            owner_extension_factory=extension,
            require_unregister_ack=True,
            cleanup_unknown=self._runtime_cleanup_unknown,
            on_cleanup_sticky=self._runtime_cleanup_sticky,
            defer_sticky_cleanup_until_close=True,
            wait_pump=self._pump_control_during_wait,
            wait_pump_delay_seconds=0.5,
        )

    def _start_account_facts(self, route: Any) -> None:
        """Attach one separate strict facts consumer to the original Risk owner."""
        settings = getattr(self.config, 'account_facts_observation', None)
        if settings is None:
            return
        from bt_api_risk.pretrade.ledger import PretradeLedger

        from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
        from bt_api_py.gateway_account_facts_identity import account_facts_consumer_ref
        from bt_api_py.risk_account_facts_coordinator import RiskAccountFactsCoordinator

        peer = self._peer()
        expected = {'service_id': route.service_id, 'instance_id': route.instance_id,
            'peer_role': peer.role.value, 'peer_principal': peer.principal_ref, 'peer_actor': peer.actor_ref,
            'peer_delegate': peer.delegate_ref, 'fingerprint': hashlib.sha256(self.config.gateway.server_public_key).hexdigest()}
        consumer = account_facts_consumer_ref(principal_ref=route.identity.principal_ref,
                                               identity=route.identity, scope=route.scope)
        def preflight():
            with PretradeLedger.transaction(self.ledger) as txn:
                state = txn.r05_progress()
                binding = txn.r05_source_binding()
                if binding is not None and binding != expected:
                    raise ValueError('R05_FACTS_SOURCE_CHANGED')
                if state is not None and (state['gateway_account'] != route.scope.account_ref
                                          or state['consumer_ref'] != consumer):
                    raise ValueError('R05_FACTS_SOURCE_CHANGED')
            return state

        try:
            initial_state = self._call_lifecycle(preflight)
        except Exception as error:
            if getattr(self.ledger, '_r05_uncertain', False):
                self._record_cleanup_uncertain(self.ledger, 'ACCOUNT_FACTS_STORAGE_UNKNOWN')
            if str(error) == 'R05_FACTS_SOURCE_CHANGED':
                _fail('RISK_SERVICE_ACCOUNT_FACTS_SOURCE_MISMATCH')
            _fail('RISK_SERVICE_ACCOUNT_FACTS_UNAVAILABLE')

        cut_enabled = bool(getattr(settings, 'order_cut_enabled', False))
        cancel_enabled = bool(getattr(settings, 'cancel_attempts_enabled', False))
        operations = ACCOUNT_FACTS_OBSERVATION_OPERATIONS
        if cut_enabled:
            operations = operations | ACCOUNT_ORDER_CUT_OPERATIONS
        if cancel_enabled:
            operations = operations | ACCOUNT_CANCEL_ATTEMPT_OPERATIONS
        runtime = self._call_lifecycle(self._service_runtime, 'upstream.account', route.scope,
            operations, strict_connection=True)
        self._runtimes.append(runtime)
        self._account_facts_runtime = runtime
        self._check_cleanup_ready()
        try:
            if self._call_lifecycle(preflight) != initial_state:
                _fail('RISK_SERVICE_ACCOUNT_FACTS_UNAVAILABLE')
            snapshot = self._call_lifecycle(runtime.start, timeout_seconds=self.config.call_timeout_seconds)
            if not self._call_lifecycle(_granted, snapshot, 'upstream.account', ACCOUNT_FACTS_OBSERVATION_OPERATIONS):
                _fail('RISK_SERVICE_ACCOUNT_FACTS_GRANT_REQUIRED')
            if cut_enabled and not self._call_lifecycle(_granted, snapshot, 'upstream.account', ACCOUNT_ORDER_CUT_OPERATIONS):
                _fail('RISK_SERVICE_ORDER_CUT_GRANT_REQUIRED')
            if cancel_enabled and not self._call_lifecycle(_granted, snapshot, 'upstream.account', ACCOUNT_CANCEL_ATTEMPT_OPERATIONS):
                _fail('RISK_SERVICE_CANCEL_ATTEMPTS_GRANT_REQUIRED')
            fingerprint = hashlib.sha256(self.config.gateway.server_public_key).hexdigest()
            client = self._call_lifecycle(GatewayAccountFactsClient, runtime, route=route, peer=self._peer(),
                fingerprint=fingerprint)
            read_registration = self._call_lifecycle(client._registration, 'account.facts.read_after.v2')
            ack_registration = self._call_lifecycle(client._registration, 'account.facts.ack')
            if read_registration != ack_registration:
                _fail('RISK_SERVICE_ACCOUNT_FACTS_GRANT_REQUIRED')
            if cut_enabled:
                from bt_api_py.gateway_order_cut_client import GatewayOrderCutClient
                from bt_api_py.service_runtime_thread import BoundCallRoute

                cut_route = BoundCallRoute(
                    route.identity,
                    route.scope,
                    route.scope_ref,
                    route.service_id,
                    route.instance_id,
                    peer,
                )
                cut_client = self._call_lifecycle(GatewayOrderCutClient, runtime, route=cut_route, fingerprint=fingerprint)
                cancel_client = None
                if cancel_enabled:
                    from bt_api_py.gateway_account_cancel_attempts_client import GatewayAccountCancelAttemptsClient
                    cancel_client = self._call_lifecycle(
                        GatewayAccountCancelAttemptsClient,
                        runtime,
                        route=route,
                        peer=peer,
                        fingerprint=fingerprint,
                        allow_qualified=True,
                    )
                coordinator = self._call_lifecycle(
                    RiskAccountFactsCoordinator,
                    client,
                    self.ledger,
                    limit=settings.page_size,
                    timeout_seconds=settings.timeout_seconds,
                    order_cut_client=cut_client,
                    cut_max_age_seconds=settings.order_cut_max_age_seconds,
                    cut_limit=settings.order_cut_page_size,
                    cut_timeout_seconds=settings.order_cut_timeout_seconds or settings.timeout_seconds,
                    now_ns=self._now_ns,
                    cancel_attempts_client=cancel_client,
                    cancel_max_age_seconds=(
                        settings.cancel_attempts_max_age_seconds if cancel_enabled else 60.0
                    ),
                    cancel_limit=settings.cancel_attempts_page_size if cancel_enabled else 256,
                    cancel_timeout_seconds=(
                        (settings.cancel_attempts_timeout_seconds or settings.timeout_seconds)
                        if cancel_enabled else None
                    ),
                )
            else:
                coordinator = self._call_lifecycle(RiskAccountFactsCoordinator, client, self.ledger,
                    limit=settings.page_size, timeout_seconds=settings.timeout_seconds)
            state = self._call_lifecycle(coordinator._progress)
        except Exception as error:
            if getattr(self.ledger, '_r05_uncertain', False):
                self._record_cleanup_uncertain(self.ledger, 'ACCOUNT_FACTS_STORAGE_UNKNOWN')
            if isinstance(error, RiskServiceStartError):
                raise
            if str(error) == 'R05_FACTS_SOURCE_CHANGED':
                _fail('RISK_SERVICE_ACCOUNT_FACTS_SOURCE_MISMATCH')
            _fail('RISK_SERVICE_ACCOUNT_FACTS_UNAVAILABLE')
        self._check_cleanup_ready()
        self._account_facts_coordinator = coordinator
        self.account_facts_status = 'RECOVERY_REQUIRED' if state is not None and state['pending'] is not None else 'READY'
        if cut_enabled:
            self.order_cut_status = 'RECOVERY_REQUIRED' if self.account_facts_status == 'RECOVERY_REQUIRED' else 'READY'
        if cancel_enabled:
            self.cancel_attempts_status = 'RECOVERY_REQUIRED' if self.account_facts_status == 'RECOVERY_REQUIRED' else 'READY'

    _ACCOUNT_FACTS_ROTATION_HEADROOM = 64
    _ACCOUNT_FACTS_MIN_UNREGISTER_HEADROOM = 8
    _OBSERVER_ROTATION_HEADROOM = 64
    _MARKET_ROTATION_HEADROOM = 64
    _CONTROL_ROTATION_HEADROOM = 64
    _FINANCIAL_ROTATION_HEADROOM = 64

    def _verified_observer_stream_epoch(self) -> str | None:
        """Return only the verified account.events cursor epoch from ObservationPump."""
        pump = self.pump
        if pump is None:
            return None
        try:
            epoch = pump.verified_epoch
        except Exception:
            return None
        return epoch if type(epoch) is str and epoch else None

    def _verified_observer_service_epoch(self) -> str | None:
        """Return the observer's active Registry service_epoch after stream verification.

        This Registry epoch is a separate value from ObservationPump's account.events
        verified epoch and from the Gateway order-journal owner_generation.
        """
        if self._verified_observer_stream_epoch() is None:
            return None
        observer_rt = self._observer_runtime
        if observer_rt is None:
            return None
        try:
            obs_snap = self._call_lifecycle(observer_rt.status)
            self._check_global_cleanup_ready(observer_rt)
        except Exception:
            self._retain_if_global_unknown(observer_rt)
            return None
        if getattr(obs_snap, 'state', None) != 'running' or getattr(obs_snap, 'healthy', False) is not True:
            return None
        bindings = getattr(obs_snap, 'bindings', ()) or ()
        epochs = {
            getattr(binding, 'service_epoch', None)
            for binding in bindings
            if getattr(binding, 'mount', None) == 'upstream.account'
            and getattr(binding, 'active', False) is True
        }
        if len(epochs) != 1:
            return None
        epoch = next(iter(epochs))
        return epoch if type(epoch) is str and epoch else None

    def _verified_observer_epoch(self) -> str | None:
        """Compatibility name for historical callers; this returns service_epoch.

        New code names the Registry service epoch explicitly so it is not confused
        with ObservationPump.verified_epoch or a Gateway journal owner_generation.
        """
        return self._verified_observer_service_epoch()

    def _maintain_account_facts_runtime(
        self, now: float, *, allow_failure_replace: bool = True
    ) -> None:
        """Proactively rotate or safely reconnect the strict account-facts runtime.

        Rotation happens before the strict single-connection socket approaches
        its message-id cap so ``service.unregister`` can still be acknowledged on
        the open socket, and never while an unacknowledged fact page is pending.
        Replacement after failure or Gateway restart waits until a live
        ``pump.poll()`` confirms Gateway is reachable (so ``service.unregister``
        is not attempted into a dead outage and cannot prematurely latch
        ``_cleanup_uncertain``), and still only proceeds after
        ``_close_runtime_once`` confirms authoritative unregister cleanup.
        """
        settings = getattr(self.config, 'account_facts_observation', None)
        if settings is None or self._observer_route is None:
            return
        runtime = self._account_facts_runtime
        if runtime is None and self._account_facts_coordinator is not None:
            return
        self._check_global_cleanup_ready(runtime if runtime is not None else self)
        observer_service_epoch = self._verified_observer_service_epoch()
        needs_replace = False
        proactive_rotate = False
        if runtime is None:
            if not allow_failure_replace or observer_service_epoch is None or now < self._account_facts_retry_at:
                return
            needs_replace = True
        else:
            try:
                snap = self._call_lifecycle(runtime.status)
                self._check_global_cleanup_ready(runtime)
            except Exception:
                self._retain_if_global_unknown(runtime)
                return
            state = getattr(snap, 'state', None)
            bindings = getattr(snap, 'bindings', ()) or ()
            facts_epochs = {
                getattr(binding, 'service_epoch', None)
                for binding in bindings
                if getattr(binding, 'mount', None) == 'upstream.account'
                and type(getattr(binding, 'service_epoch', None)) is str
                and getattr(binding, 'service_epoch', None)
            }
            facts_service_epoch = next(iter(facts_epochs)) if len(facts_epochs) == 1 else None
            remaining = getattr(snap, 'min_socket_message_ids_remaining', None)
            if state in ('failed', 'closed', 'close_failed', 'stopping'):
                if self.account_facts_status in {'READY', 'APPLIED', 'EMPTY'}:
                    self.account_facts_status = 'UNKNOWN'
                    if getattr(settings, 'order_cut_enabled', False):
                        self.order_cut_status = 'UNKNOWN'
                if not allow_failure_replace or observer_service_epoch is None or now < self._account_facts_retry_at:
                    return
                needs_replace = True
            elif (
                allow_failure_replace
                and observer_service_epoch is not None
                and facts_service_epoch is not None
                and facts_service_epoch != observer_service_epoch
            ):
                if now < self._account_facts_retry_at:
                    return
                needs_replace = True
            elif (
                state == 'running'
                and remaining == 0
                and self.account_facts_status not in {'READY', 'APPLIED', 'EMPTY'}
            ):
                if not allow_failure_replace or observer_service_epoch is None or now < self._account_facts_retry_at:
                    return
                needs_replace = True
            elif (
                state == 'running'
                and self.account_facts_status == 'UNKNOWN'
                and self._account_facts_coordinator is not None
                and not getattr(self.ledger, '_r05_uncertain', False)
            ):
                if not allow_failure_replace or observer_service_epoch is None or now < self._account_facts_retry_at:
                    return
                from bt_api_risk.pretrade.ledger import PretradeLedger
                try:
                    def _read_r05_progress():
                        with PretradeLedger.transaction(self.ledger) as txn:
                            return txn.r05_progress()
                    progress = self._call_lifecycle(_read_r05_progress)
                    self._check_global_cleanup_ready(runtime)
                except Exception:
                    if getattr(self.ledger, '_r05_uncertain', False):
                        self._record_cleanup_uncertain(self.ledger, 'ACCOUNT_FACTS_STORAGE_UNKNOWN')
                    return
                if getattr(self.ledger, '_r05_uncertain', False):
                    self._record_cleanup_uncertain(self.ledger, 'ACCOUNT_FACTS_STORAGE_UNKNOWN')
                    return
                if progress is not None and progress.get('pending') is not None:
                    self.account_facts_status = 'RECOVERY_REQUIRED'
                    if getattr(settings, 'order_cut_enabled', False):
                        self.order_cut_status = 'RECOVERY_REQUIRED'
                    return
                needs_replace = True
            elif (
                state == 'running'
                and getattr(snap, 'healthy', False) is True
                and observer_service_epoch is not None
                and self.account_facts_status in {'READY', 'APPLIED', 'EMPTY'}
                and self._account_facts_coordinator is not None
            ):
                if type(remaining) is int and remaining <= self._ACCOUNT_FACTS_ROTATION_HEADROOM:
                    try:
                        progress = self._call_lifecycle(self._account_facts_coordinator._progress)
                        self._check_global_cleanup_ready(runtime)
                    except Exception:
                        return
                    if progress is None or progress.get('pending') is None:
                        needs_replace = True
                        proactive_rotate = True
        if not needs_replace:
            return
        self._account_facts_retry_at = now + max(1.0, self.config.maintain_seconds)
        if runtime is not None:
            if not self._close_runtime_once(runtime, self._runtime_close_timeout()):
                return
            if runtime in self._runtimes:
                self._runtimes.remove(runtime)
            self._account_facts_runtime = None
            self._account_facts_coordinator = None
        try:
            self._start_account_facts(self._observer_route)
            self._check_global_cleanup_ready(self._account_facts_runtime or self)
        except RiskServiceStartError as error:
            failed_rt = self._account_facts_runtime
            if failed_rt is not None:
                if self._cleanup_uncertain:
                    self._cleanup_uncertain_resources.append(failed_rt)
                elif self._close_runtime_once(failed_rt, self._runtime_close_timeout()):
                    if failed_rt in self._runtimes:
                        self._runtimes.remove(failed_rt)
                    self._account_facts_runtime = None
                    self._account_facts_coordinator = None
            _LOG.warning(
                "Risk account_facts strict runtime %s deferred: %s",
                "rotation" if proactive_rotate else "reconnect",
                error.code,
            )
            return
        self._account_facts_retry_at = 0.0
        if 'account_facts' in self._next:
            self._next['account_facts'] = now
        _LOG.info(
            "Risk account_facts strict runtime %s succeeded (status=%s)",
            "proactive rotation" if proactive_rotate else "reconnect",
            self.account_facts_status,
        )

    def _active_account_binding(
        self, runtime: Any, required_operations: frozenset[str]
    ) -> Any | None:
        """Return one healthy, exact active upstream.account binding or None."""
        if runtime is None or self._observer_route is None:
            return None
        try:
            snapshot = self._call_lifecycle(runtime.status)
            self._check_global_cleanup_ready(runtime)
        except Exception:
            self._retain_if_global_unknown(runtime)
            return None
        if (
            getattr(snapshot, "state", None) != "running"
            or getattr(snapshot, "healthy", False) is not True
            or getattr(snapshot, "thread_alive", False) is not True
            or getattr(snapshot, "cleanup_failures", None) != 0
            or getattr(snapshot, "unregister_uncertainties", None) != 0
            or getattr(snapshot, "owner_cleanup_uncertainties", None) != 0
            or getattr(snapshot, "unknown_registration_count", None) != 0
            or getattr(snapshot, "strict_unregister_ack", None) is not True
        ):
            return None
        bindings = getattr(snapshot, "bindings", None)
        if type(bindings) is not tuple:
            return None
        active = tuple(
            binding for binding in bindings
            if getattr(binding, "mount", None) == "upstream.account"
            and getattr(binding, "active", None) is True
        )
        if len(active) != 1:
            return None
        binding = active[0]
        route = self._observer_route
        try:
            expected_peer = self._peer()
            expected_fingerprint = hashlib.sha256(self.config.gateway.server_public_key).hexdigest()
        except Exception:
            return None
        operations = getattr(binding, "operations", None)
        if (
            getattr(binding, "required", None) is not True
            or type(getattr(binding, "registration_id", None)) is not str
            or not binding.registration_id
            or type(getattr(binding, "service_epoch", None)) is not str
            or not binding.service_epoch
            or getattr(binding, "service_id", None) != route.service_id
            or getattr(binding, "instance_id", None) != route.instance_id
            or getattr(binding, "scope_ref", None) != route.scope_ref
            or getattr(binding, "scope", None) != route.scope
            or getattr(binding, "authenticated_peer", None) != expected_peer
            or getattr(binding, "server_key_fingerprint", None) != expected_fingerprint
            or type(operations) is not frozenset
            or not required_operations <= operations
        ):
            return None
        return binding

    def _read_r05_order_cut_state(self, now_ns: int) -> tuple[Any, Any, Any]:
        from bt_api_risk.pretrade.ledger import PretradeLedger

        def read_state():
            with PretradeLedger.transaction(self.ledger) as txn:
                return (
                    txn.r05_progress(),
                    txn.r05_latest_order_cut_summary(),
                    txn.r05_order_cut_coverage_verified(now_ns=now_ns),
                )

        return self._call_lifecycle(read_state)

    def _order_cut_admission_ready(self) -> bool:
        """Recheck current R-05 admission for one write; never reuse a prior status sample."""
        return self._check_order_cut_admission(degrade_on_failure=True)

    def _observe_order_cut_admission(self) -> bool:
        """Return a read-only, sample-time R-05 status observation.

        This does not authorize a later write and must not change Host status.
        Each gated write separately invokes ``_order_cut_admission_ready``.
        """
        return self._check_order_cut_admission(degrade_on_failure=False)

    def _observe_order_cut_admission_proof(self) -> tuple[bool, str | None, str | None]:
        """Return ``(ready, gateway_reference, observation_reference)`` from one verified check."""
        return self._check_order_cut_admission_with_proof(degrade_on_failure=False)

    def _observe_portfolio_summary(self) -> dict[str, Any]:
        """Return a read-only, exact-Decimal exposure & leverage snapshot from Risk facts."""
        instruments = getattr(self.config, "instruments", None) or {}
        return build_risk_portfolio_summary(self.facts, instruments=instruments)

    def _check_order_cut_admission(self, *, degrade_on_failure: bool) -> bool:
        settings = getattr(self.config, "account_facts_observation", None)
        if settings is None or getattr(settings, "order_cut_enabled", False) is not True:
            return True
        ready, _gw_ref, _obs_ref = self._check_order_cut_admission_with_proof(
            degrade_on_failure=degrade_on_failure,
            include_proof=False,
        )
        return ready

    def _check_order_cut_admission_with_proof(
        self, *, degrade_on_failure: bool, include_proof: bool = True
    ) -> tuple[bool, str | None, str | None]:
        settings = getattr(self.config, "account_facts_observation", None)
        if settings is None or getattr(settings, "order_cut_enabled", False) is not True:
            return (False, None, None)

        def unavailable() -> tuple[bool, str | None, str | None]:
            if degrade_on_failure and self.order_cut_status == "CUT_APPLIED":
                self.order_cut_status = "UNKNOWN"
            return (False, None, None)

        try:
            # ready() remains only the Service RPC readiness predicate. Trading
            # admission adds the current cut and complete applied/ACKed coverage.
            if not self.ready():
                return unavailable()
            if (
                self.account_facts_status not in {"APPLIED", "EMPTY"}
                or self.order_cut_status != "CUT_APPLIED"
                or self._account_facts_runtime is None
                or self._account_facts_coordinator is None
            ):
                return unavailable()

            from bt_api_py.gateway_order_cut_client import (
                GatewayOrderCutClient,
                GatewayOrderCutCopyResult,
                GatewayOrderCutResult,
            )
            from bt_api_py.risk_account_facts_coordinator import RiskAccountFactsCoordinator

            coordinator = self._account_facts_coordinator
            if (
                type(coordinator) is not RiskAccountFactsCoordinator
                or getattr(coordinator, "_mode", None) != "READY"
                or type(getattr(coordinator, "_cut_client", None)) is not GatewayOrderCutClient
            ):
                return unavailable()
            self._call_lifecycle(coordinator._check)
            cut_client = coordinator._cut_client

            stream_epoch_before = self._verified_observer_stream_epoch()
            observer_binding_before = self._active_account_binding(
                self._observer_runtime, OBSERVER_OPERATIONS
            )
            facts_binding_before = self._active_account_binding(
                self._account_facts_runtime,
                ACCOUNT_FACTS_OBSERVATION_OPERATIONS | ACCOUNT_ORDER_CUT_OPERATIONS,
            )
            if (
                stream_epoch_before is None
                or observer_binding_before is None
                or facts_binding_before is None
                or observer_binding_before.service_epoch != facts_binding_before.service_epoch
            ):
                return unavailable()

            runtime_snapshot = self._call_lifecycle(self._account_facts_runtime.status)
            remaining = getattr(runtime_snapshot, "min_socket_message_ids_remaining", None)
            # current() is the only wire call here. validate_copy_issued() does
            # two local token/binding/copy checks and consumes no socket IDs.
            if (
                type(remaining) is not int
                or remaining < self._ACCOUNT_FACTS_MIN_UNREGISTER_HEADROOM + 1
            ):
                return unavailable()

            now_ns = self._call_lifecycle(self._now_ns)
            if type(now_ns) is not int or now_ns <= 0:
                return unavailable()
            progress_before, cut_before, coverage_before = self._read_r05_order_cut_state(now_ns)
            if not _r05_cut_has_current_coverage(progress_before, cut_before, coverage_before):
                return unavailable()

            timeout = (
                getattr(settings, "order_cut_timeout_seconds", None)
                or settings.timeout_seconds
            )
            issued = self._call_lifecycle(cut_client.current, timeout_seconds=timeout)
            if (
                type(issued) is not GatewayOrderCutResult
                or issued.outcome != "OBSERVED"
                or issued.code is not None
            ):
                return unavailable()
            copied = self._call_lifecycle(
                cut_client.validate_copy_issued, issued, timeout_seconds=timeout
            )
            if (
                type(copied) is not GatewayOrderCutCopyResult
                or copied.outcome != "OBSERVED"
                or copied.code is not None
            ):
                return unavailable()

            now_after = self._call_lifecycle(self._now_ns)
            if type(now_after) is not int or now_after <= 0:
                return unavailable()
            progress_after, cut_after, coverage_after = self._read_r05_order_cut_state(now_after)
            stream_epoch_after = self._verified_observer_stream_epoch()
            observer_binding_after = self._active_account_binding(
                self._observer_runtime, OBSERVER_OPERATIONS
            )
            facts_binding_after = self._active_account_binding(
                self._account_facts_runtime,
                ACCOUNT_FACTS_OBSERVATION_OPERATIONS | ACCOUNT_ORDER_CUT_OPERATIONS,
            )
            self._call_lifecycle(coordinator._check)
            self._check_cleanup_ready()
            if (
                stream_epoch_after != stream_epoch_before
                or observer_binding_after != observer_binding_before
                or facts_binding_after != facts_binding_before
                or progress_after != progress_before
                or cut_after != cut_before
                or getattr(coordinator, "_mode", None) != "READY"
            ):
                return unavailable()
            matches = _r05_cut_matches_current_gateway(
                progress_after,
                cut_after,
                copied.page,
                coverage_after,
                registration_id=facts_binding_after.registration_id,
                service_epoch=facts_binding_after.service_epoch,
                observer_service_epoch=observer_binding_after.service_epoch,
                verified_stream_epoch=stream_epoch_after,
            )
            if not matches:
                return unavailable()
            if not include_proof:
                return (True, None, None)
            gateway_cfg = getattr(self.config, "gateway", None)
            gw_ref = gateway_generation_reference(
                journal_id=cut_after["journal_id"],
                owner_generation=cut_after["owner_generation"],
                service_id=gateway_cfg.service_id,
                instance_id=gateway_cfg.instance_id,
                server_key_fingerprint=gateway_server_key_fingerprint(
                    gateway_cfg.server_public_key
                ),
            )
            obs_ref = _r05_observation_reference(
                gateway_reference=gw_ref,
                cut_ref=cut_after["cut_ref"],
                fact_epoch=cut_after["fact_epoch"],
                fact_watermark=cut_after["fact_watermark"],
                service_epoch=facts_binding_after.service_epoch,
                registration_id=facts_binding_after.registration_id,
                verified_stream_epoch=stream_epoch_after,
                query_manifest_digest=cut_after["query_manifest_digest"],
                source_binding_ref=cut_after["source_binding_ref"],
            )
            return (True, gw_ref, obs_ref)
        except Exception:
            if degrade_on_failure and getattr(self.ledger, "_r05_uncertain", False):
                self._record_cleanup_uncertain(self.ledger, "ACCOUNT_FACTS_STORAGE_UNKNOWN")
            return unavailable()

    def _poll_account_facts(self) -> None:
        if self.account_facts_status not in {'READY', 'APPLIED', 'EMPTY'}:
            return
        facts_rt = self._account_facts_runtime
        if facts_rt is not None:
            from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
            if type(facts_rt) is ThreadedServiceClientRuntime:
                try:
                    remaining = facts_rt.status().min_socket_message_ids_remaining
                except Exception:
                    remaining = None
                if type(remaining) is int and remaining <= self._ACCOUNT_FACTS_MIN_UNREGISTER_HEADROOM:
                    return
        settings = getattr(self.config, 'account_facts_observation', None)
        cut_enabled = bool(settings is not None and getattr(settings, 'order_cut_enabled', False))
        cancel_enabled = bool(settings is not None and getattr(settings, 'cancel_attempts_enabled', False))
        def _note_unknown_backoff(reason: str, code: object = None) -> None:
            try:
                now = self._monotonic()
                if _finite_poll_value(now):
                    backoff = now + max(1.0, self.config.maintain_seconds)
                    if _finite_poll_value(backoff):
                        self._account_facts_retry_at = max(self._account_facts_retry_at, backoff)
            except Exception:
                pass
            _LOG.warning("Risk account_facts poll transitioned to UNKNOWN (%s code=%s)", reason, code)

        try:
            result = self._account_facts_coordinator.poll_once()
            status = result.status if type(result.status) is str else 'UNKNOWN'
            self.account_facts_status = status if status in {'APPLIED', 'EMPTY', 'RECOVERY_REQUIRED'} else 'UNKNOWN'
            if self.account_facts_status == 'UNKNOWN':
                _note_unknown_backoff('facts_poll', getattr(result, 'code', None))
            if cut_enabled:
                steps = 1
                while (
                    self.account_facts_status == 'APPLIED'
                    and getattr(self._account_facts_coordinator, '_last_has_more', False)
                    and steps < 64
                ):
                    result = self._account_facts_coordinator.poll_once()
                    status = result.status if type(result.status) is str else 'UNKNOWN'
                    self.account_facts_status = (
                        status if status in {'APPLIED', 'EMPTY', 'RECOVERY_REQUIRED'} else 'UNKNOWN'
                    )
                    if self.account_facts_status == 'UNKNOWN':
                        _note_unknown_backoff('facts_drain', getattr(result, 'code', None))
                        break
                    steps += 1
                if self.account_facts_status in {'APPLIED', 'EMPTY'}:
                    cut_result = self._account_facts_coordinator.poll_order_cut_once()
                    cut_status = cut_result.status if type(cut_result.status) is str else 'UNKNOWN'
                    allowed_cut = {
                        'CUT_APPLIED', 'CUT_GAP', 'CUT_UNAVAILABLE',
                        'RECOVERY_REQUIRED', 'NO_FACTS_STATE', 'DISABLED',
                    }
                    self.order_cut_status = cut_status if cut_status in allowed_cut else 'UNKNOWN'
                    if self.order_cut_status == 'RECOVERY_REQUIRED':
                        self.account_facts_status = 'RECOVERY_REQUIRED'
                    elif (
                        self.order_cut_status == 'UNKNOWN'
                        and getattr(self._account_facts_coordinator, '_mode', 'READY') != 'READY'
                    ):
                        self.account_facts_status = 'UNKNOWN'
                        _note_unknown_backoff('order_cut', getattr(cut_result, 'code', None))
                    elif (
                        self.order_cut_status == 'CUT_APPLIED'
                        and type(getattr(cut_result, 'fact_watermark', None)) is int
                        and type(getattr(cut_result, 'cursor', None)) is int
                        and cut_result.fact_watermark > cut_result.cursor
                    ):
                        catchup = 0
                        while catchup < 64 and self.account_facts_status in {'APPLIED', 'EMPTY'}:
                            next_res = self._account_facts_coordinator.poll_once()
                            next_status = next_res.status if type(next_res.status) is str else 'UNKNOWN'
                            self.account_facts_status = (
                                next_status if next_status in {'APPLIED', 'EMPTY', 'RECOVERY_REQUIRED'} else 'UNKNOWN'
                            )
                            if self.account_facts_status == 'UNKNOWN':
                                _note_unknown_backoff('facts_catchup', getattr(next_res, 'code', None))
                                break
                            catchup += 1
                            if (
                                self.account_facts_status != 'APPLIED'
                                or next_res.cursor >= cut_result.fact_watermark
                                or not getattr(self._account_facts_coordinator, '_last_has_more', False)
                            ):
                                break
                    if cancel_enabled and self.order_cut_status == 'CUT_APPLIED':
                        cancel_steps = 0
                        while cancel_steps < 64:
                            c_res = self._account_facts_coordinator.poll_cancel_attempts_once()
                            c_status = c_res.status if type(c_res.status) is str else 'UNKNOWN'
                            allowed_cancel = {'APPLIED', 'EMPTY', 'GAP', 'NO_FACTS_STATE', 'DISABLED'}
                            self.cancel_attempts_status = c_status if c_status in allowed_cancel else 'UNKNOWN'
                            cancel_steps += 1
                            if (
                                self.cancel_attempts_status != 'APPLIED'
                                or not getattr(self._account_facts_coordinator, '_cancel_last_has_more', False)
                            ):
                                break
                elif self.account_facts_status == 'RECOVERY_REQUIRED':
                    self.order_cut_status = 'RECOVERY_REQUIRED'
                    if cancel_enabled:
                        self.cancel_attempts_status = 'RECOVERY_REQUIRED'
                else:
                    self.order_cut_status = 'UNKNOWN'
                    if cancel_enabled:
                        self.cancel_attempts_status = 'UNKNOWN'
                if self.order_cut_status == 'CUT_APPLIED':
                    try:
                        applied_at_ns = self._call_lifecycle(self._now_ns)
                        progress, cut, coverage = self._read_r05_order_cut_state(applied_at_ns)
                        locally_applied = (
                            type(applied_at_ns) is int
                            and applied_at_ns > 0
                            and type(getattr(cut_result, 'fact_watermark', None)) is int
                            and cut is not None
                            and cut.get('fact_watermark') == cut_result.fact_watermark
                            and _r05_cut_has_current_coverage(progress, cut, coverage)
                        )
                    except Exception:
                        locally_applied = False
                    if not locally_applied:
                        self.order_cut_status = 'UNKNOWN'
                        _note_unknown_backoff('order_cut_applied_coverage')
        except Exception:
            self.account_facts_status = 'UNKNOWN'
            if cut_enabled:
                self.order_cut_status = 'UNKNOWN'
            if cancel_enabled:
                self.cancel_attempts_status = 'UNKNOWN'
            _note_unknown_backoff('exception')
        if getattr(self.ledger, '_r05_uncertain', False):
            self._record_cleanup_uncertain(self.ledger, 'ACCOUNT_FACTS_STORAGE_UNKNOWN')

    def account_facts_observation_status(self) -> str:
        """Return only the local bounded consumer state, never source authority."""
        self._check_owner_identity()
        status = self.account_facts_status
        return status if status in {'DISABLED', 'READY', 'APPLIED', 'EMPTY', 'UNKNOWN', 'RECOVERY_REQUIRED'} else 'UNKNOWN'

    def order_cut_observation_status(self) -> str:
        """Return only the local bounded order-cut consumer state, never source authority."""
        self._check_owner_identity()
        status = self.order_cut_status
        return status if status in {
            'DISABLED', 'READY', 'CUT_APPLIED', 'CUT_GAP',
            'CUT_UNAVAILABLE', 'RECOVERY_REQUIRED', 'NO_FACTS_STATE', 'UNKNOWN',
        } else 'UNKNOWN'

    def cancel_attempts_observation_status(self) -> str:
        """Return only the local bounded cancel-attempts consumer state."""
        self._check_owner_identity()
        status = self.cancel_attempts_status
        return status if status in {
            'DISABLED', 'READY', 'APPLIED', 'EMPTY', 'GAP', 'NO_FACTS_STATE', 'RECOVERY_REQUIRED', 'UNKNOWN',
        } else 'UNKNOWN'

    def _connect_delegate(self, route: Any) -> _DelegateHandle:
        _require_supported_r04(self.config)
        self._check_owner()
        self._check_global_cleanup_ready(self)
        if self._delegate_connect_in_progress:
            _fail("RISK_SERVICE_HOST_LIFECYCLE_IN_PROGRESS")
        self._delegate_connect_in_progress = True
        try:
            return self._connect_delegate_owned(route)
        finally:
            self._delegate_connect_in_progress = False

    def _connect_delegate_owned(self, route: Any) -> _DelegateHandle:
        _require_supported_r04(self.config)
        from bt_api_py.gateway_delegate_risk_host import GatewayRiskDelegateHost
        from bt_api_py.gateway_delegate_service_client import GatewayDelegateServiceClient

        if self._delegate_cleanup_unresolved:
            self._record_cleanup_uncertain(self._delegate_cleanup_unresolved[0], "DELEGATE_UNRESOLVED")
            raise RiskServiceStartError("RISK_SERVICE_GATEWAY_DELEGATE_CLEANUP_UNRESOLVED")
        runtime = self._call_lifecycle(self._service_runtime,
            "upstream.direct_write", route.scope, DELEGATE_OPERATIONS, run_id=route.scope.run_id
        )
        self._check_global_cleanup_ready(runtime)
        if self._cleanup_uncertain or self._cleanup_state != "ready":
            if self._cleanup_uncertain:
                self._cleanup_uncertain_resources.append(runtime)
            else:
                self._record_cleanup_uncertain(runtime, "DELEGATE_FACTORY_STATE_CHANGED")
            self._check_cleanup_ready()
        try:
            snapshot = self._call_lifecycle(runtime.start, timeout_seconds=self.config.call_timeout_seconds)
            self._check_global_cleanup_ready(runtime)
            granted = self._call_lifecycle(_granted, snapshot, "upstream.direct_write", DELEGATE_OPERATIONS)
            self._check_global_cleanup_ready(runtime)
            if not granted:
                raise RiskServiceStartError("RISK_SERVICE_GATEWAY_DELEGATE_UNAVAILABLE")
            host = self._call_lifecycle(GatewayRiskDelegateHost, self.delegate_storage, route=route)
            self._check_global_cleanup_ready(runtime)
            client = self._call_lifecycle(GatewayDelegateServiceClient, runtime, route=route, host=host)
            self._check_global_cleanup_ready(runtime)
        except BaseException:
            if self._cleanup_uncertain:
                self._cleanup_uncertain_resources.append(runtime)
            elif not self._close_runtime_once(runtime, self._runtime_close_timeout()):
                self._delegate_cleanup_unresolved.append(runtime)
            raise
        return _DelegateHandle(
            runtime, client, host, cleanup_guard=self._retain_if_global_unknown,
            snapshot_guard=lambda resource, snapshot: _GuardedCleanupSnapshot(self, resource, snapshot),
        )

    def build_engine(self) -> None:
        _require_supported_r04(self.config)
        self._claim_resource_owner()
        from bt_api_risk.pretrade import PretradeEngine

        from bt_api_py.risk_service_gateway import (
            DelegateOrderPort,
            GatewayControlAdapter,
            RiskFactsCache,
        )
        from bt_api_py.service_runtime_thread import BoundCallRoute

        config, gateway = self.config, self.config.gateway
        max_age = config.account_snapshot_max_age_seconds
        financial_age = config.strategy_financial_max_age_seconds
        self.facts = self._call_lifecycle(RiskFactsCache,
            config.instruments,
            now_ns=self._now_ns,
            account_max_age_ns=None if max_age is None else int(max_age * 1_000_000_000),
            financial_max_age_ns=None
            if financial_age is None
            else int(financial_age * 1_000_000_000),
        )
        self._check_cleanup_ready()
        identity = self._call_lifecycle(WireIdentity,
            WireRole.SERVICE, gateway.delegate.principal_ref, gateway.delegate.actor_ref
        )
        self._check_cleanup_ready()

        def instrument_for(venue: object, native: object) -> Any:
            binding = config.instrument_for(venue, native)
            return None if binding is None else binding.key

        self.instrument_for = instrument_for
        self.order_port = self._call_lifecycle(DelegateOrderPort,
            account_scope=config.account_scope,
            execution_domain_ref=config.execution_domain_ref,
            identity=identity,
            service_id=gateway.service_id,
            instance_id=gateway.instance_id,
            scope_ref=gateway.scope_ref,
            grant_revisions=gateway.grant_revisions,
            instrument_for=instrument_for,
            connect=self._connect_delegate,
            timeout_seconds=config.call_timeout_seconds,
            retry_seconds=config.maintain_seconds,
            monotonic=self._monotonic,
            on_cleanup_uncertain=self._record_cleanup_uncertain,
            is_cleanup_uncertain=self._port_cleanup_uncertain,
        )
        self._check_cleanup_ready()
        self.control_runtime = self._call_lifecycle(self._control_runtime)
        self._check_cleanup_ready()
        control_identity = self._call_lifecycle(
            WireIdentity,
            WireRole.APPLICATION_CLIENT,
            gateway.control.principal_ref,
            gateway.control.actor_ref,
        )
        self._check_cleanup_ready()
        control_scope = self._call_lifecycle(
            WireScope, config.execution_domain_ref, account_ref=config.account_scope
        )
        self._check_cleanup_ready()
        control_route = self._call_lifecycle(
            BoundCallRoute,
            control_identity,
            control_scope,
            gateway.scope_ref,
            gateway.service_id,
            gateway.instance_id,
            self._peer(),
        )
        self._check_cleanup_ready()
        self.control = self._call_lifecycle(GatewayControlAdapter,
            self.control_runtime,
            mount="application.direct_write",
            service_id=gateway.service_id,
            instance_id=gateway.instance_id,
            scope_ref=gateway.scope_ref,
            account_ref=config.account_scope,
            timeout_seconds=config.call_timeout_seconds,
            route=control_route,
            server_key_fingerprint=hashlib.sha256(gateway.server_public_key).hexdigest(),
            control_intent_enabled=getattr(config, "bound_control_intents_enabled", False),
            control_intent_continuity_enabled=getattr(
                config, "control_intent_continuity_enabled", False
            ),
        )
        self._check_cleanup_ready()
        try:
            self.engine = self._call_lifecycle(PretradeEngine,
                self.ledger,
                self.policy_store,
                self.rules,
                self.facts,
                self.order_port
                if self._fault_point is None
                else _FaultInjectingOrderPort(self.order_port, self._fault_point),
                self.control,
                account_scope=config.account_scope,
                route=config.route,
                now_ns=self._now_ns,
                reservation_ttl_seconds=config.reservation_ttl_seconds,
                actor_principal=gateway.delegate.principal_ref,
                dispatch_recheck_seconds=config.dispatch_recheck_seconds,
                dispatch_recheck_limit=config.dispatch_recheck_limit,
                parent_contract_enabled=getattr(config, "parent_contract_enabled", False),
            )
        except Exception:
            _fail("RISK_SERVICE_ENGINE_INVALID")
        self._check_cleanup_ready()

    def _application_runtime(self, requested: Mapping[str, frozenset[str]], extension: Any = None) -> Any:
        _require_supported_r04(self.config)
        from bt_api_py.service_runtime import ServiceClientRuntime, ZmqTransportFactory
        from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

        config, gateway = self.config, self.config.gateway
        client_config = _control_client_config(config, list(requested))
        self._check_cleanup_ready()
        identity = WireIdentity(
            WireRole.APPLICATION_CLIENT, gateway.control.principal_ref, gateway.control.actor_ref
        )
        self._check_cleanup_ready()
        scope = WireScope(config.execution_domain_ref, account_ref=config.account_scope)
        self._check_cleanup_ready()
        curve = self._curve(gateway.control)
        self._check_cleanup_ready()
        factory = ZmqTransportFactory({_GATEWAY_SECURITY: curve})
        self._check_cleanup_ready()
        peer = self._peer()
        self._check_cleanup_ready()
        frozen = dict(requested)
        return ThreadedServiceClientRuntime(
            lambda: self._construct_risk_raw_runtime(ServiceClientRuntime,
                client_config,
                identity=identity,
                scope_resolver=lambda _ref: scope,
                peer_resolver=lambda _service: peer,
                transport_factory=factory,
                requested_operations=frozen,
                require_unregister_ack=True,
            ),
            owner_extension_factory=extension,
            require_unregister_ack=True,
            cleanup_unknown=self._runtime_cleanup_unknown,
            on_cleanup_sticky=self._runtime_cleanup_sticky,
            defer_sticky_cleanup_until_close=True,
            wait_pump=self._pump_control_during_wait,
            wait_pump_delay_seconds=0.5,
        )

    def _control_runtime(self) -> Any:
        _require_supported_r04(self.config)
        from bt_api_py.risk_service_gateway import (
            CONTROL_INTENT_CONTINUITY_OPERATIONS as CONTROL_INTENT_CONTINUITY_OPERATIONS_FOR_RISK,
            CONTROL_INTENT_OPERATIONS as CONTROL_INTENT_OPERATIONS_FOR_RISK,
        )
        from bt_api_py.risk_service_gateway import CONTROL_OPERATIONS as CONTROL_OPERATIONS_FOR_RISK

        config, gateway = self.config, self.config.gateway
        mounts = ["application.direct_write"]
        control_ops = (
            CONTROL_INTENT_CONTINUITY_OPERATIONS_FOR_RISK
            if getattr(config, "control_intent_continuity_enabled", False)
            else (
                CONTROL_INTENT_OPERATIONS_FOR_RISK
                if getattr(config, "bound_control_intents_enabled", False)
                else CONTROL_OPERATIONS_FOR_RISK
            )
        )
        requested = {"application.direct_write": control_ops}
        extension = None
        if gateway.account_snapshot is not None:
            from bt_api_base.account_protocol import AccountResourceKey

            from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings

            snap = gateway.account_snapshot
            # Bound by the Gateway's hashed account reference only: this process never
            # holds the raw account or sub-account number (plan 64 section 6.1).
            settings = GatewayAccountClientSettings(
                expected_account=None,
                required_coverage=tuple(AccountResourceKey(a, b) for a, b in snap.resources),
                requested_operations=frozenset({"account.snapshot.typed"}),
                expected_account_key_id=config.account_scope,
            )
            self._check_cleanup_ready()
            mounts.append("application.account")
            requested["application.account"] = frozenset({"account.snapshot.typed"})

            def extension():
                return _AccountOwnerExtension(
                    settings, account_ref=config.account_scope, scope_ref=gateway.scope_ref,
                    cleanup_guard=self._retain_if_global_unknown,
                    runtime_cleanup_unknown=self._runtime_cleanup_unknown,
                    on_cleanup_sticky=self._runtime_cleanup_sticky,
                )

        return self._application_runtime(requested, extension)

    def start_gateway(self) -> None:
        """Start the account-wide Gateway runtimes and check the grants this config needs."""
        _require_supported_r04(self.config)
        self._claim_resource_owner()
        from bt_api_py.gateway_account_observer_client import (
            GatewayAccountObserverClient,
            GatewayAccountObserverRoute,
        )
        from bt_api_py.risk_service_gateway import (
            CONTROL_INTENT_CONTINUITY_OPERATIONS as CONTROL_INTENT_CONTINUITY_OPERATIONS_FOR_RISK,
            CONTROL_INTENT_OPERATIONS as CONTROL_INTENT_OPERATIONS_FOR_RISK,
        )
        from bt_api_py.risk_service_gateway import CONTROL_OPERATIONS as CONTROL_OPERATIONS_FOR_RISK
        from bt_api_py.risk_service_gateway import ObservationPump, payload_reader

        config, gateway = self.config, self.config.gateway
        timeout = config.call_timeout_seconds
        account_scope = self._call_lifecycle(WireScope, config.execution_domain_ref, account_ref=config.account_scope)
        self._check_cleanup_ready()
        observer_runtime = self._call_lifecycle(self._service_runtime,
            "upstream.account", account_scope, OBSERVER_OPERATIONS
        )
        self._runtimes.append(observer_runtime)
        self._check_cleanup_ready()
        try:
            snapshot = self._call_lifecycle(observer_runtime.start, timeout_seconds=timeout)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_OBSERVER_UNAVAILABLE")
        self._check_cleanup_ready()
        granted = self._call_lifecycle(_granted, snapshot, "upstream.account", OBSERVER_OPERATIONS)
        self._check_cleanup_ready()
        if not granted:
            _fail("RISK_SERVICE_GATEWAY_OBSERVER_UNAVAILABLE")
        delegate = gateway.delegate
        identity = self._call_lifecycle(WireIdentity, WireRole.SERVICE, delegate.principal_ref, delegate.actor_ref)
        self._check_cleanup_ready()
        observer_route = self._call_lifecycle(GatewayAccountObserverRoute,
            "upstream.account", gateway.service_id, gateway.instance_id, gateway.scope_ref,
            account_scope, identity,
        )
        self._check_cleanup_ready()
        observer = self._call_lifecycle(GatewayAccountObserverClient, observer_runtime, route=observer_route,
            peer=self._peer(), fingerprint=hashlib.sha256(gateway.server_public_key).hexdigest())
        self._check_cleanup_ready()
        self._observer_runtime = observer_runtime
        self._observer_route = observer_route
        self._start_account_facts(observer_route)
        self._runtimes.append(self.control_runtime)
        try:
            snapshot = self._call_lifecycle(self.control_runtime.start, timeout_seconds=timeout)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_CONTROL_UNAVAILABLE")
        self._check_cleanup_ready()
        required_control_ops = (
            CONTROL_INTENT_CONTINUITY_OPERATIONS_FOR_RISK
            if getattr(config, "control_intent_continuity_enabled", False)
            else (
                CONTROL_INTENT_OPERATIONS_FOR_RISK
                if getattr(config, "bound_control_intents_enabled", False)
                else CONTROL_OPERATIONS_FOR_RISK
            )
        )
        granted = self._call_lifecycle(_granted, snapshot, "application.direct_write", required_control_ops)
        self._check_cleanup_ready()
        if not granted:
            _fail("RISK_SERVICE_GATEWAY_CONTROL_UNAVAILABLE")
        if gateway.account_snapshot is not None:
            from bt_api_py.gateway_strategy_account import GatewayStrategyAccountClient
            from bt_api_py.risk_service_gateway import RiskAccountSnapshotSource

            granted = self._call_lifecycle(_granted, snapshot, "application.account", frozenset({"account.snapshot.typed"}))
            self._check_cleanup_ready()
            if not granted:
                _fail("RISK_SERVICE_GATEWAY_ACCOUNT_UNAVAILABLE")
            account_client = self._call_lifecycle(GatewayStrategyAccountClient, self.control_runtime)
            self._check_cleanup_ready()
            self.account = self._call_lifecycle(RiskAccountSnapshotSource,
                account_client,
                self.facts,
                instrument_ids={
                    b.key: b.instrument_id
                    for b in config.instruments.values()
                },
                timeout_seconds=timeout,
            )
            self._account_started = True
            self._check_cleanup_ready()
        if config.strategy_financial_seconds is not None:
            self._start_strategy_financial()
            self._check_cleanup_ready()
        if gateway.market is not None:
            self._start_market()
            self._check_cleanup_ready()
        intent_payload = self._call_lifecycle(payload_reader, self.delegate_storage)
        self._check_cleanup_ready()
        self.pump = self._call_lifecycle(ObservationPump,
            self.engine,
            observer,
            self.facts,
            risk_principal=delegate.principal_ref,
            intent_payload=intent_payload,
            timeout_seconds=timeout,
        )
        self._check_cleanup_ready()

    def _gateway_endpoint_listening(self) -> bool:
        """Non-mutating TCP reachability check before sending strict unregister."""
        import socket
        from urllib.parse import urlsplit

        gateway = getattr(self.config, "gateway", None)
        endpoint = getattr(gateway, "rpc_endpoint", None) or getattr(gateway, "endpoint", None)
        if not isinstance(endpoint, str) or not endpoint.startswith("tcp://"):
            return True
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
        if not host or port is None:
            return True
        try:
            with socket.create_connection((host, port), timeout=0.15):
                return True
        except OSError:
            return False

    def _reconnect_observer(self) -> None:
        """Replace a stopped, degraded, or near-cap observer only after proven cleanup.

        ``GatewayAccountObserverClient`` pins ``call_bound`` to the CURVE
        socket binding established during ``service.register``. When the socket
        approaches the 4096-message cap (or retires after a transient RPC
        timeout), proactively rotate or replace the observer registration after
        proven strict unregister cleanup so observation continuity recovers to
        ``VERIFIED`` via replay-from-zero instead of remaining stuck
        ``UNVERIFIED``.
        """
        _require_supported_r04(self.config)
        self._check_owner_identity()
        if self._lifecycle_in_progress:
            _fail("RISK_SERVICE_HOST_LIFECYCLE_IN_PROGRESS")
        from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient
        from bt_api_py.service_runtime_thread import BindingSnapshot, ThreadedServiceClientRuntime

        runtime = self._observer_runtime
        self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if self.pump is None or self._observer_route is None:
            return
        now = self._call_lifecycle(self._monotonic)
        self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if now < self._observer_retry_at:
            return
        proactive_rotate = False
        binding_auth_lost = False
        old_epoch: str | None = None
        if runtime is not None:
            try:
                snap = self._call_lifecycle(runtime.status)
                self._check_global_cleanup_ready(runtime)
            except Exception:
                self._retain_if_global_unknown(runtime)
                return
            self._check_global_cleanup_ready(runtime)
            state = getattr(snap, "state", None)
            bindings = getattr(snap, "bindings", None)
            remaining = getattr(snap, "min_socket_message_ids_remaining", None)
            if isinstance(bindings, (tuple, list)):
                old_epochs = {
                    getattr(b, "service_epoch", None)
                    for b in bindings
                    if getattr(b, "mount", None) == "upstream.account"
                    and getattr(b, "active", False) is True
                    and type(getattr(b, "service_epoch", None)) is str
                    and getattr(b, "service_epoch", None)
                }
                if len(old_epochs) == 1:
                    old_epoch = next(iter(old_epochs))
            stopped_or_failed = state in ("failed", "closed", "close_failed", "stopping")
            unhealthy_running = state == "running" and getattr(snap, "healthy", True) is False
            low_budget = (
                state == "running"
                and type(remaining) is int
                and remaining <= self._OBSERVER_ROTATION_HEADROOM
            )
            observer_bindings = (
                [
                    b for b in bindings
                    if type(b) is BindingSnapshot
                    and b.mount == "upstream.account"
                    and b.active is True
                ]
                if isinstance(bindings, (tuple, list)) else []
            )
            binding_auth_lost = (
                state == "running"
                and type(remaining) is int
                and bool(observer_bindings)
                and any(
                    b.authenticated_peer is None or b.server_key_fingerprint is None
                    for b in observer_bindings
                )
            )
            if not (stopped_or_failed or unhealthy_running or low_budget or binding_auth_lost):
                return
            proactive_rotate = (
                low_budget
                and not stopped_or_failed
                and not unhealthy_running
                and not binding_auth_lost
            )
        self._observer_retry_at = now + max(1.0, self.config.maintain_seconds)
        # The persisted cursor is not trusted during registration replacement.
        try:
            self._call_lifecycle(self.pump._gap, "observation_reconnect")
        except BaseException:
            self._retain_if_global_unknown(runtime if runtime is not None else self)
            raise
        self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if runtime is not None:
            if type(runtime) is ThreadedServiceClientRuntime and not self._gateway_endpoint_listening():
                return
            if not self._close_runtime_once(runtime, self._runtime_close_timeout()):
                return
            self._runtimes.remove(runtime)
            self._observer_runtime = None
        # Unknown remote unregister keeps the original object and blocks a new
        # registration; replacing this read registration never replays an order.
        route = self._observer_route
        replacement = self._call_lifecycle(self._service_runtime, "upstream.account", route.scope, OBSERVER_OPERATIONS)
        self._check_global_cleanup_ready(replacement)
        if self._cleanup_uncertain or self._cleanup_state != "ready":
            if self._cleanup_uncertain:
                self._cleanup_uncertain_resources.append(replacement)
            else:
                self._record_cleanup_uncertain(replacement, "OBSERVER_FACTORY_STATE_CHANGED")
            self._check_cleanup_ready()
        self._observer_runtime = replacement
        self._runtimes.append(replacement)
        try:
            snapshot = self._call_lifecycle(replacement.start, timeout_seconds=self.config.call_timeout_seconds)
            self._check_global_cleanup_ready(replacement)
            granted = self._call_lifecycle(_granted, snapshot, "upstream.account", OBSERVER_OPERATIONS)
            self._check_global_cleanup_ready(replacement)
            if not granted:
                raise RiskServiceStartError("RISK_SERVICE_GATEWAY_OBSERVER_UNAVAILABLE")
            self._check_global_cleanup_ready(replacement)
            observer = self._call_lifecycle(GatewayAccountObserverClient, replacement, route=route,
                peer=self._peer(), fingerprint=hashlib.sha256(self.config.gateway.server_public_key).hexdigest())
            self._check_global_cleanup_ready(replacement)
        except BaseException as error:
            if self._cleanup_uncertain:
                self._cleanup_uncertain_resources.append(replacement)
            elif self._close_runtime_once(replacement, self._runtime_close_timeout()):
                self._runtimes.remove(replacement)
                self._observer_runtime = None
            if not isinstance(error, Exception):
                raise
            return
        new_epoch: str | None = None
        new_bindings = getattr(snapshot, "bindings", None)
        if isinstance(new_bindings, (tuple, list)):
            new_epochs = {
                getattr(b, "service_epoch", None)
                for b in new_bindings
                if getattr(b, "mount", None) == "upstream.account"
                and getattr(b, "active", False) is True
                and type(getattr(b, "service_epoch", None)) is str
                and getattr(b, "service_epoch", None)
            }
            if len(new_epochs) == 1:
                new_epoch = next(iter(new_epochs))
        self._call_lifecycle(self.pump.replace_observer, observer)
        self._check_global_cleanup_ready(replacement)
        if self.order_port is not None and (
            not proactive_rotate or old_epoch is None or new_epoch != old_epoch
        ):
            self._call_lifecycle(self.order_port.invalidate_registrations)
            self._check_global_cleanup_ready(replacement)
        self._observer_retry_at = 0.0
        _LOG.info(
            "Risk observer runtime %s succeeded",
            "proactive rotation" if proactive_rotate
            else "binding-loss reconnect" if binding_auth_lost
            else "reconnect",
        )

    def _start_strategy_financial(self) -> None:
        _require_supported_r04(self.config)
        from bt_api_py.risk_service_gateway import (
            STRATEGY_FINANCIAL_OPERATION,
            StrategyFinancialSource,
        )

        config, gateway = self.config, self.config.gateway
        operations = frozenset({STRATEGY_FINANCIAL_OPERATION})
        runtime = self._call_lifecycle(self._application_runtime, {"application.account": operations})
        self._runtimes.append(runtime)
        self._financial_runtime = runtime
        self._check_cleanup_ready()
        try:
            snapshot = self._call_lifecycle(runtime.start, timeout_seconds=config.call_timeout_seconds)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_STRATEGY_FINANCIAL_UNAVAILABLE")
        self._check_cleanup_ready()
        granted = self._call_lifecycle(_granted, snapshot, "application.account", operations)
        self._check_cleanup_ready()
        if not granted:
            _fail("RISK_SERVICE_GATEWAY_STRATEGY_FINANCIAL_UNAVAILABLE")
        self.financial = self._call_lifecycle(StrategyFinancialSource,
            runtime,
            self.facts,
            mount="application.account",
            service_id=gateway.service_id,
            instance_id=gateway.instance_id,
            scope_ref=gateway.scope_ref,
            account_ref=config.account_scope,
            timeout_seconds=config.call_timeout_seconds,
        )
        self._financial_started = True
        self._check_cleanup_ready()

    def _start_market(self) -> None:
        _require_supported_r04(self.config)
        from bt_api_py.gateway_strategy_market import GatewayMarketSubscriptionClient
        from bt_api_py.risk_service_gateway import RiskMarketFeed, risk_market_settings

        config, market = self.config, self.config.gateway.market
        keys = {}
        for index, instrument_id in enumerate(market.instrument_ids):
            key = self._call_lifecycle(MarketSubscriptionKey,
                market.source_ref,
                market.entitlement_ref,
                config.instruments[instrument_id].key,
                market.channel,
            )
            self._check_cleanup_ready()
            keys["m" + str(index)] = (key, instrument_id)
        # Includes market.clock_observation: the Gateway gate needs Risk's own attested tick.
        settings = self._call_lifecycle(risk_market_settings, {name: key for name, (key, _) in keys.items()})
        self._check_cleanup_ready()
        curve = self._call_lifecycle(self._curve, config.gateway.delegate)
        self._check_cleanup_ready()
        profiles = {_GATEWAY_SECURITY: curve}
        stream_scope = self._call_lifecycle(WireScope, config.execution_domain_ref)
        self._check_cleanup_ready()
        account_scope = self._call_lifecycle(WireScope, config.execution_domain_ref, account_ref=config.account_scope)
        self._check_cleanup_ready()
        runtime = self._call_lifecycle(self._service_runtime,
            "upstream.market",
            account_scope,
            settings.requested_operations,
            market=True,
            extension=lambda: _MarketOwnerExtension(
                settings, stream_scope=stream_scope, security_profiles=profiles,
                cleanup_guard=self._retain_if_global_unknown,
            ),
        )
        self._runtimes.append(runtime)
        self._market_runtime = runtime
        self._check_cleanup_ready()
        try:
            snapshot = self._call_lifecycle(runtime.start, timeout_seconds=config.call_timeout_seconds)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_MARKET_UNAVAILABLE")
        self._check_cleanup_ready()
        granted = self._call_lifecycle(_granted, snapshot, "upstream.market", settings.requested_operations)
        self._check_cleanup_ready()
        if not granted:
            _fail("RISK_SERVICE_GATEWAY_MARKET_UNAVAILABLE")
        clients = {}
        for name, (key, instrument_id) in keys.items():
            client = self._call_lifecycle(GatewayMarketSubscriptionClient, runtime, name)
            self._check_cleanup_ready()
            clients[name] = (client, key, instrument_id)
        self.market = self._call_lifecycle(RiskMarketFeed,
            clients,
            self.facts,
            timeout_seconds=config.call_timeout_seconds,
        )
        self._check_cleanup_ready()
        try:
            self._call_lifecycle(self.market.start)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_MARKET_UNAVAILABLE")
        self._check_cleanup_ready()

    def _maintain_market_runtime(self, now: float) -> None:
        """Proactively rotate or safely reconnect the upstream.market runtime before 4096-msg cap.

        ``RiskMarketFeed`` calls ``market.clock_observation`` on every polled instrument tick;
        rotating the CURVE socket with headroom remaining allows clean strict unregister and
        re-registration before the 4096-message socket limit causes ``BUSINESS_OUTCOME_UNKNOWN``.
        On any replacement, cached market facts are cleared fail-closed first.
        """
        gateway = getattr(self.config, "gateway", None)
        if gateway is None or getattr(gateway, "market", None) is None:
            return
        from bt_api_py.service_runtime_thread import BindingSnapshot, ThreadedServiceClientRuntime

        runtime = getattr(self, "_market_runtime", None)
        self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if now < getattr(self, "_market_retry_at", 0.0):
            return
        proactive_rotate = False
        binding_auth_lost = False
        epoch_mismatch = False
        has_pump = getattr(self, "pump", None) is not None
        observer_service_epoch = (
            self._verified_observer_service_epoch() if has_pump else None
        )
        if runtime is None:
            needs_replace = self.market is None
        else:
            try:
                snap = self._call_lifecycle(runtime.status)
                self._check_global_cleanup_ready(runtime)
            except Exception:
                self._retain_if_global_unknown(runtime)
                return
            state = getattr(snap, "state", None)
            bindings = getattr(snap, "bindings", None)
            remaining = getattr(snap, "min_socket_message_ids_remaining", None)
            stopped_or_failed = state in ("failed", "closed", "close_failed", "stopping")
            unhealthy_running = state == "running" and getattr(snap, "healthy", True) is False
            low_budget = (
                state == "running"
                and type(remaining) is int
                and remaining <= self._MARKET_ROTATION_HEADROOM
            )
            market_bindings = (
                [
                    b for b in bindings
                    if type(b) is BindingSnapshot
                    and b.mount == "upstream.market"
                    and b.active is True
                ]
                if isinstance(bindings, (tuple, list)) else []
            )
            market_epochs = {
                b.service_epoch
                for b in market_bindings
                if type(getattr(b, "service_epoch", None)) is str and b.service_epoch
            }
            market_service_epoch = next(iter(market_epochs)) if len(market_epochs) == 1 else None
            epoch_mismatch = (
                observer_service_epoch is not None
                and market_service_epoch is not None
                and market_service_epoch != observer_service_epoch
            )
            binding_auth_lost = (
                state == "running"
                and type(remaining) is int
                and bool(market_bindings)
                and any(
                    b.authenticated_peer is None or b.server_key_fingerprint is None
                    for b in market_bindings
                )
            )
            needs_replace = (
                stopped_or_failed
                or unhealthy_running
                or low_budget
                or binding_auth_lost
                or epoch_mismatch
            )
            proactive_rotate = (
                low_budget
                and not stopped_or_failed
                and not unhealthy_running
                and not binding_auth_lost
                and not epoch_mismatch
            )
        if not needs_replace:
            return
        if (
            not proactive_rotate
            and has_pump
            and observer_service_epoch is None
            and (runtime is None or type(runtime) is ThreadedServiceClientRuntime)
        ):
            return
        self._market_retry_at = now + max(1.0, self.config.maintain_seconds)
        if self.facts is not None:
            self._call_lifecycle(self.facts.forget_market)
            self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if runtime is not None:
            if type(runtime) is ThreadedServiceClientRuntime and not self._gateway_endpoint_listening():
                return
            if not self._close_runtime_once(runtime, self._runtime_close_timeout()):
                return
            if runtime in self._runtimes:
                self._runtimes.remove(runtime)
            self._market_runtime = None
            self.market = None
        try:
            self._start_market()
            self._check_global_cleanup_ready(self._market_runtime or self)
        except RiskServiceStartError as error:
            failed_rt = self._market_runtime
            if failed_rt is not None:
                if self._cleanup_uncertain:
                    self._cleanup_uncertain_resources.append(failed_rt)
                elif self._close_runtime_once(failed_rt, self._runtime_close_timeout()):
                    if failed_rt in self._runtimes:
                        self._runtimes.remove(failed_rt)
                    self._market_runtime = None
            self.market = None
            _LOG.warning(
                "Risk market runtime %s deferred: %s",
                "rotation" if proactive_rotate else "reconnect",
                error.code,
            )
            return
        self._market_retry_at = 0.0
        _LOG.info(
            "Risk market runtime %s succeeded",
            "proactive rotation" if proactive_rotate
            else "epoch-change reconnect" if epoch_mismatch
            else "binding-loss reconnect" if binding_auth_lost
            else "reconnect",
        )

    def _maintain_control_runtime(self, now: float) -> None:
        """Proactively rotate or safely reconnect control_runtime (direct_write + account)."""
        gateway = getattr(self.config, "gateway", None)
        if gateway is None or getattr(self, "control", None) is None:
            return
        from bt_api_py.service_runtime_thread import BindingSnapshot, ThreadedServiceClientRuntime

        runtime = getattr(self, "control_runtime", None)
        if runtime is not None and not callable(getattr(runtime, "status", None)):
            return
        self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if now < getattr(self, "_control_retry_at", 0.0):
            return
        account_snapshot = getattr(gateway, "account_snapshot", None)
        proactive_rotate = False
        binding_auth_lost = False
        epoch_mismatch = False
        has_pump = getattr(self, "pump", None) is not None
        observer_service_epoch = (
            self._verified_observer_service_epoch() if has_pump else None
        )
        if runtime is None:
            needs_replace = True
        else:
            try:
                snap = self._call_lifecycle(runtime.status)
                self._check_global_cleanup_ready(runtime)
            except Exception:
                self._retain_if_global_unknown(runtime)
                return
            state = getattr(snap, "state", None)
            bindings = getattr(snap, "bindings", None)
            remaining = getattr(snap, "min_socket_message_ids_remaining", None)
            stopped_or_failed = state in ("failed", "closed", "close_failed", "stopping")
            unhealthy_running = state == "running" and getattr(snap, "healthy", True) is False
            low_budget = (
                state == "running"
                and type(remaining) is int
                and remaining <= self._CONTROL_ROTATION_HEADROOM
            )
            control_bindings = (
                [
                    b for b in bindings
                    if type(b) is BindingSnapshot
                    and b.mount in ("application.direct_write", "application.account")
                    and b.active is True
                ]
                if isinstance(bindings, (tuple, list)) else []
            )
            control_epochs = {
                b.service_epoch
                for b in control_bindings
                if type(getattr(b, "service_epoch", None)) is str and b.service_epoch
            }
            control_service_epoch = (
                next(iter(control_epochs)) if len(control_epochs) == 1 else None
            )
            epoch_mismatch = (
                observer_service_epoch is not None
                and control_service_epoch is not None
                and control_service_epoch != observer_service_epoch
            )
            binding_auth_lost = (
                state == "running"
                and type(remaining) is int
                and bool(control_bindings)
                and any(
                    b.authenticated_peer is None or b.server_key_fingerprint is None
                    for b in control_bindings
                )
            )
            needs_replace = (
                stopped_or_failed
                or unhealthy_running
                or low_budget
                or binding_auth_lost
                or epoch_mismatch
                or (account_snapshot is not None and self.account is None)
            )
            proactive_rotate = (
                low_budget
                and not stopped_or_failed
                and not unhealthy_running
                and not binding_auth_lost
                and not epoch_mismatch
                and (account_snapshot is None or self.account is not None)
            )
        if not needs_replace:
            return
        if (
            not proactive_rotate
            and has_pump
            and observer_service_epoch is None
            and (runtime is None or type(runtime) is ThreadedServiceClientRuntime)
        ):
            return
        self._control_retry_at = now + max(1.0, self.config.maintain_seconds)
        if self.facts is not None and account_snapshot is not None:
            self._call_lifecycle(self.facts.forget_account)
            self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if runtime is not None:
            if type(runtime) is ThreadedServiceClientRuntime and not self._gateway_endpoint_listening():
                return
            if not self._close_runtime_once(runtime, self._runtime_close_timeout()):
                return
            if runtime in self._runtimes:
                self._runtimes.remove(runtime)
            self.control_runtime = None
            if account_snapshot is not None:
                self.account = None

        from bt_api_py.risk_service_gateway import (
            CONTROL_INTENT_CONTINUITY_OPERATIONS as CONTROL_INTENT_CONTINUITY_OPERATIONS_FOR_RISK,
            CONTROL_INTENT_OPERATIONS as CONTROL_INTENT_OPERATIONS_FOR_RISK,
            CONTROL_OPERATIONS as CONTROL_OPERATIONS_FOR_RISK,
        )

        config = self.config
        timeout = config.call_timeout_seconds
        replacement = self._call_lifecycle(self._control_runtime)
        self._check_global_cleanup_ready(replacement)
        self.control_runtime = replacement
        self._runtimes.append(replacement)
        try:
            snapshot = self._call_lifecycle(replacement.start, timeout_seconds=timeout)
            self._check_global_cleanup_ready(replacement)
            required_control_ops = (
                CONTROL_INTENT_CONTINUITY_OPERATIONS_FOR_RISK
                if getattr(config, "control_intent_continuity_enabled", False)
                else (
                    CONTROL_INTENT_OPERATIONS_FOR_RISK
                    if getattr(config, "bound_control_intents_enabled", False)
                    else CONTROL_OPERATIONS_FOR_RISK
                )
            )
            if not self._call_lifecycle(_granted, snapshot, "application.direct_write", required_control_ops):
                raise RiskServiceStartError("RISK_SERVICE_GATEWAY_CONTROL_UNAVAILABLE")
            self._check_global_cleanup_ready(replacement)
            new_account = None
            if account_snapshot is not None:
                from bt_api_py.gateway_strategy_account import GatewayStrategyAccountClient
                from bt_api_py.risk_service_gateway import RiskAccountSnapshotSource

                if not self._call_lifecycle(
                    _granted, snapshot, "application.account", frozenset({"account.snapshot.typed"})
                ):
                    raise RiskServiceStartError("RISK_SERVICE_GATEWAY_ACCOUNT_UNAVAILABLE")
                self._check_global_cleanup_ready(replacement)
                account_client = self._call_lifecycle(GatewayStrategyAccountClient, replacement)
                self._check_global_cleanup_ready(replacement)
                new_account = self._call_lifecycle(
                    RiskAccountSnapshotSource,
                    account_client,
                    self.facts,
                    instrument_ids={b.key: b.instrument_id for b in config.instruments.values()},
                    timeout_seconds=timeout,
                )
                self._check_global_cleanup_ready(replacement)
            self._call_lifecycle(self.control.replace_runtime, replacement)
            self._check_global_cleanup_ready(replacement)
            if account_snapshot is not None:
                self.account = new_account
        except BaseException as error:
            if self._cleanup_uncertain:
                self._cleanup_uncertain_resources.append(replacement)
            elif self._close_runtime_once(replacement, self._runtime_close_timeout()):
                if replacement in self._runtimes:
                    self._runtimes.remove(replacement)
                self.control_runtime = None
            if account_snapshot is not None:
                self.account = None
            if not isinstance(error, Exception):
                raise
            _LOG.warning(
                "Risk control runtime %s deferred: %s",
                "rotation" if proactive_rotate else "reconnect",
                getattr(error, "code", type(error).__name__),
            )
            return
        self._control_retry_at = 0.0
        _LOG.info(
            "Risk control runtime %s succeeded",
            "proactive rotation" if proactive_rotate
            else "epoch-change reconnect" if epoch_mismatch
            else "binding-loss reconnect" if binding_auth_lost
            else "reconnect",
        )

    def _maintain_financial_runtime(self, now: float) -> None:
        """Proactively rotate or safely reconnect the strategy-financial runtime."""
        if getattr(self.config, "strategy_financial_seconds", None) is None:
            return
        from bt_api_py.service_runtime_thread import BindingSnapshot, ThreadedServiceClientRuntime

        runtime = getattr(self, "_financial_runtime", None)
        if runtime is not None and not callable(getattr(runtime, "status", None)):
            return
        self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if now < getattr(self, "_financial_retry_at", 0.0):
            return
        proactive_rotate = False
        binding_auth_lost = False
        epoch_mismatch = False
        has_pump = getattr(self, "pump", None) is not None
        observer_service_epoch = (
            self._verified_observer_service_epoch() if has_pump else None
        )
        if runtime is None:
            needs_replace = self.financial is None
        else:
            try:
                snap = self._call_lifecycle(runtime.status)
                self._check_global_cleanup_ready(runtime)
            except Exception:
                self._retain_if_global_unknown(runtime)
                return
            state = getattr(snap, "state", None)
            bindings = getattr(snap, "bindings", None)
            remaining = getattr(snap, "min_socket_message_ids_remaining", None)
            stopped_or_failed = state in ("failed", "closed", "close_failed", "stopping")
            unhealthy_running = state == "running" and getattr(snap, "healthy", True) is False
            low_budget = (
                state == "running"
                and type(remaining) is int
                and remaining <= self._FINANCIAL_ROTATION_HEADROOM
            )
            fin_bindings = (
                [
                    b for b in bindings
                    if type(b) is BindingSnapshot
                    and b.mount == "application.account"
                    and b.active is True
                ]
                if isinstance(bindings, (tuple, list)) else []
            )
            fin_epochs = {
                b.service_epoch
                for b in fin_bindings
                if type(getattr(b, "service_epoch", None)) is str and b.service_epoch
            }
            fin_service_epoch = next(iter(fin_epochs)) if len(fin_epochs) == 1 else None
            epoch_mismatch = (
                observer_service_epoch is not None
                and fin_service_epoch is not None
                and fin_service_epoch != observer_service_epoch
            )
            binding_auth_lost = (
                state == "running"
                and type(remaining) is int
                and bool(fin_bindings)
                and any(
                    b.authenticated_peer is None or b.server_key_fingerprint is None
                    for b in fin_bindings
                )
            )
            needs_replace = (
                stopped_or_failed
                or unhealthy_running
                or low_budget
                or binding_auth_lost
                or epoch_mismatch
            )
            proactive_rotate = (
                low_budget
                and not stopped_or_failed
                and not unhealthy_running
                and not binding_auth_lost
                and not epoch_mismatch
            )
        if not needs_replace:
            return
        if (
            not proactive_rotate
            and has_pump
            and observer_service_epoch is None
            and (runtime is None or type(runtime) is ThreadedServiceClientRuntime)
        ):
            return
        self._financial_retry_at = now + max(1.0, self.config.maintain_seconds)
        if self.facts is not None:
            self._call_lifecycle(self.facts.set_strategy_financials, {})
            self._check_global_cleanup_ready(runtime if runtime is not None else self)
        if runtime is not None:
            if type(runtime) is ThreadedServiceClientRuntime and not self._gateway_endpoint_listening():
                return
            if not self._close_runtime_once(runtime, self._runtime_close_timeout()):
                return
            if runtime in self._runtimes:
                self._runtimes.remove(runtime)
            self._financial_runtime = None
            self.financial = None
        try:
            self._start_strategy_financial()
            self._check_global_cleanup_ready(self._financial_runtime or self)
        except RiskServiceStartError as error:
            failed_rt = self._financial_runtime
            if failed_rt is not None:
                if self._cleanup_uncertain:
                    self._cleanup_uncertain_resources.append(failed_rt)
                elif self._close_runtime_once(failed_rt, self._runtime_close_timeout()):
                    if failed_rt in self._runtimes:
                        self._runtimes.remove(failed_rt)
                    self._financial_runtime = None
            self.financial = None
            _LOG.warning(
                "Risk financial runtime %s deferred: %s",
                "rotation" if proactive_rotate else "reconnect",
                error.code,
            )
            return
        self._financial_retry_at = 0.0
        _LOG.info(
            "Risk financial runtime %s succeeded",
            "proactive rotation" if proactive_rotate
            else "epoch-change reconnect" if epoch_mismatch
            else "binding-loss reconnect" if binding_auth_lost
            else "reconnect",
        )

    def ready(self) -> bool:
        try:
            self._check_owner()
        except RiskServiceStartError:
            return False
        if self.stop_event.is_set():
            return False
        try:
            _ = self.ledger.fence  # raises when the writer fence is no longer ours
        except Exception:
            return False
        if self._cleanup_uncertain or self._cleanup_state != "ready":
            return False
        return self._started and self.pump is not None and self.pump.verified_epoch is not None

    def start_service_host(self) -> None:
        _require_supported_r04(self.config)
        if type(self.config.strict_admin_cancel) is not bool:
            _fail("RISK_SERVICE_CONFIG_STRICT_ADMIN_CANCEL_INVALID")
        self._claim_resource_owner()
        from bt_api_py.risk_service_component import RiskOrderService
        from bt_api_py.service_components import ComponentResources
        from bt_api_py.service_config import parse_service_config
        from bt_api_py.service_host import ServiceHost, ServiceHostError

        config = self.config
        parent_v2_enabled = bool(getattr(config, "parent_contract_enabled", False))
        self.component = self._call_lifecycle(RiskOrderService,
            self.engine,
            self.ledger,
            account_scope=config.account_scope,
            route=config.route,
            execution_domain_ref=config.execution_domain_ref,
            principals=config.principals,
            instrument_for=self.instrument_for,
            ready=self.ready,
            note_run=self.order_port.note_run,
            cancel_status=self.control.cancel_scope_status,
            strict_admin_cancel=config.strict_admin_cancel,
            parent_v2_execution_enabled=parent_v2_enabled,
            order_cut_required=bool(
                getattr(getattr(config, "account_facts_observation", None), "order_cut_enabled", False)
            ),
            order_cut_admission_ready=self._order_cut_admission_ready,
            order_cut_admission_observe=self._observe_order_cut_admission,
            order_cut_admission_proof=self._observe_order_cut_admission_proof,
            portfolio_summary_observe=self._observe_portfolio_summary,
        )
        self._check_cleanup_ready()
        component = self.component
        try:
            document = self._call_lifecycle(service_config_document, config)
            self._check_cleanup_ready()
            parsed = self._call_lifecycle(parse_service_config, document)
            self._check_cleanup_ready()
            authority = self._call_lifecycle(build_authority, config)
            self._check_cleanup_ready()
            self.service_host = self._call_lifecycle(ServiceHost,
                parsed,
                authority,
                config.registry_database,
                lambda *_: ComponentResources.borrowed({"service": component}, ready=lambda: True),
                risk_admin_cancel_service=component if config.strict_admin_cancel else None,
                enable_risk_parent_execution_writes=parent_v2_enabled,
            )
            self._check_cleanup_ready()
            self._service_host_start_attempted = True
            self._call_lifecycle(self.service_host.start)
        except ServiceHostError as error:
            _fail("RISK_SERVICE_HOST_" + error.code)
        except Exception:
            _fail("RISK_SERVICE_HOST_START_FAILED")
        self._check_cleanup_ready()
        self._call_lifecycle(restrict_file, config.registry_database)
        self._check_cleanup_ready()

    def start(self) -> RiskServiceHost:
        """Run start-up steps 2-5, retaining storage when initialization is uncertain."""
        _require_supported_r04(self.config)
        self._check_owner()
        if self._lifecycle_in_progress:
            _fail("RISK_SERVICE_HOST_LIFECYCLE_IN_PROGRESS")
        if self._cleanup_state != "ready":
            _fail("RISK_SERVICE_HOST_CLEANUP_FINALIZED")
        try:
            self._fault_point = self._call_lifecycle(fault_point_from_environment, os.environ)
            self._check_cleanup_ready()
            self.open_storage()
            self._check_cleanup_ready()
            self.build_engine()
            self._check_cleanup_ready()
            self._call_lifecycle(bootstrap_policy, self.engine, self.ledger, self.config, self.rules)
            self._check_cleanup_ready()
            self.start_gateway()
            self._check_cleanup_ready()
            self._call_lifecycle(self.engine.recover)
            self._check_cleanup_ready()
            self._call_lifecycle(self.pump.poll)
            self._check_cleanup_ready()
            if self.market is not None:
                self._call_lifecycle(self.market.poll)
                self._check_cleanup_ready()
            if self.account is not None:
                self._call_lifecycle(self.account.refresh)
                self._check_cleanup_ready()
            if self.financial is not None:
                self._call_lifecycle(self.financial.refresh)
                self._check_cleanup_ready()
            self._started = True
            self.start_service_host()
            self._check_cleanup_ready()
            now = self._call_lifecycle(self._monotonic)
            self._check_cleanup_ready()
            if not _finite_poll_value(now):
                _fail("RISK_SERVICE_POLL_DEADLINE_INVALID")
            self._next = {
                "observation": now,
                "maintain": now,
                "market": now,
                "account": now,
                "financial": now,
            }
            if self._account_facts_coordinator is not None:
                self._next['account_facts'] = now
        except BaseException:
            try:
                self.close()
            except BaseException:
                self._cleanup_state = "uncertain"
                _RETAINED_HOSTS.add(self)
                _LOG.warning("Risk service cleanup failed; outcome uncertain")
            raise
        return self

    # ------------------------------------------------------------------ loop

    def _drain_service_requests(self, timeout_ms: int = 0, *, max_requests: int = 16) -> None:
        """Serve up to ``max_requests`` ready inbound Risk RPCs on the owner thread."""
        if self.stop_event.is_set() or self.service_host is None:
            return
        from bt_api_py.service_host import ServiceHost

        if type(self.service_host) is ServiceHost:
            self._call_lifecycle(
                self.service_host.serve_burst, timeout_ms, max_requests=max_requests
            )
            self._check_cleanup_ready()
            return
        served = self._call_lifecycle(self.service_host.serve_once, timeout_ms)
        self._check_cleanup_ready()
        if served is True:
            for _ in range(max_requests - 1):
                if self.stop_event.is_set():
                    break
                if not self._call_lifecycle(self.service_host.serve_once, 0):
                    break
                self._check_cleanup_ready()

    def run_once(self) -> None:
        _require_supported_r04(self.config)
        self._check_owner()
        if self._lifecycle_in_progress:
            _fail("RISK_SERVICE_HOST_LIFECYCLE_IN_PROGRESS")
        config = self.config
        self._drain_service_requests(config.loop_timeout_ms, max_requests=16)
        try:
            _ = self._call_lifecycle(getattr, self.ledger, "fence")
        except Exception:
            _fail("RISK_SERVICE_FENCE_LOST")
        self._check_cleanup_ready()
        now = self._call_lifecycle(self._monotonic)
        self._check_cleanup_ready()
        # Validate every due deadline before polling any source. Otherwise an
        # interval smaller than the clock's float precision can cause a busy
        # loop after the source has already been polled.
        deadlines: dict[str, float] = {}
        market_configured = getattr(getattr(config, "gateway", None), "market", None) is not None
        schedules = (
            ("observation", True, config.observation_poll_seconds),
            ("market", self.market is not None or market_configured, config.market_poll_seconds),
            ("account", self.account is not None or getattr(self, "_account_started", False), config.account_snapshot_seconds),
            ("financial", self.financial is not None or getattr(self, "_financial_started", False), config.strategy_financial_seconds),
            ('account_facts', self._account_facts_coordinator is not None
             and self.account_facts_status in {'READY', 'APPLIED', 'EMPTY'},
             None if getattr(config, 'account_facts_observation', None) is None
             else config.account_facts_observation.poll_interval_seconds),
            ("maintain", True, config.maintain_seconds),
        )
        try:
            if not _finite_poll_value(now):
                raise ValueError
            for name, enabled, interval in schedules:
                if not enabled:
                    continue
                next_due = self._next[name]
                if not _finite_poll_value(next_due):
                    raise ValueError
                if now < next_due:
                    continue
                if type(interval) not in (int, float):
                    raise ValueError
                deadline = now + interval
                if not math.isfinite(deadline) or deadline <= now:
                    raise ValueError
                deadlines[name] = deadline
        except (ArithmeticError, KeyError, TypeError, ValueError):
            _fail("RISK_SERVICE_POLL_DEADLINE_INVALID")
        remaining_phases = len(deadlines)

        def _interleave_after_phase() -> None:
            nonlocal remaining_phases
            remaining_phases -= 1
            if (
                remaining_phases > 0
                and not self.stop_event.is_set()
                and callable(getattr(self.service_host, "serve_burst", None))
            ):
                self._drain_service_requests(0, max_requests=16)

        if "observation" in deadlines:
            self._reconnect_observer()
            self._check_cleanup_ready()
            self._call_lifecycle(self.pump.poll)
            self._check_cleanup_ready()
            self._maintain_account_facts_runtime(now)
            self._check_cleanup_ready()
            self._next["observation"] = deadlines["observation"]
            _interleave_after_phase()
        if "market" in deadlines:
            self._maintain_market_runtime(now)
            self._check_cleanup_ready()
            if self.market is not None:
                self._call_lifecycle(self.market.poll)
                self._check_cleanup_ready()
            self._next["market"] = deadlines["market"]
            _interleave_after_phase()
        if "account" in deadlines:
            self._maintain_control_runtime(now)
            self._check_cleanup_ready()
            if self.account is not None:
                self._call_lifecycle(self.account.refresh)
                self._check_cleanup_ready()
            self._next["account"] = deadlines["account"]
            _interleave_after_phase()
        if "financial" in deadlines:
            self._maintain_financial_runtime(now)
            self._check_cleanup_ready()
            if self.financial is not None:
                self._call_lifecycle(self.financial.refresh)
                self._check_cleanup_ready()
            self._next["financial"] = deadlines["financial"]
            _interleave_after_phase()
        if 'account_facts' in deadlines:
            self._maintain_account_facts_runtime(now, allow_failure_replace=False)
            self._check_cleanup_ready()
            if self._account_facts_coordinator is not None:
                self._call_lifecycle(self._poll_account_facts)
                self._check_cleanup_ready()
            self._next['account_facts'] = deadlines['account_facts']
            _interleave_after_phase()
        if "maintain" in deadlines:
            self._maintain_control_runtime(now)
            self._check_cleanup_ready()
            self._call_lifecycle(self.engine.maintain)
            self._check_cleanup_ready()
            self._next["maintain"] = deadlines["maintain"]
            _interleave_after_phase()

    def run(self, *, stop_file: Path | None = None) -> None:
        _require_supported_r04(self.config)
        self._check_owner()
        while not self.stop_event.is_set():
            if marker_requested(stop_file):
                self.stop_event.set()
                break
            self.run_once()

    def close(self) -> bool:
        """Close on the storage owner thread; an uncertain initialization stays fenced.

        Retention cannot keep a caller-owned thread or this process alive.
        """
        if os.getpid() != self._creator_pid:
            return False
        if self._storage_owner is not None and threading.current_thread() is not self._storage_owner:
            # Refuse to clean up from another thread, but still ask the serving
            # owner to stop; the owner thread must run the actual cleanup. The
            # started flag stays as evidence that this host served.
            self.stop_event.set()
            return False
        if self._resource_owner is not None and threading.current_thread() is not self._resource_owner:
            self.stop_event.set()
            return False
        if self._cleanup_uncertain or self._cleanup_in_progress or self._lifecycle_in_progress:
            return False
        if self._cleanup_state == "complete":
            return True
        # A host that never acquired storage or runtime resources holds nothing
        # this process must retain. Closing it cannot resolve another owner's
        # unknown state, and treating it as uncertain would only add a new one.
        with _PROBE_GUARD:
            pending_root = _STORAGE_INIT_ROOTS.get(id(self))
        if (self._cleanup_state == "ready" and self._storage_owner is None
                and self._resource_owner is None and not self._started
                and not self._stack._exit_callbacks and not self._runtimes
                and self.service_host is None and self.order_port is None
                and pending_root is None
                and not self._storage_init_uncertain
                and not self._storage_cleanup_uncertain
                and not (self._storage_entry_in_flight or self._storage_init_in_flight
                         or self._delegate_init_in_flight or self._storage_handoff_in_flight)
                and not self._delegate_cleanup_unresolved
                and self._uncertain_storage is None):
            # Only a host with no storage lifecycle in flight is known empty;
            # an in-progress entry or initialization may already hold an
            # unregistered resource and must not be reported complete.
            self._cleanup_state = "complete"
            return True
        if self._cleanup_state != "ready":
            return False
        with _PROBE_GUARD:
            if _PROBE_STATE["uncertain"]:
                self._record_cleanup_uncertain_locked(self, "CONCURRENT_CLEANUP_UNKNOWN")
                return False

        self._cleanup_state = "in_progress"
        self._cleanup_in_progress = True
        current_resource: Any = self
        try:
            if self._retain_if_global_unknown(current_resource):
                return False
            self.stop_event.set()
            if self._cleanup_uncertain or self._retain_if_global_unknown(current_resource):
                return False
            self._started = False
            with _PROBE_GUARD:
                pending_storage_root = _STORAGE_INIT_ROOTS.get(id(self))
            if (self._storage_init_uncertain or self._storage_cleanup_uncertain
                    or self._storage_entry_in_flight or self._storage_init_in_flight
                    or self._delegate_init_in_flight
                    or self._storage_handoff_in_flight or pending_storage_root is not None
                    or self._delegate_cleanup_unresolved):
                resource = (self._delegate_cleanup_unresolved[0] if self._delegate_cleanup_unresolved
                            else pending_storage_root if pending_storage_root is not None
                            else self._uncertain_storage)
                self._record_cleanup_uncertain(resource, "PREVIOUS_CLEANUP_UNCONFIRMED")
                return False
            if self.order_port is not None:
                current_resource = self.order_port
                if self._retain_if_global_unknown(current_resource):
                    return False
                if getattr(self.order_port, "cleanup_uncertain", None) is not False:
                    self._record_cleanup_uncertain(current_resource, "PORT_CLEANUP_UNCONFIRMED")
                    return False
                if self._cleanup_uncertain or self._retain_if_global_unknown(current_resource):
                    return False
            if self.service_host is not None:
                current_resource = self.service_host
                if self._retain_if_global_unknown(current_resource):
                    return False
                state = self.service_host.state
                if self._cleanup_uncertain or self._retain_if_global_unknown(current_resource):
                    return False
                if type(state) is not str:
                    self._record_cleanup_uncertain(current_resource, "SERVICE_STATE_UNKNOWN")
                    return False
                if state == "new" and self._service_host_start_attempted:
                    self._record_cleanup_uncertain(current_resource, "SERVICE_START_UNKNOWN")
                    return False
                if state in ("new", "started"):
                    self.service_host.close()
                    if self._cleanup_uncertain or self._retain_if_global_unknown(current_resource):
                        return False
                    state = self.service_host.state
                    if self._cleanup_uncertain or self._retain_if_global_unknown(current_resource):
                        return False
                    if type(state) is not str or state != "closed":
                        self._record_cleanup_uncertain(current_resource, "SERVICE_CLOSE_UNCONFIRMED")
                        return False
                elif state not in ("failed", "closed"):
                    self._record_cleanup_uncertain(current_resource, "SERVICE_CLOSE_UNCONFIRMED")
                    return False
            if self._cleanup_uncertain or self._retain_if_global_unknown(current_resource):
                return False
            if self.order_port is not None:
                current_resource = self.order_port
                if self._retain_if_global_unknown(current_resource):
                    return False
                result = self.order_port.close()
                global_unknown = self._retain_if_global_unknown(current_resource)
                if result is not True or self._cleanup_uncertain or global_unknown:
                    if (result is False and not self._cleanup_uncertain
                            and getattr(self.order_port, "cleanup_uncertain", None) is False):
                        # An ordinary blocked slot is not a failed cleanup, but
                        # this host still cannot complete its current shutdown.
                        _RETAINED_HOSTS.add(self)
                    elif not self._cleanup_uncertain:
                        self._record_cleanup_uncertain(current_resource, "PORT_CLOSE_UNCONFIRMED")
                    return False
            for runtime in reversed(self._runtimes[:]):
                current_resource = runtime
                if self._retain_if_global_unknown(current_resource):
                    return False
                snapshot = runtime.close(timeout_seconds=self._runtime_close_timeout())
                if self._cleanup_uncertain or self._retain_if_global_unknown(current_resource):
                    return False
                confirmed = runtime_cleanup_confirmed(_GuardedCleanupSnapshot(self, runtime, snapshot)) is True
                if (self._cleanup_uncertain or self._retain_if_global_unknown(current_resource)
                        or not confirmed):
                    if not self._cleanup_uncertain:
                        self._record_cleanup_uncertain(current_resource, "RUNTIME_CLOSE_UNCONFIRMED")
                    return False
                self._runtimes.remove(runtime)
            current_resource = self._stack
            if self._retain_if_global_unknown(current_resource):
                return False
            with _PROBE_GUARD:
                storage_root = _STORAGE_ACTIVE_ROOTS.get(id(self))
            if type(self._stack) is not ExitStack:
                self._storage_cleanup_uncertain = True
                self._record_cleanup_uncertain(current_resource, "STORAGE_STACK_UNCONFIRMED")
                return False
            if (self._storage_owner is None and
                    (storage_root is not None or self._stack._exit_callbacks)):
                self._storage_cleanup_uncertain = True
                self._record_cleanup_uncertain(current_resource, "STORAGE_OWNER_UNCONFIRMED")
                _LOG.error("Risk storage cleanup failed; outcome uncertain")
                return False
            if self._storage_owner is not None:
                if (type(storage_root) is not tuple or len(storage_root) != 5
                        or storage_root[0] is not self):
                    self._storage_cleanup_uncertain = True
                    self._record_cleanup_uncertain(current_resource, "STORAGE_OWNER_UNCONFIRMED")
                    return False
                if (not _only_exit_callback(self._stack, storage_root[1], ExitStack.__exit__)
                        or not _only_exit_callback(storage_root[1], storage_root[2])):
                    self._storage_cleanup_uncertain = True
                    self._record_cleanup_uncertain(storage_root, "STORAGE_STACK_UNCONFIRMED")
                    return False
            try:
                self._stack.close()  # releases the writer fence last
            except BaseException:
                # ExitStack may already have consumed callbacks and released a fence.
                self._storage_cleanup_uncertain = True
                _LOG.error("Risk storage cleanup failed; outcome uncertain")
                raise
            if (storage_root is not None and
                    not _fenced_journal_close_confirmed(storage_root[2], storage_root[3], storage_root[4])):
                self._storage_cleanup_uncertain = True
                self._record_cleanup_uncertain(storage_root, "STORAGE_CLOSE_UNCONFIRMED")
                return False
            if self._cleanup_uncertain or self._retain_if_global_unknown(current_resource):
                return False
            with _PROBE_GUARD:
                if self._cleanup_uncertain or _PROBE_STATE["uncertain"]:
                    if not self._cleanup_uncertain:
                        self._record_cleanup_uncertain_locked(current_resource, "CONCURRENT_CLEANUP_UNKNOWN")
                    return False
                self._storage_owner = None
                self._cleanup_state = "complete"
                _STORAGE_ACTIVE_ROOTS.pop(id(self), None)
                return True
        except BaseException as error:
            self._record_cleanup_uncertain(current_resource, "CLEANUP_EXCEPTION")
            _LOG.error("Risk service cleanup failed; outcome uncertain")
            if not isinstance(error, Exception) and current_resource is not self._stack:
                raise
            return False
        finally:
            self._cleanup_in_progress = False
            if self._cleanup_state != "complete":
                self._cleanup_state = "uncertain"


# ----------------------------------------------------------------------------- CLI


def _print(value: Mapping) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _cleanup_wait_diagnostic(host: RiskServiceHost) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read bounded, fixed-label observations without touching retained resources."""
    reasons: list[str] = []
    resources: list[str] = []
    if not _PROBE_GUARD.acquire(blocking=False):
        return ("UNKNOWN",), ("UNKNOWN",)
    try:
        if type(host) is not RiskServiceHost or type(_PROBE_STATE) is not dict:
            return ("UNKNOWN",), ("UNKNOWN",)
        fields = object.__getattribute__(host, "__dict__")
        entries = _PROBE_STATE.get("uncertain")
        if type(fields) is not dict or type(entries) is not list:
            return ("UNKNOWN",), ("UNKNOWN",)
        owned = (
            ("HOST", host),
            ("OBSERVER", fields.get("_observer_runtime")),
            ('ACCOUNT_FACTS', fields.get('_account_facts_runtime')),
            ("MARKET_RUNTIME", fields.get("_market_runtime")),
            ("FINANCIAL_RUNTIME", fields.get("_financial_runtime")),
            ("CONTROL", fields.get("control_runtime")),
            ("DELEGATE_PORT", fields.get("order_port")),
            ("MARKET", fields.get("market")),
            ("ACCOUNT", fields.get("account")),
            ("FINANCIAL", fields.get("financial")),
            ("SERVICE", fields.get("service_host")),
            ("STORAGE", fields.get("_stack")),
            ("STORAGE", fields.get("_uncertain_storage")),
            ('STORAGE', fields.get('ledger')),
        )
        runtimes = fields.get("_runtimes")
        runtimes = runtimes[:64] if type(runtimes) is list else ()
        delegates = fields.get("_delegate_cleanup_unresolved")
        delegates = delegates[:64] if type(delegates) is list else ()
        for entry in entries[:128]:
            if type(entry) is not tuple or len(entry) != 3 or entry[0] is not host:
                continue
            resource, reason = entry[1], entry[2]
            reason = (reason if type(reason) is str and reason in _CLEANUP_DIAGNOSTIC_REASONS
                      else "UNKNOWN")
            if reason not in reasons and len(reasons) < 8:
                reasons.append(reason)
            category = next((name for name, value in owned
                             if value is not None and resource is value), None)
            if category is None:
                if any(resource is value for value in delegates):
                    category = "DELEGATE"
                elif any(resource is value for value in runtimes):
                    category = "RUNTIME"
                else:
                    category = "UNKNOWN"
            if category not in resources and len(resources) < 8:
                resources.append(category)
    except Exception:
        # Diagnostics must not change the existing fail-closed wait path.
        return ("UNKNOWN",), ("UNKNOWN",)
    finally:
        _PROBE_GUARD.release()
    return tuple(reasons) or ("UNKNOWN",), tuple(resources) or ("UNKNOWN",)


def _wait_for_cleanup(
    host: RiskServiceHost,
    *,
    _wait: Callable[[], Any] | None = None,
) -> None:
    state = getattr(host, "_cleanup_state", None)
    if state == "complete":
        return
    if state != "uncertain" and getattr(host, "_cleanup_uncertain", False) is not True:
        try:
            if host.close() is True:
                return
        except Exception:
            _LOG.warning("Risk service cleanup failed; outcome uncertain")
    _RETAINED_HOSTS.add(host)
    _LOG.error("Risk service cleanup incomplete; account fence ownership unconfirmed")
    reasons, resources = _cleanup_wait_diagnostic(host)
    _LOG.error("Risk cleanup diagnostic state=cleanup_uncertain reasons=%s resources=%s",
               ",".join(reasons), ",".join(resources))
    ready_file = getattr(host, "_ready_file", None)
    if isinstance(ready_file, Path):
        try:
            ready_file.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "state": "cleanup_uncertain",
                        "reasons": list(reasons),
                        "resources": list(resources),
                    }
                ),
                encoding="utf-8",
            )
            restrict_file(ready_file)
        except Exception:
            pass
    wait = _wait if _wait is not None else threading.Event().wait
    while True:
        wait()


def _command_run(args: argparse.Namespace) -> int:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    try:
        config = load_risk_service_config(args.config)
    except RiskServiceConfigError as error:
        sys.stderr.write("RISK_SERVICE_START_REFUSED " + error.code + "\n")
        return 2
    try:
        _require_supported_r04(config)
    except RiskServiceStartError as error:
        sys.stderr.write("RISK_SERVICE_START_REFUSED " + error.code + "\n")
        return 2
    host = RiskServiceHost(config)

    def stop(_signum, _frame):
        host.stop_event.set()

    previous = {
        sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        try:
            host.start()
        except RiskServiceStartError as error:
            sys.stderr.write("RISK_SERVICE_START_REFUSED " + error.code + "\n")
            return 2
        if args.ready_file:
            ready = Path(args.ready_file)
            host._ready_file = ready
            ready.write_text(json.dumps({"pid": os.getpid(), "state": "serving"}), encoding="utf-8")
            restrict_file(ready)
        try:
            if args.enable_owner_stop_file:
                stop_file = new_marker(Path(args.config).parent, "risk")
                print("RISK_SERVICE_STOP_FILE " + stop_file.name, flush=True)
                host.run(stop_file=stop_file)
            else:
                host.run()
        except RiskServiceStartError as error:
            sys.stderr.write("RISK_SERVICE_STOPPED " + error.code + "\n")
            sys.stderr.flush()
            return 3
        except Exception:
            _LOG.exception("Risk service main loop raised unexpected exception")
            raise
    finally:
        _wait_for_cleanup(host)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if args.enable_owner_stop_file:
        print("RISK_SERVICE_STOPPED", flush=True)
    return 0


def _command_validate_policy(args: argparse.Namespace) -> int:
    allowlist: Mapping[str, int] = {}
    if args.config:
        try:
            config = load_risk_service_config(args.config)
            _require_supported_r04(config)
            allowlist = config.trusted_rules
        except RiskServiceConfigError as error:
            _print({"valid": False, "code": error.code})
            return 2
        except RiskServiceStartError as error:
            _print({"valid": False, "code": error.code})
            return 2
    from bt_api_risk.pretrade import RiskPolicyError
    from bt_api_risk.pretrade.policy import validate_policy_document

    try:
        text, rules_text = read_policy_files(Path(args.policy))
        policy = validate_policy_document(
            text, exchange_rules_text=rules_text, rule_allowlist=dict(allowlist)
        )
    except RiskServiceStartError as error:
        _print({"valid": False, "code": error.code})
        return 1
    except RiskPolicyError as error:
        _print({"valid": False, "code": error.code})
        return 1
    _print({"valid": True, "policy_id": policy.policy_id, "digest": policy.digest})
    return 0


class _Inert:
    """Ports for an offline ledger read: every Gateway call is unavailable."""

    def __getattr__(self, _name):
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("RISK_SERVICE_OFFLINE")

        return unavailable

    def trading_day(self):
        return None


def _command_status(args: argparse.Namespace) -> int:
    try:
        config = load_risk_service_config(args.config)
    except RiskServiceConfigError as error:
        _print({"code": error.code})
        return 2
    try:
        _require_supported_r04(config)
        if _pretrade_selection(config)[1] is not None:
            _fail('RISK_SERVICE_MIGRATION_RUN_REQUIRED')
    except RiskServiceStartError as error:
        _print({"code": error.code})
        return 2
    if not config.risk_database.is_file():
        _print({"code": "RISK_SERVICE_STORAGE_MISSING"})
        return 2
    host = RiskServiceHost(config)
    # Like run, a status process with unresolved cleanup must keep its owner
    # thread alive on ordinary termination signals. Non-main-thread callers
    # cannot install signal handlers and retain responsibility for their owner.
    previous = {}
    if threading.current_thread() is threading.main_thread():
        def stop(_signum, _frame):
            host.stop_event.set()

        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        host.open_storage()
        from bt_api_risk.pretrade import PretradeEngine

        engine = PretradeEngine(
            host.ledger,
            host.policy_store,
            host.rules,
            _Inert(),
            _Inert(),
            _Inert(),
            account_scope=config.account_scope,
            route=config.route,
            now_ns=time.time_ns,
            reservation_ttl_seconds=config.reservation_ttl_seconds,
            actor_principal=config.gateway.delegate.principal_ref,
            dispatch_recheck_seconds=config.dispatch_recheck_seconds,
            dispatch_recheck_limit=config.dispatch_recheck_limit,
            parent_contract_enabled=getattr(config, "parent_contract_enabled", False),
        )
        from bt_api_py.risk_service_component import json_safe

        _print({"running": False, "summary": json_safe(engine.status_summary())})
        return 0
    except RiskServiceStartError as error:
        # Storage failure or a different scope's SQLite writer cannot establish
        # whether this account's service is running.
        _print({"running": True if error.code == "RISK_SERVICE_FENCE_HELD" else None,
                "code": error.code})
        return 3 if error.code == "RISK_SERVICE_FENCE_HELD" else 2
    finally:
        _wait_for_cleanup(host)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bt-risk-service")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the Risk service for one account")
    run.add_argument("--config", required=True)
    run.add_argument("--ready-file", default=None)
    run.add_argument(
        "--enable-owner-stop-file",
        action="store_true",
        help="allow local cooperative shutdown with a protected empty marker beside the configuration",
    )
    validate = commands.add_parser("validate-policy", help="validate a policy file offline")
    validate.add_argument("policy")
    validate.add_argument("--config", default=None)
    status = commands.add_parser("status", help="local ledger summary (no Gateway)")
    status.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        return _command_run(args)
    if args.command == "validate-policy":
        return _command_validate_policy(args)
    return _command_status(args)


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())


__all__ = [
    "DELEGATE_OPERATIONS",
    "OBSERVER_OPERATIONS",
    "RiskServiceHost",
    "RiskServiceStartError",
    "bootstrap_policy",
    "build_authority",
    "main",
    "probe_writer_fence",
    "read_policy_files",
    "service_config_document",
]
