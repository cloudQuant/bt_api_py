from __future__ import annotations

from types import SimpleNamespace

import pytest
from bt_api_base.account_protocol import AccountKey

from bt_api_py.service_account_certification import (
    ServiceAccountCertificationClient,
    ServiceAccountCertificationError,
)
from bt_api_py.service_runtime import ServiceClientRuntime

_ACCOUNT = AccountKey("synthetic", "binance-usdm", "paper", "owner-1")
_COMMON = {
    "account_key_id": _ACCOUNT.account_key_id,
    "session_generation": "generation-1",
    "observation_id": "observation-1",
    "collected_at_utc": "2026-10-16T04:00:00.000000Z",
}


class _Runtime(ServiceClientRuntime):
    def __init__(self, replies, *, change_epoch=False):
        self.replies = list(replies)
        self.maintain_calls = 0
        self.call_log = []
        self.change_epoch = change_epoch

    def maintain(self):
        self.maintain_calls += 1

    def account_binding_view(self, mount, account_ref, *, required_operations):
        return SimpleNamespace(
            mount=mount,
            service_id="service-a",
            instance_id="gateway-a",
            scope_ref="scope-a",
            account_ref=account_ref,
            registration_id="registration-a",
            service_epoch="epoch-b" if self.change_epoch and len(self.call_log) else "epoch-a",
            rpc_scope=("domain-a", account_ref),
            transport_ref="transport-a",
            operations=required_operations,
        )

    def call(self, mount, operation, arguments, *, account_ref=None, scope_ref=None):
        self.call_log.append((mount, operation, dict(arguments), account_ref, scope_ref))
        return self.replies.pop(0)


def _client(runtime):
    return ServiceAccountCertificationClient(
        runtime,
        "store.account",
        account_ref="account-a",
        expected_account=_ACCOUNT,
        expected_scope_ref="scope-a",
    )


def test_client_reads_typed_facts_on_explicit_account_route():
    runtime = _Runtime(
        [
            {
                "observation": {
                    **_COMMON,
                    "can_trade": True,
                    "can_deposit": None,
                    "can_withdraw": False,
                    "dual_side_position": True,
                }
            },
            {"observation": {**_COMMON, "server_time_ms": 1_792_123_321}},
        ]
    )
    client = _client(runtime)
    config = client.read_account_configuration(expected_generation="generation-1")
    server_time = client.read_exchange_server_time(expected_generation="generation-1")
    assert config.can_trade is True and config.can_deposit is None
    assert config.dual_side_position is True
    assert server_time.server_time_ms == 1_792_123_321
    assert runtime.maintain_calls == 2
    assert [entry[1] for entry in runtime.call_log] == [
        "account.configuration",
        "account.server_time_probe",
    ]
    assert all(entry[2] == {} and entry[3:] == ("account-a", "scope-a") for entry in runtime.call_log)


@pytest.mark.parametrize(
    "reply",
    (
        {"observation": {**_COMMON, "server_time_ms": True}},
        {"observation": {**_COMMON, "server_time_ms": "1792123321"}},
        {"observation": {**_COMMON, "server_time_ms": 1_792_123_321, "extra": 1}},
    ),
)
def test_client_refuses_malformed_server_time_without_fallback(reply):
    with pytest.raises(ServiceAccountCertificationError):
        _client(_Runtime([reply])).read_exchange_server_time()


def test_client_refuses_identity_generation_and_binding_changes():
    wrong_owner = {**_COMMON, "account_key_id": "account-" + "c" * 64,
                   "server_time_ms": 1_792_123_321}
    with pytest.raises(ServiceAccountCertificationError) as owner_error:
        _client(_Runtime([{"observation": wrong_owner}])).read_exchange_server_time()
    assert owner_error.value.code == "ACCOUNT_IDENTITY_MISMATCH"

    with pytest.raises(ServiceAccountCertificationError) as generation_error:
        _client(_Runtime([{"observation": {**_COMMON, "server_time_ms": 1_792_123_321}}])) \
            .read_exchange_server_time(expected_generation="another-generation")
    assert generation_error.value.code == "SESSION_GENERATION_CHANGED"

    with pytest.raises(ServiceAccountCertificationError) as binding_error:
        _client(_Runtime(
            [{"observation": {**_COMMON, "server_time_ms": 1_792_123_321}}],
            change_epoch=True,
        )).read_exchange_server_time()
    assert binding_error.value.code == "ACCOUNT_BINDING_CHANGED"

