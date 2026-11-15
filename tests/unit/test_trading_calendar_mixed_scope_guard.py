"""Offline cross-process competition for single-target simulation versions."""

import json
import subprocess
import sys
from datetime import date

import pytest
from bt_api_ctp.calendar_akshare_fetch import collect_akshare_contract

from bt_api_py import trading_calendar_akshare_observation as observation
from bt_api_py import trading_calendar_mixed_publication as mixed_publication
from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from tests.unit.test_trading_calendar_mock_cli_integration import command
from tests.unit.test_trading_calendar_mock_fresh_observation import FAKE, confirm
from tests.unit.test_trading_calendar_mock_persistent import run_args, settings
from tests.unit.test_trading_calendar_observation_run import env_without_network

FS = "TRUSTED_LOCAL_POSIX"


def candidate(root, run_id, parent_id, venue, observation_id, candidate_id):
    def collector(**options):
        return collect_akshare_contract(
            **options,
            _runner=lambda *_: (
                "CHILD_OK",
                {"status": "OK", "rows": [["交易时间", "日盘 09:00-10:00"]]},
            ),
        )

    result = observation.observe(
        root=str(root),
        observation_id=observation_id,
        run_id=run_id,
        venue=venue,
        contract="RB2701" if venue == "SHFE" else "M2701",
        requested_date=date(2026, 11, 13),
        timeout=1,
        filesystem=FS,
        _collector=collector,
    )
    assert result["record_state"] == "COMMITTED"
    record = mixed.build_template(
        root=str(root),
        candidate_root=str(root),
        filesystem=FS,
        candidate_id=parent_id,
        observation_id=observation_id,
        run_id=run_id,
        target={
            "venue": venue,
            "kind": "future",
            "product": "pfuture",
            "mock_contract": f"mock_{venue.lower()}_future_contract",
            "valid_from": "2026-12-31",
            "valid_to": "2027-01-04",
        },
        assumptions={
            "DAY": {"phase": "continuous", "timezone": "Asia/Shanghai"},
            "NIGHT": {"phase": "continuous", "timezone": "Asia/Shanghai"},
        },
    )
    digest = mixed.template_digest(
        record, root=str(root), candidate_root=str(root), filesystem=FS, include_trace=True
    )
    built = mixed_publication.build_once(
        root=str(root),
        filesystem=FS,
        candidate_id=candidate_id,
        template=record,
        evidence_sha256=digest,
    )
    assert built["status"] == "COMMITTED", built


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_cross_process_target_switch_is_rechecked_under_store_lock(tmp_path, monkeypatch, mode):
    root, config = settings(tmp_path, mode)
    env = env_without_network(tmp_path, FAKE)
    env["FAKE_CALLS"] = str(tmp_path / "calls")
    rc, rows, err = command(env, *run_args(root, config))
    assert rc == (0 if mode == "auto" else 10), (rows, err)
    run = next(row for row in rows if row.get("candidate_state") == "COMMITTED")
    if mode == "manual":
        assert confirm(env, root, rows[-1])["status"] == "COMMITTED"
    operator = MockCalendarPublication(root=str(root), run_id=run["run_id"])
    candidate(root, run["run_id"], run["derived_id"], "SHFE", "1" * 32, "2" * 32)
    candidate(root, run["run_id"], run["derived_id"], "DCE", "3" * 32, "4" * 32)
    proposal = operator.prepare(candidate_id="2" * 32)
    assert proposal["status"] == "CHANGED", proposal
    original_reserve = operator._reserve
    ran = []

    def reserve(proposal, publication_id, version_id, *, state):
        if publication_id == "MOCK_shfe" and not ran:
            ran.append(True)
            script = """
import json, sys
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
op = MockCalendarPublication(root=sys.argv[1], run_id=sys.argv[2])
proposal = op.prepare(candidate_id='4'*32)
confirmation = {'reviewer':'MOCK_reviewer','confirmed_at':'2026-11-13T00:00:00.000000Z'} if sys.argv[3] == 'manual' else {}
result = op.publish(proposal=proposal, publication_id='MOCK_dce', version_id='MOCK_dce_ver', emit=lambda _:None, **confirmation)
print(json.dumps(result))
"""
            other = subprocess.run(
                [sys.executable, "-c", script, str(root), run["run_id"], mode],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert other.returncode == 0, other.stderr
            assert json.loads(other.stdout)["status"] == "COMMITTED", other.stdout
        return original_reserve(proposal, publication_id, version_id, state=state)

    monkeypatch.setattr(operator, "_reserve", reserve)
    confirmation = (
        {"reviewer": "MOCK_reviewer", "confirmed_at": "2026-11-13T00:00:00.000000Z"}
        if mode == "manual"
        else {}
    )
    outcome = operator.publish(
        proposal=proposal,
        publication_id="MOCK_shfe",
        version_id="MOCK_shfe_ver",
        emit=lambda _: None,
        **confirmation,
    )
    assert ran and outcome["reason"] == "MIXED_SCOPE_CONFLICT", outcome
    assert outcome["publication_visibility"] == "NOT_COMMITTED"
    assert outcome["reservation_state"] == "RESERVED"
    assert operator._store.read_current().simulation_provenance["target"]["venue"] == "DCE"
    assert not (root / "publications" / "MOCK_shfe.json").exists()
    assert operator.prepare(candidate_id="2" * 32)["reason"] == "MIXED_SCOPE_CONFLICT"
    assert (
        operator.inspect_publication_claim(publication_id="MOCK_shfe")["status"]
        == "VISIBLE_UNAUTHENTICATED"
    )
