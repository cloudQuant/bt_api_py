"""Provider pre-start rejection stays distinct from uncertain cleanup."""

import builtins
import copy
import json
from dataclasses import replace

import pytest

from bt_api_py import trading_calendar_observation_run as observation
from bt_api_py import trading_calendar_preview as preview
from bt_api_py import trading_calendar_preview_reports as reports
from bt_api_py import trading_calendar_product_fetch_observation as products
from tests.unit.test_trading_calendar_preview import holiday, run, source


def test_preview_preserves_provider_unsupported_status_and_unstarted_parse():
    result = run(
        enable_shfe=True,
        collector=lambda **kwargs: source("SUPERVISOR_UNSUPPORTED"),
        holiday_collector=lambda **kwargs: holiday("SUPERVISOR_UNSUPPORTED"),
    )
    assert result.status == "INCOMPLETE" and result.publication == "NOT_ATTEMPTED"
    assert result.source_status == result.holiday_status == "SUPERVISOR_UNSUPPORTED"
    assert (
        result.attempts[0].parse_status
        == result.holiday_attempts[0].parse_status
        == "NOT_ATTEMPTED"
    )
    encoded = json.dumps(result.to_dict()).encode()
    assert preview._parse_worker_result(encoded, result.exit_code) == result
    assert reports._validated_report(result) is not None
    data = observation._report_data(result)
    row = observation._source(data, "hours", True)
    assert row["state"] == "NOT_ATTEMPTED" and row["transport"] == "SUPERVISOR_UNSUPPORTED"
    assert row["verification"] == "UNVERIFIED"


def test_prior_attempt_prevents_claim_that_source_was_never_attempted():
    data = {
        "attempts": [
            {"transport_status": "NETWORK_TIMEOUT", "parse_status": "NOT_ATTEMPTED"},
            {"transport_status": "SUPERVISOR_UNSUPPORTED", "parse_status": "NOT_ATTEMPTED"},
        ]
    }
    row = observation._source(data, "hours", True)
    assert row["state"] == "UNKNOWN" and row["transport"] == "SUPERVISOR_UNSUPPORTED"


def test_fixed_source_status_validation_rejects_misclassified_or_forged_values():
    value = products.unknown_fetch("CU_FUTURE", "SUPERVISOR_UNSUPPORTED", "SUPERVISOR_UNSUPPORTED")
    products.validate_fetch(value)
    for change in (
        {"reason": "FETCH_FAILED"},
        {"reason": "CHILD_CLEANUP_UNCERTAIN"},
        {"transport_status": "CHILD_CLEANUP_UNCERTAIN"},
        {"status": "CANDIDATE"},
        {"parse_status": "CANDIDATE"},
    ):
        wrong = copy.deepcopy(value)
        wrong.update(change)
        with pytest.raises(ValueError):
            products.validate_fetch(wrong)


def test_import_failure_is_distinct_from_failure_after_call(monkeypatch):
    original = builtins.__import__

    def fail_import(name, *args, **kwargs):
        if name == "bt_api_ctp.calendar_shfe_product_fetch":
            raise ImportError("injected before call")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_import)
    value = products.fetch_observation("CU_FUTURE", total_timeout=1)
    assert (value["reason"], value["transport_status"]) == (
        "COLLECTOR_UNAVAILABLE",
        "NOT_ATTEMPTED",
    )
    products.validate_fetch(value)


def test_validation_exception_after_collector_is_cleanup_uncertain(monkeypatch):
    from bt_api_ctp import calendar_shfe_product_fetch as fetch

    body = b'<script>let pageList = [{"ContractSymbol":"CU","ListingExchange":"SHFE","data_standard":"2023-01-01"}];</script>'
    page = fetch.parse_shfe_product_page(source_url=fetch.SOURCES["CU_FUTURE"], html=body)
    result = fetch.collect_product_page(
        source_key="CU_FUTURE", runner=lambda *args: ("HTTP_OK", page)
    )
    result = replace(result, transport_assurance="SUPERVISED_DEFAULT_TLS_POLICY")
    monkeypatch.setattr(fetch, "collect_product_page", lambda **kwargs: result)

    def invalid(_):
        raise ImportError("injected after collector")

    monkeypatch.setattr(products, "_validate_product", invalid)
    value = products.fetch_observation("CU_FUTURE", total_timeout=1)
    assert (value["reason"], value["transport_status"]) == (
        "CHILD_CLEANUP_UNCERTAIN",
        "CHILD_CLEANUP_UNCERTAIN",
    )
