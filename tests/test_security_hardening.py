"""TLS 证书验证后门删除 + Prometheus 默认回环绑定测试（E-04/E-05）。"""

from __future__ import annotations

import inspect

import pytest


def test_certificate_validation_none_rejected() -> None:
    """certificate_validation='none' 必须无法构造 CERT_NONE 配置。"""
    from bt_api_py.security_compliance.network.tls_manager import TLSManager

    manager = TLSManager({"certificate_validation": "none", "cipher_suites": []})
    with pytest.raises(ValueError, match="cannot be disabled"):
        manager.get_ssl_context()


def test_prometheus_exporter_default_host_is_loopback() -> None:
    """PrometheusExporter 默认 bind 必须是 127.0.0.1。"""
    from bt_api_py.monitoring.prometheus import PrometheusExporter, start_prometheus_exporter

    assert inspect.signature(PrometheusExporter.__init__).parameters["host"].default == "127.0.0.1"
    assert inspect.signature(start_prometheus_exporter).parameters["host"].default == "127.0.0.1"


def test_prometheus_public_bind_emits_warning(monkeypatch) -> None:
    """公网暴露（host=0.0.0.0）必须打印安全告警日志。"""
    from bt_api_py.monitoring import prometheus as pm

    warnings: list[str] = []
    monkeypatch.setattr(pm._logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    pm.PrometheusExporter(host="0.0.0.0")

    assert warnings  # 有安全告警
