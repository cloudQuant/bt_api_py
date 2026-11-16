"""The CTP coverage fix must not reinterpret existing Gateway snapshots."""
from datetime import datetime, timezone

import pytest
from bt_api_base.account_protocol import AccountKey, AccountObservation, AccountResourceKey
from bt_api_ctp.account_resource_coverage import CTP_ACCOUNT_RESOURCE_COVERAGE
from bt_api_gateway import GatewayEventJournal
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker, GatewayAccountSessionWorkerError
from bt_api_gateway.session_guard import LocalAccountSessionGuard


def test_existing_other_coverage_is_rejected_on_restart(tmp_path):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    key = AccountKey("ctp", "simnow", "simnow", "synthetic-account", "synthetic-broker")
    old = (AccountResourceKey("future", "main"),)
    observation = AccountObservation(key, "synthetic-observation", old, (), (), None, now, now,
                                     "multi_query", True, True)

    class Provider:
        def connect(self, **kwargs):
            pass

        def read_account(self, **kwargs):
            return observation

        def close(self):
            return True

    journal = GatewayEventJournal(tmp_path / "gateway.sqlite")
    guards = tmp_path / "guards"
    guards.mkdir()

    def worker(coverage):
        return GatewayAccountSessionWorker(
            Provider(), journal, account_key=key, journal_account_scope="account:shared",
            state_scope="account:state", guard=LocalAccountSessionGuard(guards, key.account_key_id, "owner"),
            required_coverage=coverage,
        )

    first = worker(old)
    try:
        first.start()
        assert first.latest_snapshot is not None
    finally:
        first.close()
    second = worker(CTP_ACCOUNT_RESOURCE_COVERAGE)
    try:
        with pytest.raises(GatewayAccountSessionWorkerError) as error:
            second.start()
        assert error.value.code == "STATE_STREAM_COVERAGE_MISMATCH"
    finally:
        second.close()
