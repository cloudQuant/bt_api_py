"""Protected synthetic operator configuration for optional recovery observation."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import bt_api_py.ctp_gateway_operator_profile as operator
from bt_api_py.ctp_gateway_local_profile import (
    CtpLocalProfileError,
    load_local_ctp_resources,
    load_simnow_trading_resources,
)
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError
from tests.unit.test_iter63_ctp_gateway_local_profile import _clock, _fixture, _returns


def recovery(tmp_path, **changes):
    result = {
        "journal_path": str(tmp_path / "observations.sqlite"),
        "source_capacity": 4,
        "page_limit": 4,
        "max_events": 20,
        "max_bytes": 100000,
        "sqlite_timeout_seconds": 0.1,
        "poll_interval_seconds": 0.01,
        "owner_wait_seconds": 0.1,
        "close_timeout_seconds": 1,
        "qualification_mode": "local_clock_v1",
        "clock_max_interval_ns": 1_000_000_000,
    }
    result.update(changes)
    return result


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["returns"] = _returns()

    def load():
        file.write_text(json.dumps(doc), encoding="utf-8")
        return load_simnow_trading_resources(config)

    return doc, file, config, load


def test_absent_default_and_explicit_mode_preserve_derived_scope_without_io(
    bundle, tmp_path, monkeypatch
):
    import bt_api_py.gateway_recovery_observation as module

    doc, _, _, load = bundle
    assert load().recovery_observation_settings is None

    def forbidden(*_args, **_kwargs):
        pytest.fail("configuration parsing opened the journal")

    monkeypatch.setattr(module, "RecoveryObservationJournal", forbidden)
    doc["recovery_observation"] = recovery(tmp_path)
    resources = load()
    value = resources.recovery_observation_settings
    assert value.scope_ref == resources.settings.account_scope
    assert value.source_ref == resources.settings.market_source_ref
    assert (
        value.qualification_mode == "local_clock_v1"
        and value.clock_max_interval_ns == 1_000_000_000
    )
    assert not (tmp_path / "observations.sqlite").exists()
    doc["recovery_observation"] = recovery(
        tmp_path, qualification_mode="disabled", clock_max_interval_ns=None
    )
    assert load().recovery_observation_settings.qualification_mode == "disabled"


@pytest.mark.parametrize("field", tuple(recovery(Path("."))))
def test_each_required_field_must_be_present(bundle, tmp_path, field):
    doc, _, _, load = bundle
    value = recovery(tmp_path)
    del value[field]
    doc["recovery_observation"] = value
    with pytest.raises(CtpLocalProfileError):
        load()


def test_disabled_mode_rejects_a_clock_threshold(bundle, tmp_path):
    doc, _, _, load = bundle
    doc["recovery_observation"] = recovery(tmp_path, qualification_mode="disabled")
    with pytest.raises(CtpLocalProfileError):
        load()


@pytest.mark.parametrize("case", ["null", "missing", "extra", "scope", "source"])
def test_null_missing_extra_and_caller_scope_are_rejected(bundle, tmp_path, case):
    doc, _, _, load = bundle
    value = recovery(tmp_path)
    if case == "null":
        value = None
    elif case == "missing":
        del value["qualification_mode"]
    elif case == "extra":
        value["extra"] = 1
    elif case == "scope":
        value["scope_ref"] = "other"
    else:
        value["source_ref"] = "other"
    doc["recovery_observation"] = value
    with pytest.raises(CtpLocalProfileError):
        load()


@pytest.mark.parametrize(
    "field,value",
    [
        ("journal_path", "relative.sqlite"),
        ("journal_path", None),
        ("source_capacity", True),
        ("source_capacity", 1),
        ("source_capacity", 4097),
        ("page_limit", 0),
        ("page_limit", 257),
        ("page_limit", 1.0),
        ("max_events", 0),
        ("max_events", 1_000_001),
        ("max_bytes", 0),
        ("max_bytes", (1 << 30) + 1),
        ("sqlite_timeout_seconds", True),
        ("sqlite_timeout_seconds", 5.1),
        ("poll_interval_seconds", 0),
        ("poll_interval_seconds", 61),
        ("owner_wait_seconds", 0),
        ("owner_wait_seconds", 5.1),
        ("close_timeout_seconds", 0),
        ("close_timeout_seconds", 31),
        ("qualification_mode", "other"),
        ("clock_max_interval_ns", None),
        ("clock_max_interval_ns", True),
        ("clock_max_interval_ns", 0),
        ("clock_max_interval_ns", 1 << 63),
    ],
)
def test_types_and_bounds_fail_closed(bundle, tmp_path, field, value):
    doc, _, _, load = bundle
    doc["recovery_observation"] = recovery(tmp_path, **{field: value})
    with pytest.raises(CtpLocalProfileError):
        load()


def test_readonly_profile_rejects_recovery_section(bundle, tmp_path):
    doc, file, config, _ = bundle
    del doc["returns"]
    doc["recovery_observation"] = recovery(tmp_path)
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError):
        load_local_ctp_resources(config)


@pytest.mark.parametrize("capacity", [0, 2, 4096])
def test_clock_history_capacity_is_explicit_and_optional(bundle, capacity):
    doc, _, _, load = bundle
    doc["clock"] = {**_clock(), "history_capacity": capacity}
    assert load().clock_refresh_settings.history_capacity == capacity
    doc["clock"] = _clock()
    assert load().clock_refresh_settings.history_capacity == 0


@pytest.mark.parametrize("capacity", [True, 1, -1, 4097, 2.0, None])
def test_clock_history_capacity_rejects_invalid_values(bundle, capacity):
    doc, _, _, load = bundle
    doc["clock"] = {**_clock(), "history_capacity": capacity}
    with pytest.raises(CtpLocalProfileError):
        load()


def test_wrapper_forwards_exact_settings_and_safe_configuration_error(
    bundle, tmp_path, monkeypatch
):
    doc, _, config, load = bundle
    doc["recovery_observation"] = recovery(tmp_path)
    resources = load()
    sent = []
    marker = object()

    def builder(*_args, **kwargs):
        sent.append(kwargs)
        return SimpleNamespace(deployment_factory=lambda: marker)

    monkeypatch.setattr(operator, "build_ctp_gateway_trading_profile", builder)
    factory = operator.create_ctp_gateway_profile_factory(
        "test-profile", resource_loader=lambda _: resources
    )
    assert factory.create_deployment(config) is marker
    assert sent[0]["recovery_observation_settings"] is resources.recovery_observation_settings

    def invalid(*_args, **_kwargs):
        raise CtpGatewayTradingError("INVALID_RECOVERY_OBSERVATION_SETTINGS")

    monkeypatch.setattr(operator, "build_ctp_gateway_trading_profile", invalid)
    with pytest.raises(
        operator.GatewayOperatorApplicationError, match="INVALID_RECOVERY_OBSERVATION_SETTINGS"
    ):
        factory.create_deployment(config)
