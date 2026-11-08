"""Synthetic RPC projection and opt-in contract checks."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_direct_service import (
    STORAGE_OPERATIONS,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    _identity_public,
    validate_service_options,
)
from bt_api_py.service_config import ServiceConfigError, parse_service_config
from tests.integration.test_iter63_gateway_direct_service import _service_config


def test_native_identity_is_bounded_redacted_and_durable():
    identity = {
        "exchange_name": "TEST",
        "client_order_id": "id",
        "session_generation": 1,
        "fields_json": json.dumps({"inst_id": "rb", "front_id": 1}),
    }
    row = SimpleNamespace(native_identity_json=json.dumps(identity), strong_identity_keys=("key",))
    projected, keys = _identity_public(row)
    assert projected == {
        "exchange_name": "TEST",
        "client_order_id": "id",
        "session_generation": 1,
        "fields": {"inst_id": "rb", "front_id": 1},
    }
    assert keys == [hashlib.sha256(b"key").hexdigest()]
    assert _identity_public(
        SimpleNamespace(native_identity_json=None, strong_identity_keys=())
    ) == (None, [])
    for fields in ({"api_key": "bad"}, {"nested": {"token": "bad"}}, {"huge": "x" * 257}):
        bad = dict(identity, fields_json=json.dumps(fields))
        with pytest.raises(RuntimeError, match="INVALID_STORED_IDENTITY"):
            _identity_public(
                SimpleNamespace(native_identity_json=json.dumps(bad), strong_identity_keys=("key",))
            )
    duplicate = dict(identity, fields_json='{"inst_id":"rb","inst_id":"sa"}')
    with pytest.raises(RuntimeError, match="INVALID_STORED_IDENTITY"):
        _identity_public(
            SimpleNamespace(
                native_identity_json=json.dumps(duplicate), strong_identity_keys=("key",)
            )
        )
    with pytest.raises(RuntimeError, match="INVALID_STORED_IDENTITY"):
        _identity_public(SimpleNamespace(native_identity_json=None, strong_identity_keys=("key",)))


def test_dispatch_option_requires_exact_bool_and_storage_default():
    assert (
        validate_service_options({"role": "trading_owner"}, ("service.direct_write",))[
            "dispatch_enabled"
        ]
        is False
    )
    for bad in (1, None, "true"):
        with pytest.raises(ValueError, match="INVALID_DIRECT_OPTIONS"):
            validate_service_options(
                {"role": "trading_owner", "dispatch_enabled": bad}, ("service.direct_write",)
            )
        source = _service_config("tcp://127.0.0.1:49122").to_public_dict()
        source["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = bad
        with pytest.raises(ServiceConfigError):
            parse_service_config(source)


def test_storage_only_binding_and_unstarted_owner_are_not_dispatch_capabilities(tmp_path):
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal", "account-a", generation="gen"
    )
    grants = {"p": GatewayDirectGrant("s", frozenset({"rb"}))}
    binding = GatewayDirectServiceBinding(journal, grants)
    assert binding.supported_operations == STORAGE_OPERATIONS
    owner = GatewayDirectOwner(
        lambda: None, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    with pytest.raises(ValueError, match="DIRECT_OWNER_JOURNAL_MISMATCH"):
        GatewayDirectServiceBinding(journal, grants, owner=owner)
    assert owner.close(1)
