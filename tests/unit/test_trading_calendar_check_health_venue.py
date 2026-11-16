"""Venue-specific check health based on durable synthetic batch evidence."""

from datetime import date
import json
import sys

import pytest

from bt_api_py import trading_calendar_schedule_once as schedule
from tests.unit.test_trading_calendar_schedule_once import (
    _schedule_subprocess,
    execute,
    setup_root,
)

FIRST_OPEN = "2026-12-31T16:30:00+00:00"
AS_OF = date(2027, 1, 4)


def health(root, venue, **kwargs):
    return schedule.inspect_check_health(
        root=str(root), venue=venue, as_of_date=AS_OF, filesystem=schedule.FS, **kwargs
    )


def health_with_batch_environment(root, venue, env):
    # The fake batch installs its fallback template via sitecustomize. Read the
    # evidence under that same synthetic provider configuration.
    script = """
import json, sys
from datetime import date
from bt_api_py import trading_calendar_schedule_once as schedule
value = schedule.inspect_check_health(root=sys.argv[1], venue=sys.argv[2],
    as_of_date=date(2027, 1, 4), filesystem=schedule.FS)
print(json.dumps({'final': value}))
"""
    return execute(env, [sys.executable, "-c", script, str(root), venue])


@pytest.mark.parametrize("partial", [False, True])
def test_health_uses_each_venue_result(tmp_path, partial):
    root, env, _, path = setup_root(tmp_path)
    if partial:
        env["FAIL_DCE"] = "1"
    run = execute(env, _schedule_subprocess(env, path, date_utc=FIRST_OPEN))
    assert run["status"] == "COMMITTED"
    assert run["batch_status"] == ("PARTIAL" if partial else "COMPLETE")

    shfe = health_with_batch_environment(root, "SHFE", env)
    dce = health_with_batch_environment(root, "DCE", env)
    gfex = health_with_batch_environment(root, "GFEX", env)
    assert shfe["status"] == "OK" and shfe["days"][0]["status"] == "SUCCESS"
    assert dce["status"] == ("WARNING" if partial else "OK")
    assert gfex["status"] == "WARNING"
    assert gfex["days"][0]["reason_codes"] == ["VENUE_NOT_CHECKED"]


def test_missing_batch_evidence_is_unknown(tmp_path):
    root, env, _, path = setup_root(tmp_path)
    run = execute(env, _schedule_subprocess(env, path, date_utc=FIRST_OPEN))
    evidence = root / "observation-runs" / (
        "mock-daily-batch-" + run["identity_bundle"]["batch_id"] + ".json"
    )
    evidence.write_bytes(b"damaged test evidence")
    result = health(root, "SHFE")
    assert result["status"] == "UNKNOWN"
    assert result["days"][0]["reason_codes"] == ["CHECK_EVIDENCE_UNAVAILABLE"]


def test_configurable_threshold_is_bounded_and_counts_trading_days(tmp_path):
    root, env, _, path = setup_root(tmp_path)
    assert execute(env, _schedule_subprocess(env, path, date_utc=FIRST_OPEN))["status"] == "COMMITTED"
    assert health(root, "SHFE", threshold=1)["status"] == "WARNING"
    assert health(root, "SHFE", threshold=2)["status"] == "OK"
    assert health(root, "SHFE", threshold=1)["trading_days"] == ["2027-01-04"]
    for threshold in (0, -1, True, 1.0, schedule._HEALTH_MAX_TRADING_DAYS + 1):
        assert health(root, "SHFE", threshold=threshold)["reason"] == "INVALID_INPUT"


def test_check_health_cli_accepts_threshold(tmp_path, capsys):
    root, env, _, path = setup_root(tmp_path)
    assert execute(env, _schedule_subprocess(env, path, date_utc=FIRST_OPEN))["status"] == "COMMITTED"
    code = schedule.main([
        "--action", "check-health", "--root", str(root), "--venue", "SHFE",
        "--as-of-date", "2027-01-04", "--threshold", "1", "--trusted-local-posix",
    ])
    result = json.loads(capsys.readouterr().out)
    assert code == 10 and result["threshold"] == 1 and result["status"] == "WARNING"


@pytest.mark.parametrize("args", [
    ["--action", "check-health", "--root", ".", "--venue", "SHFE", "--threshold", "0"],
    ["--action", "scan-publications", "--root", ".", "--threshold", "1"],
])
def test_cli_rejects_invalid_threshold_use(args):
    with pytest.raises(SystemExit) as error:
        schedule.main([*args, "--trusted-local-posix"])
    assert error.value.code == 2
