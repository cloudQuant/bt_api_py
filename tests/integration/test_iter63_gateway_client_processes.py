"""Spawned offline Gateway/strategy clients with synthetic provider and test SQLite host.

The test-only SQLite adapter is not Backtrader persistence or provider evidence.
"""

import json
import multiprocessing
import os
import queue
import time
from contextlib import suppress
from pathlib import Path

import pytest
import zmq
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    SERVICE,
    _service_config,
)

OPS = frozenset({"direct.reserve", "direct.release", "direct.command_status", "direct.dispatch"})


def _gateway_process(root, endpoint, server_keys, client_keys, messages, controls, sends):
    owner = host = None
    try:
        root = Path(root)
        guard_dir = root / "guard"
        guard_dir.mkdir()
        holder = []

        def owner_factory():
            guard = LocalAccountSessionGuard(guard_dir, ACCOUNT, "synthetic-gateway")
            generation = guard.acquire()
            journal = GatewayDirectCommandJournal.create(
                root / "commands.sqlite", ACCOUNT, generation=generation
            )
            holder.append(journal)

            def native(payload, port, scope):
                ref = f"{payload['idempotency_key']}-{payload['client_order_id']}"
                identity = NativeOrderIdentity(
                    "TEST", ref, {"inst_id": "rb", "trading_day": "20300101"}, 1
                )
                record = PreDispatchRecord(
                    operation=WriteOperation.PLACE,
                    host_scope=scope,
                    exchange_name="TEST",
                    symbol=payload["symbol"],
                    identity=identity,
                    target_identity=None,
                    request_summary=payload,
                    trading_day="20300101",
                    session_generation=1,
                    created_at_utc="synthetic",
                )
                marker = port.commit(record)
                if marker is None:
                    return NativeDispatchOutcome(False, identity, None)
                sends.put((os.getpid(), payload["idempotency_key"], payload["client_order_id"]))
                return NativeDispatchOutcome(True, identity, marker)

            from iter63_claim_support import SyntheticClaimPort, claiming

            # Decision B: no dispatch without a claim port.
            dispatcher = GatewayDirectDispatcher(
                journal,
                "TEST",
                guard,
                claiming(native),
                lambda identity: ("TEST:" + identity.client_order_id,),
                lambda row: True,
                claim_port=SyntheticClaimPort(),
            )
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

        owner = GatewayDirectOwner(
            owner_factory, queue_size=4, max_queue_wait_seconds=20, busy_timeout_seconds=20
        )
        if not owner.start(10):
            raise RuntimeError("OWNER_START_FAILED")
        journal = holder[0]
        grants = {
            identity.principal_ref: GatewayDirectGrant(identity.actor_ref, frozenset({"rb"}))
            for identity in (CLIENT_A, CLIENT_B)
        }
        binding = GatewayDirectServiceBinding(journal, grants, owner=owner)
        config = _service_config(endpoint).to_public_dict()
        config["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = True
        config = parse_service_config(config)

        def grant(identity):
            return PrincipalGrant(
                identity,
                (
                    ResourceGrant(
                        "gateway-a",
                        "service.direct_write",
                        "scope-a",
                        WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref),
                        OPS,
                    ),
                ),
            )

        authority = TrustedServiceAuthority(
            identity=SERVICE,
            scope=WireScope(DOMAIN),
            scope_ref="scope-a",
            scope_version="scope-v1",
            security_ref="security-a",
            security_version="security-v1",
            registration_policy_ref="direct-policy-v1",
            registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1",
            service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0],
            curve_secret_key=server_keys[1],
            principals={
                client_keys[0][0]: CLIENT_A.principal_ref,
                client_keys[1][0]: CLIENT_B.principal_ref,
            },
            grants={
                CLIENT_A.principal_ref: (grant(CLIENT_A),),
                CLIENT_B.principal_ref: (grant(CLIENT_B),),
            },
        )
        host = ServiceHost(
            config,
            authority,
            root / "registry.sqlite",
            lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
        )
        host.start()
        messages.put(("ready", os.getpid(), journal.journal_id))
        running = True
        while running:
            try:
                command = controls.get_nowait()
            except queue.Empty:
                command = None
            if command == "stop":
                running = False
            elif command == "stop-b":
                result = host._registry.stop_strategy_new(
                    WireScope(
                        DOMAIN, account_ref=ACCOUNT, strategy_ref=CLIENT_B.actor_ref, run_id="run-b"
                    ),
                    "stop-b-test",
                )
                messages.put(("stop-b", result["status"]))
            host.serve_once(10)
    except BaseException as exc:
        messages.put(("error", type(exc).__name__, str(getattr(exc, "code", ""))))
    finally:
        try:
            if host is not None and host.state == "started":
                host.close()
            if owner is not None and not owner.close(10):
                raise RuntimeError("OWNER_CLOSE_FAILED")
            messages.put(("closed", os.getpid()))
        except BaseException as exc:
            messages.put(("cleanup_error", type(exc).__name__))


def test_synthetic_gateway_spawn_starts_and_shuts_down(tmp_path):
    from tests.integration.test_service_account_instrument_metadata import _free_endpoint

    context = multiprocessing.get_context("spawn")
    messages, controls, sends = (context.Queue() for _ in range(3))
    server_keys = zmq.curve_keypair()
    client_keys = (zmq.curve_keypair(), zmq.curve_keypair())
    gateway = context.Process(
        target=_gateway_process,
        args=(str(tmp_path), _free_endpoint(), server_keys, client_keys, messages, controls, sends),
    )
    gateway.start()
    try:
        ready = messages.get(timeout=15)
        assert ready[0] == "ready", ready
        assert ready[1] == gateway.pid and ready[1] != os.getpid()
    finally:
        controls.put("stop")
        gateway.join(15)
        if gateway.is_alive():
            gateway.terminate()
            gateway.join(5)
            pytest.fail("spawned Gateway did not stop")
    assert gateway.exitcode == 0
    assert messages.get(timeout=3) == ("closed", gateway.pid)


class _TestSqliteHost:
    """Test-only durable DTO store; not a production or Backtrader host."""

    def __init__(self, path, binding):
        import sqlite3

        self.path, self.binding = str(path), binding
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS test_host (kind TEXT, key TEXT, value TEXT, PRIMARY KEY(kind,key))"
            )

    def _get(self, kind, key):
        import sqlite3

        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT value FROM test_host WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def _put(self, kind, key, value, *, immutable=False):
        import sqlite3

        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT value FROM test_host WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
            if immutable and old is not None and old[0] != encoded:
                raise ValueError("TEST_HOST_CONFLICT")
            if kind == "record" and old is not None:
                prior = json.loads(old[0])
                if (
                    value["version"] < prior["version"]
                    or value["reservation_id"] != prior["reservation_id"]
                    or value["digest"] != prior["digest"]
                ):
                    raise ValueError("TEST_HOST_RECORD_CONFLICT")
            db.execute("INSERT OR REPLACE INTO test_host VALUES (?, ?, ?)", (kind, key, encoded))

    def save_intent(self, intent):
        import dataclasses

        route = intent.route
        self._put(
            "intent",
            intent.request_key,
            {
                "binding": dataclasses.asdict(intent.binding),
                "request_key": intent.request_key,
                "canonical_payload": intent.canonical_payload,
                "digest": intent.digest,
                "ttl_seconds": intent.ttl_seconds,
                "route": {
                    "mount": route.mount,
                    "service_id": route.service_id,
                    "instance_id": route.instance_id,
                    "scope_ref": route.scope_ref,
                    "scope": dataclasses.asdict(route.scope),
                    "identity": {
                        "role": route.identity.role.value,
                        "principal_ref": route.identity.principal_ref,
                        "actor_ref": route.identity.actor_ref,
                        "delegate_ref": route.identity.delegate_ref,
                    },
                },
            },
            immutable=True,
        )

    def load_intent(self, request_key):
        from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
        from bt_api_base.service_protocol import WireIdentity, WireRole

        from bt_api_py.gateway_direct_client import GatewayClientRoute, GatewayOrderIntent

        value = self._get("intent", request_key)
        if value is None:
            return None
        r = value["route"]
        identity = r["identity"]
        route = GatewayClientRoute(
            r["mount"],
            r["service_id"],
            r["instance_id"],
            r["scope_ref"],
            WireScope(**r["scope"]),
            WireIdentity(
                WireRole(identity["role"]),
                identity["principal_ref"],
                identity["actor_ref"],
                identity["delegate_ref"],
            ),
        )
        return GatewayOrderIntent(
            GatewayCommandBinding(**value["binding"]),
            route,
            value["request_key"],
            value["canonical_payload"],
            value["digest"],
            value["ttl_seconds"],
        )

    def save_record(self, request_key, record):
        self._put("record", request_key, record.to_mapping())

    def load_record(self, request_key):
        from bt_api_base.gateway_direct_protocol import GatewayCommandRecord

        value = self._get("record", request_key)
        return (
            None
            if value is None
            else GatewayCommandRecord.from_mapping(value, expected_binding=self.binding)
        )

    def commit_pre_dispatch(self, request_key, record):
        marker = "synthetic-marker-" + request_key
        self._put(
            "marker",
            request_key,
            {
                "marker": marker,
                "reservation_id": record.reservation_id,
                "digest": record.digest,
                "version": record.version,
            },
            immutable=True,
        )
        return marker

    def save_release(self, plan):
        import dataclasses

        self._put("release", plan.request_key, dataclasses.asdict(plan), immutable=True)

    def load_release(self, request_key):
        from bt_api_py.gateway_direct_client import GatewayReleasePlan

        value = self._get("release", request_key)
        return None if value is None else GatewayReleasePlan(**value)


def _strategy_process(root, endpoint, server_public, keys, journal_id, which, command, messages):
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding

    from bt_api_py._contracts.models import OrderRequest
    from bt_api_py.gateway_direct_client import GatewayClientRoute, GatewayDirectClient
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    from tests.integration.test_iter63_gateway_direct_service import _client_config
    from tests.unit.test_iter63_gateway_direct_service import _order

    identity = CLIENT_A if which == "a" else CLIENT_B
    run_id = "run-" + which
    binding = GatewayCommandBinding(journal_id, ACCOUNT, identity.actor_ref, identity.principal_ref)
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run_id)
    route = GatewayClientRoute(
        "broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, identity
    )
    store = _TestSqliteHost(Path(root) / f"client-{which}.sqlite", binding)

    def factory():
        transport_factory = ZmqTransportFactory(
            {"security-a": ClientCurveKeys(server_public, *keys)}
        )

        class Transport:
            def __init__(self, wrapped):
                self.wrapped, self.dropped = wrapped, False

            def request(self, request, *, timeout):
                reply = self.wrapped.request(request, timeout=timeout)
                if (
                    which == "b"
                    and request.header.operation == "direct.dispatch"
                    and not self.dropped
                ):
                    self.dropped = True
                    raise TimeoutError("synthetic lost dispatch reply")
                return reply

            def close(self):
                self.wrapped.close()

        return ServiceClientRuntime(
            parse_client_config(_client_config(endpoint, identity).to_public_dict()),
            identity=identity,
            scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
            peer_resolver=lambda _: SERVICE,
            transport_factory=lambda *args: Transport(transport_factory(*args)),
            run_id=run_id,
        )

    runtime = ThreadedServiceClientRuntime(factory)
    try:
        runtime.start(timeout_seconds=10)
        client = GatewayDirectClient(runtime, binding=binding, route=route, host=store)
        messages.send(("ready", os.getpid()))
        while True:
            op, key = command.recv()
            if op == "stop":
                break
            if op == "reserve":
                payload = _order(key=key, symbol="rb")
                payload["client_order_id"] = "client-" + which
                result = client.reserve(
                    OrderRequest.from_dict(payload), ttl_seconds=30, timeout_seconds=10
                )
            elif op == "dispatch":
                result = client.dispatch(key, timeout_seconds=10)
            elif op == "status":
                result = client.status(key, timeout_seconds=10)
            elif op == "status_raw":
                result = runtime.call(
                    "broker.direct_write",
                    "direct.command_status",
                    {"request_key": key},
                    timeout_seconds=10,
                )
                messages.send(("raw", dict(result)))
                continue
            elif op == "reopen":
                store = _TestSqliteHost(Path(root) / f"client-{which}.sqlite", binding)
                client = GatewayDirectClient(runtime, binding=binding, route=route, host=store)
                messages.send(("reopened", os.getpid()))
                continue
            else:
                raise ValueError("unknown test operation")
            record = result.record
            messages.send(
                (
                    "result",
                    result.outcome,
                    result.code,
                    result.marker_id,
                    None if record is None else record.to_mapping(),
                )
            )
    except BaseException as exc:
        messages.send(("error", type(exc).__name__, str(getattr(exc, "code", ""))))
    finally:
        try:
            runtime.close(timeout_seconds=10)
            messages.send(("closed", os.getpid()))
        except BaseException as exc:
            messages.send(("cleanup_error", type(exc).__name__))
        messages.close()


def _recv(pipe):
    assert pipe.poll(15), "spawned test process did not respond"
    return pipe.recv()


def test_two_strategy_processes_share_only_gateway_owner(tmp_path):
    from tests.integration.test_service_account_instrument_metadata import _free_endpoint

    ctx = multiprocessing.get_context("spawn")
    messages, controls, sends = (ctx.Queue() for _ in range(3))
    server_keys = zmq.curve_keypair()
    client_keys = (zmq.curve_keypair(), zmq.curve_keypair())
    endpoint = _free_endpoint()
    gateway = ctx.Process(
        target=_gateway_process,
        args=(str(tmp_path), endpoint, server_keys, client_keys, messages, controls, sends),
    )
    children, pipes = [], []
    gateway.start()
    try:
        ready = messages.get(timeout=15)
        assert ready[0] == "ready", ready
        journal_id = ready[2]
        for index, which in enumerate(("a", "b")):
            parent_command, child_command = ctx.Pipe()
            parent_reply, child_reply = ctx.Pipe()
            child = ctx.Process(
                target=_strategy_process,
                args=(
                    str(tmp_path),
                    endpoint,
                    server_keys[0],
                    client_keys[index],
                    journal_id,
                    which,
                    child_command,
                    child_reply,
                ),
            )
            child.start()
            child_command.close()
            child_reply.close()
            children.append(child)
            pipes.append((parent_command, parent_reply))
            assert _recv(parent_reply) == ("ready", child.pid)
        assert len({os.getpid(), gateway.pid, *(child.pid for child in children)}) == 4

        def ask(index, op, key):
            commands, replies = pipes[index]
            commands.send((op, key))
            result = _recv(replies)
            assert result[0] != "error", result
            return result

        a = ask(0, "reserve", "same")
        b = ask(1, "reserve", "same")
        assert a[1] == b[1] == "OBSERVED"
        assert a[4]["strategy_scope"] == "strategy-a" and b[4]["strategy_scope"] == "strategy-b"
        assert a[4]["reservation_id"] != b[4]["reservation_id"]
        assert ask(0, "dispatch", "same")[1] == "QUEUED"
        # The B reply is deliberately lost after the Gateway accepted the request.
        assert ask(1, "dispatch", "same")[1] == "UNKNOWN"
        # The client did not receive a record update and still has RESERVED1.
        import sqlite3

        with sqlite3.connect(tmp_path / "client-b.sqlite") as db:
            local = json.loads(
                db.execute(
                    "SELECT value FROM test_host WHERE kind='record' AND key='same'"
                ).fetchone()[0]
            )
        assert (local["state"], local["version"]) == ("RESERVED", 1)
        sent_rows = [sends.get(timeout=5), sends.get(timeout=5)]
        assert all(item[0] == gateway.pid for item in sent_rows)
        assert {item[2] for item in sent_rows} == {"client-a", "client-b"}
        deadline = time.monotonic() + 10
        while True:
            with sqlite3.connect(tmp_path / "commands.sqlite") as db:
                (state,) = db.execute(
                    "SELECT status FROM direct_journal_commands WHERE strategy=? AND request_key=?",
                    ("strategy-b", "same"),
                ).fetchone()
            if state == "SENT":
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        # No status query first: retry the original durable command identity.
        retry = ask(1, "dispatch", "same")
        assert retry[1] == "OBSERVED" and (retry[4]["state"], retry[4]["version"]) == ("SENT", 3)
        with pytest.raises(queue.Empty):
            sends.get(timeout=0.2)

        def sent(index):
            until = time.monotonic() + 10
            while time.monotonic() < until:
                result = ask(index, "status", "same")
                if result[1] == "OBSERVED" and result[4]["state"] == "SENT":
                    return result
                time.sleep(0.02)
            pytest.fail("synthetic command did not reach SENT")

        sent_a, sent_b = sent(0), sent(1)
        assert sent_a[4]["native_identity"]["fields"]["inst_id"] == "rb"
        assert sent_b[4]["native_identity"]["fields"]["inst_id"] == "rb"
        # Restart strategy B as a genuinely new process, retaining only its
        # temporary test host database and the original logical run identity.
        pipes[1][0].send(("stop", ""))
        assert _recv(pipes[1][1]) == ("closed", children[1].pid)
        children[1].join(15)
        assert children[1].exitcode == 0
        prior_pid = children[1].pid
        pipes[1][0].close()
        pipes[1][1].close()
        parent_command, child_command = ctx.Pipe()
        parent_reply, child_reply = ctx.Pipe()
        replacement = ctx.Process(
            target=_strategy_process,
            args=(
                str(tmp_path),
                endpoint,
                server_keys[0],
                client_keys[1],
                journal_id,
                "b",
                child_command,
                child_reply,
            ),
        )
        replacement.start()
        child_command.close()
        child_reply.close()
        children[1] = replacement
        pipes[1] = (parent_command, parent_reply)
        assert _recv(parent_reply) == ("ready", replacement.pid)
        assert replacement.pid not in {prior_pid, gateway.pid, children[0].pid, os.getpid()}
        assert ask(1, "status", "same")[4]["reservation_id"] == b[4]["reservation_id"]
        assert ask(0, "dispatch", "same")[1] != "QUEUED"
        with pytest.raises(queue.Empty):
            sends.get(timeout=0.2)
        assert ask(1, "reserve", "b-only")[1] == "OBSERVED"
        assert ask(0, "status_raw", "b-only")[1] == {
            "status": "REQUEST_REJECTED",
            "code": "NOT_FOUND",
        }
        controls.put("stop-b")
        assert messages.get(timeout=5) == ("stop-b", "applied")
        assert ask(1, "status", "same")[4]["state"] == "SENT"
        assert ask(1, "reserve", "b-after-stop")[1] != "OBSERVED"
        assert ask(0, "reserve", "a-after-stop")[1] == "OBSERVED"
        with pytest.raises(queue.Empty):
            sends.get(timeout=0.2)
    finally:
        cleanup_errors = []
        for command, _reply in pipes:
            with suppress(BrokenPipeError, OSError):
                command.send(("stop", ""))
        for child, (command, reply) in zip(children, pipes, strict=True):
            child.join(15)
            if child.is_alive():
                child.terminate()
                child.join(5)
                cleanup_errors.append("strategy client required termination")
            if child.exitcode != 0:
                cleanup_errors.append(f"strategy client exit {child.exitcode}")
            try:
                if not reply.poll(3) or reply.recv() != ("closed", child.pid):
                    cleanup_errors.append("strategy client cleanup not acknowledged")
            except (EOFError, OSError):
                cleanup_errors.append("strategy client cleanup channel closed")
            command.close()
            reply.close()
        controls.put("stop")
        gateway.join(15)
        if gateway.is_alive():
            gateway.terminate()
            gateway.join(5)
            cleanup_errors.append("Gateway required termination")
        if gateway.exitcode != 0:
            cleanup_errors.append(f"Gateway exit {gateway.exitcode}")
        try:
            if messages.get(timeout=3) != ("closed", gateway.pid):
                cleanup_errors.append("Gateway cleanup not acknowledged")
        except queue.Empty:
            cleanup_errors.append("Gateway cleanup not acknowledged")
        assert not cleanup_errors, cleanup_errors
