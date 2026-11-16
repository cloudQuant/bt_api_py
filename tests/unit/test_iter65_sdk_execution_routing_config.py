"""Strict, parse-time admission of the SDK's Execution selection rules.

These tests are offline: they parse plain trees, start no runtime and contact
no service. Selecting Execution without a Gateway route is a finite rejection,
not a first-order failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py.service_config import ServiceConfigError, parse_client_config

KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")


def routing_raw(**overrides):
    value = {
        "schema_version": 1,
        "component": "exec-instance",
        "account_ref": "acct",
        "strategy_ref": "strategy",
        "execution_domain_ref": "paper",
        "grant_revision": "grant-v1",
        "delegation": {"delegation_id": "delegation-1", "principal_ref": "principal-a"},
        "instruments": [KEY.to_dict()],
        "rules": [
            {
                "id": "rule-1",
                "match": {
                    "instrument": KEY.to_dict(),
                    "side": "buy",
                    "offset": "open",
                    "order_types": ["limit"],
                    "minimum_quantity": "1",
                    "quantity_unit": "lots",
                },
                "scheme": {"name": "pass-v1", "version": 1},
                "algorithm": {"class": "PASS", "version": 1, "params": {}},
                "deadline": {"kind": "absolute", "at": "2099-12-31T08:00:00+08:00"},
                "time_in_force": "DAY",
                "trading_day": "20261109",
            }
        ],
    }
    value.update(overrides)
    return value


def _profile():
    return {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7101"},
        "security_profile_ref": "test-security",
    }


def _binding(mount, *, account_ref="acct", instance_id="exec-instance", service_id="exec-service"):
    return {
        "mount": mount,
        "service_id": service_id,
        "instance_id": instance_id,
        "scope_ref": "scope-a",
        "transport_ref": "rpc",
        "account_ref": account_ref,
    }


def strategy_config(
    *, gateway=True, parent=True, routing=True, routing_value=None, strategy_ref="strategy"
):
    clients = {}
    if gateway:
        clients["gateway"] = {
            "config_schema_version": 1,
            "settings": {
                "bindings": [
                    _binding(
                        "broker.direct_write", instance_id="gw-instance", service_id="gw-service"
                    )
                ]
            },
        }
    bindings = [_binding("broker.execution_parent" if parent else "broker.order_query")]
    execution = {"config_schema_version": 1, "settings": {"bindings": bindings}}
    if routing:
        execution["settings"]["options"] = {
            "execution_routing": routing_raw() if routing_value is None else routing_value
        }
    clients["execution"] = execution
    return {
        "config_schema_version": 6,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": strategy_ref},
        "clients": clients,
        "communication": {"config_schema_version": 1, "profiles": {"rpc": _profile()}},
    }


def _code(raw):
    with pytest.raises(ServiceConfigError) as info:
        parse_client_config(raw)
    return info.value.code


def test_valid_routing_option_parses_and_is_published():
    parsed = parse_client_config(strategy_config())
    option = parsed.to_public_dict()["clients"]["execution"]["settings"]["options"][
        "execution_routing"
    ]
    assert option["component"] == "exec-instance"
    assert option["account_ref"] == "acct"
    assert option["strategy_ref"] == "strategy"
    assert option["execution_domain_ref"] == "paper"
    assert option["instruments"] == [KEY.to_dict()]
    assert option["rules"][0]["id"] == "rule-1"
    assert option["rules"][0]["deadline"]["at"] == "2099-12-31T00:00:00.000000Z"
    assert len(parsed.fingerprint()) == 64


def test_selection_requires_gateway_route():
    raw = strategy_config(gateway=False)
    assert _code(raw) == "EXECUTION_REQUIRES_GATEWAY"


def test_selection_requires_parent_binding():
    raw = strategy_config(parent=False)
    assert _code(raw) == "EXECUTION_ROUTING_REQUIRES_PARENT_BINDING"


def test_component_must_name_the_parent_binding_instance():
    raw = strategy_config(routing_value=routing_raw(component="other-instance"))
    assert _code(raw) == "EXECUTION_ROUTING_COMPONENT_MISMATCH"


def test_strategy_scope_must_match_registration():
    raw = strategy_config(routing_value=routing_raw(strategy_ref="other-strategy"))
    assert _code(raw) == "EXECUTION_ROUTING_SCOPE_MISMATCH"


def test_account_scope_must_match_the_parent_binding():
    raw = strategy_config(routing_value=routing_raw(account_ref="other-account"))
    assert _code(raw) == "EXECUTION_ROUTING_REQUIRES_PARENT_BINDING"


@pytest.mark.parametrize("order_types", [["market"], ["limit", "market"]])
def test_statically_unsupported_types_are_refused_at_parse(order_types):
    rule = routing_raw()["rules"][0]
    rule["match"]["order_types"] = order_types
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "EXECUTION_ORDER_TYPE_UNSUPPORTED"


def test_duplicate_rule_ids_are_refused():
    rule = routing_raw()["rules"][0]
    second = json.loads(json.dumps(rule))
    second["id"] = "rule-1"
    raw = strategy_config(routing_value=routing_raw(rules=[rule, second]))
    assert _code(raw) == "EXECUTION_ROUTING_RULE_DUPLICATE"


def test_unknown_rule_field_is_refused():
    rule = routing_raw()["rules"][0]
    rule["unknown"] = 1
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "INVALID_EXECUTION_CONFIG"


@pytest.mark.parametrize("quantity", [1.5, "nan", "0", "-1", True, "1e5", None])
def test_non_decimal_minimum_quantity_is_refused(quantity):
    rule = routing_raw()["rules"][0]
    rule["match"]["minimum_quantity"] = quantity
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "INVALID_EXECUTION_CONFIG"


def test_naive_deadline_is_refused():
    rule = routing_raw()["rules"][0]
    rule["deadline"] = {"kind": "absolute", "at": "2099-12-31T08:00:00"}
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "INVALID_EXECUTION_CONFIG"


def test_relative_deadline_is_refused():
    rule = routing_raw()["rules"][0]
    rule["deadline"] = {"kind": "relative", "seconds": 60}
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "INVALID_EXECUTION_CONFIG"


@pytest.mark.parametrize("offset", ["close", "close_today", "close_yesterday"])
def test_close_rule_requires_a_position_target(offset):
    rule = routing_raw()["rules"][0]
    rule["match"]["offset"] = offset
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "INVALID_EXECUTION_CONFIG"


def test_open_rule_forbids_a_position_target():
    rule = routing_raw()["rules"][0]
    rule["target_position_ref"] = "position-1"
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "INVALID_EXECUTION_CONFIG"


def test_gtd_requires_explicit_allowed_days():
    rule = routing_raw()["rules"][0]
    rule["time_in_force"] = "GTD"
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "INVALID_EXECUTION_CONFIG"


def test_day_rejects_multiple_allowed_days():
    rule = routing_raw()["rules"][0]
    rule["allowed_trading_days"] = ["20261109", "20261110"]
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "INVALID_EXECUTION_CONFIG"


def test_rule_instrument_must_be_a_configured_identity():
    rule = routing_raw()["rules"][0]
    rule["match"]["instrument"] = MarketInstrumentKey(
        "ctp", "DCE", "simnow", "future", "rb", "rb"
    ).to_dict()
    raw = strategy_config(routing_value=routing_raw(rules=[rule]))
    assert _code(raw) == "EXECUTION_ROUTING_INSTRUMENT_UNKNOWN"


def test_strategy_registration_must_continue_to_match():
    raw = strategy_config(strategy_ref="other-strategy")
    assert _code(raw) == "EXECUTION_ROUTING_SCOPE_MISMATCH"


def test_application_client_cannot_select_routing():
    raw = {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": "application-a"},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        _binding(
                            "application.direct_write",
                            instance_id="gw-instance",
                            service_id="gw-service",
                        )
                    ]
                },
            },
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [_binding("application.execution_management")],
                    "options": {"execution_routing": routing_raw()},
                },
            },
        },
        "communication": {"config_schema_version": 1, "profiles": {"rpc": _profile()}},
    }
    assert _code(raw) == "UNKNOWN_FIELD"


_ISOLATED_IMPORT = r"""
import importlib
import importlib.abc
import json
import sys
from pathlib import Path

sdk_root = Path(sys.argv[1])
base_source = Path(sys.argv[2])
raw = json.loads(sys.argv[3])
sys.path.insert(0, str(base_source))
sys.path.insert(0, str(sdk_root))

blocked_roots = {"bt_api_execution", "bt_api_gateway", "bt_api_risk", "bt_api_monitor"}
attempted = []

class DomainImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if any(fullname == root or fullname.startswith(root + ".") for root in blocked_roots):
            attempted.append(fullname)
            raise ImportError(fullname)
        return None

sys.meta_path.insert(0, DomainImportBlocker())
from bt_api_py.service_config import parse_client_config
parse_client_config(raw)
loaded = sorted(
    name
    for name in sys.modules
    if any(name == root or name.startswith(root + ".") for root in blocked_roots)
)
print(json.dumps({
    "routing_imported": "bt_api_py.execution_routing" in sys.modules,
    "attempted": attempted,
    "loaded_domain_modules": loaded,
}, sort_keys=True))
"""


def _isolated(raw):
    sdk_root = Path(__file__).resolve().parents[2]
    base_source = sdk_root / "bt_api" / "bt_api_base" / "src"
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _ISOLATED_IMPORT,
            str(sdk_root),
            str(base_source),
            json.dumps(raw),
        ],
        cwd=sdk_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_unselected_config_imports_neither_routing_nor_execution():
    report = _isolated(strategy_config(routing=False))
    assert report == {
        "routing_imported": False,
        "attempted": [],
        "loaded_domain_modules": [],
    }


def test_selected_config_imports_routing_but_not_execution():
    report = _isolated(strategy_config())
    assert report == {
        "routing_imported": True,
        "attempted": [],
        "loaded_domain_modules": [],
    }


def test_v2_service_config_normalizes_and_reparses_frozen_fields():
    from bt_api_py.execution_routing import parse_execution_routing

    raw = routing_raw(schema_version=2, algorithm_build_id="build-1")
    raw["rules"][0]["schedule_binding"] = {
        "quantity_step": "1",
        "price_step": "0.5",
        "retry_limit": 2,
    }
    del raw["rules"][0]["algorithm"]
    parsed = parse_client_config(strategy_config(routing_value=raw))
    normalized = parsed.to_public_dict()["clients"]["execution"]["settings"]["options"][
        "execution_routing"
    ]
    assert normalized["schema_version"] == 2 and normalized["algorithm_build_id"] == "build-1"
    assert normalized["rules"][0]["algorithm"] == {"class": "PASS", "version": 1, "params": {}}
    assert normalized["rules"][0]["schedule_binding"] == raw["rules"][0]["schedule_binding"]
    assert parse_execution_routing(normalized).to_plain() == normalized


def test_strategy_routing_import_does_not_load_execution_package():
    code = (
        "import sys; import bt_api_py.execution_routing; "
        "assert not any(k == 'bt_api_execution' or k.startswith('bt_api_execution.') "
        "for k in sys.modules)"
    )
    subprocess.run([sys.executable, '-c', code], check=True)
