"""Prepared PLACE coordination with a synthetic provider, without network I/O."""

from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.direct_place_protocol import PreparedPlaceRequest
from bt_api_base.direct_write_protocol import (
    DirectSubmitResult,
    DirectWriteError,
    NativeOrderIdentity,
    WriteAdmissionDenied,
    WriteOperation,
)

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.write_admission import WriteAdmission

ROUTE = "SYNTHETIC"


def make_request():
    return OrderRequest(
        symbol="rb",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        account_id="account-a",
        client_order_id="order-a",
        price=Decimal("10"),
        idempotency_key="key-a",
    )


class PreparedProvider:
    execution_ids_trading_day_scoped = True

    def __init__(self):
        self.generation, self.day = 1, "20300101"
        self.prepared, self.released, self.sends, self.grants = [], [], [], []
        self.throw = self.bad_identity = False
        self.observer = lambda: None

    def session_generation(self):
        return self.generation

    def trading_day(self):
        return self.day

    def prepare_order(self, request):
        identity = NativeOrderIdentity(
            ROUTE, "1", {"trading_day": self.day, "front_id": 1, "session_id": 2}, self.generation
        )
        prepared = PreparedPlaceRequest(
            "prep-a",
            identity,
            request.symbol,
            self.day,
            self.generation,
            {"RequestID": 1, "LimitPrice": "0x1.4000000000000p+3"},
            ("place-key-a",),
            {"source": "synthetic"},
        )
        self.prepared.append(prepared)
        return prepared

    def release_prepared_order(self, prepared):
        self.released.append(prepared)

    def submit_prepared_order(self, prepared, grant, verifier):
        self.grants.append(grant)
        verifier.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.PLACE,
            session_generation=self.generation,
        )
        self.sends.append(prepared)
        self.observer()
        if self.throw:
            raise OSError("synthetic native failure")
        identity = (
            replace(prepared.identity, client_order_id="wrong")
            if self.bad_identity
            else prepared.identity
        )
        return DirectSubmitResult(True, identity)


class Commit:
    def __init__(self):
        self.records = []
        self.hook = lambda record: None
        self.result = "marker-a"

    def commit(self, record):
        self.records.append(record)
        self.hook(record)
        return self.result


@pytest.fixture
def context():
    provider = PreparedProvider()
    admission = WriteAdmission({ROUTE: True})
    return provider, admission, DirectWriteCoordinator(admission, lambda _: provider), Commit()


def place(context, request=None):
    provider, admission, coordinator, commit = context
    request = make_request() if request is None else request
    return coordinator.place_prepared(
        ROUTE,
        request,
        commit_port=commit,
        host_scope="account-a",
        request_summary=request.to_dict(),
    )


def test_prepared_marker_before_native_and_correlator(context):
    provider, _, coordinator, commit = context

    def on_send():
        assert len(commit.records) == 1
        assert commit.records[0].prepared_place is provider.prepared[0]
        assert commit.records[0].to_dict()["prepared_place"]["request_keys"] == ["place-key-a"]

    provider.observer = on_send
    result = place(context)
    assert result.sent is True and result.marker_id == "marker-a" and len(provider.sends) == 1
    assert result.identity == provider.prepared[0].identity
    assert coordinator._correlator(ROUTE).lookup(result.identity, provider.day) == (
        "marker-a",
        "account-a",
    )
    with pytest.raises(WriteAdmissionDenied):
        context[1].consume(
            provider.grants[0],
            exchange_name=ROUTE,
            operation=WriteOperation.PLACE,
            session_generation=1,
        )


def test_commit_failure_never_sends_and_releases(context):
    provider, _, _, commit = context
    commit.result = None
    result = place(context)
    assert result.sent is False and result.marker_id is None
    assert result.error_code == "pre_dispatch_commit_failed"
    assert provider.sends == [] and provider.released == provider.prepared


@pytest.mark.parametrize("change", ["generation", "day", "request", "prepared", "admission"])
def test_changed_state_after_marker_never_sends(context, change):
    provider, admission, _, commit = context
    request = make_request()

    def mutate(_record):
        if change == "generation":
            provider.generation += 1
        elif change == "day":
            provider.day = "20300102"
        elif change == "request":
            object.__setattr__(request, "quantity", Decimal("2"))
        elif change == "prepared":
            object.__setattr__(provider.prepared[0], "symbol", "other")
        else:
            with admission._lock:
                admission._enabled[ROUTE] = False

    commit.hook = mutate
    result = place(context, request)
    assert result.sent is False and result.marker_id == "marker-a"
    assert provider.sends == [] and len(provider.released) == 1


@pytest.mark.parametrize("bad", ["exception", "identity"])
def test_after_submit_uncertain_never_fabricates_failure(context, bad):
    provider = context[0]
    provider.throw, provider.bad_identity = bad == "exception", bad == "identity"
    result = place(context)
    assert result.sent is None and result.error_code == "direct_submit_outcome_unknown"
    assert len(provider.sends) == 1 and provider.released == []


def test_prepared_path_never_falls_back_to_legacy(context):
    provider = context[0]
    provider.prepare_order = None
    legacy = []
    provider.reserve_order = lambda *_: legacy.append(True)
    with pytest.raises(DirectWriteError, match="direct_prepared_place_unavailable"):
        place(context)
    assert legacy == [] and provider.sends == []


def test_revoked_grant_never_reaches_synthetic_native(context, monkeypatch):
    provider, admission, _, _ = context
    issue = admission.issue

    def revoke(*args, **kwargs):
        grant = issue(*args, **kwargs)
        admission.revoke_grant(grant)
        return grant

    monkeypatch.setattr(admission, "issue", revoke)
    result = place(context)
    assert result.sent is None and result.error_code == "direct_submit_outcome_unknown"
    assert provider.sends == []


def test_generation_changed_after_grant_never_enters_provider_submit(context, monkeypatch):
    provider, admission, _, _ = context
    issue = admission.issue

    def change(*args, **kwargs):
        grant = issue(*args, **kwargs)
        provider.generation += 1
        return grant

    monkeypatch.setattr(admission, "issue", change)
    result = place(context)
    assert result.sent is False and result.marker_id == "marker-a"
    assert provider.grants == [] and provider.sends == [] and len(provider.released) == 1


def test_gateway_explicit_prepared_place_persists_same_marker(tmp_path, context):
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_dispatcher

    provider, _, coordinator, _ = context
    (tmp_path / "guard").mkdir()
    guard = LocalAccountSessionGuard(tmp_path / "guard", "account-a", "owner")
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", "account-a", generation=guard.acquire()
    )
    try:
        request = make_request()
        row = journal.reserve("strategy-a", "key-a", "principal-a", request.to_dict())
        dispatcher = compose_gateway_direct_dispatcher(
            coordinator,
            ROUTE,
            journal,
            guard,
            lambda _: True,
            expected_local_provider=provider,
            prepared_place=True,
        )
        result = dispatcher.dispatch(
            "strategy-a", "key-a", "principal-a", row.reservation_id, row.digest, row.version
        )
        assert result.status == "SENT" and len(provider.sends) == 1
        assert journal.get_prepared_place(row.reservation_id) == provider.prepared[0]
        assert journal.find_place_by_request_key("place-key-a").reservation_id == row.reservation_id
    finally:
        guard.release()


def test_request_changed_by_preparation_is_rejected_before_marker(context, monkeypatch):
    provider = context[0]
    prepare = provider.prepare_order

    def mutate(request):
        prepared = prepare(request)
        object.__setattr__(request, "quantity", Decimal("2"))
        return prepared

    monkeypatch.setattr(provider, "prepare_order", mutate)
    result = place(context)
    assert result.sent is False and result.error_code == "place_preparation_invalid"
    assert context[3].records == [] and provider.sends == [] and len(provider.released) == 1
