"""Pure-memory checks for opt-in account owner cleanup protection."""

import pytest
from bt_api_base.account_protocol import AccountKey, AccountResourceKey

from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings, GatewayAccountOwner
from bt_api_py.service_runtime_thread import OwnerOperationError


class Client:
    def __init__(self, action=lambda: None):
        self.action = action
        self.calls = 0

    def close(self):
        self.calls += 1
        return self.action()


def owner(**policy):
    settings = GatewayAccountClientSettings(
        AccountKey("synthetic", "unit", "paper", "account"),
        (AccountResourceKey("spot", "cash"),),
        frozenset({"account.snapshot.typed"}),
    )
    return GatewayAccountOwner(settings, account_ref="account", scope_ref="scope", **policy)


def failed_close(target):
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()


def test_callback_publishes_unknown_and_stays_sticky():
    unknown, notices = [False], []
    target = owner(strict_cleanup=True, cleanup_guard=lambda: unknown[0],
                   cleanup_uncertain_callback=lambda: notices.append(1))
    client = Client(lambda: unknown.__setitem__(0, True))
    target._client = client
    failed_close(target)
    unknown[0] = False
    failed_close(target)
    assert client.calls == 1 and target._client is client
    assert target._cleanup_inflight is client and target.cleanup_uncertainties == 1
    assert notices == [1]


@pytest.mark.parametrize("bad", [True, None, 0, RuntimeError("guard")])
def test_ambiguous_guard_preserves_client(bad):
    def guard():
        if isinstance(bad, Exception):
            raise bad
        return bad
    target = owner(strict_cleanup=True, cleanup_guard=guard)
    client = target._client = Client()
    failed_close(target)
    failed_close(target)
    assert client.calls == 0 and target._client is client


@pytest.mark.parametrize("action", [lambda: 1, lambda: (_ for _ in ()).throw(KeyboardInterrupt())])
def test_unclear_close_retains_client(action):
    target = owner(strict_cleanup=True)
    client = target._client = Client(action)
    failed_close(target)
    failed_close(target)
    assert client.calls == 1 and target._client is client


def test_replacement_is_not_removed_and_original_remains_referenced():
    target = owner(strict_cleanup=True)
    replacement = Client()
    client = target._client = Client(lambda: setattr(target, "_client", replacement))
    failed_close(target)
    failed_close(target)
    assert target._client is replacement and target._cleanup_inflight is client
    assert client.calls == 1 and replacement.calls == 0


def test_guard_replacement_prevents_original_close():
    target = owner(strict_cleanup=True)
    replacement = Client()
    client = target._client = Client()
    def guard():
        target._client = replacement
        return False
    target._cleanup_guard = guard
    failed_close(target)
    assert target._client is replacement and target._cleanup_inflight is client
    assert client.calls == replacement.calls == 0


@pytest.mark.parametrize("during_guard", [True, False])
def test_reentrant_close_and_notification_are_safe(during_guard):
    notices = []
    target = owner(strict_cleanup=True)
    def reenter():
        failed_close(target)
    def notify():
        notices.append(1)
        reenter()
        raise KeyboardInterrupt()
    target._cleanup_uncertain_callback = notify
    if during_guard:
        def guard():
            reenter()
            return False
        target._cleanup_guard = guard
    client = target._client = Client(lambda: None if during_guard else reenter())
    failed_close(target)
    failed_close(target)
    assert client.calls == (0 if during_guard else 1)
    assert target._client is client and notices == [1]


def test_success_and_default_legacy():
    checks = []
    target = owner(strict_cleanup=True, cleanup_guard=lambda: checks.append(1) or False)
    client = target._client = Client()
    target.close()
    target.close()
    assert client.calls == 1 and target._client is None and checks == [1, 1]
    assert target.cleanup_uncertainties == 0

    legacy = owner(cleanup_guard=lambda: True)
    legacy_client = legacy._client = Client(lambda: 42)
    legacy.close()
    assert legacy_client.calls == 1 and legacy._client is None
    assert legacy.cleanup_uncertainties == 0


def test_constructor_reentry_retains_created_client(monkeypatch):
    import bt_api_py.gateway_strategy_account as account_module

    target = owner(strict_cleanup=True)
    client = Client()
    def factory(*args, **kwargs):
        failed_close(target)
        return client
    monkeypatch.setattr(account_module, "ServiceAccountClient", factory)
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.start(object())
    failed_close(target)
    with pytest.raises(OwnerOperationError, match="ACCOUNT_CLIENT_NOT_NEW"):
        target.start(object())
    assert target._cleanup_inflight is client and client.calls == 0
