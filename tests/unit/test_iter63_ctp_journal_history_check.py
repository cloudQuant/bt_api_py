"""The CTP trading profile spaces out the journal's whole-table checks (I63-E135)."""
from tests.unit.test_iter63_gateway_cancel_only_start import _profile


def test_ctp_profile_sets_the_history_check_interval_on_its_journal(tmp_path, monkeypatch):
    _, build = _profile(tmp_path, monkeypatch, cancel_only=('ops-1', 'check'))
    deployment = build().deployment_factory()
    deployment.start()
    try:
        run, = deployment._host._gateway_cancel_only_runs
        journal = run.control._journal
        assert journal._deep_interval == 30.0
        # The profile's own writes after opening did not disturb it: the journal still answers.
        cause = run.owner.submit_service_call(lambda: run.control.get(run.cause_id)).result(timeout_seconds=2)
        assert cause['active'] == 1
    finally:
        deployment.close()
