from __future__ import annotations

import threading
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

import bt_api_py.gateway_operator_application as application_module
from bt_api_py.gateway_operator import GatewayOperatorDeployment, GatewayOperatorDeploymentError
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplication,
    GatewayOperatorApplicationError,
    GatewayOperatorLaunchConfig,
    GatewayOperatorProfile,
    GatewayOperatorProfileRegistry,
    load_gateway_operator_launch_config,
)


class _ThreadCheckedDeployment(GatewayOperatorDeployment):
    def __init__(self, *, block_recovery: threading.Event | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.block_recovery = block_recovery
        self.recovery_entered = threading.Event()
        self._state = "new"
        self._generation = 0
        self.close_entered = threading.Event()
        self.release_close = threading.Event()
        self.close_calls = 0
        self.fail_first_close = False
        self.fail_state_after_close_error = False

    @property
    def state(self) -> str:
        if self.fail_state_after_close_error and self._state == "close_failed":
            raise RuntimeError("synthetic status observer failed")
        return self._state

    @property
    def cleanup_status(self):
        self.calls.append(("cleanup_status", threading.get_ident()))
        return {"unclaimed_provider_count": 0, "last_cleanup_code": None}

    @property
    def account_session_status(self):
        self.calls.append(("account_session_status", threading.get_ident()))
        return {
            "state": "ready",
            "session_generation": f"generation-{self._generation}",
            "ready": self._state == "started",
            "fresh_for_seconds": 0.25,
            "account_ref": "must-not-escape-status",
        }

    def start(self):
        self.calls.append(("start", threading.get_ident()))
        self._state = "started"
        return self

    def serve_once(self, timeout_ms: int = 0) -> bool:
        self.calls.append(("serve_once", threading.get_ident()))
        if timeout_ms:
            time.sleep(min(timeout_ms, 10) / 1000)
        return False

    def recover_account_session(self):
        self.calls.append(("recover", threading.get_ident()))
        self.recovery_entered.set()
        if self.block_recovery is not None:
            self.block_recovery.wait(2)
        self._generation += 1
        return self.account_session_status

    def close(self) -> None:
        self.calls.append(("close", threading.get_ident()))
        self.close_calls += 1
        self.close_entered.set()
        if self.fail_first_close and self.close_calls == 1:
            self.release_close.wait(2)
            self._state = "close_failed"
            raise RuntimeError("synthetic close did not confirm")
        self._state = "closed"


class _StartupCleanupDeployment(_ThreadCheckedDeployment):
    def __init__(self) -> None:
        super().__init__()
        self.pending_unclaimed = 1

    @property
    def cleanup_status(self):
        self.calls.append(("cleanup_status", threading.get_ident()))
        return {
            "unclaimed_provider_count": self.pending_unclaimed,
            "last_cleanup_code": (
                "UNCLAIMED_PROVIDER_CLOSE_UNCONFIRMED" if self.pending_unclaimed else None
            ),
        }

    def start(self):
        self.calls.append(("start", threading.get_ident()))
        raise GatewayOperatorDeploymentError("MARKET_BINDING_REQUIRED")

    def retry_unclaimed_provider_cleanup(self):
        self.calls.append(("retry_unclaimed_provider_cleanup", threading.get_ident()))
        self.pending_unclaimed = 0
        return {"closed": 1, "pending": 0}


def _account_profile(factory) -> GatewayOperatorProfile:
    return GatewayOperatorProfile("test-account-v1", "account_session_worker", factory)


def test_application_owns_factory_deployment_and_controls_on_one_thread() -> None:
    main_thread = threading.get_ident()
    deployment = _ThreadCheckedDeployment()
    factory_threads: list[int] = []

    def factory() -> GatewayOperatorDeployment:
        factory_threads.append(threading.get_ident())
        return deployment

    profile = _account_profile(factory)
    registry = GatewayOperatorProfileRegistry((profile,))
    app = GatewayOperatorApplication.from_launch_config(
        GatewayOperatorLaunchConfig("test-account-v1"), registry, poll_interval_ms=5
    )

    started = app.start(timeout_seconds=2)
    assert started["state"] == "running"
    assert started["ready"] is True
    assert started["role"] == "account_session_worker"
    account = started["account_session"]
    assert account["session_generation"] == "generation-0"
    assert "account_ref" not in account
    assert (
        app.recover_account_session(timeout_seconds=2)["account_session"]["session_generation"]
        == "generation-1"
    )
    stopped = app.stop(timeout_seconds=2)
    assert stopped["state"] == "closed"

    owner_ids = {thread_id for _, thread_id in deployment.calls}
    assert len(factory_threads) == 1
    assert factory_threads[0] != main_thread
    assert owner_ids == {factory_threads[0]}
    assert "endpoint" not in app.status
    assert "account_ref" not in repr(app.status)
    assert app._thread is not None
    app._thread.join(1)
    assert not app._thread.is_alive()


def test_recovery_timeout_retains_inflight_command_and_prevents_duplicate() -> None:
    release_recovery = threading.Event()
    deployment = _ThreadCheckedDeployment(block_recovery=release_recovery)
    app = GatewayOperatorApplication(_account_profile(lambda: deployment), poll_interval_ms=5)
    app.start(timeout_seconds=2)

    with pytest.raises(GatewayOperatorApplicationError) as timeout:
        app.recover_account_session(timeout_seconds=0.02)
    assert timeout.value.code == "APPLICATION_RECOVERY_TIMEOUT"
    assert deployment.recovery_entered.wait(1)
    assert app.status["operation"]["state"] == "outcome_uncertain"
    with pytest.raises(GatewayOperatorApplicationError) as duplicate:
        app.recover_account_session(timeout_seconds=1)
    assert duplicate.value.code == "APPLICATION_OPERATION_IN_PROGRESS"

    release_recovery.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if app.status["operation"]["state"] == "succeeded":
            break
        time.sleep(0.005)
    assert app.status["operation"]["state"] == "succeeded"
    assert app.status["account_session"]["session_generation"] == "generation-1"
    assert app.stop(timeout_seconds=2)["state"] == "closed"


def test_stop_timeout_keeps_owner_for_explicit_close_retry() -> None:
    deployment = _ThreadCheckedDeployment()
    deployment.fail_first_close = True
    deployment.fail_state_after_close_error = True
    app = GatewayOperatorApplication(_account_profile(lambda: deployment), poll_interval_ms=5)
    try:
        app.start(timeout_seconds=2)

        with pytest.raises(GatewayOperatorApplicationError) as timeout:
            app.stop(timeout_seconds=0.02)
        assert timeout.value.code == "APPLICATION_STOP_TIMEOUT"
        assert deployment.close_entered.wait(1)
        assert app.status["state"] == "uncertain"
        assert app._thread is not None and app._thread.is_alive()

        deployment.release_close.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            operation = app.status["operation"]
            if (
                operation["state"] == "outcome_uncertain"
                and operation["error_code"] == "APPLICATION_CLOSE_UNCERTAIN"
                and deployment.close_calls == 1
            ):
                break
            time.sleep(0.005)

        assert app.status["state"] == "uncertain"
        assert app.status["error_code"] == "APPLICATION_CLOSE_UNCERTAIN"
        assert app._thread is not None and app._thread.is_alive()
        status = app.stop(timeout_seconds=2)
        assert status["state"] == "closed"
        assert deployment.close_calls == 2
    finally:
        deployment.release_close.set()
        if app._thread is not None and app._thread.is_alive():
            with suppress(GatewayOperatorApplicationError):
                app.stop(timeout_seconds=2)
            app._thread.join(1)
            assert not app._thread.is_alive()


def test_confirmed_close_does_not_become_uncertain_during_owner_thread_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_thread_exit = threading.Event()
    owner_main_returned = threading.Event()
    real_thread_module = threading
    real_thread = threading.Thread

    class DelayedExitThread(real_thread):
        def __init__(self, *args, **kwargs) -> None:
            owner_main = kwargs["target"]

            def hold_after_owner_main() -> None:
                owner_main()
                owner_main_returned.set()
                release_thread_exit.wait(2)

            kwargs["target"] = hold_after_owner_main
            super().__init__(*args, **kwargs)

    deployment = _ThreadCheckedDeployment()
    app = GatewayOperatorApplication(_account_profile(lambda: deployment), poll_interval_ms=5)
    monkeypatch.setattr(
        application_module,
        "threading",
        SimpleNamespace(
            Thread=DelayedExitThread,
            current_thread=real_thread_module.current_thread,
        ),
    )
    try:
        app.start(timeout_seconds=2)

        assert app.stop(timeout_seconds=0.01)["state"] == "closed"
        assert owner_main_returned.wait(1)
        assert app._thread is not None and app._thread.is_alive()
        # The owner has completed all Host work but is still unwinding. A
        # repeated stop must return the terminal status instead of queueing a
        # cleanup command to a thread that no longer services commands.
        assert app.stop(timeout_seconds=0.01)["state"] == "closed"
        assert deployment.close_calls == 1
    finally:
        release_thread_exit.set()
        if app._thread is not None:
            app._thread.join(1)
            assert not app._thread.is_alive()


def test_failed_start_retains_owner_until_unclaimed_provider_cleanup_is_retried() -> None:
    main_thread = threading.get_ident()
    deployment = _StartupCleanupDeployment()
    app = GatewayOperatorApplication(_account_profile(lambda: deployment), poll_interval_ms=5)

    with pytest.raises(GatewayOperatorApplicationError) as failure:
        app.start(timeout_seconds=2)
    assert failure.value.code == "MARKET_BINDING_REQUIRED"
    assert app.status["state"] == "failed"
    assert app.status["cleanup"]["unclaimed_provider_count"] == 1
    assert app._thread is not None and app._thread.is_alive()

    status = app.stop(timeout_seconds=2)
    assert status["state"] == "failed"
    assert status["cleanup"]["unclaimed_provider_count"] == 0
    retry_calls = [
        call for call in deployment.calls if call[0] == "retry_unclaimed_provider_cleanup"
    ]
    assert len(retry_calls) == 1
    assert retry_calls[0][1] != main_thread
    assert app._thread is not None and not app._thread.is_alive()


def test_launch_config_only_selects_a_registered_code_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "operator.yaml"
    config_path.write_text(
        "gateway_operator_config_schema_version: 1\nprofile_ref: test-account-v1\n",
        encoding="utf-8",
    )
    config = load_gateway_operator_launch_config(config_path)
    registry = GatewayOperatorProfileRegistry(
        (_account_profile(lambda: _ThreadCheckedDeployment()),)
    )
    assert config.profile_ref == "test-account-v1"
    app = GatewayOperatorApplication.from_launch_config(config, registry)
    assert app.status["state"] == "new"

    config_path.write_text(
        "gateway_operator_config_schema_version: 1\n"
        "profile_ref: test-account-v1\n"
        "deployment_factory: arbitrary.module:factory\n",
        encoding="utf-8",
    )
    with pytest.raises(GatewayOperatorApplicationError) as invalid:
        load_gateway_operator_launch_config(config_path)
    assert invalid.value.code == "INVALID_LAUNCH_CONFIG"

    with pytest.raises(GatewayOperatorApplicationError) as missing:
        GatewayOperatorApplication.from_launch_config(
            GatewayOperatorLaunchConfig("client-selected-profile"), registry
        )
    assert missing.value.code == "PROFILE_NOT_REGISTERED"
