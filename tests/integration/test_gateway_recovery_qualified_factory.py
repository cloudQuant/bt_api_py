"""Actual factory, owner and journal; native source and clock are synthetic."""

import os
import time
from threading import get_ident

import pytest
from bt_api_base.market_recovery_observation import RecoveryObservationPage
from bt_api_ctp.gateway_session import CtpGatewaySession
from bt_api_ctp.market_provider import CtpQuoteV2MarketProvider
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

import bt_api_py.ctp_gateway_trading as trading
from bt_api_py.clock_verification import ClockVerifier
from bt_api_py.gateway_server_clock_refresh import GatewayServerClockRefreshSettings
from tests.integration.test_gateway_recovery_observation_factory import options
from tests.test_iter63_ctp_gateway_trading import FakeProvider, FakeSession, _resources, _wait
from tests.unit.test_gateway_recovery_qualified_capture import observation
from tests.unit.test_iter63_ctp_gateway_market_profile import setup


def setup_factory(tmp_path, monkeypatch, *, clock):
    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    captured_threads, records = [], []
    epoch = "b" * 32

    class Session(FakeSession):
        def __init__(self, key, feed, stream, **kwargs):
            super().__init__(key, feed, stream, **kwargs)
            self.real = CtpGatewaySession(key, feed, stream, **kwargs)

        @property
        def market_provider(self):
            assert get_ident() == self.owner
            return self.real._market

    def source(provider, *, after_sequence, limit):
        captured_threads.append(get_ident())
        assert type(provider) is CtpQuoteV2MarketProvider
        return RecoveryObservationPage(
            epoch,
            os.getpid(),
            after_sequence,
            1,
            len(records),
            tuple(records[after_sequence : after_sequence + limit]),
            False,
        )

    monkeypatch.setattr(trading, "_load_ctp_types", lambda: (Session, FakeProvider))
    monkeypatch.setattr(CtpQuoteV2MarketProvider, "read_recovery_observations", source)
    cfg = options(
        tmp_path, settings, qualification_mode="local_clock_v1", clock_max_interval_ns=1_000_000_000
    )
    extras = {}
    if clock:
        from bt_api_py import clock_verification

        monkeypatch.setattr(clock_verification, "_sntp_query", lambda *_: (0, 1000))
        extras = {
            "clock_verifier_factory": lambda: ClockVerifier(
                servers=("trusted",), validity_seconds=5
            ),
            "clock_allowed_servers": frozenset({"trusted"}),
            "clock_refresh_settings": GatewayServerClockRefreshSettings(
                5_000_000, 1, 0.05, history_capacity=16
            ),
        }
    resources = _resources(build(recovery_observation_settings=cfg, **extras), authority)
    binding = resources.values["binding"]
    recorder = binding.recovery_observation_status.__self__
    journal = RecoveryObservationJournal(
        cfg.journal_path,
        scope_ref=cfg.scope_ref,
        max_events=cfg.max_events,
        max_bytes=cfg.max_bytes,
        timeout_seconds=0.1,
        qualification_mode="local_clock_v1",
    )
    return resources, binding, recorder, journal, records, captured_threads


def rows(journal):
    return [row for row in journal.read_after(0, limit=20).records if row.kind == "observation"]


def closure(recorder):
    function = recorder._capture
    return dict(
        zip(
            function.__code__.co_freevars,
            (cell.cell_contents for cell in function.__closure__),
            strict=False,
        )
    )


def test_factory_without_clock_persists_source_and_clock_unknown(tmp_path, monkeypatch):
    resources, binding, recorder, journal, records, threads = setup_factory(
        tmp_path, monkeypatch, clock=False
    )
    try:
        _wait(lambda: binding.recovery_observation_status()["state"] == "ready")
        base = time.monotonic_ns() - 10_000_000
        records.extend((observation(1, base), observation(2, base, state="UNKNOWN")))
        _wait(lambda: len(rows(journal)) == 2)
        actual = rows(journal)
        assert [r.qualification.reason for r in actual] == ["CLOCK_UNAVAILABLE", "SOURCE_UNKNOWN"]
        assert [r.observation for r in actual] == records
        assert threads and set(threads) == {FakeSession.instances[-1].owner}
        assert closure(recorder)["bound_clock_refresher"] is None
    finally:
        resources.close()


def test_factory_keeps_original_clock_binding_and_invalid_source_is_unknown(tmp_path, monkeypatch):
    resources, binding, recorder, journal, records, _ = setup_factory(
        tmp_path, monkeypatch, clock=True
    )
    owner = binding._owner
    captured = closure(recorder)
    source, holder = captured["bound_clock_refresher"], captured["holder"]
    try:
        _wait(
            lambda: owner.submit_service_call(source.current).result(timeout_seconds=1) is not None
        )
        history = owner.submit_service_call(lambda: source.read_history(after_sequence=0)).result(
            timeout_seconds=1
        )
        base = history.records[-1].observed_monotonic_ns
        _wait(lambda: time.monotonic_ns() > base + 1000)
        records.append(observation(1, base, start=150, through=250))
        _wait(lambda: len(rows(journal)) == 1)
        first = rows(journal)[0]
        assert first.qualification.status == "QUALIFIED"
        assert first.observation == records[0]

        original_allowed = source._allowed_servers
        owner.submit_service_call(
            lambda: setattr(source, "_allowed_servers", frozenset({"other"}))
        ).result(timeout_seconds=1)
        records.append(observation(2, base, start=300, through=400))
        _wait(lambda: len(rows(journal)) == 2)
        assert rows(journal)[1].qualification.reason == "OWNER_CHANGED"
        owner.submit_service_call(
            lambda: setattr(source, "_allowed_servers", original_allowed)
        ).result(timeout_seconds=1)

        owner.submit_service_call(lambda: holder.__setitem__("clock_refresher", object())).result(
            timeout_seconds=1
        )
        _wait(
            lambda: (
                binding.recovery_observation_status()["last_failure_code"] == "OWNER_CALL_FAILED"
            )
        )
        assert len(rows(journal)) == 2
        owner.submit_service_call(lambda: holder.__setitem__("clock_refresher", source)).result(
            timeout_seconds=1
        )
        _wait(lambda: binding.recovery_observation_status()["state"] == "ready")
        assert captured["qualified_capture"][0]._qualifier._source is source
    finally:
        holder["clock_refresher"] = source
        resources.close()


def test_factory_v2_refuses_existing_v1_without_removing_or_replacing_it(tmp_path, monkeypatch):
    from bt_api_py.service_components import ServiceResourceConstructionError
    from tests.unit.test_gateway_recovery_observation import page

    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)
    cfg = options(
        tmp_path, settings, qualification_mode="local_clock_v1", clock_max_interval_ns=1_000_000_000
    )
    old = RecoveryObservationJournal(
        cfg.journal_path,
        scope_ref=cfg.scope_ref,
        max_events=cfg.max_events,
        max_bytes=cfg.max_bytes,
        timeout_seconds=0.1,
    )
    old.append_page(cfg.source_ref, page(0))
    before = old.read_after(0, limit=10)

    class Session(FakeSession):
        def __init__(self, key, feed, stream, **kwargs):
            super().__init__(key, feed, stream, **kwargs)
            self.real = CtpGatewaySession(key, feed, stream, **kwargs)

        @property
        def market_provider(self):
            return self.real._market

    monkeypatch.setattr(trading, "_load_ctp_types", lambda: (Session, FakeProvider))
    with pytest.raises(ServiceResourceConstructionError) as error:
        _resources(build(recovery_observation_settings=cfg), authority, cleanup_on_error=False)
    error.value.resources.close()
    assert old.read_after(0, limit=10) == before
    assert FakeSession.instances[-1].events[-1][0] == "close"


def test_initial_clock_invalidation_fixes_unknown_helper_for_owner_lifetime(tmp_path, monkeypatch):
    from bt_api_py.gateway_recovery_clock_qualification import GatewayRecoveryClockQualifier

    original = GatewayRecoveryClockQualifier.__init__
    changed = []

    def invalidate_between_qualifier_and_helper(self, source, **kwargs):
        original(self, source, **kwargs)
        saved = source._allowed_servers
        source._allowed_servers = frozenset({"other"})
        changed.append((source, saved))

    monkeypatch.setattr(
        GatewayRecoveryClockQualifier, "__init__", invalidate_between_qualifier_and_helper
    )
    resources, binding, recorder, journal, records, _ = setup_factory(
        tmp_path, monkeypatch, clock=True
    )
    owner = binding._owner
    try:
        _wait(lambda: bool(changed) and binding.recovery_observation_status()["state"] == "ready")
        source, saved = changed[0]
        helper = closure(recorder)["qualified_capture"][0]
        assert helper._qualifier is None
        owner.submit_service_call(lambda: setattr(source, "_allowed_servers", saved)).result(
            timeout_seconds=1
        )
        base = time.monotonic_ns() - 10_000_000
        records.append(observation(1, base))
        _wait(lambda: len(rows(journal)) == 1)
        assert rows(journal)[0].qualification.reason == "CLOCK_UNAVAILABLE"
        assert closure(recorder)["qualified_capture"][0] is helper and helper._qualifier is None
        assert len(changed) == 1
    finally:
        if changed:
            changed[0][0]._allowed_servers = changed[0][1]
        resources.close()
