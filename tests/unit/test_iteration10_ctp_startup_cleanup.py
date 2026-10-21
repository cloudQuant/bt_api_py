"""Iteration 10 CTP startup-cleanup contract tests.

Covers the generation-zero startup attempt/stop projection added for the
Venue01 CTP account child:

* Gateway transport validation accepts only finite attempt/receipt payloads.
* The three trusted parent projectors accept exact phases and identities.
* The CTP provider accepts only its own complete generation-zero receipt and
  keeps unresolved factory custody instead of reporting a clean close.
"""

from __future__ import annotations

import sys
import threading
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CTP_SRC = (_REPO_ROOT / "bt_api" / "bt_api_ctp" / "src").resolve()
_GATEWAY_SRC = (_REPO_ROOT / "bt_api" / "bt_api_gateway" / "src").resolve()
for _source in (str(_CTP_SRC), str(_GATEWAY_SRC)):
    if _source in sys.path:
        sys.path.remove(_source)
    sys.path.insert(0, _source)

_loaded_ctp = sys.modules.get("bt_api_ctp")
if _loaded_ctp is not None and not Path(_loaded_ctp.__file__).resolve().is_relative_to(_CTP_SRC):
    pytest.skip(
        "bt_api_ctp was imported from a non-checkout location before this module; "
        "run this file directly to exercise the CTP checkout",
        allow_module_level=True,
    )

from bt_api_base.account_protocol import AccountKey  # noqa: E402
from bt_api_ctp.account_provider import (  # noqa: E402
    CtpAccountSessionProviderError,
    CtpReadOnlyAccountSessionProvider,
    _native_stop_confirmed,
)
from bt_api_ctp.ctp import client as ctp_client  # noqa: E402
from bt_api_ctp.ctp.client import (  # noqa: E402
    CtpNativeStartupAttempt,
    CtpNativeStartupStopReceipt,
    CtpNativeStopReceipt,
    TraderClient,
)
from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture  # noqa: E402
from bt_api_gateway.account_process import (  # noqa: E402
    GatewayAccountSessionProcessBinding,
    GatewayAccountSessionProcessError,
    GatewayAccountSessionProfileUnavailableError,
    _decode_frame,
    _encode_frame,
    _envelope,
    _validate_close_payload,
    _validate_startup_attempt_payload,
    _validate_startup_stop_receipt,
    serve_account_session_child,
)

from bt_api_py.ctp_account_process import (  # noqa: E402
    project_ctp_native_startup_attempt,
    project_ctp_native_startup_stop_receipt,
    project_ctp_native_stop_receipt,
)

_ATTEMPT_ID = "a" * 32
_OTHER_ATTEMPT_ID = "b" * 32
_GENERATION = "0123456789abcdef0123456789abcdef"
_ACCOUNT = AccountKey(
    provider="ctp",
    venue="simnow",
    environment="simnow",
    account_id="fake-investor",
    subaccount_id="fake-broker",
)


def _startup_attempt(attempt_id: str | None, phase: str) -> dict[str, object]:
    return {"api_attempt_id": attempt_id, "phase": phase}


def _startup_receipt(
    attempt_id: str | None = _ATTEMPT_ID, **overrides: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "api_attempt_id": attempt_id,
        "outcome": "api_created_released",
        "connection_generation": 0,
        "native_released": True,
        "join_required": False,
        "join_completed": True,
        "thread_alive": False,
        "timed_out": False,
        "complete": True,
    }
    payload.update(overrides)
    return payload


def _active_receipt(connection_generation: int = 7) -> dict[str, object]:
    return {
        "connection_generation": connection_generation,
        "join_required": False,
        "join_completed": True,
        "native_released": True,
        "thread_alive": False,
        "timed_out": False,
        "complete": True,
    }


def _startup_provider() -> CtpReadOnlyAccountSessionProvider:
    def unused_factory() -> object:
        raise AssertionError("the startup projector must not build a feed")

    return CtpReadOnlyAccountSessionProvider(unused_factory, stop_timeout_seconds=0.1)


# ---------------------------------------------------------------------------
# Gateway transport validation
# ---------------------------------------------------------------------------


def test_transport_accepts_finite_startup_attempt_and_receipt() -> None:
    preflight = _startup_attempt(None, "preflight_no_owner")
    assert _validate_startup_attempt_payload(preflight) == preflight

    created = _validate_startup_attempt_payload(_startup_attempt(_ATTEMPT_ID, "api_created"))
    assert created == _startup_attempt(_ATTEMPT_ID, "api_created")

    receipt = _startup_receipt()
    assert _validate_startup_stop_receipt(receipt, created) == receipt


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"phase": "api_created"},
        {"api_attempt_id": _ATTEMPT_ID},
        {"api_attempt_id": _ATTEMPT_ID, "phase": "api_created", "extra": True},
        _startup_attempt(_ATTEMPT_ID, "preflight_no_owner"),
        _startup_attempt(None, "factory_uncertain"),
        _startup_attempt(None, "api_created"),
        _startup_attempt(_ATTEMPT_ID.upper(), "api_created"),
        _startup_attempt("a" * 31, "api_created"),
        _startup_attempt("g" * 32, "api_created"),
        _startup_attempt(1, "api_created"),
        _startup_attempt(_ATTEMPT_ID, "unknown_phase"),
    ],
)
def test_transport_rejects_missing_or_invalid_startup_attempt(value: object) -> None:
    with pytest.raises(GatewayAccountSessionProcessError, match="NATIVE_STARTUP_ATTEMPT_INVALID"):
        _validate_startup_attempt_payload(value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_attempt_id": _OTHER_ATTEMPT_ID},
        {"outcome": "factory_uncertain"},
        {"connection_generation": 1},
        {"connection_generation": False},
        {"native_released": False},
        {"native_released": 1},
        {"join_required": True, "join_completed": False},
        {"thread_alive": True},
        {"timed_out": True},
        {"complete": False},
        {"complete": 1},
    ],
)
def test_transport_rejects_noncanonical_startup_receipt(overrides: dict[str, object]) -> None:
    attempt = _validate_startup_attempt_payload(_startup_attempt(_ATTEMPT_ID, "api_created"))
    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_STOP_RECEIPT_INVALID"
    ):
        _validate_startup_stop_receipt(_startup_receipt(**overrides), attempt)


def test_transport_rejects_missing_fields_and_non_api_created_attempts() -> None:
    created = _validate_startup_attempt_payload(_startup_attempt(_ATTEMPT_ID, "api_created"))
    missing = _startup_receipt()
    del missing["api_attempt_id"]
    extra = _startup_receipt()
    extra["connection_generation_text"] = "0"
    for malformed in (None, {}, missing, extra):
        with pytest.raises(
            GatewayAccountSessionProcessError, match="NATIVE_STARTUP_STOP_RECEIPT_INVALID"
        ):
            _validate_startup_stop_receipt(malformed, created)

    for phase in ("preflight_no_owner", "factory_uncertain"):
        attempt_id = None if phase == "preflight_no_owner" else _ATTEMPT_ID
        attempt = _validate_startup_attempt_payload(_startup_attempt(attempt_id, phase))
        with pytest.raises(
            GatewayAccountSessionProcessError, match="NATIVE_STARTUP_STOP_RECEIPT_INVALID"
        ):
            _validate_startup_stop_receipt(_startup_receipt(), attempt)


def test_transport_close_ack_requires_matching_startup_proof() -> None:
    preflight_attempt = _validate_startup_attempt_payload(
        _startup_attempt(None, "preflight_no_owner")
    )
    preflight_ack = {
        "native_owner_created": False,
        "native_connection_generation": None,
        "native_stop_receipt": None,
        "close_confirmed": True,
    }
    accepted = _validate_close_payload(preflight_ack, _GENERATION, None, preflight_attempt)
    assert accepted["native_owner_created"] is False
    assert accepted["native_startup_stop_receipt"] is None

    created_attempt = _validate_startup_attempt_payload(
        _startup_attempt(_ATTEMPT_ID, "api_created")
    )
    startup_ack = {
        "native_owner_created": True,
        "native_connection_generation": None,
        "native_stop_receipt": None,
        "native_startup_stop_receipt": _startup_receipt(),
        "close_confirmed": True,
    }
    accepted = _validate_close_payload(startup_ack, _GENERATION, None, created_attempt)
    assert accepted["native_startup_stop_receipt"] == _startup_receipt()

    without_receipt = dict(startup_ack)
    del without_receipt["native_startup_stop_receipt"]
    without_receipt["native_owner_created"] = False
    with pytest.raises(GatewayAccountSessionProcessError, match="CLOSE_STARTUP_RECEIPT_MISSING"):
        _validate_close_payload(without_receipt, _GENERATION, None, created_attempt)

    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_STOP_RECEIPT_INVALID"
    ):
        _validate_close_payload(
            {**startup_ack, "native_startup_stop_receipt": _startup_receipt(_OTHER_ATTEMPT_ID)},
            _GENERATION,
            None,
            created_attempt,
        )

    factory_attempt = _validate_startup_attempt_payload(
        _startup_attempt(_ATTEMPT_ID, "factory_uncertain")
    )
    with pytest.raises(GatewayAccountSessionProcessError, match="CLOSE_STARTUP_UNCERTAIN"):
        _validate_close_payload(preflight_ack, _GENERATION, None, factory_attempt)

    active_ack = {
        "native_owner_created": True,
        "native_connection_generation": 7,
        "native_stop_receipt": _active_receipt(),
        "close_confirmed": True,
    }
    assert _validate_close_payload(active_ack, _GENERATION, (True, 7))["close_confirmed"] is True


# ---------------------------------------------------------------------------
# Parent projectors
# ---------------------------------------------------------------------------


def test_parent_startup_attempt_projector_accepts_phases_and_rejects_drift() -> None:
    provider = _startup_provider()
    binding = object()

    provider._startup_attempt = CtpNativeStartupAttempt(None, "preflight_no_owner", binding)
    assert project_ctp_native_startup_attempt(provider) == {
        "api_attempt_id": None,
        "phase": "preflight_no_owner",
    }

    provider._startup_attempt = CtpNativeStartupAttempt(_ATTEMPT_ID, "api_created", binding)
    assert project_ctp_native_startup_attempt(provider) == {
        "api_attempt_id": _ATTEMPT_ID,
        "phase": "api_created",
    }

    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_PROVIDER_INVALID"
    ):
        project_ctp_native_startup_attempt(object())

    for invalid in (
        SimpleNamespace(api_attempt_id=_ATTEMPT_ID, phase="api_created"),
        CtpNativeStartupAttempt(_ATTEMPT_ID, "not_a_phase", binding),
        CtpNativeStartupAttempt(_ATTEMPT_ID, "preflight_no_owner", binding),
        CtpNativeStartupAttempt(None, "api_created", binding),
        CtpNativeStartupAttempt(_ATTEMPT_ID.upper(), "api_created", binding),
    ):
        provider._startup_attempt = invalid
        with pytest.raises(
            GatewayAccountSessionProcessError, match="NATIVE_STARTUP_ATTEMPT_INVALID"
        ):
            project_ctp_native_startup_attempt(provider)


def test_parent_startup_stop_projector_requires_exact_attempt_and_binding() -> None:
    provider = _startup_provider()
    binding = object()
    provider._startup_attempt = CtpNativeStartupAttempt(_ATTEMPT_ID, "api_created", binding)
    provider._last_native_startup_stop_receipt = CtpNativeStartupStopReceipt(
        _ATTEMPT_ID,
        "api_created_released",
        0,
        True,
        False,
        True,
        False,
        False,
        binding,
    )
    assert project_ctp_native_startup_stop_receipt(provider, _ATTEMPT_ID) == _startup_receipt()

    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_RECEIPT_UNCONFIRMED"
    ):
        project_ctp_native_startup_stop_receipt(provider, _OTHER_ATTEMPT_ID)

    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_PROVIDER_INVALID"
    ):
        project_ctp_native_startup_stop_receipt(object(), _ATTEMPT_ID)

    provider._last_native_startup_stop_receipt = CtpNativeStartupStopReceipt(
        _OTHER_ATTEMPT_ID,
        "api_created_released",
        0,
        True,
        False,
        True,
        False,
        False,
        binding,
    )
    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_RECEIPT_UNAVAILABLE"
    ):
        project_ctp_native_startup_stop_receipt(provider, _ATTEMPT_ID)

    provider._last_native_startup_stop_receipt = CtpNativeStartupStopReceipt(
        _ATTEMPT_ID,
        "api_created_released",
        0,
        True,
        False,
        True,
        False,
        False,
        object(),
    )
    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_RECEIPT_UNAVAILABLE"
    ):
        project_ctp_native_startup_stop_receipt(provider, _ATTEMPT_ID)

    provider._startup_attempt = CtpNativeStartupAttempt(None, "preflight_no_owner", binding)
    provider._last_native_startup_stop_receipt = CtpNativeStartupStopReceipt(
        None,
        "preflight_no_owner",
        0,
        None,
        False,
        True,
        False,
        False,
        binding,
    )
    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_RECEIPT_UNCONFIRMED"
    ):
        project_ctp_native_startup_stop_receipt(provider, _ATTEMPT_ID)

    provider._last_native_startup_stop_receipt = None
    with pytest.raises(
        GatewayAccountSessionProcessError, match="NATIVE_STARTUP_RECEIPT_UNCONFIRMED"
    ):
        project_ctp_native_startup_stop_receipt(provider, _ATTEMPT_ID)


def test_parent_native_stop_projector_keeps_positive_generation_contract() -> None:
    provider = _startup_provider()
    provider._native_owner_created = True
    provider._native_connection_generation = 7
    provider._last_native_stop_receipt = CtpNativeStopReceipt(7, False, True, True, False, False)
    assert project_ctp_native_stop_receipt(provider, 7) == _active_receipt()

    with pytest.raises(GatewayAccountSessionProcessError, match="NATIVE_RECEIPT_UNCONFIRMED"):
        project_ctp_native_stop_receipt(provider, 8)

    with pytest.raises(GatewayAccountSessionProcessError, match="NATIVE_RECEIPT_PROVIDER_INVALID"):
        project_ctp_native_stop_receipt(object(), 7)

    provider._native_connection_generation = True
    with pytest.raises(GatewayAccountSessionProcessError, match="NATIVE_RECEIPT_UNAVAILABLE"):
        project_ctp_native_stop_receipt(provider, 7)


# ---------------------------------------------------------------------------
# CTP provider generation-zero cleanup
# ---------------------------------------------------------------------------


class _FakeApi:
    def __init__(self) -> None:
        self.release_calls = 0
        self.init_calls = 0

    def RegisterSpi(self, _spi: object) -> int:
        return 0

    def SubscribePrivateTopic(self, _topic: int) -> int:
        return 0

    def SubscribePublicTopic(self, _topic: int) -> int:
        return 0

    def RegisterFront(self, _front: str) -> int:
        return 0

    def Init(self) -> None:
        self.init_calls += 1

    def Release(self) -> None:
        self.release_calls += 1


def _feed() -> CtpRequestDataFuture:
    return CtpRequestDataFuture(
        broker_id="fake-broker",
        user_id="fake-investor",
        password="fake-only",
        td_front="tcp://127.0.0.1:10001",
        md_front="tcp://127.0.0.1:10002",
        ctp_env_profile="config_front_pair",
        auto_settlement_confirm=False,
        connect_timeout=0.1,
    )


def _provider(feed: CtpRequestDataFuture) -> CtpReadOnlyAccountSessionProvider:
    return CtpReadOnlyAccountSessionProvider(lambda: feed, stop_timeout_seconds=0.1)


def _install_fake_runtime(monkeypatch: pytest.MonkeyPatch, api_factory) -> None:
    monkeypatch.setattr(ctp_client, "_check_native_module", lambda: None)
    monkeypatch.setattr(ctp_client, "_flow_dir", lambda _prefix: "fake-flow")
    monkeypatch.setattr(
        ctp_client,
        "CThostFtdcTraderApi",
        SimpleNamespace(CreateFtdcTraderApi=api_factory),
    )


def _failed_connect(provider: CtpReadOnlyAccountSessionProvider) -> None:
    with pytest.raises(CtpAccountSessionProviderError):
        provider.connect(account_key=_ACCOUNT, generation="fake-generation", timeout=0.1)


def test_provider_close_accepts_complete_preflight_no_owner_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls = 0

    def factory(_flow: str) -> _FakeApi:
        nonlocal factory_calls
        factory_calls += 1
        return _FakeApi()

    _install_fake_runtime(monkeypatch, factory)
    monkeypatch.setattr(
        ctp_client,
        "_check_native_module",
        lambda: (_ for _ in ()).throw(ImportError("blocked native preflight")),
    )
    feed = _feed()
    provider = _provider(feed)

    _failed_connect(provider)

    assert factory_calls == 0
    attempt = provider.native_startup_attempt
    assert attempt is not None
    assert (attempt.api_attempt_id, attempt.phase) == (None, "preflight_no_owner")
    assert provider.native_owner_created is False
    assert provider.close() is True
    receipt = provider.last_native_startup_stop_receipt
    assert type(receipt) is CtpNativeStartupStopReceipt
    assert receipt.outcome == "preflight_no_owner"
    assert receipt.complete is True
    assert feed._trader is None


def test_provider_close_accepts_only_exact_generation_zero_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi()
    _install_fake_runtime(monkeypatch, lambda _flow: api)
    monkeypatch.setattr(
        ctp_client,
        "_TraderSpi",
        lambda *_args: (_ for _ in ()).throw(ValueError("fake SPI constructor")),
    )
    feed = _feed()
    provider = _provider(feed)

    _failed_connect(provider)

    attempt = provider.native_startup_attempt
    assert attempt is not None
    assert attempt.phase == "api_created"
    assert type(attempt.api_attempt_id) is str and len(attempt.api_attempt_id) == 32
    assert api.release_calls == 1
    assert provider.close() is True

    receipt = provider.last_native_startup_stop_receipt
    assert type(receipt) is CtpNativeStartupStopReceipt
    assert receipt.api_attempt_id == attempt.api_attempt_id
    assert receipt._client_binding is attempt._client_binding
    assert receipt.outcome == "api_created_released"
    assert receipt.connection_generation == 0
    assert receipt.complete is True
    assert provider.close() is True
    assert api.release_calls == 1
    assert feed._trader is None
    assert provider._request_feed is None


def test_provider_close_rejects_same_attempt_id_from_another_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi()
    _install_fake_runtime(monkeypatch, lambda _flow: api)
    monkeypatch.setattr(
        ctp_client,
        "_TraderSpi",
        lambda *_args: (_ for _ in ()).throw(ValueError("fake SPI constructor")),
    )
    feed = _feed()
    provider = _provider(feed)
    _failed_connect(provider)
    trader = feed._trader
    attempt = provider.native_startup_attempt
    assert type(trader) is TraderClient
    assert attempt is not None and type(attempt.api_attempt_id) is str

    foreign = CtpNativeStartupStopReceipt(
        attempt.api_attempt_id,
        "api_created_released",
        0,
        True,
        False,
        True,
        False,
        False,
        object(),
    )
    assert foreign.complete is True
    monkeypatch.setattr(trader, "stop_startup_and_wait", lambda *, timeout: foreign)

    assert provider.close() is False
    assert provider._request_feed is feed
    assert feed._trader is trader
    assert api.release_calls == 1
    assert provider.last_native_startup_stop_receipt is None


def test_provider_close_keeps_factory_custody_and_client_blocks_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory_calls = 0

    def factory(_flow: str) -> _FakeApi:
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError("fake factory failure")

    _install_fake_runtime(monkeypatch, factory)
    feed = _feed()
    provider = _provider(feed)

    _failed_connect(provider)

    trader = feed._trader
    assert type(trader) is TraderClient
    attempt = provider.native_startup_attempt
    assert attempt is not None
    assert attempt.phase == "factory_uncertain"
    with pytest.raises(CtpAccountSessionProviderError, match="NATIVE_CUSTODY_UNCERTAIN"):
        _ = provider.native_owner_created

    assert provider.close() is False
    assert provider._request_feed is feed
    assert feed._trader is trader

    legacy_receipt = trader.stop_and_wait(timeout=0.1)
    assert type(legacy_receipt) is CtpNativeStopReceipt
    assert legacy_receipt.complete is False
    startup_receipt = trader.stop_startup_and_wait(timeout=0.1)
    assert type(startup_receipt) is CtpNativeStartupStopReceipt
    assert startup_receipt.outcome == "factory_uncertain"
    assert startup_receipt.complete is False

    with pytest.raises(RuntimeError, match="ctp_trader_client_startup_factory_uncertain"):
        trader.start(block=False)
    assert factory_calls == 1
    current = trader.native_startup_attempt
    assert current.phase == "factory_uncertain"
    assert current.api_attempt_id == attempt.api_attempt_id
    assert current._client_binding is attempt._client_binding
    assert provider.close() is False
    assert provider._request_feed is feed
    assert feed._trader is trader


def test_legacy_active_stop_confirmation_rejects_generation_zero() -> None:
    active = CtpNativeStopReceipt(7, False, True, True, False, False)
    assert _native_stop_confirmed(active, expected_generation=7) is True
    assert _native_stop_confirmed(active, expected_generation=8) is False
    assert (
        _native_stop_confirmed(
            CtpNativeStopReceipt(True, False, True, True, False, False),
            expected_generation=1,
        )
        is False
    )

    preflight = CtpNativeStartupStopReceipt(
        None,
        "preflight_no_owner",
        0,
        None,
        False,
        True,
        False,
        False,
        object(),
    )
    assert preflight.complete is True
    assert _native_stop_confirmed(preflight, expected_generation=0) is False


# ---------------------------------------------------------------------------
# Child close paths and TraderClient startup custody
# ---------------------------------------------------------------------------


class _ChildPipe:
    def __init__(self, requests: list[dict[str, object]]) -> None:
        self.incoming = deque(_encode_frame(request) for request in requests)
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def recv_bytes(self, maxlength: int) -> bytes:
        if not self.incoming:
            raise EOFError
        raw = self.incoming.popleft()
        if len(raw) > maxlength:
            raise OSError("oversized")
        return raw

    def send_bytes(self, value: bytes) -> None:
        self.sent.append(_decode_frame(value, expected_message_type="response"))

    def close(self) -> None:
        self.closed = True


def _child_requests(profile_ref: str, operations: tuple[str, ...]) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []
    for index, operation in enumerate(("bootstrap", *operations), start=1):
        requests.append(
            _envelope(
                message_type="request",
                request_id=f"{index:032x}",
                generation=_GENERATION,
                operation=operation,
                status="request",
                payload=(
                    {"profile_ref": profile_ref, "read_only": True}
                    if operation == "bootstrap"
                    else {"timeout_seconds": 0.05}
                ),
            )
        )
    return requests


def _raising_feed_factory() -> object:
    raise RuntimeError("synthetic feed factory failure")


def _no_attempt_provider_factory(
    profile_ref: str,
) -> GatewayAccountSessionProcessBinding:
    if profile_ref != "synthetic.no-attempt.v1":
        raise GatewayAccountSessionProfileUnavailableError()
    return GatewayAccountSessionProcessBinding(
        _ACCOUNT,
        CtpReadOnlyAccountSessionProvider(_raising_feed_factory, stop_timeout_seconds=0.1),
    )


def test_child_acks_close_after_connect_failure_without_startup_attempt() -> None:
    connection = _ChildPipe(
        _child_requests("synthetic.no-attempt.v1", ("connect", "close"))
    )

    serve_account_session_child(
        connection,
        provider_factory=_no_attempt_provider_factory,
        startup_attempt_projector=project_ctp_native_startup_attempt,
        startup_stop_receipt_projector=project_ctp_native_startup_stop_receipt,
    )

    assert [frame["status"] for frame in connection.sent] == [
        "ready",
        "operation_failed",
        "closed",
    ]
    assert connection.sent[1]["payload"] == {
        "error_code": "QUERY_FAILED",
        "native_startup_attempt": {
            "api_attempt_id": None,
            "phase": "preflight_no_owner",
        },
    }
    assert connection.sent[2]["payload"] == {
        "native_owner_created": False,
        "native_connection_generation": None,
        "native_stop_receipt": None,
        "close_confirmed": True,
    }
    assert connection.closed


class _MalformedAttemptProvider:
    """Fake provider that claims unknown native custody after a failed connect."""

    native_owner_created: object = None
    native_connection_generation = None

    def connect(self, *, account_key: AccountKey, generation: str, timeout: float) -> None:
        del account_key, generation, timeout
        raise RuntimeError("synthetic custody-uncertain connect failure")

    def read_account(self, *, generation: str, timeout: float) -> object:
        del generation, timeout
        raise AssertionError("the malformed-attempt provider is not readable")

    def close(self) -> bool:
        return True


def _malformed_attempt_provider_factory(
    profile_ref: str,
) -> GatewayAccountSessionProcessBinding:
    if profile_ref != "synthetic.malformed-attempt.v1":
        raise GatewayAccountSessionProfileUnavailableError()
    return GatewayAccountSessionProcessBinding(_ACCOUNT, _MalformedAttemptProvider())


def _raising_startup_attempt_projector(provider: object) -> dict[str, object]:
    del provider
    raise ValueError("synthetic malformed startup attempt")


def test_child_close_stays_unconfirmed_for_a_malformed_custody_attempt() -> None:
    connection = _ChildPipe(
        _child_requests("synthetic.malformed-attempt.v1", ("connect", "close"))
    )

    serve_account_session_child(
        connection,
        provider_factory=_malformed_attempt_provider_factory,
        startup_attempt_projector=_raising_startup_attempt_projector,
    )

    assert [frame["status"] for frame in connection.sent] == [
        "ready",
        "operation_failed",
        "close_unconfirmed",
    ]
    assert connection.sent[2]["payload"] == {
        "error_code": "NATIVE_STARTUP_ATTEMPT_UNAVAILABLE"
    }
    assert connection.closed


_RECONNECT_PROVIDERS: list[_ReconnectProvider] = []


class _ReconnectProvider:
    """Fake provider that reports a failed-start attempt, then a live session."""

    def __init__(self) -> None:
        self.connect_calls = 0
        self.close_calls = 0
        self.startup_attempt: dict[str, object] = {
            "api_attempt_id": _ATTEMPT_ID,
            "phase": "api_created",
        }
        self.startup_stop_receipt: dict[str, object] = _startup_receipt()
        self.owner: object = None
        self.generation: int | None = None

    @property
    def native_owner_created(self) -> object:
        return self.owner

    @property
    def native_connection_generation(self) -> int | None:
        return self.generation

    def connect(self, *, account_key: AccountKey, generation: str, timeout: float) -> None:
        del account_key, generation, timeout
        self.connect_calls += 1
        if self.connect_calls == 1:
            raise RuntimeError("synthetic first-connect failure")
        self.owner = True
        self.generation = 7

    def read_account(self, *, generation: str, timeout: float) -> object:
        del generation, timeout
        raise AssertionError("the reconnect provider is not readable")

    def close(self) -> bool:
        self.close_calls += 1
        if self.close_calls == 1:
            return False
        self.owner = False
        self.generation = None
        return True


def _reconnect_provider_factory(
    profile_ref: str,
) -> GatewayAccountSessionProcessBinding:
    if profile_ref != "synthetic.reconnect.v1":
        raise GatewayAccountSessionProfileUnavailableError()
    provider = _ReconnectProvider()
    _RECONNECT_PROVIDERS.append(provider)
    return GatewayAccountSessionProcessBinding(_ACCOUNT, provider)


def _fake_startup_attempt_projector(provider: object) -> dict[str, object]:
    return dict(provider.startup_attempt)  # type: ignore[attr-defined]


def _fake_startup_stop_receipt_projector(
    provider: object, expected_attempt_id: str
) -> dict[str, object]:
    del expected_attempt_id
    return dict(provider.startup_stop_receipt)  # type: ignore[attr-defined]


def test_child_clears_startup_attempt_after_a_live_reconnect() -> None:
    _RECONNECT_PROVIDERS.clear()
    connection = _ChildPipe(
        _child_requests(
            "synthetic.reconnect.v1",
            ("connect", "close", "connect", "close"),
        )
    )

    serve_account_session_child(
        connection,
        provider_factory=_reconnect_provider_factory,
        startup_attempt_projector=_fake_startup_attempt_projector,
        startup_stop_receipt_projector=_fake_startup_stop_receipt_projector,
    )

    provider = _RECONNECT_PROVIDERS[0]
    assert provider.connect_calls == 2
    assert [frame["status"] for frame in connection.sent] == [
        "ready",
        "operation_failed",
        "close_unconfirmed",
        "connected",
        # The attempt captured before the live reconnect must not authorize a
        # later generation-zero close acknowledgement.
        "close_unconfirmed",
    ]
    assert connection.sent[2]["payload"] == {"error_code": "PROVIDER_CLOSE_UNCONFIRMED"}
    assert connection.sent[4]["payload"] == {"error_code": "NATIVE_CLOSE_UNCONFIRMED"}


class _ImmediateJoinApi:
    def __init__(self) -> None:
        self.release_calls = 0
        self.released = threading.Event()

    def RegisterSpi(self, _spi: object | None) -> int:
        return 0

    def Join(self) -> int:
        return 0

    def Release(self) -> None:
        self.release_calls += 1
        self.released.set()


def _trader_client() -> TraderClient:
    return TraderClient(
        "tcp://127.0.0.1:10001",
        "fake-broker",
        "fake-investor",
        "fake-only",
    )


def test_abort_startup_records_join_custody_for_the_startup_receipt() -> None:
    client = _trader_client()
    api = _ImmediateJoinApi()
    spi = SimpleNamespace()
    with client._query_state_lock:
        client._api = api
        client._spi = spi
        client._native_init_started = True
        client._lifecycle_generation = 1
        client._starting_generation = 1
        client._startup_api_attempt_id = _ATTEMPT_ID
        client._startup_api_phase = "api_created"
        client._startup_native_api = api
        client._startup_base_connection_generation = client._connection_generation

    assert client._abort_startup(api, spi, 1) is True
    assert api.released.wait(1.0)

    receipt = client.stop_startup_and_wait(timeout=0.5)
    assert type(receipt) is CtpNativeStartupStopReceipt
    assert receipt.outcome == "api_created_released"
    assert receipt.join_required is True
    assert receipt.join_completed is True
    assert receipt.native_released is True
    assert receipt.thread_alive is False
    assert receipt.timed_out is False
    assert api.release_calls == 1


def test_uninitialized_cleanup_releases_exact_api_behind_a_live_session() -> None:
    client = _trader_client()
    live_api = _ImmediateJoinApi()
    abandoned_api = _ImmediateJoinApi()
    with client._query_state_lock:
        client._api = live_api
        client._spi = object()

    assert client._stop_uninitialized_native_api(abandoned_api, spi=None) is True

    assert abandoned_api.release_calls == 1
    assert live_api.release_calls == 0
    assert client._api is live_api


def test_startup_preflight_stop_cancels_an_inflight_start_reservation() -> None:
    client = _trader_client()
    with client._query_state_lock:
        client._starting_generation = 1
        client._lifecycle_generation = 1

    receipt = client.stop_startup_and_wait(timeout=0.1)

    assert type(receipt) is CtpNativeStartupStopReceipt
    assert receipt.outcome == "preflight_no_owner"
    assert receipt.complete is True
    assert client._starting_generation is None
    assert client._lifecycle_generation != 1


def test_positive_generation_stop_retry_keeps_the_detailed_receipt() -> None:
    client = _trader_client()
    api = _ImmediateJoinApi()
    tracker = ctp_client._CtpNativeJoinTracker()
    tracker.begin()
    tracker.returned(0)
    with client._query_state_lock:
        client._connection_generation = 7
        client._startup_api_attempt_id = _ATTEMPT_ID
        client._startup_api_phase = "api_created"
        client._startup_native_api = api
        client._startup_base_connection_generation = 0
        client._last_stopped_native_api = api
        client._last_stopped_connection_generation = 7
        client._last_stop_join_required = True
        client._last_stop_join_tracker = tracker

    receipt = client.stop_and_wait(timeout=0.05)

    assert type(receipt) is CtpNativeStopReceipt
    assert receipt.connection_generation == 7
    assert receipt.join_required is True
    assert receipt.join_completed is True
    assert receipt.native_released is False
    assert receipt.timed_out is True
