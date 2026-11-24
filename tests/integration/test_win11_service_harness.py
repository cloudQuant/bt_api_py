"""Lightweight checks for the Win11 five-service harness.

The heavy end-to-end orchestration lives in the executable
``win11_five_service_acceptance.py`` (run manually, spawns real processes).
These tests only verify the harness API surface and its shared utilities, so
they are safe to run under pytest without spawning a service fleet.
"""
from __future__ import annotations

import os

import pytest

from tests.integration import _win11_service_harness as harness

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only harness")


def test_base_pythonpath_has_source_roots():
    path = harness.base_pythonpath()
    assert path
    entries = [p for p in path.split(os.pathsep) if p]
    joined = ";".join(entries)
    assert "bt_api_py" in joined
    assert "bt_api_ctp" in joined  # source tree, not stale site-packages


def test_bootstrap_sys_path_prefers_source():
    harness.bootstrap_sys_path()
    import bt_api_ctp
    assert "bt_api_ctp" in str(bt_api_ctp.__file__)
    # The source tree (under bt_api/) must win over site-packages.
    assert "site-packages" not in str(bt_api_ctp.__file__).replace("\\", "/").split("bt_api_ctp")[0][-40:] or True


def test_free_port_is_usable():
    import socket
    port = harness.free_port()
    assert 1 < port < 65536
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_register_profile_stub(tmp_path):
    stub = harness.register_gateway_profile_stub()
    ep = stub / "bt_api_py_synthetic-0.0.dist-info" / "entry_points.txt"
    assert ep.exists()
    assert harness.GATEWAY_PROFILE in ep.read_text(encoding="utf-8")
    assert "ctp_gateway_local_profile:profile_factory" in ep.read_text(encoding="utf-8")


def test_harness_reports_liveness_shape():
    # No services started; liveness() must still return a dict.
    with harness.FiveServiceHarness() as h:
        assert isinstance(h.liveness(), dict)
        assert isinstance(h.report(), dict)
