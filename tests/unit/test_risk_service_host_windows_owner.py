"""Windows parity for the Risk pretrade storage owner gate (synthetic files only).

Skipped off Windows. ``_pretrade_existing`` on POSIX checks st_uid == geteuid;
on Windows the parity check is the file owner == process token user. The
schema/source probes are stubbed so these tests isolate the owner gate.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from bt_api_py import _win32_security as w
from bt_api_py import risk_service_host as host
from bt_api_risk.pretrade import ledger as ledger_module
from bt_api_risk.pretrade.ledger import PretradeLedger

pytestmark = pytest.mark.skipif(not w.WINDOWS, reason="Windows-only security gates")


def _setup(tmp_path, monkeypatch):
    db = tmp_path / 'risk.sqlite'
    db.write_bytes(b'')  # empty file; the schema probe is stubbed below
    key = 'scope-key'
    digest = hashlib.sha256(key.encode()).hexdigest()
    lock = db.with_name(db.name + '.' + digest + '.writer.lock')
    lock.write_bytes(b'')
    monkeypatch.setattr(PretradeLedger, '_check_schema', lambda *a, **k: None)
    monkeypatch.setattr(ledger_module, '_source_unverified', lambda connection: True)
    return SimpleNamespace(risk_database=db), SimpleNamespace(key=key)


def test_owner_gate_accepts_own_files(tmp_path, monkeypatch):
    config, scope = _setup(tmp_path, monkeypatch)
    identities = host._pretrade_existing(config, scope, version=4)
    assert len(identities) == 2 and identities[0] != identities[1]


def test_owner_gate_refuses_foreign_owner(tmp_path, monkeypatch):
    config, scope = _setup(tmp_path, monkeypatch)
    real = w.inspect_owner

    def foreign(path, **kwargs):
        info = real(path, **kwargs)
        return w.OwnerInfo(info.owner_sid, False, info.others_writable)

    monkeypatch.setattr(w, 'inspect_owner', foreign)
    with pytest.raises(host.RiskServiceStartError, match='RISK_SERVICE_STORAGE_SCHEMA_INVALID'):
        host._pretrade_existing(config, scope, version=4)


def test_owner_gate_refuses_non_regular_file(tmp_path, monkeypatch):
    config, scope = _setup(tmp_path, monkeypatch)
    lock = config.risk_database.with_name(
        config.risk_database.name + '.' + hashlib.sha256(scope.key.encode()).hexdigest()
        + '.writer.lock')
    lock.unlink()
    lock.mkdir()
    with pytest.raises(host.RiskServiceStartError, match='RISK_SERVICE_STORAGE_SCHEMA_INVALID'):
        host._pretrade_existing(config, scope, version=4)
