"""Iteration 67 (D67-01 / §3.3): cross-platform ``~user`` home resolution.

Regression for the Windows-specific defect where ``ntpath.expanduser``
mechanically rewrites an unknown ``~user`` into a plausible path, so a check
based only on ``expanded.startswith("~")`` silently accepted it.  The parser
must give the same finite ``calendar_root_invalid_home`` refusal on Windows,
Linux and macOS, while still accepting a resolvable named user.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bt_api_gateway import calendar_config as cc
from bt_api_gateway.calendar_config import (
    GatewayCalendarConfigCandidate,
    GatewayCalendarConfigError,
    parse_gateway_calendar_config,
)


def _parse(root, config_file):
    return parse_gateway_calendar_config(
        {"calendars": {"root": root}}, config_file=config_file
    )


def test_unknown_user_is_refused_when_expanduser_fabricates_a_path(tmp_path, monkeypatch):
    """Simulate ntpath: an unknown ``~user`` becomes a non-existent abs path."""
    real_expanduser = os.path.expanduser
    fabricated = str(tmp_path / "no_such_home_dir")  # deliberately NOT created

    def fake_expanduser(path):
        if path == "~ghost_iter67":
            return fabricated
        return real_expanduser(path)

    monkeypatch.setattr(cc.os.path, "expanduser", fake_expanduser)
    with pytest.raises(GatewayCalendarConfigError) as caught:
        _parse("~ghost_iter67/calendars", tmp_path / "config.yaml")
    assert caught.value.code == "calendar_root_invalid_home"


def test_resolvable_named_user_home_is_accepted(tmp_path, monkeypatch):
    """A named user whose home actually exists must resolve like bare ``~``."""
    home_dir = tmp_path / "named_user_home"
    home_dir.mkdir()
    real_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path == "~named_iter67":
            return str(home_dir)
        return real_expanduser(path)

    monkeypatch.setattr(cc.os.path, "expanduser", fake_expanduser)
    candidate = _parse("~named_iter67/calendars", tmp_path / "config.yaml")
    assert type(candidate) is GatewayCalendarConfigCandidate
    assert candidate.status == "CONFIGURED"
    assert candidate.root_kind == "directory"
    assert candidate.root_path == Path(os.path.normpath(home_dir / "calendars"))


def test_bare_home_still_expands_to_current_user(tmp_path):
    candidate = _parse("~/calendars", tmp_path / "config.yaml")
    assert candidate.root_path == Path(os.path.normpath(Path(os.path.expanduser("~/calendars"))))
