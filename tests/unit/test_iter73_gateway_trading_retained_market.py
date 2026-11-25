"""Iteration 73.10 regression tests for Gateway trading profile retained_market_provider wiring."""

from __future__ import annotations

import os
import subprocess
import threading
import time

import pytest
from bt_api_base.market_protocol import MarketProviderEvent

import bt_api_py.ctp_gateway_trading as module
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, FakeSession
from tests.unit.test_iter63_ctp_gateway_market_profile import key, setup


def _protect_windows_owner_only(path) -> None:
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME", "")
    if not user:
        return
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_trading_profile_uses_retained_market_provider_during_td_reconnect(tmp_path, monkeypatch):
    """TD disconnect making .market_provider unavailable must not kill active MD handles."""
    _protect_windows_owner_only(tmp_path)
    build, _, settings, authority, _ = setup(tmp_path, monkeypatch)

    td_disconnected = threading.Event()
    retained_calls = []

    class FakeMarketProvider:
        def __init__(self):
            self.handles = []

        def subscribe(self, market_key, *, generation):
            handle = (market_key, generation)
            self.handles.append(handle)
            return handle

        def unsubscribe(self, handle):
            return True

        def source_epoch_reset_required(self, handle):
            return False

        def retained_events_ready(self, handle):
            return True

        def poll(self, handle, *, limit):
            return ()

        def close(self):
            return True

    class ReconnectingFakeSession(FakeSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._fake_market = FakeMarketProvider()

        @property
        def market_provider(self):
            if td_disconnected.is_set():
                raise RuntimeError("SESSION_UNAVAILABLE")
            return self._fake_market

        def retained_market_provider(self):
            retained_calls.append(td_disconnected.is_set())
            return self._fake_market

        def connection_status(self):
            return "reconnecting" if td_disconnected.is_set() else "ready"

    # Reuse the exact FakeProvider from test_iter63_ctp_gateway_trading.
    from tests.test_iter63_ctp_gateway_trading import FakeProvider

    monkeypatch.setattr(module, "_load_ctp_types", lambda: (ReconnectingFakeSession, FakeProvider))

    profile = build()
    deployment = profile.deployment_factory()
    resources = deployment._provide_resources(
        "gateway",
        "gateway-a",
        {"role": "trading_owner", "dispatch_enabled": True},
        authority.scope,
    )
    try:
        hub = resources.values["hub"]
        hub._set_publisher(lambda _env: True)
        sub_key = key()
        hub.subscribe(CLIENT_A.principal_ref, sub_key, "reg-1")
        # Trigger TD disconnect: .market_provider would now raise SESSION_UNAVAILABLE.
        td_disconnected.set()
        for _ in range(5):
            hub.maintain(["reg-1"])
            time.sleep(0.02)
        proxy = hub._provider
        assert len(proxy._states) == 1
        state = next(iter(proxy._states.values()))
        assert state.failed is False
        assert any(flag is True for flag in retained_calls)
    finally:
        resources.close()
