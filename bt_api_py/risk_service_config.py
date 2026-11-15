"""Service configuration of the independent Risk service process (iteration 64, 64.09).

Schema ``bt_api_risk.service.v1`` (YAML or JSON, strict): unknown keys, duplicate
keys, binary floats and missing required values are refused with a finite
``RiskServiceConfigError`` code; no number is invented. Paths are resolved
against the directory of the configuration file after ``~`` and environment
variable expansion. Secret key files must be owner-only regular files on POSIX.
Key material is read into memory and never printed, logged or echoed.

This module does not import ``bt_api_risk`` (the process that composes the
pre-trade core imports it lazily, INV64-02). It parses configuration; it
grants nothing. The Gateway and the Risk ServiceHost still authenticate every
principal and authorize every operation.
"""

from __future__ import annotations

import json
import math
import ntpath
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS, RISK_ORDER_OPERATIONS

RISK_SERVICE_CONFIG_SCHEMA = "bt_api_risk.service.v1"
MAX_RESERVATION_TTL_SECONDS = Decimal(30)  # the Gateway delegate reservation upper bound
_MAX_CONFIG_BYTES = 1_048_576
_MAX_YAML_DEPTH = 64
_MAX_YAML_WORK = 100_000
_MAX_KEY_FILE_BYTES = 4096
PRINCIPAL_ROLES = frozenset({"strategy", "execution", "operator"})
_ORDER_ONLY = RISK_ORDER_OPERATIONS
_PARENT_OPERATIONS = frozenset({"risk.parent.reserve", "risk.parent.release", "risk.parent.status"})
_R04_IMPLEMENTATION = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:"
    r"[A-Za-z_][A-Za-z0-9_]*\Z", re.ASCII
)


class RiskServiceConfigError(ValueError):
    """Finite configuration error; never carries a value, path or key."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RiskServiceConfigError(code)


def _ref(value: object, limit: int = 256) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and value.isprintable()
        and len(value.encode("utf-8", errors="replace")) <= limit
    )


def _account_key_id(value: object) -> bool:
    """``AccountKey.account_key_id`` form: ``account-`` and 64 lowercase hex digits."""
    return (
        type(value) is str
        and len(value) == 72
        and value.startswith("account-")
        and all(ch in "0123456789abcdef" for ch in value[8:])
    )


# ----------------------------------------------------------------------------- loading


def _unique_pairs(pairs):
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("RISK_SERVICE_CONFIG_DUPLICATE_KEY")
        result[key] = value
    return result


def _reject_float(_text):
    _fail("RISK_SERVICE_CONFIG_FLOAT_REFUSED")


def _reject_constant(_text):
    _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")


class _YamlBudget:
    def __init__(self):
        self.used = 0

    def charge(self, count):
        if count > _MAX_YAML_WORK - self.used:
            _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")
        self.used += count


def _yaml_load(text: str) -> Any:
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml is a declared dependency
        _fail("RISK_SERVICE_CONFIG_YAML_UNAVAILABLE")

    class _StrictLoader(yaml.SafeLoader):
        def __init__(self, stream):
            super().__init__(stream)
            self._budget = _YamlBudget()
            self._depth = 0

        def compose_node(self, parent, index):
            self._budget.charge(1)  # includes alias events
            if self._depth >= _MAX_YAML_DEPTH:
                _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")
            self._depth += 1
            try:
                return super().compose_node(parent, index)
            finally:
                self._depth -= 1

        def construct_document(self, node):
            # Count every alias occurrence before construction, including shared
            # subtrees. Detect cycles without recursively walking the graph.
            self._budget.charge(1)
            stack = [(node, 0, False)]
            ancestors = set()
            while stack:
                current, depth, leaving = stack.pop()
                if leaving:
                    ancestors.remove(id(current))
                    continue
                if depth >= _MAX_YAML_DEPTH or id(current) in ancestors:
                    _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")
                if isinstance(current, yaml.nodes.MappingNode):
                    count = 2 * len(current.value)
                elif isinstance(current, yaml.nodes.SequenceNode):
                    count = len(current.value)
                else:
                    continue
                self._budget.charge(count)  # before putting children on the stack
                ancestors.add(id(current))
                stack.append((current, depth, True))
                if isinstance(current, yaml.nodes.MappingNode):
                    for key, value in reversed(current.value):
                        stack.append((value, depth + 1, False))
                        stack.append((key, depth + 1, False))
                else:
                    for child in reversed(current.value):
                        stack.append((child, depth + 1, False))
            return super().construct_document(node)

        def flatten_mapping(self, node, depth=0):
            # Match SafeLoader's merge ordering while charging scans and copies
            # before they occur. Build retained pairs once instead of repeated
            # list deletion, which is quadratic for many merge keys.
            if depth >= _MAX_YAML_DEPTH:
                _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")
            self._budget.charge(len(node.value))
            merge, retained = [], []
            had_merge = False
            for key, value in node.value:
                if key.tag == "tag:yaml.org,2002:merge":
                    had_merge = True
                    if isinstance(value, yaml.nodes.MappingNode):
                        self.flatten_mapping(value, depth + 1)
                        self._budget.charge(len(value.value))
                        merge.extend(value.value)
                    elif isinstance(value, yaml.nodes.SequenceNode):
                        self._budget.charge(len(value.value))
                        submerge = []
                        for child in value.value:
                            if not isinstance(child, yaml.nodes.MappingNode):
                                _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")
                            self.flatten_mapping(child, depth + 1)
                            submerge.append(child.value)
                        for pairs in reversed(submerge):
                            self._budget.charge(len(pairs))
                            merge.extend(pairs)
                    else:
                        _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")
                else:
                    if key.tag == "tag:yaml.org,2002:value":
                        key.tag = "tag:yaml.org,2002:str"
                    retained.append((key, value))
            if had_merge:
                self._budget.charge(len(merge) + len(retained))
                node.value = merge + retained

    def mapping(loader, node, deep=False):
        loader.flatten_mapping(node)
        pairs = []
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            pairs.append((key, loader.construct_object(value_node, deep=deep)))
        return _unique_pairs(pairs)

    def refuse_float(_loader, _node):
        _fail("RISK_SERVICE_CONFIG_FLOAT_REFUSED")

    _StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    _StrictLoader.add_constructor("tag:yaml.org,2002:float", refuse_float)
    try:
        return yaml.load(text, Loader=_StrictLoader)  # strict SafeLoader subclass
    except RiskServiceConfigError:
        raise
    except Exception:
        raise RiskServiceConfigError("RISK_SERVICE_CONFIG_INVALID_DOCUMENT") from None


def load_document(text: str) -> Any:
    """Parse JSON (when the text starts with ``{``) or strict YAML; floats refused."""
    if type(text) is not str or len(text.encode("utf-8", errors="replace")) > _MAX_CONFIG_BYTES:
        _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")
    if text.lstrip().startswith("{"):
        try:
            return json.loads(
                text,
                object_pairs_hook=_unique_pairs,
                parse_float=_reject_float,
                parse_constant=_reject_constant,
            )
        except RiskServiceConfigError:
            raise
        except (ValueError, RecursionError):
            _fail("RISK_SERVICE_CONFIG_INVALID_DOCUMENT")
    return _yaml_load(text)


def _mapping(value: object, *, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict:
    if type(value) is not dict:
        _fail("RISK_SERVICE_CONFIG_SHAPE_INVALID")
    keys = set(value)
    if not set(required) <= keys:
        _fail("RISK_SERVICE_CONFIG_KEY_MISSING")
    if keys - set(required) - set(optional):
        _fail("RISK_SERVICE_CONFIG_KEY_UNKNOWN")
    return value


def _text(value: object, code: str = "RISK_SERVICE_CONFIG_VALUE_INVALID") -> str:
    if not _ref(value):
        _fail(code)
    return value  # type: ignore[return-value]


def _decimal(value: object, *, code: str = "RISK_SERVICE_CONFIG_NUMBER_INVALID") -> Decimal:
    if type(value) is bool or type(value) is float:
        _fail("RISK_SERVICE_CONFIG_FLOAT_REFUSED" if type(value) is float else code)
    if type(value) is not int and not (
        type(value) is str and value and value == value.strip()
    ):
        _fail(code)
    try:
        number = Decimal(value)
    except (InvalidOperation, OverflowError, ValueError):
        _fail(code)
    if not number.is_finite() or number <= 0:
        _fail(code)
    return number


def _seconds(value: object, *, maximum: Decimal | None = None) -> float:
    number = _decimal(value)
    if maximum is not None and number > maximum:
        _fail("RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE")
    try:
        seconds = float(number)
    except (OverflowError, ValueError):
        _fail("RISK_SERVICE_CONFIG_NUMBER_INVALID")
    if not math.isfinite(seconds) or seconds <= 0:
        _fail("RISK_SERVICE_CONFIG_NUMBER_INVALID")
    return seconds


def _nanosecond_seconds(value: object, *, maximum: Decimal) -> float:
    seconds = _seconds(value, maximum=maximum)
    # Match the Risk conversion to integer nanoseconds.
    nanoseconds = int(seconds * 1_000_000_000)
    if nanoseconds < 1:
        _fail("RISK_SERVICE_CONFIG_NUMBER_INVALID")
    # Construct the exact seconds represented by the integer without applying
    # the active Decimal context; multiplication or scaleb could round it.
    quantized_seconds = Decimal((0, tuple(int(digit) for digit in str(nanoseconds)), -9))
    if quantized_seconds > _decimal(value):
        _fail("RISK_SERVICE_CONFIG_NUMBER_INVALID")
    return seconds


def _int(value: object, *, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        _fail("RISK_SERVICE_CONFIG_NUMBER_INVALID")
    return value


def _resolve_windows_path(raw: str, base: str) -> str:
    """Resolve Windows path syntax without accessing a filesystem.

    Drive-relative and root-relative paths depend on process drive state rather
    than the configuration directory, so they are not accepted.
    """
    expanded = ntpath.expanduser(ntpath.expandvars(raw))
    if "$" in expanded or expanded.startswith("~") or re.search(r"%[^%]+%", expanded):
        _fail("RISK_SERVICE_CONFIG_PATH_UNRESOLVED")
    path = PureWindowsPath(expanded)
    if not path.is_absolute():
        if path.drive or path.root or not PureWindowsPath(base).is_absolute():
            _fail("RISK_SERVICE_CONFIG_PATH_INVALID")
        expanded = ntpath.join(base, expanded)
    return ntpath.normpath(expanded)


def resolve_path(raw: object, base: Path) -> Path:
    """Expand ``~`` and environment variables, then resolve against ``base``."""
    if type(raw) is not str or not raw or raw != raw.strip() or "\x00" in raw:
        _fail("RISK_SERVICE_CONFIG_PATH_INVALID")
    if os.name == "nt":
        return Path(_resolve_windows_path(raw, str(base)))
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if "$" in expanded or expanded.startswith("~"):
        _fail("RISK_SERVICE_CONFIG_PATH_UNRESOLVED")
    path = Path(expanded)
    if not path.is_absolute():
        path = base / path
    return Path(os.path.normpath(path))


def read_key_file(path: Path, *, secret: bool) -> bytes:
    """Read one Z85 CURVE key (40 ASCII characters); secrets must be owner-only."""
    try:
        info = os.lstat(path)
    except OSError:
        _fail("RISK_SERVICE_KEY_FILE_UNAVAILABLE")
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_KEY_FILE_BYTES:
        _fail("RISK_SERVICE_KEY_FILE_UNAVAILABLE")
    if secret and os.name == "posix" and (
        info.st_mode & 0o077 or info.st_uid != os.getuid()
    ):
        _fail("RISK_SERVICE_KEY_FILE_PERMISSIONS")
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("RISK_SERVICE_KEY_FILE_UNAVAILABLE")
    key = raw.strip()
    if len(key) != 40 or not all(33 <= byte < 127 for byte in key):
        _fail("RISK_SERVICE_KEY_FILE_INVALID")
    return key


def ensure_private_directory(path: Path) -> Path:
    """Create the storage directory 0700 when missing; refuse a non-directory."""
    if path.exists():
        if not path.is_dir() or path.is_symlink():
            _fail("RISK_SERVICE_STORAGE_INVALID")
        return path
    try:
        path.mkdir(parents=True, mode=0o700)
        if os.name == "posix":
            os.chmod(path, 0o700)
    except OSError:
        _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
    return path


def restrict_file(path: Path) -> None:
    """Best-effort 0600 for a file this process created (POSIX only)."""
    if os.name == "posix" and path.exists() and not path.is_symlink():
        try:
            os.chmod(path, 0o600)
        except OSError:
            _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")


# ----------------------------------------------------------------------------- values


@dataclass(frozen=True)
class RiskInstrumentBinding:
    """One tradable contract: the policy id, its neutral key and optional static facts.

    ``multiplier``/``tick_size`` come from this configuration because the Gateway
    instrument metadata read is granted only to strategy principals; None means
    unknown (R-01b and R-02 tick bands then refuse instead of guessing).
    """

    instrument_id: str
    key: MarketInstrumentKey
    multiplier: Decimal | None
    tick_size: Decimal | None


@dataclass(frozen=True)
class RiskPrincipal:
    principal_ref: str
    role: str
    actor_ref: str
    strategy_refs: frozenset[str]
    operations: frozenset[str]
    public_key: bytes
    cancel_account_allowed: bool = False
    cancel_strategy_refs: frozenset[str] = frozenset()
    cancel_instrument_refs: frozenset[str] = frozenset()

    def __repr__(self) -> str:  # never print key material
        return f"RiskPrincipal(principal_ref={self.principal_ref!r}, role={self.role!r})"


@dataclass(frozen=True)
class RiskGatewayIdentity:
    principal_ref: str
    actor_ref: str
    public_key: bytes
    secret_key: bytes

    def __repr__(self) -> str:
        return f"RiskGatewayIdentity(principal_ref={self.principal_ref!r})"


@dataclass(frozen=True)
class RiskMarketConfig:
    source_ref: str
    entitlement_ref: str
    channel: str
    instrument_ids: tuple[str, ...]


@dataclass(frozen=True)
class RiskAccountSnapshotConfig:
    """The operator account snapshot read (G64-06).

    It names no account: the read is bound to ``account.account_scope``, the
    Gateway's hashed account reference (``AccountKey.account_key_id``). The raw
    account and sub-account numbers never appear in this configuration (plan 64
    section 6.1); ``account_id``, ``subaccount_id`` and ``venue`` are unknown keys.
    """

    resources: tuple[tuple[str, str], ...]  # (product_type, margin_pool)


@dataclass(frozen=True)
class RiskGatewayUpstream:
    service_id: str
    principal_ref: str
    instance_id: str
    scope_ref: str
    rpc_endpoint: str
    market_endpoint: str | None
    server_public_key: bytes
    delegate: RiskGatewayIdentity
    control: RiskGatewayIdentity
    grant_revisions: Mapping[str, str]
    market: RiskMarketConfig | None
    account_snapshot: RiskAccountSnapshotConfig | None

    def __repr__(self) -> str:
        return f"RiskGatewayUpstream(service_id={self.service_id!r})"


@dataclass(frozen=True)
class RiskR04Selection:
    """A v2 selection claim; it does not establish trust or authorization."""

    implementation: str
    implementation_version: int
    claim_contract_version: int
    observation_contract_version: int

    def __post_init__(self) -> None:
        if (type(self.implementation) is not str or len(self.implementation) > 256
                or _R04_IMPLEMENTATION.fullmatch(self.implementation) is None
                or type(self.implementation_version) is not int
                or not 1 <= self.implementation_version <= 2**31 - 1
                or type(self.claim_contract_version) is not int or self.claim_contract_version != 2
                or type(self.observation_contract_version) is not int
                or self.observation_contract_version != 2):
            _fail("RISK_SERVICE_CONFIG_RULES_INVALID")


def _r04_selection(value: object) -> RiskR04Selection | None:
    """Parse an explicit selection; the default and explicit v1 are identical."""
    item = _mapping(value, required=("mode",), optional=(
        "implementation", "implementation_version", "claim_contract_version",
        "observation_contract_version",
    ))
    mode = item["mode"]
    if type(mode) is not str:
        _fail("RISK_SERVICE_CONFIG_RULES_INVALID")
    if mode == "v1" and set(item) == {"mode"}:
        return None
    if mode != "v2" or set(item) != {
        "mode", "implementation", "implementation_version", "claim_contract_version",
        "observation_contract_version",
    }:
        _fail("RISK_SERVICE_CONFIG_RULES_INVALID")
    return RiskR04Selection(
        item["implementation"], item["implementation_version"],
        item["claim_contract_version"], item["observation_contract_version"],
    )


@dataclass(frozen=True)
class RiskServiceConfig:
    path: Path
    service_id: str
    service_principal_ref: str
    instance_id: str
    scope_ref: str
    rpc_bind: str
    server_public_key: bytes
    server_secret_key: bytes
    route: str
    provider: str
    environment: str
    account_scope: str
    execution_domain_ref: str
    instruments: Mapping[str, RiskInstrumentBinding]
    storage_directory: Path
    policy_file: Path
    trusted_rules: Mapping[str, int]
    principals: Mapping[str, RiskPrincipal]
    gateway: RiskGatewayUpstream
    call_timeout_seconds: float
    reservation_ttl_seconds: float
    loop_timeout_ms: int
    observation_poll_seconds: float
    maintain_seconds: float
    # maintain() asks the Gateway again about a CLAIMED/DISPATCHED permit with no observed
    # order once it is older than this, at most ``dispatch_recheck_limit`` permits per call.
    dispatch_recheck_seconds: float
    dispatch_recheck_limit: int
    market_poll_seconds: float | None
    account_snapshot_seconds: float | None
    account_snapshot_max_age_seconds: float | None
    strategy_financial_seconds: float | None = None
    strategy_financial_max_age_seconds: float | None = None
    r04_selection: RiskR04Selection | None = None
    strict_admin_cancel: bool = False

    def __repr__(self) -> str:
        return f"RiskServiceConfig(service_id={self.service_id!r})"

    @property
    def risk_database(self) -> Path:
        return self.storage_directory / "risk.sqlite"

    @property
    def registry_database(self) -> Path:
        return self.storage_directory / "risk_registry.sqlite"

    def instrument_for(self, venue: object, native_symbol: object) -> RiskInstrumentBinding | None:
        match = None
        for binding in self.instruments.values():
            if binding.key.venue == venue and binding.key.native_symbol == native_symbol:
                if match is not None:
                    return None
                match = binding
        return match


# ----------------------------------------------------------------------------- parsing


def _instruments(raw: object, provider: str, environment: str) -> Mapping[str, RiskInstrumentBinding]:
    if type(raw) is not dict or not raw:
        _fail("RISK_SERVICE_CONFIG_INSTRUMENTS_REQUIRED")
    result: dict[str, RiskInstrumentBinding] = {}
    for instrument_id, value in raw.items():
        _text(instrument_id)
        item = _mapping(
            value,
            required=("venue", "native_symbol", "product_type"),
            optional=("multiplier", "tick_size"),
        )
        venue, native = _text(item["venue"]), _text(item["native_symbol"])
        try:
            key = MarketInstrumentKey(
                provider=provider,
                venue=venue,
                environment=environment,
                product_type=_text(item["product_type"]),
                instrument_id=instrument_id,
                native_symbol=native,
            )
        except (TypeError, ValueError):
            _fail("RISK_SERVICE_CONFIG_INSTRUMENT_INVALID")
        result[instrument_id] = RiskInstrumentBinding(
            instrument_id,
            key,
            None if item.get("multiplier") is None else _decimal(item["multiplier"]),
            None if item.get("tick_size") is None else _decimal(item["tick_size"]),
        )
    return MappingProxyType(result)


def _principal_specs(raw: object) -> list[tuple]:
    """Validate all principal declarations before reading any key files."""
    if type(raw) is not list or not raw:
        _fail("RISK_SERVICE_CONFIG_PRINCIPALS_REQUIRED")
    specs = []
    seen: set[str] = set()
    for value in raw:
        item = _mapping(
            value,
            required=(
                "principal_ref",
                "role",
                "actor_ref",
                "strategy_refs",
                "operations",
                "public_key_file",
            ),
            optional=("cancel_scope_grants",),
        )
        principal_ref, actor_ref = _text(item["principal_ref"]), _text(item["actor_ref"])
        role = item["role"]
        if type(role) is not str or role not in PRINCIPAL_ROLES:
            _fail("RISK_SERVICE_CONFIG_ROLE_INVALID")
        strategies, operations = item["strategy_refs"], item["operations"]
        if (
            type(strategies) is not list
            or type(operations) is not list
            or not operations
            or any(not _ref(value) for value in strategies)
            or any(not _ref(value) for value in operations)
            or len(set(strategies)) != len(strategies)
            or len(set(operations)) != len(operations)
        ):
            _fail("RISK_SERVICE_CONFIG_PRINCIPAL_INVALID")
        strategy_refs = frozenset(_text(x) for x in strategies)
        ops = frozenset(_text(x) for x in operations)
        cancel_account = False
        cancel_strategies: frozenset[str] = frozenset()
        cancel_instruments: frozenset[str] = frozenset()
        if "cancel_scope_grants" in item:
            if role != "operator":
                _fail("RISK_SERVICE_CONFIG_CANCEL_GRANTS_INVALID")
            grant = _mapping(item["cancel_scope_grants"], required=("account", "strategies", "instruments"))
            grant_strategies, grant_instruments = grant["strategies"], grant["instruments"]
            if (type(grant["account"]) is not bool
                    or type(grant_strategies) is not list or type(grant_instruments) is not list
                    or len(grant_strategies) > 10_000 or len(grant_instruments) > 10_000
                    or any(not _ref(value) for value in grant_strategies)
                    or any(not _ref(value) or value.count(":") != 1
                           or not all(_ref(part) for part in value.split(":"))
                           for value in grant_instruments)
                    or len(set(grant_strategies)) != len(grant_strategies)
                    or len(set(grant_instruments)) != len(grant_instruments)):
                _fail("RISK_SERVICE_CONFIG_CANCEL_GRANTS_INVALID")
            cancel_account = grant["account"]
            cancel_strategies = frozenset(grant_strategies)
            cancel_instruments = frozenset(grant_instruments)
        if role == "operator":
            if strategy_refs or not ops <= RISK_ADMIN_OPERATIONS:
                _fail("RISK_SERVICE_CONFIG_OPERATIONS_INVALID")
        elif role == "strategy":
            # One strategy principal acts for exactly its own strategy; never for parents.
            if strategy_refs != {actor_ref} or not ops <= _ORDER_ONLY - _PARENT_OPERATIONS:
                _fail("RISK_SERVICE_CONFIG_OPERATIONS_INVALID")
        elif not strategy_refs or not ops <= _ORDER_ONLY:
            _fail("RISK_SERVICE_CONFIG_OPERATIONS_INVALID")
        if principal_ref in seen:
            _fail("RISK_SERVICE_CONFIG_PRINCIPAL_DUPLICATE")
        seen.add(principal_ref)
        specs.append((principal_ref, role, actor_ref, strategy_refs, ops, item["public_key_file"],
                      cancel_account, cancel_strategies, cancel_instruments))
    return specs


def _principals(raw: object, base: Path) -> Mapping[str, RiskPrincipal]:
    specs = _principal_specs(raw)
    result: dict[str, RiskPrincipal] = {}
    keys: set[bytes] = set()
    for (principal_ref, role, actor_ref, strategy_refs, ops, public_key_file,
         cancel_account, cancel_strategies, cancel_instruments) in specs:
        public = read_key_file(resolve_path(public_key_file, base), secret=False)
        if public in keys:
            _fail("RISK_SERVICE_CONFIG_PRINCIPAL_DUPLICATE")
        keys.add(public)
        result[principal_ref] = RiskPrincipal(
            principal_ref, role, actor_ref, strategy_refs, ops, public,
            cancel_account, cancel_strategies, cancel_instruments,
        )
    return MappingProxyType(result)


def _market_instrument_ids(raw: object, instruments: Mapping[str, RiskInstrumentBinding]) -> tuple[str, ...]:
    """Validate IDs before hashing or looking them up in configured instruments."""
    if (
        type(raw) is not list
        or not raw
        or any(not _ref(value) for value in raw)
        or len(set(raw)) != len(raw)
        or any(value not in instruments for value in raw)
    ):
        _fail("RISK_SERVICE_CONFIG_MARKET_INVALID")
    return tuple(raw)


def _account_snapshot(raw: object) -> RiskAccountSnapshotConfig:
    """Validate snapshot resources without reading keys or acquiring services."""
    snap = _mapping(raw, required=("resources",))
    resources = snap["resources"]
    if type(resources) is not list or not resources:
        _fail("RISK_SERVICE_CONFIG_ACCOUNT_SNAPSHOT_INVALID")
    pairs = []
    for entry in resources:
        entry = _mapping(entry, required=("product_type", "margin_pool"))
        pairs.append((_text(entry["product_type"]), _text(entry["margin_pool"])))
    if len(set(pairs)) != len(pairs):
        _fail("RISK_SERVICE_CONFIG_ACCOUNT_SNAPSHOT_INVALID")
    return RiskAccountSnapshotConfig(tuple(pairs))


def _market_declaration(
    item: dict, instruments: Mapping[str, RiskInstrumentBinding]
) -> tuple[RiskMarketConfig | None, str | None]:
    """Validate the complete market declaration before any key is read."""
    market = None
    market_endpoint = item.get("market_endpoint")
    if item.get("market") is not None:
        market_raw = _mapping(
            item["market"], required=("source_ref", "entitlement_ref", "channel", "instruments")
        )
        ids = _market_instrument_ids(market_raw["instruments"], instruments)
        market = RiskMarketConfig(
            _text(market_raw["source_ref"]),
            _text(market_raw["entitlement_ref"]),
            _text(market_raw["channel"]),
            ids,
        )
        if market_endpoint is None:
            _fail("RISK_SERVICE_CONFIG_MARKET_ENDPOINT_REQUIRED")
    if market_endpoint is not None:
        _text(market_endpoint)
    return market, market_endpoint


def _validate_key_paths(
    service: dict, specs: list, gateway: dict, delegate: dict, base: Path
) -> None:
    """Resolve declared key paths without opening any key files."""
    control = _mapping(
        gateway["control"],
        required=("principal_ref", "actor_ref", "public_key_file", "secret_key_file"),
    )
    for identity in (delegate, control):
        _text(identity["principal_ref"])
        _text(identity["actor_ref"])
    paths = [service["public_key_file"], service["secret_key_file"]]
    paths.extend(spec[5] for spec in specs)
    paths.append(gateway["server_public_key_file"])
    for identity in (delegate, control):
        paths.extend((identity["public_key_file"], identity["secret_key_file"]))
    for raw in paths:
        resolve_path(raw, base)


def _identity(raw: object, base: Path) -> RiskGatewayIdentity:
    item = _mapping(
        raw, required=("principal_ref", "actor_ref", "public_key_file", "secret_key_file")
    )
    return RiskGatewayIdentity(
        _text(item["principal_ref"]),
        _text(item["actor_ref"]),
        read_key_file(resolve_path(item["public_key_file"], base), secret=False),
        read_key_file(resolve_path(item["secret_key_file"], base), secret=True),
    )


def _gateway_declaration(raw: object) -> tuple[dict, dict, Mapping[str, str]]:
    item = _mapping(
        raw,
        required=(
            "service_id",
            "principal_ref",
            "instance_id",
            "scope_ref",
            "rpc_endpoint",
            "server_public_key_file",
            "delegate",
            "control",
        ),
        optional=("market_endpoint", "market", "account_snapshot"),
    )
    delegate_raw = _mapping(
        item["delegate"],
        required=(
            "principal_ref",
            "actor_ref",
            "public_key_file",
            "secret_key_file",
            "grant_revisions",
        ),
    )
    revisions = delegate_raw["grant_revisions"]
    if type(revisions) is not dict or not revisions:
        _fail("RISK_SERVICE_CONFIG_GRANT_REVISION_MISSING")
    grant_revisions = MappingProxyType({_text(k): _text(v) for k, v in revisions.items()})
    return item, delegate_raw, grant_revisions


def _gateway(raw: object, base: Path, instruments: Mapping[str, RiskInstrumentBinding]) -> RiskGatewayUpstream:
    item, delegate_raw, grant_revisions = _gateway_declaration(raw)
    market, market_endpoint = _market_declaration(item, instruments)
    snapshot = None
    if item.get("account_snapshot") is not None:
        snapshot = _account_snapshot(item["account_snapshot"])
    delegate = _identity(
        {k: v for k, v in delegate_raw.items() if k != "grant_revisions"}, base
    )
    control = _identity(item["control"], base)
    if control.principal_ref == delegate.principal_ref or control.public_key == delegate.public_key:
        _fail("RISK_SERVICE_CONFIG_GATEWAY_IDENTITY_REUSED")
    return RiskGatewayUpstream(
        service_id=_text(item["service_id"]),
        principal_ref=_text(item["principal_ref"]),
        instance_id=_text(item["instance_id"]),
        scope_ref=_text(item["scope_ref"]),
        rpc_endpoint=_text(item["rpc_endpoint"]),
        market_endpoint=market_endpoint,
        server_public_key=read_key_file(
            resolve_path(item["server_public_key_file"], base), secret=False
        ),
        delegate=delegate,
        control=control,
        grant_revisions=grant_revisions,
        market=market,
        account_snapshot=snapshot,
    )


def parse_risk_service_config(document: object, *, path: Path) -> RiskServiceConfig:
    """Validate an already parsed document; ``path`` is the configuration file."""
    # Refuse v2 before resolving paths or reading key files. The claim and
    # observation implementations are not yet wired into the service.
    if type(document) is dict and type(document.get("rules")) is dict:
        raw_rules = document["rules"]
        if "r04" in raw_rules:
            selection = _r04_selection(raw_rules["r04"])
            if selection is not None:
                _fail("RISK_SERVICE_R04_V2_UNAVAILABLE")
    base = Path(path).resolve().parent
    top = _mapping(
        document,
        required=(
            "schema",
            "service",
            "account",
            "storage",
            "policy",
            "principals",
            "gateway",
            "deadlines",
            "refresh",
        ),
        optional=("rules",),
    )
    if top["schema"] != RISK_SERVICE_CONFIG_SCHEMA:
        _fail("RISK_SERVICE_CONFIG_SCHEMA_UNSUPPORTED")
    service = _mapping(
        top["service"],
        required=(
            "service_id",
            "principal_ref",
            "instance_id",
            "scope_ref",
            "rpc_bind",
            "public_key_file",
            "secret_key_file",
        ),
        optional=("strict_admin_cancel",),
    )
    strict_admin_cancel = service.get("strict_admin_cancel", False)
    if type(strict_admin_cancel) is not bool:
        _fail("RISK_SERVICE_CONFIG_STRICT_ADMIN_CANCEL_INVALID")
    account = _mapping(
        top["account"],
        required=(
            "route",
            "provider",
            "environment",
            "account_scope",
            "execution_domain_ref",
            "instruments",
        ),
    )
    provider, environment = _text(account["provider"]), _text(account["environment"])
    instruments = _instruments(account["instruments"], provider, environment)
    storage = _mapping(top["storage"], required=("directory",))
    policy = _mapping(top["policy"], required=("file",))
    rules = _mapping(top.get("rules", {}), required=(), optional=("trusted_implementations", "r04"))
    r04_selection = None if "r04" not in rules else _r04_selection(rules["r04"])
    trusted_raw = rules.get("trusted_implementations", {})
    if type(trusted_raw) is not dict:
        _fail("RISK_SERVICE_CONFIG_RULES_INVALID")
    trusted: dict[str, int] = {}
    for name, version in trusted_raw.items():
        if not _ref(name) or ":" not in name or type(version) is not int or version <= 0:
            _fail("RISK_SERVICE_CONFIG_RULES_INVALID")
        trusted[name] = version
    # Reject malformed market and account-snapshot resources before key reads. A
    # complete Gateway mapping is still validated by _gateway below.
    specs = _principal_specs(top["principals"])
    gateway_raw, delegate_raw, grant_revisions = _gateway_declaration(top["gateway"])
    represented = set().union(*(spec[3] for spec in specs if spec[1] != "operator"))
    if not represented <= set(grant_revisions):
        _fail("RISK_SERVICE_CONFIG_GRANT_REVISION_MISSING")
    market, _ = _market_declaration(gateway_raw, instruments)
    if gateway_raw.get("account_snapshot") is not None:
        _account_snapshot(gateway_raw["account_snapshot"])
        if not _account_key_id(account["account_scope"]):
            _fail("RISK_SERVICE_CONFIG_ACCOUNT_SNAPSHOT_INVALID")
    refresh = _mapping(
        top["refresh"],
        required=(
            "loop_timeout_ms",
            "observation_poll_seconds",
            "maintain_seconds",
            "dispatch_recheck_limit",
        ),
        optional=(
            "market_poll_seconds",
            "account_snapshot_seconds",
            "account_snapshot_max_age_seconds",
            "strategy_financial_seconds",
            "strategy_financial_max_age_seconds",
        ),
    )
    market_poll = refresh.get("market_poll_seconds")
    if (market is None) != (market_poll is None):
        _fail("RISK_SERVICE_CONFIG_REFRESH_INVALID")
    if market_poll is not None:
        _seconds(market_poll, maximum=Decimal(3600))
    _validate_key_paths(service, specs, gateway_raw, delegate_raw, base)
    principals = _principals(top["principals"], base)
    gateway = _gateway(top["gateway"], base, instruments)
    deadlines = _mapping(
        top["deadlines"],
        required=("call_timeout_seconds", "reservation_ttl_seconds", "dispatch_recheck_seconds"),
    )
    snapshot_every = refresh.get("account_snapshot_seconds")
    snapshot_age = refresh.get("account_snapshot_max_age_seconds")
    if (gateway.market is None) != (market_poll is None):
        _fail("RISK_SERVICE_CONFIG_REFRESH_INVALID")
    if (gateway.account_snapshot is None) != (snapshot_every is None) or (
        snapshot_every is None
    ) != (snapshot_age is None):
        _fail("RISK_SERVICE_CONFIG_REFRESH_INVALID")
    if gateway.account_snapshot is not None and not _account_key_id(account["account_scope"]):
        # The snapshot read is bound to the Gateway's hashed account reference only.
        _fail("RISK_SERVICE_CONFIG_ACCOUNT_SNAPSHOT_INVALID")
    # G64-06(b) strategy financial read: enabled by giving both intervals, or neither.
    financial_every = refresh.get("strategy_financial_seconds")
    financial_age = refresh.get("strategy_financial_max_age_seconds")
    if (financial_every is None) != (financial_age is None):
        _fail("RISK_SERVICE_CONFIG_REFRESH_INVALID")
    server_public = read_key_file(resolve_path(service["public_key_file"], base), secret=False)
    server_secret = read_key_file(resolve_path(service["secret_key_file"], base), secret=True)
    return RiskServiceConfig(
        path=Path(path).resolve(),
        service_id=_text(service["service_id"]),
        service_principal_ref=_text(service["principal_ref"]),
        instance_id=_text(service["instance_id"]),
        scope_ref=_text(service["scope_ref"]),
        rpc_bind=_text(service["rpc_bind"]),
        server_public_key=server_public,
        server_secret_key=server_secret,
        route=_text(account["route"]),
        provider=provider,
        environment=environment,
        account_scope=_text(account["account_scope"]),
        execution_domain_ref=_text(account["execution_domain_ref"]),
        instruments=instruments,
        storage_directory=resolve_path(storage["directory"], base),
        policy_file=resolve_path(policy["file"], base),
        trusted_rules=MappingProxyType(trusted),
        principals=principals,
        gateway=gateway,
        call_timeout_seconds=_seconds(deadlines["call_timeout_seconds"], maximum=Decimal(300)),
        reservation_ttl_seconds=_nanosecond_seconds(
            deadlines["reservation_ttl_seconds"], maximum=MAX_RESERVATION_TTL_SECONDS
        ),
        loop_timeout_ms=_int(refresh["loop_timeout_ms"], low=1, high=60_000),
        observation_poll_seconds=_seconds(refresh["observation_poll_seconds"], maximum=Decimal(3600)),
        maintain_seconds=_seconds(refresh["maintain_seconds"], maximum=Decimal(3600)),
        dispatch_recheck_seconds=_nanosecond_seconds(
            deadlines["dispatch_recheck_seconds"], maximum=Decimal(3600)
        ),
        dispatch_recheck_limit=_int(refresh["dispatch_recheck_limit"], low=1, high=1024),
        market_poll_seconds=None
        if market_poll is None
        else _seconds(market_poll, maximum=Decimal(3600)),
        account_snapshot_seconds=None
        if snapshot_every is None
        else _seconds(snapshot_every, maximum=Decimal(3600)),
        account_snapshot_max_age_seconds=None
        if snapshot_age is None
        else _nanosecond_seconds(snapshot_age, maximum=Decimal(86400)),
        strategy_financial_seconds=None
        if financial_every is None
        else _seconds(financial_every, maximum=Decimal(3600)),
        strategy_financial_max_age_seconds=None
        if financial_age is None
        else _nanosecond_seconds(financial_age, maximum=Decimal(86400)),
        r04_selection=r04_selection,
        strict_admin_cancel=strict_admin_cancel,
    )


def load_risk_service_config(path: str | os.PathLike) -> RiskServiceConfig:
    """Read and validate one service configuration file (no network, no storage)."""
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size > _MAX_CONFIG_BYTES:
            _fail("RISK_SERVICE_CONFIG_UNAVAILABLE")
        text = path.read_text(encoding="utf-8")
    except RiskServiceConfigError:
        raise
    except (OSError, UnicodeDecodeError):
        _fail("RISK_SERVICE_CONFIG_UNAVAILABLE")
    return parse_risk_service_config(load_document(text), path=path)


__all__ = [
    "MAX_RESERVATION_TTL_SECONDS",
    "PRINCIPAL_ROLES",
    "RISK_SERVICE_CONFIG_SCHEMA",
    "RiskAccountSnapshotConfig",
    "RiskGatewayIdentity",
    "RiskGatewayUpstream",
    "RiskInstrumentBinding",
    "RiskMarketConfig",
    "RiskPrincipal",
    "RiskR04Selection",
    "RiskServiceConfig",
    "RiskServiceConfigError",
    "ensure_private_directory",
    "load_document",
    "load_risk_service_config",
    "parse_risk_service_config",
    "read_key_file",
    "resolve_path",
    "restrict_file",
]
