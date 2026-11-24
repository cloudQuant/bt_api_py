"""Shared admission bounds service-observation owner groups before creation."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from bt_api_py import service_config, service_market, service_runtime
from bt_api_py import service_observation_clients as observation
from bt_api_py.service_observation_assembly import (
    ServiceObservationError,
    ServiceObservationInstrument,
)
from bt_api_py.service_observation_clients import (
    ServiceObservationAdmission,
    open_service_observation_clients,
)
from tests.unit import test_service_market as market_fixture
from tests.unit.test_service_observation_typed_opt_in import (
    FakeRuntime,
    service_fixture,
)


class FakeMarket:
    def __init__(self, *_args, **_kwargs):
        self._state = "new"
        self._subscriber = None
        self._remote_active = False
        self._remote_unknown = False
        self._unsubscription_uncertain = False
        self._uncertain_registration_id = None
        self.close_calls = 0
        self.reenter_close = None
        self.interrupt_after_side_effect = False

    def close(self):
        self.close_calls += 1
        self._state = "closing"
        if self.reenter_close is not None:
            self.reenter_close()
        if self.interrupt_after_side_effect:
            self._state = "close_failed"
            raise KeyboardInterrupt
        self._subscriber = None
        self._state = "closed"


@pytest.fixture
def fake_observation(monkeypatch):
    FakeRuntime.instances.clear()
    FakeRuntime.capability = frozenset(
        {"account.snapshot", "account.snapshot.typed", "account.instrument_metadata"}
    )
    FakeRuntime.bad_map = False
    monkeypatch.setattr(
        service_config,
        "parse_client_config",
        lambda raw: SimpleNamespace(to_public_dict=lambda: raw),
    )
    monkeypatch.setattr(service_runtime, "ServiceClientRuntime", FakeRuntime)
    monkeypatch.setattr(service_market, "ServiceMarketClient", FakeMarket)
    monkeypatch.setattr(observation, "_KEEPALIVE", [])
    monkeypatch.setattr(observation, "_KEEPALIVE_LOCK", threading.Lock())
    service, env = service_fixture("typed")
    instruments = (ServiceObservationInstrument("sub", market_key=market_fixture._KEY),)
    return service, env, instruments


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5, "1"])
def test_admission_rejects_invalid_capacity(capacity):
    with pytest.raises(ServiceObservationError, match="SERVICE_ADMISSION_CAPACITY_INVALID"):
        ServiceObservationAdmission(capacity)


def test_shared_admission_reserves_before_runtime_build_and_releases_on_pass(
    fake_observation, monkeypatch,
):
    service, env, instruments = fake_observation
    admission = ServiceObservationAdmission(max_groups=1)
    original_build = observation._build_service_observation_runtime
    build_entered = threading.Event()
    allow_build = threading.Event()
    build_calls = []

    def blocked_build(*args, **kwargs):
        build_calls.append(None)
        build_entered.set()
        assert allow_build.wait(timeout=10)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(observation, "_build_service_observation_runtime", blocked_build)
    opened = threading.Event()
    close_ready = threading.Event()

    def open_and_close_on_owner_thread():
        clients = open_service_observation_clients(
            service, instruments=instruments, environ=env, admission=admission,
        )
        opened.set()
        assert close_ready.wait(timeout=10)
        return clients.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(open_and_close_on_owner_thread)
        try:
            assert build_entered.wait(timeout=10)
            assert admission.in_use == 1
            with pytest.raises(ServiceObservationError, match="SERVICE_RESOURCE_CAPACITY_EXHAUSTED"):
                open_service_observation_clients(
                    service, instruments=instruments, environ=env, admission=admission,
                )
            assert len(build_calls) == 1
            allow_build.set()
            assert opened.wait(timeout=10)
            assert admission.in_use == 1
        finally:
            allow_build.set()
            close_ready.set()
        assert future.result(timeout=10)["status"] == "PASS"

    assert admission.in_use == 0
    # A confirmed close makes the shared slot available to the next group.
    clients = open_service_observation_clients(
        service, instruments=instruments, environ=env, admission=admission,
    )
    assert admission.in_use == 1
    assert clients.close()["status"] == "PASS"
    assert admission.in_use == 0


def test_omitting_admission_preserves_uncapped_default(fake_observation):
    service, env, instruments = fake_observation
    first = open_service_observation_clients(service, instruments=instruments, environ=env)
    second = open_service_observation_clients(service, instruments=instruments, environ=env)
    assert first._admission_lease is None
    assert second._admission_lease is None
    assert first.close()["status"] == "PASS"
    assert second.close()["status"] == "PASS"


def test_unconfigured_close_preserves_legacy_external_boolean_contract(fake_observation):
    service, env, instruments = fake_observation
    clients = open_service_observation_clients(service, instruments=instruments, environ=env)
    market = clients.market_clients["sub"]
    account_client = clients.account_client

    report = clients.close(close_facades=False, facades_confirmed=True)

    assert report["status"] == "PASS"
    assert market.close_calls == 0
    assert market._state == "new"
    assert account_client._closed is False


@pytest.mark.parametrize("close_fails", [False, True])
def test_partial_baseexception_cleanup_keeps_refs_until_confirmed(
    fake_observation, monkeypatch, close_fails,
):
    service, env, instruments = fake_observation
    admission = ServiceObservationAdmission(max_groups=1)

    def interrupted_account_call(self, *_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeRuntime, "call", interrupted_account_call)
    original_close = FakeRuntime.close
    close_calls = []

    def close_runtime(self):
        close_calls.append(self)
        if close_fails:
            raise KeyboardInterrupt
        return original_close(self)

    monkeypatch.setattr(FakeRuntime, "close", close_runtime)
    with pytest.raises(KeyboardInterrupt):
        open_service_observation_clients(
            service, instruments=instruments, environ=env, admission=admission,
        )

    assert close_calls
    if close_fails:
        assert admission.in_use == 1
        lease = next(iter(admission._leases))
        runtime, account_client, market_clients = lease._owners
        assert runtime is close_calls[0]
        assert account_client is not None
        assert isinstance(market_clients, dict)
        assert len(observation._KEEPALIVE) == 1
        retained = observation._KEEPALIVE[0]
        assert retained[0] is runtime
        assert retained[1] is account_client
        assert retained[2] is market_clients
    else:
        assert admission.in_use == 0
        assert observation._KEEPALIVE == []


def test_unconfigured_startup_baseexception_preserves_legacy_no_cleanup(
    fake_observation, monkeypatch,
):
    service, env, instruments = fake_observation

    def interrupted_account_call(self, *_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeRuntime, "call", interrupted_account_call)
    with pytest.raises(KeyboardInterrupt):
        open_service_observation_clients(service, instruments=instruments, environ=env)

    assert FakeRuntime.instances[-1]._state == "registered"
    assert observation._KEEPALIVE == []


def test_unconfirmed_close_keeps_admission_slot_and_owner_references(
    fake_observation, monkeypatch,
):
    service, env, instruments = fake_observation
    admission = ServiceObservationAdmission(max_groups=1)
    clients = open_service_observation_clients(
        service, instruments=instruments, environ=env, admission=admission,
    )
    close_calls = []

    def uncertain_close(self):
        close_calls.append(self)
        # Keep the SDK runtime in a non-closed state so its report is uncertain.

    monkeypatch.setattr(FakeRuntime, "close", uncertain_close)
    with pytest.raises(ServiceObservationError, match="SERVICE_CLEANUP_UNCONFIRMED"):
        clients.close()
    assert admission.in_use == 1
    with pytest.raises(ServiceObservationError, match="SERVICE_RESOURCE_CAPACITY_EXHAUSTED"):
        open_service_observation_clients(
            service, instruments=instruments, environ=env, admission=admission,
        )
    cached = clients.close()
    assert cached["status"] == "UNCONFIRMED"
    assert len(close_calls) == 1
    assert admission.in_use == 1
    lease = next(iter(admission._leases))
    assert lease._owners[0] is clients.runtime
    assert lease._owners[1] is clients.account_client


def test_admission_does_not_trust_external_facade_confirmation_boolean(fake_observation):
    service, env, instruments = fake_observation
    admission = ServiceObservationAdmission(max_groups=1)
    clients = open_service_observation_clients(
        service, instruments=instruments, environ=env, admission=admission,
    )
    market = clients.market_clients["sub"]
    account_client = clients.account_client

    with pytest.raises(ServiceObservationError, match="SERVICE_CLEANUP_UNCONFIRMED"):
        clients.close(close_facades=False, facades_confirmed=True)

    assert clients.close_report["status"] == "UNCONFIRMED"
    assert "external_facade_close_unverified" in clients.close_report["errors"]
    assert market.close_calls == 0
    assert account_client._closed is False
    assert admission.in_use == 1


def test_admission_keeps_started_market_group_uncertain(fake_observation):
    service, env, instruments = fake_observation
    admission = ServiceObservationAdmission(max_groups=1)
    clients = open_service_observation_clients(
        service, instruments=instruments, environ=env, admission=admission,
    )
    market = clients.market_clients["sub"]
    market._state = "started"
    market._subscriber = object()
    market._remote_active = True

    with pytest.raises(ServiceObservationError, match="SERVICE_CLEANUP_UNCONFIRMED"):
        clients.close()

    assert market.close_calls == 1
    assert market._state == "closed"
    assert "market_producer_quiescence_unverified" in clients.close_report["errors"]
    assert admission.in_use == 1


def test_admission_close_baseexception_is_sticky_after_side_effect(fake_observation):
    service, env, instruments = fake_observation
    admission = ServiceObservationAdmission(max_groups=1)
    clients = open_service_observation_clients(
        service, instruments=instruments, environ=env, admission=admission,
    )
    market = clients.market_clients["sub"]
    market.interrupt_after_side_effect = True

    with pytest.raises(KeyboardInterrupt):
        clients.close()
    assert market.close_calls == 1
    assert clients.close_report is None
    assert admission.in_use == 1
    assert observation._KEEPALIVE

    with pytest.raises(ServiceObservationError, match="SERVICE_CLEANUP_UNCONFIRMED"):
        clients.close()
    assert market.close_calls == 1
    assert admission.in_use == 1


def test_admission_reentrant_close_is_sticky_and_not_replayed(fake_observation):
    service, env, instruments = fake_observation
    admission = ServiceObservationAdmission(max_groups=1)
    clients = open_service_observation_clients(
        service, instruments=instruments, environ=env, admission=admission,
    )
    market = clients.market_clients["sub"]
    market.reenter_close = clients.close

    with pytest.raises(ServiceObservationError, match="SERVICE_CLEANUP_UNCONFIRMED"):
        clients.close()
    assert market.close_calls == 1
    assert clients.close_report["status"] == "UNCONFIRMED"
    assert "reentrant_close_unverified" in clients.close_report["errors"]
    assert admission.in_use == 1

    assert clients.close()["status"] == "UNCONFIRMED"
    assert market.close_calls == 1
