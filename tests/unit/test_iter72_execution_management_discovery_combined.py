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
