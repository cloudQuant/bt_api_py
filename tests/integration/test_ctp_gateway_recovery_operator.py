"""Protected synthetic operator bundle through the real owner and SQLite journal."""

import json
import os
import time
from copy import deepcopy

import pytest
from bt_api_base.market_recovery_observation import RecoveryObservationPage
from bt_api_ctp.gateway_session import CtpGatewaySession
from bt_api_ctp.market_provider import CtpQuoteV2MarketProvider
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

import bt_api_py.ctp_gateway_trading as trading
from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
from bt_api_py.ctp_gateway_operator_profile import create_ctp_gateway_profile_factory
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.test_iter63_ctp_gateway_trading import FakeProvider, FakeSession, _wait
from tests.unit.test_ctp_gateway_recovery_operator_config import recovery
from tests.unit.test_gateway_recovery_observation import page
from tests.unit.test_gateway_recovery_qualified_capture import observation
from tests.unit.test_iter63_ctp_gateway_local_profile import _clock, _fixture, _market, _returns


def configure(tmp_path, monkeypatch, *, history_capacity=None):
    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["returns"] = _returns()
    doc["market"] = _market(doc)
    key = doc["market"]["subscriptions"][0]
    key["source_ref"] = doc["settings"]["market_source_ref"]
    key["entitlement_ref"] = doc["settings"]["market_entitlement_ref"]
    doc["clock"] = _clock()
    if history_capacity is not None:
        doc["clock"]["history_capacity"] = history_capacity
    doc["recovery_observation"] = recovery(tmp_path)
    market_grant = deepcopy(doc["clients"][0]["resources"][0])
    market_grant["port"] = "service.market_source"
    market_grant["operations"] = ["market.subscribe"]
    doc["clients"][0]["resources"].append(market_grant)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.market_source")
    raw["communication"]["profiles"]["server-transport"]["endpoints"]["market_bind"] = (
        _free_endpoint()
    )
    config = parse_service_config(raw)
    file.write_text(json.dumps(doc), encoding="utf-8")

    class Session(FakeSession):
        def __init__(self, key, feed, stream, **kwargs):
            super().__init__(key, feed, stream, **kwargs)
            self.real = CtpGatewaySession(key, feed, stream, **kwargs)

        @property
        def market_provider(self):
            return self.real._market

    monkeypatch.setattr(trading, "_load_ctp_types", lambda: (Session, FakeProvider))
    base = time.monotonic_ns() - 1_000_000_000
    events = (
        observation(1, base, epoch="a" * 32),
        observation(2, base, state="UNKNOWN", epoch="a" * 32),
    )
    monkeypatch.setattr(
        CtpQuoteV2MarketProvider,
        "read_recovery_observations",
        lambda self, *, after_sequence, limit: RecoveryObservationPage(
            "a" * 32,
            os.getpid(),
            after_sequence,
            1,
            2,
            tuple(events[after_sequence : after_sequence + limit]),
            False,
        ),
    )
    from bt_api_py import clock_verification

    monkeypatch.setattr(clock_verification, "_sntp_query", lambda *_: (0, 100))
    return config, load_simnow_trading_resources(config)


def deployment(config):
    factory = create_ctp_gateway_profile_factory(
        "synthetic-operator", resource_loader=load_simnow_trading_resources
    )
    return factory.create_deployment(config)


def provide(deployed, authority):
    return deployed._provide_resources(
        "gateway", "gateway-a", {"role": "trading_owner", "dispatch_enabled": True}, authority.scope
    )


def journal(settings, mode):
    return RecoveryObservationJournal(
        settings.journal_path,
        scope_ref=settings.scope_ref,
        max_events=settings.max_events,
        max_bytes=settings.max_bytes,
        timeout_seconds=settings.sqlite_timeout_seconds,
        qualification_mode=mode,
    )


def test_operator_bundle_to_v2_observation_and_clean_close(tmp_path, monkeypatch):
    config, trusted = configure(tmp_path, monkeypatch)
    assert trusted.clock_refresh_settings.history_capacity == 0
    settings = trusted.recovery_observation_settings
    resources = provide(deployment(config), trusted.authority)
    binding = resources.values["binding"]
    try:
        status = binding.recovery_observation_status
        _wait(lambda: status()["state"] == "ready")
        records = journal(settings, "local_clock_v1").read_after(0, limit=20).records
        rows = [record for record in records if record.kind == "observation"]
        assert [row.observation.state for row in rows] == ["RECOVERING", "UNKNOWN"]
        assert [row.qualification.reason for row in rows] == [
            "HISTORY_UNAVAILABLE",
            "SOURCE_UNKNOWN",
        ]
        assert all(row.qualification.start_utc_lower_ns is None for row in rows)
        assert settings.scope_ref == trusted.settings.account_scope
        assert settings.source_ref == trusted.settings.market_source_ref
    finally:
        resources.close()
    assert status()["state"] == "closed" and binding._owner.closed
    records = journal(settings, "local_clock_v1").read_after(0, limit=20).records
    assert records[-1].kind == "capture_stopped"
    assert FakeSession.instances[-1].events[-1][0] == "close"


def test_operator_v2_refuses_existing_v1_and_preserves_original(tmp_path, monkeypatch):
    config, trusted = configure(tmp_path, monkeypatch)
    settings = trusted.recovery_observation_settings
    existing = journal(settings, "disabled")
    existing.append_page(settings.source_ref, page(0))
    before = existing.read_after(0, limit=20)
    with pytest.raises(ServiceResourceConstructionError) as error:
        provide(deployment(config), trusted.authority)
    error.value.resources.close()
    assert journal(settings, "disabled").read_after(0, limit=20) == before
    assert FakeSession.instances[-1].events[-1][0] == "close"


def test_operator_explicit_clock_history_qualifies_published_local_coverage(tmp_path, monkeypatch):
    """A synthetic clock sample and coverage interval travel through the real factory."""
    from bt_api_py import clock_verification

    config, trusted = configure(tmp_path, monkeypatch, history_capacity=16)
    assert trusted.clock_refresh_settings.history_capacity == 16
    query_calls = []

    def synthetic_clock(*_args):
        query_calls.append(True)
        return 0, 100

    monkeypatch.setattr(clock_verification, "_sntp_query", synthetic_clock)
    events = []

    def synthetic_source(_provider, *, after_sequence, limit):
        return RecoveryObservationPage(
            "a" * 32,
            os.getpid(),
            after_sequence,
            1,
            len(events),
            tuple(events[after_sequence : after_sequence + limit]),
            False,
        )

    monkeypatch.setattr(CtpQuoteV2MarketProvider, "read_recovery_observations", synthetic_source)
    settings = trusted.recovery_observation_settings
    resources = provide(deployment(config), trusted.authority)
    binding = resources.values["binding"]
    owner = binding._owner
    try:
        recorder = binding.recovery_observation_status.__self__
        capture = recorder._capture
        closed = dict(
            zip(
                capture.__code__.co_freevars,
                (cell.cell_contents for cell in capture.__closure__),
                strict=False,
            )
        )
        refresher = closed["bound_clock_refresher"]

        def sampled_history():
            result = owner.submit_service_call(
                lambda: refresher.read_history(after_sequence=0)
            ).result(timeout_seconds=1)
            return result if any(row.kind == "SAMPLE" for row in result.records) else None

        _wait(lambda: bool(query_calls))
        _wait(sampled_history)
        history = sampled_history()
        sample = next(row for row in history.records if row.kind == "SAMPLE")
        _wait(lambda: time.monotonic_ns() > sample.observed_monotonic_ns + 2_000_000)

        def publish_coverage():
            end = time.monotonic_ns() - 1_000
            start = end - 1_000_000
            record = observation(1, start, start=0, through=end - start, epoch="a" * 32)
            events.append(record)
            return record

        record = owner.submit_service_call(publish_coverage).result(timeout_seconds=1)
        assert sample.observed_monotonic_ns < record.coverage_started_monotonic_ns
        assert (
            record.coverage_through_monotonic_ns < sample.snapshot.evidence.valid_until_monotonic_ns
        )
        log = journal(settings, "local_clock_v1")

        def observation_rows():
            return [row for row in log.read_after(0, limit=20).records if row.kind == "observation"]

        _wait(lambda: len(observation_rows()) == 1)
        row = observation_rows()[0]
        assert row.observation == record
        assert (row.qualification.status, row.qualification.reason) == ("QUALIFIED", "OK")
        assert row.qualification.source_epoch == history.source_epoch
        assert (
            row.qualification.first_sample_sequence,
            row.qualification.last_sample_sequence,
        ) == (sample.sequence, sample.sequence)
        assert row.qualification.start_utc_lower_ns <= row.qualification.start_utc_upper_ns
        assert row.qualification.end_utc_lower_ns <= row.qualification.end_utc_upper_ns
    finally:
        resources.close()
    assert binding._owner.closed and binding.recovery_observation_status()["state"] == "closed"
    assert FakeSession.instances[-1].events[-1][0] == "close"
