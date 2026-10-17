"""Host-level fixtures for the DIM09 v3 three-leg group service slice.

The harness composes the real SDK group services in one process, mirroring
``tests/unit/test_service_execution_group.py`` (the two-leg end-to-end node)
instead of spawning CURVE hosts: only the exchange leaf is synthetic, while
the wire request crosses the real coordinator service, typed borrowed-client
route, real Risk reservations adapter, real Execution facade, proof journal,
and durable SQLite stores.  The spawned-host harness of
``test_arb03_dim05_group_partial_fill_service.py`` is deliberately not reused
because it imports the in-flight ``service_host.py`` WIP.

Wire contract implemented by this harness (DIM09 step 2):

* v3 submit bodies use the exact five-key shape ``version``, ``group_id``,
  ``deadline_ns``, ``first_leg_id``, ``legs`` with per-leg keys ``leg_id``,
  ``account_ref``, ``intent``, ``ratio`` and exactly three legs in
  put -> future -> call order on one account.
* ``restart_owner`` models an account-owner process restart on the same
  durable stores (new service object, ``recover_interrupted_dispatches``).
* ``restart_coordinator`` models a coordinator restart: the two durable
  stores are closed and reopened on the same paths, a renewed registration
  epoch inspects the persisted snapshot, and the original-ID query path must
  reconcile an UNKNOWN leg without resubmitting it.
* ``Dim09ThreeLegLoopback.drop_submit_replies_for`` runs the real owner
  handler to completion (durable write committed) and only then raises, so
  the coordinator models a lost reply, never a lost write.

This harness quotes through the real production trusted quote builder
``bt_api_execution.group_budget.build_instrument_risk_group_budget_quote``
for every group version: the execution-side widening (worktree bytes, sha256
``0c7c7981375eaca3b5982db11afef500a58c48158933ad9df84c951a60ac90de``)
accepts unquoted requests with ``version in {1, 3}``, so the legacy two-leg
and the v3 three-leg flows both take the same trusted
``InstrumentRiskRegistry.assess`` path with no test-side replication.  The
execution widening is uncommitted in its submodule worktree and is evaluated
here as the artifact under review.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter,
    ExecutionScope,
    OrderIntent,
    OrderType,
    PortfolioGroupBudgetPolicy,
    Side,
    SqliteExecutionStore,
    SqliteOrderGroupStore,
    TimeInForce,
    build_instrument_risk_group_budget_quote,
    group_leg_intent_id,
    risk_account_scope_fingerprint,
)
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    AccountScope,
    DispatchEvidenceClass,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import BorrowedServiceClient
from bt_api_py.service_execution_group import (
    GROUP_ACCOUNT_OWNER_OPERATIONS,
    GROUP_COORDINATOR_OPERATIONS,
    ExecutionGroupOrderEntryService,
    GroupAccountRoute,
    GroupCoordinatorService,
    GroupCoordinatorTarget,
    GroupInstrumentRule,
)
from bt_api_py.service_execution_group_authority import (
    ExecutionGroupAuthority,
    ExecutionStoreGroupCompletionVerifier,
)
from bt_api_py.service_execution_group_client import (
    AcceptedGroupBindingStore,
    GroupOriginRunBindingStore,
)
from bt_api_py.service_execution_group_proofs import (
    GroupProofJournal,
    GroupProofRuntimeBundle,
    GroupProofSource,
    GroupProofTarget,
)
from bt_api_py.service_execution_group_proofs import (
    GroupReservationTarget as ProofReservationTarget,
)
from bt_api_py.service_group_reservations import (
    GroupReservationsService,
    GroupReservationTarget,
)
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    ServiceCallContext,
    TrustedGroupReservationPolicy,
    TrustedGroupResolutionPolicy,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from tests.unit._service_group_provider_fixtures import (
    _V2_FILL_SCHEMA,
    _V2_INSTRUMENT,
    _V2_METADATA_VERSION,
    _V2_ORDER_SCHEMA,
    _V2_PROVIDER_ACCOUNT,
    _V2SyntheticBtApiFeed,
)

_DOMAIN = "dim09:three-leg-host"
_STRATEGY = "dim09-strategy"
_RUN = "dim09-run"
_PORTFOLIO = "dim09-portfolio"
_COORDINATOR_INSTANCE = "dim09-group-coordinator"
_COORDINATOR_SCOPE_REF = "dim09-group-scope-v1"
_COORDINATOR_PRINCIPAL = "dim09-coordinator-principal"
_STRATEGY_PRINCIPAL = "dim09-strategy-principal"
_ACCOUNT = "account-a"
_OWNER_INSTANCE = "dim09-account-owner"
# The executor-defined put -> future -> call staging order.
LEG_IDS = ("leg-put", "leg-future", "leg-call")
LEG_SIDES = (Side.BUY, Side.SELL, Side.BUY)
LEG_RATIOS = ("1", "1", "1")
_LEGACY_IDS = ("leg-entry", "leg-hedge")
_LEGACY_SIDES = (Side.BUY, Side.SELL)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def _synthetic_plugin_discovery():
    """Restrict only installed provider discovery during synthetic BtApi setup."""

    import importlib

    from bt_api_base.plugins.loader import PluginLoader

    bt_api_module = importlib.import_module("bt_api_py.bt_api")
    original_discover = PluginLoader.__dict__["_discover_entry_points"]
    original_plugins_loaded = bt_api_module._plugins_loaded
    discovered_groups: list[str] = []
    restricted_groups: list[str] = []
    facts: dict[str, Any] = {
        "original_plugins_loaded": original_plugins_loaded,
        "discovered_groups": (),
        "restricted_groups": (),
        "delegated_groups": (),
        "loader_descriptor_restored": False,
        "plugins_loaded_flag_restored": False,
    }

    def discover(self, group: str):
        discovered_groups.append(group)
        if group == "bt_api.plugins":
            restricted_groups.append(group)
            return []
        return original_discover(self, group)

    setattr(PluginLoader, "_discover_entry_points", discover)
    bt_api_module._plugins_loaded = False
    try:
        yield facts
    finally:
        setattr(PluginLoader, "_discover_entry_points", original_discover)
        bt_api_module._plugins_loaded = original_plugins_loaded
        facts.update(
            discovered_groups=tuple(discovered_groups),
            restricted_groups=tuple(restricted_groups),
            delegated_groups=tuple(
                group for group in discovered_groups if group != "bt_api.plugins"
            ),
            loader_descriptor_restored=(
                PluginLoader.__dict__["_discover_entry_points"] is original_discover
            ),
            plugins_loaded_flag_restored=(
                bt_api_module._plugins_loaded is original_plugins_loaded
            ),
        )


class _ThreeLegMappedFeed(_V2SyntheticBtApiFeed):
    """The synthetic leaf, pinned to the exact typed v3 child order shape."""

    def __init__(
        self,
        ledger_path: str,
        initial_states: tuple[str, ...],
        provider_account: str,
        *,
        expected_provider_orders: tuple[tuple[str, Decimal, Decimal, str], ...] | None = None,
        expected_size_in_contracts: bool = False,
        expected_position_mode: str | None = None,
    ) -> None:
        super().__init__(ledger_path, initial_states, provider_account)
        if type(expected_size_in_contracts) is not bool:
            raise TypeError("expected_size_in_contracts must be bool")
        if expected_position_mode not in {None, "net"}:
            raise ValueError("synthetic OKX position mode must be net")
        if expected_provider_orders is not None and (
            type(expected_provider_orders) is not tuple
            or len(expected_provider_orders) != 3
            or any(
                type(item) is not tuple
                or len(item) != 4
                or type(item[0]) is not str
                or not item[0]
                or type(item[1]) is not Decimal
                or not item[1].is_finite()
                or item[1] <= 0
                or type(item[2]) is not Decimal
                or not item[2].is_finite()
                or item[2] <= 0
                or type(item[3]) is not str
                or item[3] not in {"buy-limit", "sell-limit"}
                for item in expected_provider_orders
            )
        ):
            raise TypeError("expected provider order terms must be a typed ordered tuple")
        self._expected_provider_orders = expected_provider_orders
        self._expected_size_in_contracts = expected_size_in_contracts
        self._expected_position_mode = expected_position_mode
        self.position_mode_query_count = 0
        self._provider_order_index = 0
        if expected_position_mode is not None:
            self.get_config = self._n5_get_config
            self.query_order = self._n5_query_order

    def _n5_get_config(self, *, extra_data: Any = None) -> dict[str, Any]:
        """Return the bounded OKX native fact used by BtApi mode resolution."""
        if self._expected_position_mode is None:
            raise AssertionError("position-mode query was not configured for this fixture")
        self.position_mode_query_count += 1
        return {"data": [{"posMode": "net_mode"}]}

    def _n5_query_order(
        self,
        symbol: str,
        order_id: str | None = None,
        *,
        client_order_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Accept the committed OKX DirectBackend's native query call shape."""
        if client_order_id is None:
            client_order_id = order_id
        elif order_id is not None and order_id != client_order_id:
            raise AssertionError("synthetic order query identifiers disagree")
        if type(client_order_id) is not str or not client_order_id:
            raise AssertionError("synthetic OKX order query requires a client order ID")
        return super().query_order(symbol, client_order_id, **kwargs)

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        *,
        client_order_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # Check the arguments the real DirectBackend/FeedAdapter actually
        # deliver from the typed mapper before recording a write. The default
        # remains the historical one-instrument fixture; ratio/multiplier
        # cases provide an exact, ordered expected-call tuple.
        if self._expected_provider_orders is None:
            assert symbol == _V2_INSTRUMENT
            assert Decimal(str(volume)) == Decimal("1"), volume
            assert Decimal(str(price)) == Decimal("100"), price
            assert order_type in {"buy-limit", "sell-limit"}
        else:
            assert self._provider_order_index < len(self._expected_provider_orders)
            expected = self._expected_provider_orders[self._provider_order_index]
            assert (symbol, Decimal(str(volume)), Decimal(str(price)), order_type) == expected
            self._provider_order_index += 1
        assert client_order_id.startswith("grp-") and len(client_order_id) == 44
        if self._expected_size_in_contracts:
            size_in_contracts = kwargs.pop("size_in_contracts", None)
            assert size_in_contracts is True
        else:
            assert "size_in_contracts" not in kwargs
        expected_kwargs = {
            "offset": "open",
            "post_only": False,
            "reduce_only": False,
            "time_in_force": "IOC",
        }
        if self._expected_position_mode is not None:
            expected_kwargs["position_side"] = self._expected_position_mode
        assert kwargs == expected_kwargs
        return super().make_order(
            symbol,
            volume,
            price,
            order_type,
            client_order_id=client_order_id,
            **kwargs,
        )


class _UnusedExecutionDispatch:
    """The group session's call-local dispatcher must own every provider write."""

    def submit(self, _intent: OrderIntent) -> object:
        raise AssertionError("ordinary Execution dispatcher bypassed group proof session")

    def cancel(self, _intent: object) -> object:
        raise AssertionError("group-only service must not dispatch cancellation")


class Dim09ThreeLegLoopback:
    """Borrowed RPC surface that invokes the real group service handlers."""

    state = "registered"
    healthy = True

    def __init__(
        self,
        reservation_context: ServiceCallContext,
        entry_context: ServiceCallContext,
        query_context: ServiceCallContext,
        *,
        drop_submit_replies_for: tuple[str, ...] = (),
    ) -> None:
        self.reservation_context = reservation_context
        self.entry_context = entry_context
        self.query_context = query_context
        self.reservations: GroupReservationsService | None = None
        self.orders: ExecutionGroupOrderEntryService | None = None
        self.calls: list[tuple[str, str, str, str, dict[str, Any]]] = []
        self.replies: list[tuple[str, str, dict[str, Any]]] = []
        # Lost-reply injection: the owner handler still runs to completion and
        # commits its durable write; only the reply never reaches the
        # coordinator.
        self.drop_submit_replies_for = frozenset(drop_submit_replies_for)

    def call(
        self,
        mount: str,
        operation: str,
        arguments: dict[str, Any],
        *,
        account_ref: str | None = None,
        portfolio_ref: str | None = None,
        scope_ref: str | None = None,
        max_reply_message_bytes: int | None = None,
    ) -> dict[str, Any]:
        if (
            account_ref is None
            or portfolio_ref is None
            or scope_ref is None
            or max_reply_message_bytes != 1_000_000
        ):
            raise AssertionError("group RPC omitted its bounded trusted route")
        self.calls.append((mount, operation, account_ref, scope_ref, dict(arguments)))
        if mount == "upstream.account_reservations" and self.reservations is not None:
            reply = self.reservations.handle(
                operation,
                context=self.reservation_context,
                arguments=arguments,
            )
            self.replies.append((mount, operation, dict(reply)))
            return reply
        if mount == "upstream.orders" and self.orders is not None:
            reply = self.orders.handle(
                operation,
                context=self.entry_context,
                arguments=arguments,
            )
            self.replies.append((mount, operation, dict(reply)))
            if (
                operation == "orders.submit_group_leg"
                and arguments["intent"]["intent_id"] in self.drop_submit_replies_for
            ):
                raise ConnectionError("dim09 synthetic submit reply loss")
            return reply
        if mount == "upstream.order_query" and self.orders is not None:
            reply = self.orders.handle(
                operation,
                context=self.query_context,
                arguments=arguments,
            )
            self.replies.append((mount, operation, dict(reply)))
            return reply
        raise AssertionError(f"unexpected or unbound group RPC route: {mount}/{operation}")

    def operation_names(self, *, since: int = 0) -> list[str]:
        return [call[1] for call in self.calls[since:]]

    def submitted_intent_ids(self, *, since: int = 0) -> list[str]:
        return [
            call[4]["intent"]["intent_id"]
            for call in self.calls[since:]
            if call[1] == "orders.submit_group_leg"
        ]

    def mark(self) -> int:
        return len(self.calls)


def _strategy_scope(*, run_id: str | None = _RUN) -> WireScope:
    return WireScope(
        execution_domain_ref=_DOMAIN,
        account_refs=(_ACCOUNT,),
        strategy_ref=_STRATEGY,
        run_id=run_id,
        portfolio_ref=_PORTFOLIO,
    )


def _registration(
    now_ns: int,
    *,
    registration_id: str = "dim09-strategy-registration-a",
    service_epoch: str = "dim09-strategy-epoch-a",
) -> ActiveRegistrationContext:
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _STRATEGY_PRINCIPAL,
        actor_ref=_STRATEGY,
    )
    context = ServiceCallContext(
        identity=identity,
        scope=_strategy_scope(),
        registration_id=registration_id,
        principal_ref=_STRATEGY_PRINCIPAL,
    )
    return ActiveRegistrationContext(
        context=context,
        instance_id=_COORDINATOR_INSTANCE,
        port="service.order_group_entry",
        scope_ref=_COORDINATOR_SCOPE_REF,
        operations=GROUP_COORDINATOR_OPERATIONS,
        write_operations=frozenset({"groups.submit"}),
        service_epoch=service_epoch,
        policy_version="dim09-policy-v1",
        requirements_version="dim09-requirements-v1",
        expires_at=now_ns / 1_000_000_000 + 3_600,
        strategy_write_run_id=_RUN,
    )


def _budget_binding(
    metadata: InstrumentRiskMetadata | tuple[InstrumentRiskMetadata, ...],
    *,
    portfolio_ref: str = _PORTFOLIO,
) -> tuple[PortfolioGroupBudgetPolicy, Any]:
    policy = PortfolioGroupBudgetPolicy(
        portfolio_ref=portfolio_ref,
        policy_id="dim09-portfolio-budget-v1",
        policy_version=1,
        valuation_unit="USD",
        max_group_gross_notional=Decimal("1000000"),
        max_active_gross_notional=Decimal("10000000"),
        max_active_groups=100,
    )
    registry = InstrumentRiskRegistry(metadata if type(metadata) is tuple else (metadata,))
    return policy, partial(
        build_instrument_risk_group_budget_quote,
        registry=registry,
    )


class Dim09ThreeLegCluster:
    """One in-process deployment of the real coordinator + account owner."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: Any,
        *,
        initial_states: tuple[str, ...] = ("filled", "filled", "filled"),
        group_admission: Any = None,
        drop_submit_replies_for: tuple[str, ...] = (),
        supports_close: bool = False,
        instrument_metadata: tuple[InstrumentRiskMetadata, ...] | None = None,
        base_units_per_order_unit_by_instrument: Mapping[str, Decimal] | None = None,
        expected_provider_orders: tuple[tuple[str, Decimal, Decimal, str], ...] | None = None,
        provider_exchange_name: str = "FIXTURE___SPOT",
        mapper_exchange_name: str | None = None,
    ) -> None:
        if type(supports_close) is not bool:
            raise TypeError("supports_close must be bool")
        if provider_exchange_name not in {"FIXTURE___SPOT", "OKX___SWAP"}:
            raise ValueError("provider exchange must be an allowed synthetic fixture venue")
        if mapper_exchange_name is None:
            mapper_exchange_name = (
                "BINANCE___SPOT"
                if provider_exchange_name == "FIXTURE___SPOT"
                else provider_exchange_name
            )
        if mapper_exchange_name not in {"BINANCE___SPOT", "OKX___SWAP"}:
            raise ValueError("mapper exchange must select a committed fixture mapper")
        self.tmp_path = tmp_path
        self.supports_close = supports_close
        self._stack = ExitStack()
        self._extra_closers: list[Any] = []
        try:
            self._build(
                monkeypatch,
                initial_states,
                group_admission,
                drop_submit_replies_for,
                supports_close,
                instrument_metadata,
                base_units_per_order_unit_by_instrument,
                expected_provider_orders,
                provider_exchange_name,
                mapper_exchange_name,
            )
        except BaseException:
            self.close()
            raise

    # ------------------------------------------------------------------ build

    def _build(
        self,
        monkeypatch: Any,
        initial_states: tuple[str, ...],
        group_admission: Any,
        drop_submit_replies_for: tuple[str, ...],
        supports_close: bool,
        instrument_metadata: tuple[InstrumentRiskMetadata, ...] | None,
        base_units_per_order_unit_by_instrument: Mapping[str, Decimal] | None,
        expected_provider_orders: tuple[tuple[str, Decimal, Decimal, str], ...] | None,
        provider_exchange_name: str,
        mapper_exchange_name: str,
    ) -> None:
        tmp_path = self.tmp_path
        now_ns = time.time_ns()
        self.deadline_ns = now_ns + 120_000_000_000
        self.group_admission = group_admission
        self.account_ref = _ACCOUNT
        provider_name = provider_exchange_name.partition("___")[0].casefold()
        execution_scope = ExecutionScope(
            provider_name, "simulation", _ACCOUNT, _STRATEGY, "20261016"
        )
        self.execution_scope = execution_scope
        self.owner_scope = WireScope(
            _DOMAIN,
            account_ref=_ACCOUNT,
            strategy_ref=_STRATEGY,
            portfolio_ref=_PORTFOLIO,
        )
        self.strategy_template = WireScope(
            _DOMAIN,
            account_refs=(_ACCOUNT,),
            strategy_ref=_STRATEGY,
            portfolio_ref=_PORTFOLIO,
        )
        if instrument_metadata is None:
            metadata_records = (
                InstrumentRiskMetadata(
                    instrument=_V2_INSTRUMENT,
                    metadata_version=_V2_METADATA_VERSION,
                    as_of_ns=now_ns - 1_000_000_000,
                    expires_at_ns=now_ns + 3_600_000_000_000,
                    tick_size=Decimal("0.01"),
                    quantity_step=Decimal("0.001"),
                    contract_multiplier=Decimal("1"),
                    max_gross_notional=Decimal("100000"),
                    taker_fee_bps=Decimal("0"),
                    fixed_fee=Decimal("0"),
                    max_slippage_bps=Decimal("0"),
                    quantity_unit="base",
                    valuation_unit="USD",
                    quote_currency="USD",
                ),
            )
        else:
            if (
                type(instrument_metadata) is not tuple
                or not instrument_metadata
                or any(type(item) is not InstrumentRiskMetadata for item in instrument_metadata)
                or len({item.instrument for item in instrument_metadata})
                != len(instrument_metadata)
            ):
                raise TypeError("instrument_metadata must be a unique typed tuple")
            metadata_records = instrument_metadata
        metadata = metadata_records[0]
        self.metadata = metadata
        self.instrument_metadata = MappingProxyType(
            {item.instrument: item for item in metadata_records}
        )
        if base_units_per_order_unit_by_instrument is None:
            base_unit_scales = {item.instrument: Decimal("1") for item in metadata_records}
        else:
            if not isinstance(base_units_per_order_unit_by_instrument, Mapping):
                raise TypeError("base-unit scales must be a mapping")
            base_unit_scales = dict(base_units_per_order_unit_by_instrument)
            if set(base_unit_scales) != set(self.instrument_metadata) or any(
                type(value) is not Decimal or not value.is_finite() or value <= 0
                for value in base_unit_scales.values()
            ):
                raise TypeError("base-unit scales must cover every configured instrument")
        self.base_units_per_order_unit_by_instrument = MappingProxyType(base_unit_scales)
        registry = InstrumentRiskRegistry(metadata_records)
        provider_account = _V2_PROVIDER_ACCOUNT
        self.provider_exchange_name = provider_exchange_name
        self.mapper_exchange_name = mapper_exchange_name
        risk_scope = AccountScope(provider_name, provider_account, "simulation")
        self.risk_scope = risk_scope
        risk_target = RiskOrderTarget(
            account_scope=risk_scope,
            strategy_id=_STRATEGY,
            exchange_name=provider_exchange_name,
            provider_account_id=provider_account,
            registry=registry,
        )
        self.provider = _ThreeLegMappedFeed(
            str(tmp_path / "dim09-provider.sqlite3"),
            tuple(initial_states),
            provider_account,
            expected_provider_orders=expected_provider_orders,
            expected_size_in_contracts=mapper_exchange_name == "OKX___SWAP",
            expected_position_mode=("net" if provider_exchange_name == "OKX___SWAP" else None),
        )

        # The default fixture uses a synthetic Binance-shaped spot mapper.
        # N5 may bind the committed OKX mapper function locally to the synthetic
        # venue alias while keeping the owner scope correctly classified as
        # simulation. A separate focused node exercises the actual OKX route.
        from bt_api_py._contracts.models import OrderRequest as BtOrderRequest
        from bt_api_py._contracts.models import OrderType as BtOrderType
        from bt_api_py._contracts.models import Side as BtSide
        from bt_api_py._venue_mappers import MAPPERS

        fixture_mapper = MAPPERS[mapper_exchange_name]
        preflight_quantity_unit = (
            "contracts" if mapper_exchange_name == "OKX___SWAP" else "base"
        )
        mapped_preflight = fixture_mapper(
            BtOrderRequest(
                symbol=_V2_INSTRUMENT,
                side=BtSide.BUY,
                order_type=BtOrderType.LIMIT,
                quantity=Decimal("1"),
                account_id=provider_account,
                client_order_id="dim09-fixture-preflight",
                price=Decimal("100"),
                time_in_force="IOC",
                reduce_only=False,
                idempotency_key="dim09-fixture-preflight",
                quantity_unit=preflight_quantity_unit,
                offset="open",
            )
        )
        expected_preflight = {
            "symbol": _V2_INSTRUMENT,
            "vol": "1",
            "price": "100",
            "order_type": "buy-limit",
            "offset": "open",
            "post_only": False,
            "client_order_id": "dim09-fixture-preflight",
            "reduce_only": False,
            "time_in_force": "IOC",
        }
        if mapper_exchange_name == "OKX___SWAP":
            expected_preflight["size_in_contracts"] = True
        assert mapped_preflight == expected_preflight
        if provider_exchange_name == "FIXTURE___SPOT":
            monkeypatch.setitem(MAPPERS, provider_exchange_name, fixture_mapper)

        with _synthetic_plugin_discovery() as plugin_discovery_facts:
            bt_api = BtApi(debug=False)
        self.synthetic_plugin_discovery_facts = plugin_discovery_facts
        assert plugin_discovery_facts["restricted_groups"] == ("bt_api.plugins",)
        assert plugin_discovery_facts["loader_descriptor_restored"] is True
        assert plugin_discovery_facts["plugins_loaded_flag_restored"] is True
        self._stack.callback(bt_api.close)
        bt_api.exchange_feeds[provider_exchange_name] = self.provider

        execution_store = SqliteExecutionStore(tmp_path / "dim09-execution.sqlite3")
        self._stack.callback(execution_store.close)
        risk_order_journal = RiskOrderJournal(
            tmp_path / "dim09-risk-order.sqlite3", writer_id="dim09-risk-order-owner"
        )
        proof_journal = GroupProofJournal(
            tmp_path / "dim09-proof.sqlite3", writer_id="dim09-proof-owner"
        )
        self._stack.callback(proof_journal.close)
        self.execution_store = execution_store
        self.proof_journal = proof_journal

        provider_contract = RiskBtApiProviderContract(
            exchange_name=provider_exchange_name,
            profile_id="service-group-provider-v2",
            order_schema=_V2_ORDER_SCHEMA,
            fill_schema=_V2_FILL_SCHEMA,
            client_order_id_query=True,
            client_order_id_query_unique=True,
            complete_order_snapshot=True,
            complete_fill_history=True,
            provider_accepts_client_order_id=True,
            provider_status_mapping=(
                ("cancelled", "canceled"),
                ("expired", "expired"),
                ("filled", "completed"),
                ("open", "accepted"),
                ("partial", "partial"),
                ("rejected", "rejected"),
            ),
            supported_order_types=("LIMIT",),
            supported_time_in_force=("GTC", "IOC") if supports_close else ("IOC",),
            supported_position_effects=("OPEN", "CLOSE") if supports_close else ("OPEN",),
            normalizer_schema="bt-api-order-source-provenance-v2",
            order_revision_field="order_revision",
            fill_revision_field="fill_revision",
            fill_watermark_field="fill_watermark",
            fee_amount_field="fee_amount",
            fee_currency_field="fee_currency",
            quote_currency_by_instrument=tuple(
                sorted(
                    (item.instrument, item.quote_currency or item.valuation_unit or "USD")
                    for item in metadata_records
                )
            ),
        )
        proof_target = GroupProofTarget(
            execution_scope=execution_scope,
            risk_target=risk_target,
            provider_contract=provider_contract,
            bt_api=bt_api,
            execution_store=execution_store,
            risk_order_journal=risk_order_journal,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
        )
        proof_source = GroupProofSource(proof_journal, {self.owner_scope: proof_target})
        self.proof_source = proof_source
        risk_gate = DurableRiskGate(
            tmp_path / "dim09-risk.sqlite3",
            RiskPolicy(
                "dim09-three-leg-risk-policy-v1",
                Decimal("100000"),
                max_increase_count=10,
                permit_ttl_seconds=240,
                require_strategy_allocation=False,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
            ),
            execution_journal_authority=proof_source,
        )
        self._stack.callback(risk_gate.close)
        risk_gate.set_strategy_allocation(
            risk_scope,
            _STRATEGY,
            "dim09-allocation-v1",
            max_notional=Decimal("100000"),
            notional_unit="USD",
        )
        self.risk_gate = risk_gate
        mapper = InstrumentRiskAdmissionMapper(
            risk_scope,
            registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda routed_scope: risk_gate.get_strategy_allocation(
                risk_scope, routed_scope.strategy_id
            ),
            wire_account_ref=_ACCOUNT,
        )
        risk_admission = DurableRiskAdmissionAdapter(risk_gate, mapper)
        account_admission = AccountScopedGroupAdmissionAdapter(
            risk_admission, account_scope=risk_scope
        )
        reservation_target = GroupReservationTarget(risk_scope, risk_admission)
        proof_reservation_target = ProofReservationTarget(risk_scope, risk_admission)
        bundle = GroupProofRuntimeBundle(
            risk_admission,
            account_admission,
            MappingProxyType({self.owner_scope: proof_reservation_target}),
            MappingProxyType({self.owner_scope: proof_target}),
            proof_source,
        )
        bundle.require_runtime_bundle(
            account_scoped_admission=account_admission,
            risk_admission=risk_admission,
        )
        self.risk_admission = risk_admission
        self.account_admission = account_admission

        accepted_store = AcceptedGroupBindingStore(tmp_path / "dim09-accepted.sqlite3")
        self._stack.callback(accepted_store.close)
        authority = ExecutionGroupAuthority(
            tmp_path / "dim09-authority.sqlite3",
            completion_verifier=ExecutionStoreGroupCompletionVerifier(
                execution_store, execution_scopes=(execution_scope,)
            ),
        )
        self._stack.callback(authority.close)
        self.accepted_store = accepted_store
        self.authority = authority

        service_identity = WireIdentity(
            WireRole.SERVICE, _COORDINATOR_PRINCIPAL, "dim09-coordinator-actor"
        )
        self.service_identity = service_identity
        reservation_scope_ref = "dim09-reservation-scope-a"
        entry_scope_ref = "dim09-order-entry-scope-a"
        query_scope_ref = "dim09-order-query-scope-a"
        self.entry_scope_ref = entry_scope_ref
        self.reservation_scope_ref = reservation_scope_ref
        self.query_scope_ref = query_scope_ref
        reservation_context = ServiceCallContext(
            identity=service_identity,
            scope=self.owner_scope,
            registration_id="dim09-reservation-registration",
            principal_ref=_COORDINATOR_PRINCIPAL,
        )
        entry_context = ServiceCallContext(
            identity=service_identity,
            scope=self.owner_scope,
            registration_id="dim09-order-entry-registration",
            principal_ref=_COORDINATOR_PRINCIPAL,
        )
        query_context = ServiceCallContext(
            identity=service_identity,
            scope=self.owner_scope,
            registration_id="dim09-order-query-registration",
            principal_ref=_COORDINATOR_PRINCIPAL,
        )
        self.reservation_context = reservation_context
        self.entry_context = entry_context
        self.query_context = query_context
        expiry = time.time() + 3600
        service_epoch = "dim09-owner-epoch-a"
        policy_version = "dim09-policy-v1"
        requirements_version = "dim09-requirements-v1"
        reservation_operations = GroupReservationsService.SUPPORTED_OPERATIONS
        reservation_registration = ActiveRegistrationContext(
            context=reservation_context,
            instance_id=_OWNER_INSTANCE,
            port="service.group_reservations",
            scope_ref=reservation_scope_ref,
            operations=reservation_operations,
            write_operations=frozenset({"reservations.reserve", "reservations.release"}),
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expiry,
            strategy_write_run_id=_RUN,
        )
        entry_operations = frozenset(
            {"orders.submit_group_leg", "orders.resolve_group_leg"}
        )
        entry_registration = ActiveRegistrationContext(
            context=entry_context,
            instance_id=_OWNER_INSTANCE,
            port="service.order_entry",
            scope_ref=entry_scope_ref,
            operations=entry_operations,
            write_operations=entry_operations,
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expiry,
            strategy_write_run_id=_RUN,
        )
        query_registration = ActiveRegistrationContext(
            context=query_context,
            instance_id=_OWNER_INSTANCE,
            port="service.order_query",
            scope_ref=query_scope_ref,
            operations=frozenset({"orders.query_group_leg"}),
            write_operations=frozenset(),
            service_epoch=service_epoch,
            policy_version=policy_version,
            requirements_version=requirements_version,
            expires_at=expiry,
            strategy_write_run_id=_RUN,
        )
        self.reservation_registration = reservation_registration
        self.entry_registration = entry_registration
        self.query_registration = query_registration
        reservation_policy = TrustedGroupReservationPolicy(
            _OWNER_INSTANCE,
            _COORDINATOR_PRINCIPAL,
            service_identity,
            reservation_scope_ref,
            self.owner_scope,
            _RUN,
            _ACCOUNT,
        )
        write_policy = TrustedGroupWritePolicy(
            _OWNER_INSTANCE,
            _COORDINATOR_PRINCIPAL,
            service_identity,
            entry_scope_ref,
            self.owner_scope,
            _RUN,
        )
        resolution_policy = TrustedGroupResolutionPolicy(
            _OWNER_INSTANCE,
            _COORDINATOR_PRINCIPAL,
            service_identity,
            entry_scope_ref,
            self.owner_scope,
            _RUN,
            _ACCOUNT,
        )
        self.reservation_policy = reservation_policy
        self.write_policy = write_policy
        self.resolution_policy = resolution_policy

        self.owner_service = self._build_owner_service(writer_id=_OWNER_INSTANCE)
        self._extra_closers.append(self.owner_service.close)
        assert self.owner_service.supported_operations == GROUP_ACCOUNT_OWNER_OPERATIONS
        assert self.owner_service.recover_interrupted_dispatches() == ()
        self.owner_service.maintain_registrations((entry_registration, query_registration))
        assert self.owner_service.ready is True

        authority.control.activate(
            entry_context,
            execution_scope=execution_scope,
            risk_account_scope_key=risk_scope.key,
        )
        reservation_service = GroupReservationsService(
            {self.owner_scope: reservation_target}, accepted_store, (reservation_policy,)
        )
        self._stack.callback(reservation_service.close)
        reservation_service.maintain_registrations((reservation_registration,))
        assert reservation_service.ready is True
        self.reservation_service = reservation_service

        loopback = Dim09ThreeLegLoopback(
            reservation_context,
            entry_context,
            query_context,
            drop_submit_replies_for=drop_submit_replies_for,
        )
        loopback.reservations = reservation_service
        loopback.orders = self.owner_service
        self.loopback = loopback
        self.borrowed_upstream = BorrowedServiceClient(loopback)

        coordinator_store = SqliteOrderGroupStore(tmp_path / "dim09-groups.sqlite3")
        self._stack.callback(coordinator_store.close)
        origin_run_bindings = GroupOriginRunBindingStore(tmp_path / "dim09-group-runs.sqlite3")
        self._stack.callback(origin_run_bindings.close)
        self.coordinator_store = coordinator_store
        self.origin_run_bindings = origin_run_bindings
        route = GroupAccountRoute(
            execution_scope=execution_scope,
            risk_scope_fingerprint=risk_account_scope_fingerprint(risk_scope.key),
            reservation_scope_ref=reservation_scope_ref,
            order_scope_ref=entry_scope_ref,
            query_scope_ref=query_scope_ref,
            instruments={
                item.instrument: GroupInstrumentRule(
                    metadata=item,
                    base_units_per_order_unit=base_unit_scales[item.instrument],
                    allowed_order_types=frozenset({OrderType.LIMIT}),
                )
                for item in metadata_records
            },
        )
        budget_policy, budget_quote_builder = _budget_binding(
            metadata_records, portfolio_ref=self.strategy_template.portfolio_ref
        )
        self.portfolio_budget_policy = budget_policy
        self.coordinator_target = GroupCoordinatorTarget(
            scope_ref=_COORDINATOR_SCOPE_REF,
            accounts={_ACCOUNT: route},
            max_group_lifetime_ns=300_000_000_000,
            portfolio_budget_policy=budget_policy,
            budget_quote_builder=budget_quote_builder,
        )
        self.coordinator_service = GroupCoordinatorService(
            coordinator_store,
            {self.strategy_template: self.coordinator_target},
            origin_run_bindings,
            self.borrowed_upstream,
            service_identity,
            instance_id=_COORDINATOR_INSTANCE,
            group_admission=group_admission,
        )
        self._extra_closers.append(self.coordinator_service.close)
        self.strategy_registration = _registration(now_ns)
        self.coordinator_service.maintain_registrations((self.strategy_registration,))
        assert self.coordinator_service.ready is True

    def _build_owner_service(self, *, writer_id: str) -> ExecutionGroupOrderEntryService:
        return ExecutionGroupOrderEntryService(
            self.execution_store,
            {self.owner_scope: self.execution_scope},
            _UnusedExecutionDispatch(),
            self.account_admission,
            self.accepted_store,
            self.authority.guard,
            self.proof_source,
            (self.write_policy,),
            (self.resolution_policy,),
            writer_id=writer_id,
        )

    # -------------------------------------------------------------- wire body

    def leg_intent(
        self,
        leg_id: str,
        side: Side,
        *,
        group_id: str,
        deadline_ns: int | None = None,
        instrument: str | None = None,
        quantity: Decimal = Decimal("1"),
        price: Decimal = Decimal("100"),
    ) -> dict[str, Any]:
        deadline = self.deadline_ns if deadline_ns is None else deadline_ns
        selected_instrument = self.metadata.instrument if instrument is None else instrument
        metadata = self.instrument_metadata.get(selected_instrument)
        if metadata is None:
            raise ValueError("leg instrument is not present in trusted test metadata")
        return OrderIntent.limit(
            intent_id=group_leg_intent_id(group_id, leg_id),
            scope=self.execution_scope,
            signal_id=f"dim09-signal-{leg_id}",
            instrument=selected_instrument,
            side=side,
            quantity=quantity,
            price=price,
            time_in_force=TimeInForce.IOC,
            metadata_version=metadata.metadata_version,
            deadline_ns=deadline,
            tags={INSTRUMENT_METADATA_DIGEST_TAG: metadata.digest},
        ).to_payload()

    def bound_intent_id(self, group_id: str, leg_id: str) -> str:
        return group_leg_intent_id(group_id, leg_id)

    def v3_body(
        self,
        group_id: str,
        *,
        leg_ids: tuple[str, ...] = LEG_IDS,
        sides: tuple[Side, ...] = LEG_SIDES,
        ratios: tuple[str, ...] = LEG_RATIOS,
        leg_instruments: tuple[str, ...] | None = None,
        quantities: tuple[Decimal, ...] | None = None,
        prices: tuple[Decimal, ...] | None = None,
        deadline_ns: int | None = None,
        version: object = 3,
    ) -> dict[str, Any]:
        deadline = self.deadline_ns if deadline_ns is None else deadline_ns
        instruments = (
            tuple(self.metadata.instrument for _ in LEG_IDS)
            if leg_instruments is None
            else leg_instruments
        )
        leg_quantities = tuple(Decimal("1") for _ in LEG_IDS) if quantities is None else quantities
        leg_prices = tuple(Decimal("100") for _ in LEG_IDS) if prices is None else prices
        if any(
            type(items) is not tuple or len(items) != 3
            for items in (instruments, leg_quantities, leg_prices)
        ):
            raise ValueError("v3 leg instruments, quantities, and prices require three items")
        return {
            "version": version,
            "group_id": group_id,
            "deadline_ns": deadline,
            "first_leg_id": leg_ids[0],
            "legs": [
                {
                    "leg_id": leg_id,
                    "account_ref": _ACCOUNT,
                    "ratio": ratio,
                    "intent": self.leg_intent(
                        leg_id,
                        side,
                        group_id=group_id,
                        deadline_ns=deadline,
                        instrument=instrument,
                        quantity=quantity,
                        price=price,
                    ),
                }
                for leg_id, side, ratio, instrument, quantity, price in zip(
                    leg_ids,
                    sides,
                    ratios,
                    instruments,
                    leg_quantities,
                    leg_prices,
                    strict=True,
                )
            ],
        }

    def legacy_body(
        self,
        group_id: str,
        *,
        leg_ids: tuple[str, ...] = _LEGACY_IDS,
        sides: tuple[Side, ...] = _LEGACY_SIDES,
        deadline_ns: int | None = None,
    ) -> dict[str, Any]:
        deadline = self.deadline_ns if deadline_ns is None else deadline_ns
        return {
            "group_id": group_id,
            "deadline_ns": deadline,
            "first_leg_id": leg_ids[0],
            "legs": [
                {
                    "leg_id": leg_id,
                    "account_ref": _ACCOUNT,
                    "intent": self.leg_intent(
                        leg_id, side, group_id=group_id, deadline_ns=deadline
                    ),
                }
                for leg_id, side in zip(leg_ids, sides)
            ],
        }

    # ------------------------------------------------------------- operations

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        return dict(
            self.coordinator_service.handle(
                "groups.submit",
                context=self.strategy_registration.context,
                arguments=body,
            )
        )

    def query(self, group_id: str) -> dict[str, Any]:
        return dict(
            self.coordinator_service.handle(
                "groups.query",
                context=self.strategy_registration.context,
                arguments={"group_id": group_id},
            )
        )

    def current_snapshot(self, group_id: str):
        from bt_api_execution import GroupOrderSnapshot

        reply = self.query(group_id)
        assert reply["ok"] is True, reply
        return GroupOrderSnapshot.from_payload(reply["result"]["snapshot"])

    # ---------------------------------------------------------------- restart

    def restart_owner(self) -> None:
        """Model an account-owner process restart on the same durable stores."""

        self.owner_service.close()
        # The restarted process reuses its configured writer identity, so the
        # durable writer lease is renewed under the same fencing generation.
        restarted = self._build_owner_service(writer_id=_OWNER_INSTANCE)
        self._extra_closers.append(restarted.close)
        restarted.maintain_registrations(
            (self.entry_registration, self.query_registration)
        )
        assert restarted.ready is True
        # A restarted owner must find no live dispatch latch to recover; the
        # committed child write is durable and served by query only.
        assert restarted.recover_interrupted_dispatches() == ()
        self.owner_service = restarted
        self.loopback.orders = restarted

    def restart_coordinator(self) -> None:
        """Reopen the coordinator's durable stores under a renewed epoch."""

        self.coordinator_service.close()
        self.coordinator_store.close()
        self.origin_run_bindings.close()
        coordinator_store = SqliteOrderGroupStore(self.tmp_path / "dim09-groups.sqlite3")
        self._extra_closers.append(coordinator_store.close)
        origin_run_bindings = GroupOriginRunBindingStore(
            self.tmp_path / "dim09-group-runs.sqlite3"
        )
        self._extra_closers.append(origin_run_bindings.close)
        service = GroupCoordinatorService(
            coordinator_store,
            {self.strategy_template: self.coordinator_target},
            origin_run_bindings,
            self.borrowed_upstream,
            self.service_identity,
            instance_id=_COORDINATOR_INSTANCE,
            group_admission=self.group_admission,
        )
        self._extra_closers.append(service.close)
        registration = _registration(
            time.time_ns(),
            registration_id="dim09-strategy-registration-b",
            service_epoch="dim09-strategy-epoch-b",
        )
        service.maintain_registrations((registration,))
        assert service.ready is True
        self.coordinator_store = coordinator_store
        self.origin_run_bindings = origin_run_bindings
        self.coordinator_service = service
        self.strategy_registration = registration

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        for closer in reversed(self._extra_closers):
            closer()
        self._extra_closers.clear()
        self._stack.close()
