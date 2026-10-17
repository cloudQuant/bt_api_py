"""Durable Risk-side single-dispatch state for DIM05 roll/exit."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from bt_api_execution import ExecutionState

from bt_api_py.service_risk_group_roll_exit import (
    RiskGroupRollExitActionStore,
    RiskGroupRollExitError,
)
from tests.unit.test_service_group_roll_exit import (
    _authorization,
    _observation,
    _source_and_request,
)
from bt_api_py.service_group_roll_exit import GroupRollExitPhase


def test_risk_roll_exit_store_cas_is_durable_and_rejects_stale_fill(tmp_path: Path) -> None:
    source, request = _source_and_request()
    authorization = _authorization(source, request)
    database = tmp_path / "risk-roll-exit.sqlite3"

    first_store = RiskGroupRollExitActionStore(database)
    prepared = first_store.prepare(authorization)
    assert prepared["phase"] == GroupRollExitPhase.PREPARED.value
    assert first_store.start(
        authorization.action_id, authorization.authorization_sha256
    ) is True
    assert first_store.start(
        authorization.action_id, authorization.authorization_sha256
    ) is False

    partial = _observation(
        authorization,
        phase=GroupRollExitPhase.STATUS_OBSERVED,
        state=ExecutionState.PARTIALLY_FILLED,
        filled=Decimal("0.5"),
        revision=5,
    )
    observed = first_store.observe(partial)
    assert observed["phase"] == GroupRollExitPhase.STATUS_OBSERVED.value

    reopened = RiskGroupRollExitActionStore(database)
    durable = reopened.get(authorization.action_id)
    assert durable is not None
    assert durable["phase"] == GroupRollExitPhase.STATUS_OBSERVED.value
    assert durable["authorization_sha256"] == authorization.authorization_sha256

    stale = _observation(
        authorization,
        phase=GroupRollExitPhase.STATUS_OBSERVED,
        state=ExecutionState.PARTIALLY_FILLED,
        filled=Decimal("0.4"),
        revision=4,
    )
    with pytest.raises(RiskGroupRollExitError, match="GROUP_ROLL_EXIT_STALE_OBSERVATION"):
        reopened.observe(stale)
    unchanged = reopened.get(authorization.action_id)
    assert unchanged is not None
    assert unchanged["phase"] == GroupRollExitPhase.STATUS_OBSERVED.value
    assert unchanged["observation_json"] == observed["observation_json"]

