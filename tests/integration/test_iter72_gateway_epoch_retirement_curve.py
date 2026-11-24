"""Authenticated Gateway epoch retirement for one Risk account-read lease.

This is local synthetic evidence only: a spawned Gateway, temporary SQLite
journals, and CURVE clients with generated keys. It does not start Risk or use
provider/account inputs.
"""
from __future__ import annotations

import multiprocessing
import time

from bt_api_base.service_protocol import WireScope
from bt_api_transport_zmq.server import CurveClientCredentials
from bt_api_transport_zmq.service_v2 import ZmqServiceClient

from tests.integration import _iter64_recovery_support as support
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_service_host import _request as wire_request


def _call(client, operation, payload, *, scope=support.ACCOUNT_ONLY, control=False):
    schema_id = "sdk.service.control" if control else "sdk.service.call"
    return client.request(
        wire_request(
            operation,
            payload,
            schema_id=schema_id,
            identity=support.RISK,
            scope=scope,
        ),
        timeout=5.0,
    ).payload


def _new_client(endpoint, keys):
    return ZmqServiceClient(
        endpoint,
        support.GATEWAY,
        credentials=CurveClientCredentials(
            keys["gateway_server"][0],
            keys["risk_delegate"][0],
            keys["risk_delegate"][1],
        ),
    )


def _register(client, registration_key, client_session_id):
    return _call(
        client,
        "service.register",
        {
            "registration_key": registration_key,
            "client_session_id": client_session_id,
            "instance_id": "gateway-a",
            "port": "service.account_read",
            "scope_ref": "scope-a",
            "operations": ["account.events.progress"],
        },
        control=True,
    )


def _progress(client, registration):
    return _call(
        client,
        "account.events.progress",
        {
            "registration_id": registration["registration_id"],
            "service_epoch": registration["service_epoch"],
            "instance_id": "gateway-a",
            "arguments": {},
        },
    )


def test_risk_account_read_retirement_is_acknowledged_over_new_curve_epoch(tmp_path):
    context = multiprocessing.get_context("spawn")
    gateway_root = tmp_path / "gw"
    gateway_root.mkdir()
    support._restrict_fixture_path(gateway_root, mode=0o700)
    keys = support.keypairs(1)
    endpoint = _free_endpoint()
    messages, controls = context.Queue(), context.Queue()
    generations = []
    client = None

    def start_gateway():
        process = context.Process(
            target=support.gateway_main,
            args=(
                str(gateway_root),
                endpoint,
                keys["gateway_server"],
                support.gateway_publics(keys, 1),
                1,
                messages,
                controls,
            ),
        )
        owner = support.SyntheticGatewayStop(process, controls, messages)
        process.start()
        ready = support.next_message(messages, "ready", 40)
        assert ready[1] == process.pid
        owner.bind_ready(ready)
        generations.append((process, owner))
        return ready

    def stop_gateway(process, owner):
        owner.close()
        assert process.is_alive() is False
        assert process.exitcode == 0
        process.close()

    try:
        first_ready = start_gateway()
        client = _new_client(endpoint, keys)
        old_reply = _register(
            client,
            "iter72-retired-" + str(time.time_ns()),
            "iter72-session-old-" + str(time.time_ns()),
        )
        assert old_reply["ok"] is True and old_reply["code"] == "OK", old_reply
        old = old_reply["result"]
        assert set(old) == {
            "registration_id",
            "service_epoch",
            "expires_at",
            "lease_remaining_seconds",
            "operations",
            "access_policy_ref",
            "access_policy_version",
            "risk_policy_status",
            "risk_policy_ref",
            "risk_policy_version",
            "requirements_ref",
            "policy_version",
            "requirements_version",
        }
        assert old["operations"] == ("account.events.progress",)
        assert _progress(client, old)["result"]["status"] == "OK"

        # Simulate lost local transport state: close without service.unregister.
        client.close()
        client = None
        first_process, first_owner = generations[-1]
        stop_gateway(first_process, first_owner)

        second_ready = start_gateway()
        assert second_ready[2] == first_ready[2]  # the same protected journal
        client = _new_client(endpoint, keys)

        stale_read = _progress(client, old)
        assert stale_read == {"ok": False, "code": "STALE_EPOCH", "result": {}}

        wrong_scope = WireScope(support.DOMAIN, account_ref="account-other")
        wrong_scope_ack = _call(
            client,
            "service.unregister",
            {
                "registration_id": old["registration_id"],
                "service_epoch": old["service_epoch"],
            },
            scope=wrong_scope,
            control=True,
        )
        assert wrong_scope_ack == {"ok": False, "code": "SCOPE_DENIED", "result": {}}

        retired_ack = _call(
            client,
            "service.unregister",
            {
                "registration_id": old["registration_id"],
                "service_epoch": old["service_epoch"],
            },
            control=True,
        )
        assert retired_ack == {
            "ok": True,
            "code": "OK",
            "result": {"unregistered": True, "registration_id": old["registration_id"]},
        }

        new_reply = _register(
            client,
            "iter72-fresh-" + str(time.time_ns()),
            "iter72-session-new-" + str(time.time_ns()),
        )
        assert new_reply["ok"] is True and new_reply["code"] == "OK", new_reply
        new = new_reply["result"]
        assert new["registration_id"] != old["registration_id"]
        assert new["service_epoch"] != old["service_epoch"]
        assert _progress(client, new)["result"]["status"] == "OK"
        released = _call(
            client,
            "service.unregister",
            {
                "registration_id": new["registration_id"],
                "service_epoch": new["service_epoch"],
            },
            control=True,
        )
        assert released == {
            "ok": True,
            "code": "OK",
            "result": {"unregistered": True, "registration_id": new["registration_id"]},
        }

        client.close()
        client = None
        second_process, second_owner = generations[-1]
        stop_gateway(second_process, second_owner)
    finally:
        if client is not None:
            client.close()
        for process, owner in reversed(generations):
            if owner._closed:
                continue
            if owner._bound:
                owner.close()
            else:
                owner.terminate_after_constructor_failure()
            if process.pid is not None and process.is_alive() is False:
                process.close()
        for channel in (messages, controls):
            channel.close()
            channel.join_thread()
