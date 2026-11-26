"""Synthetic-only, no provider session or network."""
import json
import os
import stat

import pytest

import bt_api_py.simnow_acceptance_bootstrap as bootstrap
from tests.integration.test_simnow_acceptance_bundle import sample


def test_new_private_files_preserve_real_journal_and_issuer(tmp_path):
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer

    root = tmp_path.resolve() / "private"
    root.mkdir(mode=0o700)
    result = sample(root)
    bundle = json.loads(result.gateway_resources.read_text())
    settings, observation = bundle["settings"], bundle["account_observation"]
    journal = GatewayDirectCommandJournal(settings["journal_path"], settings["account_scope"], generation="synthetic",
        expected_journal_id=settings["expected_journal_id"], minimum_revision=0,
        controls_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True)
    assert journal.journal_id == settings["expected_journal_id"]
    issuer = DurableOrderCorrelationIssuer.open_existing(observation["observation_identity_path"], expected_issuer_id=observation["expected_observation_issuer_id"])
    issuer.close()
    for path in root.rglob("*"):
        assert not path.is_symlink()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600)
    assert "password-synthetic" not in repr(result)
    assert "user-synthetic" not in repr(result)
    with pytest.raises(bootstrap.SimnowBootstrapError, match="^SIMNOW_BOOTSTRAP_FAILED$"):
        sample(root)


def test_risk_control_bundle_grants_identity_binding_read():
    assert set(bootstrap._RISK_CONTROL_OPERATIONS) == {
        "direct.control.identity",
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }


@pytest.mark.skipif(os.name != "posix", reason="bootstrap requires POSIX directory handles")
def test_generated_risk_control_resource_uses_exact_binding_operations(tmp_path):
    root = tmp_path.resolve() / "private"
    root.mkdir(mode=0o700)
    result = sample(root)
    bundle = json.loads(result.gateway_resources.read_text())
    risk_operators = [
        operator
        for operator in bundle["controls"]["operators"]
        if operator["identity"]["actor_ref"] == "risk-control-actor"
    ]
    assert len(risk_operators) == 1
    assert len(risk_operators[0]["resources"]) == 1
    operations = risk_operators[0]["resources"][0]["operations"]
    assert len(operations) == len(set(operations))
    assert set(operations) == set(bootstrap._RISK_CONTROL_OPERATIONS)


def test_existing_file_and_symlink_directory_rejected(tmp_path):
    root = tmp_path.resolve() / "private"
    root.mkdir(mode=0o700)
    original = root / "existing"
    original.write_text("unchanged")
    original.chmod(0o600)
    with pytest.raises(bootstrap.SimnowBootstrapError, match="^SIMNOW_BOOTSTRAP_FAILED$"):
        sample(root)
    assert original.read_text() == "unchanged"
    assert len(list(root.iterdir())) == 1
    root2 = tmp_path.resolve() / "empty"
    root2.mkdir(mode=0o700)
    link = tmp_path.resolve() / "link"
    link.symlink_to(root2)
    with pytest.raises(bootstrap.SimnowBootstrapError, match="^SIMNOW_BOOTSTRAP_FAILED$"):
        sample(link)
    assert not list(root2.iterdir())


def test_failure_keeps_marker_and_created_state_no_sensitive_error(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "private"
    root.mkdir(mode=0o700)
    original = bootstrap._write
    def failing(path, value):
        if path.name == "gateway-resources.json":
            raise RuntimeError("password-synthetic user-synthetic")
        return original(path, value)
    monkeypatch.setattr(bootstrap, "_write", failing)
    with pytest.raises(bootstrap.SimnowBootstrapError) as failure:
        sample(root)
    assert str(failure.value) == "SIMNOW_BOOTSTRAP_FAILED"
    assert failure.value.__cause__ is None
    assert (root / "BOOTSTRAP_STARTED").is_file()
    assert (root / "state" / "gateway.sqlite").is_file()
    assert (root / "state" / "issuer.sqlite").is_file()
    assert not (root / "BOOTSTRAP_COMPLETE").exists()
    with pytest.raises(bootstrap.SimnowBootstrapError):
        sample(root)


def test_rejects_directory_inside_git_worktree(tmp_path):
    worktree = tmp_path.resolve() / "repo"
    worktree.mkdir(mode=0o700)
    (worktree / ".git").mkdir(mode=0o700)
    root = worktree / "private"
    root.mkdir(mode=0o700)
    with pytest.raises(bootstrap.SimnowBootstrapError, match="^SIMNOW_BOOTSTRAP_FAILED$"):
        sample(root)
    assert not list(root.iterdir())


def test_inconsistent_instrument_coverage_creates_no_files(tmp_path):
    root = tmp_path.resolve() / "private"
    root.mkdir(mode=0o700)
    with pytest.raises(bootstrap.SimnowBootstrapError, match="^SIMNOW_BOOTSTRAP_FAILED$"):
        sample(root, margin_pool="main")
    assert not list(root.iterdir())
