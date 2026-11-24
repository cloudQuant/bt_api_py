"""Real local CURVE discovery over a borrowed Execution journal owner."""
import sqlite3
from pathlib import Path

from tests.unit.test_execution_combined_service_host import (
    management_client,
    parent_submit_and_query,
    planned_parent,
    running_combined,
    seed,
    setup_combined,
)


def test_discovery_observes_empty_then_submitted_parent_without_host_restart(tmp_path):
    raw, endpoint, parent_keys, read_keys = setup_combined(tmp_path)
    seed(raw)
    wrapper = None
    with running_combined(raw) as host:
        assert host.parent.journal is host.management.journal
        wrapper, client = management_client(raw['management'], read_keys)
        try:
            empty = client.discover_recent(query_id='discovery-empty')
            assert empty.status == 'OBSERVED'
            assert empty.source == 'LOCAL_JOURNAL' and empty.parent_refs == ()
            assert empty.journal_incarnation == host.parent.journal._manifest['incarnation']

            payload = planned_parent()
            parent_ref = payload['spec']['parent_ref']
            parent_submit_and_query(endpoint, parent_keys, payload)

            discovered = client.discover_recent(query_id='discovery-after-submit')
            assert discovered.status == 'OBSERVED'
            assert discovered.parent_refs == (parent_ref,)
            assert discovered.journal_incarnation == empty.journal_incarnation
            readback = client.query(parent_ref, query_id='readback-after-submit')
            assert readback.status == 'OBSERVED'
            assert readback.snapshot.parent_ref == parent_ref
            assert readback.snapshot.intake_local_state == 'RECEIVED'
            assert readback.snapshot.journal_incarnation == discovered.journal_incarnation
            assert host.parent.journal is host.management.journal
            assert host.management.state == 'running'
            assert host.management.listener.status().outcome == 'RUNNING'
        finally:
            if wrapper is not None:
                wrapper.close(timeout_seconds=5)

    audit_path = Path(raw['management']['storage']['audit_root']) / 'execution-management-read.sqlite3'
    with sqlite3.connect(audit_path) as audit:
        rows = audit.execute(
            'SELECT operation_version,outcome,journal_incarnation,target_digest,result_digest '
            'FROM audit_attempts ORDER BY sequence').fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        (4, 'OBSERVED'), (4, 'OBSERVED'), (1, 'OBSERVED')]
    assert rows[0][2] == rows[1][2] == rows[2][2]
    assert all(len(row[3]) == len(row[4]) == 64 for row in rows)


def test_discovery_and_point_read_survive_live_curve_rotation(tmp_path, monkeypatch):
    import hashlib
    import time

    from bt_api_base.service_protocol import WireScope
    from bt_api_transport_zmq import service_v2
    from bt_api_py.execution_management_read_client import ExecutionManagementReadClient
    from bt_api_py.execution_management_read_grant import PARENT_DISCOVER_RECENT_V1, PARENT_READ_V1
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import BoundExecutionManagementReadTarget, ServiceClientRuntime
    from bt_api_py.service_runtime_thread import (
        ThreadedServiceClientRuntime,
        _CLIENT_SOCKET_RECOVERY_HEADROOM,
    )
    from tests.integration.test_execution_read_service_process import runtime as make_runtime

    raw, endpoint, parent_keys, read_keys = setup_combined(tmp_path)
    seed(raw)
    grant = raw['management']['grant']
    listener = raw['management']['listener']
    template, scope, identity, peer = make_runtime(
        raw['management'], read_keys,
        scope=WireScope('paper', account_ref='account-a', strategy_ref='strategy-a'),
    )
    prototype = template._factory()

    runtimes, rpc_calls, supplied_targets = [], [], []
    unregister_results, close_calls = [], []
    forced_ids, forced_transports, threshold = set(), [], []
    original_remaining = service_v2.ZmqServiceClient.remaining_socket_message_ids.fget

    def remaining(client):
        return (_CLIENT_SOCKET_RECOVERY_HEADROOM if id(client) in forced_ids
                else original_remaining(client))

    monkeypatch.setattr(service_v2.ZmqServiceClient, 'remaining_socket_message_ids', property(remaining))

    def runtime_factory():
        generation = len(runtimes)
        runtime = ServiceClientRuntime(
            parse_client_config(prototype._config),
            identity=identity,
            scope_resolver=prototype._scope_resolver,
            peer_resolver=prototype._peer_resolver,
            transport_factory=prototype._factory,
            run_id=f'synthetic-execution-discovery-{generation}',
            requested_operations=prototype._requested_operations,
            require_unregister_ack=True,
        )
        call_bound, unregister, close = (
            runtime.call_bound, runtime._unregister_binding, runtime.close
        )

        def tracked_call(target, operation, arguments, *, deadline_monotonic):
            rpc_calls.append((runtime.run_id, target.runtime_run_id, target.registration_id,
                              target.service_epoch, operation, arguments.get('query_id')))
            return call_bound(target, operation, arguments, deadline_monotonic=deadline_monotonic)

        def tracked_unregister(binding):
            result = unregister(binding)
            unregister_results.append((runtime.run_id, binding.registration_id,
                                       binding.service_epoch, result))
            return result

        def tracked_close():
            close_calls.append(runtime.run_id)
            return close()

        runtime.call_bound = tracked_call
        runtime._unregister_binding = tracked_unregister
        runtime.close = tracked_close
        runtimes.append(runtime)
        return runtime

    wrapper = ThreadedServiceClientRuntime(
        runtime_factory, require_unregister_ack=True, recover_on_socket_retirement=True
    )
    delegate_call_bound = wrapper.call_bound

    def track_supplied_target(
        target, operation, arguments, *, timeout_seconds=5.0, deadline_monotonic=None
    ):
        supplied_targets.append((target.runtime_run_id, target.registration_id, target.service_epoch,
                                 operation, arguments.get('query_id')))
        return delegate_call_bound(
            target, operation, arguments, timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
        )

    wrapper.call_bound = track_supplied_target
    closed = None
    with running_combined(raw) as host:
        assert host.parent.journal is host.management.journal
        try:
            initial = wrapper.start(timeout_seconds=8)
            assert initial.state == 'running' and initial.healthy and initial.strict_unregister_ack is True
            assert initial.min_socket_message_ids_remaining > _CLIENT_SOCKET_RECOVERY_HEADROOM
            (old_binding,) = initial.bindings
            old_runtime = runtimes[0]
            adapters = tuple(old_runtime._transports.values())
            assert len(adapters) == 1
            old_transport = adapters[0]._transport
            target = BoundExecutionManagementReadTarget(
                'application.execution_management', 'service.execution_management_read',
                grant['service_id'], grant['instance_id'], grant['scope_ref'], scope, identity,
                initial.run_id, 'management-zmq', 'key', listener['endpoint'], peer,
                hashlib.sha256(read_keys[0]).hexdigest(),
                old_binding.registration_id, old_binding.service_epoch,
            )
            client = ExecutionManagementReadClient(wrapper, target=target, grant_revision=grant['revision'])

            payload = planned_parent()
            parent_ref = payload['spec']['parent_ref']
            parent_submit_and_query(endpoint, parent_keys, payload)
            before = client.query(parent_ref, query_id='rotation-parent-before')
            assert before.status == 'OBSERVED' and before.snapshot.parent_ref == parent_ref
            assert before.snapshot.intake_local_state == 'RECEIVED'
            assert before.snapshot.journal_incarnation == host.parent.journal._manifest['incarnation']
            assert wrapper.status().run_id == initial.run_id and len(runtimes) == 1
            remaining_before_force = original_remaining(old_transport)
            assert type(remaining_before_force) is int
            assert remaining_before_force > _CLIENT_SOCKET_RECOVERY_HEADROOM
            forced_transports.append(old_transport)
            forced_ids.add(id(old_transport))
            threshold.append((
                initial.run_id,
                service_v2.ZmqServiceClient.remaining_socket_message_ids.fget(old_transport),
            ))
            assert len(threshold) == 1 and threshold[0] == (
                initial.run_id, _CLIENT_SOCKET_RECOVERY_HEADROOM
            )

            deadline = time.monotonic() + 10
            successor = wrapper.status()
            while time.monotonic() < deadline:
                successor = wrapper.status()
                if successor.run_id != initial.run_id and successor.healthy:
                    break
                time.sleep(0.01)
            assert successor.state == 'running' and successor.healthy
            assert successor.run_id != initial.run_id and len(runtimes) == 2
            (new_binding,) = successor.bindings
            assert new_binding.registration_id != old_binding.registration_id
            assert new_binding.service_epoch == old_binding.service_epoch
            assert new_binding.authenticated_peer == peer
            assert new_binding.server_key_fingerprint == target.server_key_fingerprint

            old = runtimes[0]
            assert old.state == 'closed' and old.bindings == ()
            assert (old.cleanup_failures, old.unregister_uncertainties,
                    old.unknown_registration_count, old.open_transport_count) == (0, 0, 0, 0)
            assert close_calls == [initial.run_id]
            assert unregister_results == [(
                initial.run_id, old_binding.registration_id, old_binding.service_epoch, True
            )]

            discovered = client.discover_recent(query_id='rotation-discovery-after')
            assert discovered.status == 'OBSERVED' and discovered.source == 'LOCAL_JOURNAL'
            assert discovered.parent_refs == (parent_ref,)
            assert discovered.journal_incarnation == before.snapshot.journal_incarnation
            readback = client.query(parent_ref, query_id='rotation-parent-after')
            assert readback.status == 'OBSERVED' and readback.snapshot.parent_ref == parent_ref
            assert readback.snapshot.intake_local_state == 'RECEIVED'
            assert readback.snapshot.journal_incarnation == discovered.journal_incarnation
            assert host.parent.journal is host.management.journal
            assert host.management.listener.status().outcome == 'RUNNING'

            assert supplied_targets == [
                (initial.run_id, old_binding.registration_id, old_binding.service_epoch,
                 PARENT_READ_V1, 'rotation-parent-before'),
                (initial.run_id, old_binding.registration_id, old_binding.service_epoch,
                 PARENT_DISCOVER_RECENT_V1, 'rotation-discovery-after'),
                (initial.run_id, old_binding.registration_id, old_binding.service_epoch,
                 PARENT_READ_V1, 'rotation-parent-after'),
            ]
            assert rpc_calls == [
                (initial.run_id, initial.run_id, old_binding.registration_id, old_binding.service_epoch,
                 PARENT_READ_V1, 'rotation-parent-before'),
                (successor.run_id, successor.run_id, new_binding.registration_id, new_binding.service_epoch,
                 PARENT_DISCOVER_RECENT_V1, 'rotation-discovery-after'),
                (successor.run_id, successor.run_id, new_binding.registration_id, new_binding.service_epoch,
                 PARENT_READ_V1, 'rotation-parent-after'),
            ]
        finally:
            closed = wrapper.close(timeout_seconds=10)

    assert len(forced_transports) == 1
    assert close_calls == [runtime.run_id for runtime in runtimes]
    assert len(unregister_results) == len(runtimes) == 2
    assert all(result[-1] is True for result in unregister_results)
    assert (closed.state, closed.thread_alive, closed.strict_unregister_ack) == ('closed', False, True)
    assert (closed.cleanup_failures, closed.unregister_uncertainties,
            closed.unknown_registration_count, closed.open_transport_count) == (0, 0, 0, 0)
    assert closed.bindings == () and host.state == 'closed'

    audit_path = Path(raw['management']['storage']['audit_root']) / 'execution-management-read.sqlite3'
    with sqlite3.connect(audit_path) as audit:
        rows = audit.execute(
            'SELECT operation_version,outcome,journal_incarnation,target_digest,result_digest '
            'FROM audit_attempts ORDER BY sequence'
        ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        (1, 'OBSERVED'), (4, 'OBSERVED'), (1, 'OBSERVED')
    ]
    assert len({row[2] for row in rows}) == 1
    assert all(len(row[3]) == len(row[4]) == 64 for row in rows)
