"""Windows parity for Runtime operator security gates (synthetic files only).

Skipped off Windows. These exercise the same fail-closed owner/ACE gates as
the POSIX mode-0600 check using an explicit owner-only DACL, the Windows
equivalent of ``chmod 0600``. No real secrets, accounts or paths are read.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bt_api_py import _win32_security as w
from bt_api_py import runtime_operator_config as cfg
from tests.unit.test_runtime_operator_config import TOKEN_SCHEMA, _deployment

pytestmark = pytest.mark.skipif(not w.WINDOWS, reason="Windows-only security gates")


def _owner_only_dacl(path, *, extra_sid=None):
    """Replace the DACL with owner + root-equivalent ACEs only."""
    import win32security

    sids = [w.current_user_sid(), 'S-1-5-18', 'S-1-5-32-544']
    if extra_sid is not None:
        sids.append(extra_sid)
    dacl = win32security.ACL()
    for sid in sids:
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, 0x1F01FF, win32security.ConvertStringSidToSid(sid))
    sd = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
    sd.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION, sd)


def _token(tmp_path, *, secure=True):
    path, envelope = _deployment(tmp_path)
    secret = Path(envelope['subject_token_file'])
    token = 'private-token-' + 'x' * 32
    secret.write_text(json.dumps({'schema': TOKEN_SCHEMA, 'tokens': [
        {'token': token, 'operator': 'alice'}]}))
    if secure:
        _owner_only_dacl(secret)
    return path, secret, token


def test_owner_only_token_loads_on_windows(tmp_path):
    assert w.has_privileges_available() is True
    path, _secret, token = _token(tmp_path)
    config = cfg.load_config(path)
    auth = cfg.load_authenticator(config)
    assert auth.authenticate(token).identity.name == 'alice'
    assert not auth.authenticate('wrong-token').authenticated
    assert token not in repr(auth) and token not in repr(config)


def test_other_principal_ace_is_refused_on_windows(tmp_path):
    path, secret, token = _token(tmp_path, secure=False)
    _owner_only_dacl(secret, extra_sid='S-1-1-0')  # Everyone
    config = cfg.load_config(path)
    with pytest.raises(cfg.RuntimeOperatorError, match='SUBJECT_TOKENS_INVALID'):
        cfg.load_authenticator(config)


def test_null_dacl_is_refused_on_windows(tmp_path):
    import win32security

    path, secret, token = _token(tmp_path)
    sd = win32security.GetFileSecurity(str(secret), win32security.DACL_SECURITY_INFORMATION)
    sd.SetSecurityDescriptorDacl(1, None, 0)
    win32security.SetFileSecurity(str(secret), win32security.DACL_SECURITY_INFORMATION, sd)
    config = cfg.load_config(path)
    with pytest.raises(cfg.RuntimeOperatorError, match='SUBJECT_TOKENS_INVALID'):
        cfg.load_authenticator(config)


def test_reparse_point_token_is_refused_on_windows(tmp_path):
    path, secret, token = _token(tmp_path)
    real = secret.with_suffix('.real')
    secret.rename(real)
    try:
        secret.symlink_to(real)
    except OSError:
        pytest.skip('symlink creation requires privilege on this host')
    config = cfg.load_config(path)
    with pytest.raises(cfg.RuntimeOperatorError, match='SUBJECT_TOKENS_INVALID'):
        cfg.load_authenticator(config)
