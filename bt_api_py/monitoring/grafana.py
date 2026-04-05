"""
Grafana dashboard configuration for bt_api_py monitoring.

Provides pre-built dashboards for monitoring trading API performance.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class GrafanaDashboardBuilder:
    """Builds Grafana dashboards for bt_api_py monitoring."""

    def __init__(self, title: str = "BT API Py Dashboard") -> None:
        self.dashboard: dict[str, Any] = {
            "dashboard": {
                "id": None,
                "title": title,
                "tags": ["bt_api_py", "trading", "monitoring"],
                "timezone": "browser",
                "panels": [],
                "time": {"from": "now-1h", "to": "now"},
                "refresh": "5s",
                "schemaVersion": 27,
                "version": 1,
                "templating": {"list": []},
                "annotations": {"list": []},
            }
        }

    def add_panel(self, panel: dict[str, Any]) -> GrafanaDashboardBuilder:
        """Add a panel to the dashboard."""
        d: dict[str, Any] = self.dashboard["dashboard"]
        panels: list[Any] = d["panels"]
        # Auto-generate ID and grid position
        panel_id = len(panels) + 1
        panel["id"] = panel_id

        # Calculate grid position
        existing_panels = len(panels)
        row = existing_panels // 2  # 2 panels per row
        col = existing_panels % 2

        panel["gridPos"] = {
            "h": 8,  # Height
            "w": 12,  # Width (half of 24)
            "x": col * 12,
            "y": row * 8,
        }

        panels.append(panel)
        return self

    def add_system_metrics_row(self) -> GrafanaDashboardBuilder:
        """Add system metrics row."""
        # CPU Usage
        self.add_panel(
            {
                "title": "CPU Usage (%)",
                "type": "stat",
                "targets": [
                    {
                        "expr": "btapi_system_cpu_usage_percent",
                        "legendFormat": "{{instance}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "percent",
                        "min": 0,
                        "max": 100,
                        "thresholds": {
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 70},
                                {"color": "red", "value": 90},
                            ]
                        },
                    }
                },
            }
        )

        # Memory Usage
        self.add_panel(
            {
                "title": "Memory Usage (%)",
                "type": "stat",
                "targets": [
                    {
                        "expr": "btapi_system_memory_usage_percent",
                        "legendFormat": "{{instance}}",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "percent",
                        "min": 0,
                        "max": 100,
                        "thresholds": {
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 80},
                                {"color": "red", "value": 95},
                            ]
                        },
                    }
                },
            }
        )

        return self

    def add_trading_metrics_row(self) -> GrafanaDashboardBuilder:
        """Add trading metrics row."""
        # Order Rate
        self.add_panel(
            {
                "title": "Orders per Second",
                "type": "stat",
                "targets": [
                    {
                        "expr": "rate(btapi_orders_total[5m])",
                        "legendFormat": "Orders/sec",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "reqps",
                        "thresholds": {
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 50},
                                {"color": "red", "value": 100},
                            ]
                        },
                    }
                },
            }
        )

        # Order Success Rate
        self.add_panel(
            {
                "title": "Order Success Rate (%)",
                "type": "stat",
                "targets": [
                    {
                        "expr": "btapi_orders_success_total / btapi_orders_total * 100",
                        "legendFormat": "Success Rate",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "percent",
                        "min": 0,
                        "max": 100,
                        "thresholds": {
                            "steps": [
                                {"color": "red", "value": None},
                                {"color": "yellow", "value": 95},
                                {"color": "green", "value": 99},
                            ]
                        },
                    }
                },
            }
        )

        return self

    def add_latency_metrics_row(self) -> GrafanaDashboardBuilder:
        """Add latency metrics row."""
        # Order Latency
        self.add_panel(
            {
                "title": "Order Latency",
                "type": "graph",
                "targets": [
                    {
                        "expr": "histogram_quantile(0.50, btapi_order_latency_seconds_bucket)",
                        "legendFormat": "50th percentile",
                        "refId": "A",
                    },
                    {
                        "expr": "histogram_quantile(0.95, btapi_order_latency_seconds_bucket)",
                        "legendFormat": "95th percentile",
                        "refId": "B",
                    },
                    {
                        "expr": "histogram_quantile(0.99, btapi_order_latency_seconds_bucket)",
                        "legendFormat": "99th percentile",
                        "refId": "C",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "min": 0,
                    }
                },
                "yAxes": [
                    {"min": 0, "max": None, "format": "short"},
                    {"min": 0, "max": None, "format": "short"},
                ],
            }
        )

        # API Latency
        self.add_panel(
            {
                "title": "API Request Latency",
                "type": "graph",
                "targets": [
                    {
                        "expr": "histogram_quantile(0.50, btapi_api_latency_seconds_bucket)",
                        "legendFormat": "50th percentile",
                        "refId": "A",
                    },
                    {
                        "expr": "histogram_quantile(0.95, btapi_api_latency_seconds_bucket)",
                        "legendFormat": "95th percentile",
                        "refId": "B",
                    },
                    {
                        "expr": "histogram_quantile(0.99, btapi_api_latency_seconds_bucket)",
                        "legendFormat": "99th percentile",
                        "refId": "C",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "min": 0,
                    }
                },
            }
        )

        return self

    def add_exchange_health_row(self) -> GrafanaDashboardBuilder:
        """Add exchange health metrics row."""
        # Exchange Health Status
        self.add_panel(
            {
                "title": "Exchange Health Status",
                "type": "stat",
                "targets": [
                    {
                        "expr": "exchange_binance_health_status",
                        "legendFormat": "Binance",
                        "refId": "A",
                    },
                    {
                        "expr": "exchange_okx_health_status",
                        "legendFormat": "OKX",
                        "refId": "B",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "mappings": [
                            {
                                "options": {"0": {"text": "Unknown", "color": "gray"}},
                                "type": "value",
                            },
                            {
                                "options": {"1": {"text": "Unhealthy", "color": "red"}},
                                "type": "value",
                            },
                            {
                                "options": {"2": {"text": "Degraded", "color": "yellow"}},
                                "type": "value",
                            },
                            {
                                "options": {"3": {"text": "Healthy", "color": "green"}},
                                "type": "value",
                            },
                        ],
                    }
                },
            }
        )

        # Health Check Response Times
        self.add_panel(
            {
                "title": "Health Check Response Time",
                "type": "graph",
                "targets": [
                    {
                        "expr": "histogram_quantile(0.50, exchange_binance_health_check_duration_seconds_bucket)",
                        "legendFormat": "Binance 50th",
                        "refId": "A",
                    },
                    {
                        "expr": "histogram_quantile(0.95, exchange_binance_health_check_duration_seconds_bucket)",
                        "legendFormat": "Binance 95th",
                        "refId": "B",
                    },
                    {
                        "expr": "histogram_quantile(0.50, exchange_okx_health_check_duration_seconds_bucket)",
                        "legendFormat": "OKX 50th",
                        "refId": "C",
                    },
                    {
                        "expr": "histogram_quantile(0.95, exchange_okx_health_check_duration_seconds_bucket)",
                        "legendFormat": "OKX 95th",
                        "refId": "D",
                    },
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "s",
                        "min": 0,
                    }
                },
            }
        )

        return self

    def add_connection_metrics_row(self) -> GrafanaDashboardBuilder:
        """Add connection metrics row."""
        # Active Connections
        self.add_panel(
            {
                "title": "Active Connections",
                "type": "stat",
                "targets": [
                    {
                        "expr": "btapi_active_connections",
                        "legendFormat": "Connections",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "short",
                        "min": 0,
                        "thresholds": {
                            "steps": [
                                {"color": "green", "value": None},
                                {"color": "yellow", "value": 80},
                                {"color": "red", "value": 95},
                            ]
                        },
                    }
                },
            }
        )

        # Connection Errors
        self.add_panel(
            {
                "title": "Connection Error Rate",
                "type": "graph",
                "targets": [
                    {
                        "expr": "rate(btapi_connection_errors_total[5m])",
                        "legendFormat": "Errors/sec",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "reqps",
                        "min": 0,
                    }
                },
            }
        )

        return self

    def add_network_metrics_row(self) -> GrafanaDashboardBuilder:
        """Add network metrics row."""
        # Network Traffic
        self.add_panel(
            {
                "title": "Network Bytes Sent",
                "type": "graph",
                "targets": [
                    {
                        "expr": "rate(btapi_network_bytes_sent_total[5m])",
                        "legendFormat": "Bytes/sec",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "Bps",
                        "min": 0,
                    }
                },
            }
        )

        # Network Traffic Received
        self.add_panel(
            {
                "title": "Network Bytes Received",
                "type": "graph",
                "targets": [
                    {
                        "expr": "rate(btapi_network_bytes_recv_total[5m])",
                        "legendFormat": "Bytes/sec",
                        "refId": "A",
                    }
                ],
                "fieldConfig": {
                    "defaults": {
                        "unit": "Bps",
                        "min": 0,
                    }
                },
            }
        )

        return self

    def build(self) -> dict[str, Any]:
        """Build the complete dashboard."""
        return self.dashboard

    def to_json(self) -> str:
        """Convert dashboard to JSON string."""
        return json.dumps(self.dashboard, indent=2)


def create_trading_dashboard() -> dict[str, Any]:
    """Create a comprehensive trading dashboard."""
    return (
        GrafanaDashboardBuilder("BT API Py Trading Dashboard")
        .add_system_metrics_row()
        .add_trading_metrics_row()
        .add_latency_metrics_row()
        .add_exchange_health_row()
        .add_connection_metrics_row()
        .add_network_metrics_row()
        .build()
    )


def create_system_dashboard() -> dict[str, Any]:
    """Create a system-focused dashboard."""
    builder = GrafanaDashboardBuilder("BT API Py System Dashboard")

    # CPU metrics
    builder.add_panel(
        {
            "title": "CPU Usage",
            "type": "graph",
            "targets": [
                {
                    "expr": "btapi_system_cpu_usage_percent",
                    "legendFormat": "CPU Usage %",
                    "refId": "A",
                }
            ],
            "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100}},
        }
    )

    # Memory metrics
    builder.add_panel(
        {
            "title": "Memory Usage",
            "type": "graph",
            "targets": [
                {
                    "expr": "btapi_system_memory_usage_percent",
                    "legendFormat": "Memory Usage %",
                    "refId": "A",
                }
            ],
            "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100}},
        }
    )

    # Process metrics
    builder.add_panel(
        {
            "title": "Process Memory",
            "type": "graph",
            "targets": [
                {
                    "expr": "btapi_process_memory_usage_bytes / 1024 / 1024",
                    "legendFormat": "Memory (MB)",
                    "refId": "A",
                }
            ],
            "fieldConfig": {"defaults": {"unit": "MB", "min": 0}},
        }
    )

    # Thread count
    builder.add_panel(
        {
            "title": "Thread Count",
            "type": "graph",
            "targets": [
                {
                    "expr": "btapi_process_threads_count",
                    "legendFormat": "Threads",
                    "refId": "A",
                }
            ],
            "fieldConfig": {"defaults": {"unit": "short", "min": 0}},
        }
    )

    return builder.build()


def create_exchange_dashboard(exchange_name: str) -> dict[str, Any]:
    """Create exchange-specific dashboard."""
    builder = GrafanaDashboardBuilder(f"{exchange_name} Exchange Dashboard")

    # Exchange health
    builder.add_panel(
        {
            "title": "Exchange Health Status",
            "type": "stat",
            "targets": [
                {
                    "expr": f"exchange_{exchange_name.lower()}_health_status",
                    "legendFormat": exchange_name,
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "mappings": [
                        {"options": {"0": {"text": "Unknown", "color": "gray"}}, "type": "value"},
                        {"options": {"1": {"text": "Unhealthy", "color": "red"}}, "type": "value"},
                        {
                            "options": {"2": {"text": "Degraded", "color": "yellow"}},
                            "type": "value",
                        },
                        {"options": {"3": {"text": "Healthy", "color": "green"}}, "type": "value"},
                    ],
                }
            },
        }
    )

    # API latency
    builder.add_panel(
        {
            "title": "API Latency",
            "type": "graph",
            "targets": [
                {
                    "expr": f"histogram_quantile(0.95, {exchange_name.lower()}_api_latency_seconds_bucket)",
                    "legendFormat": "95th percentile",
                    "refId": "A",
                }
            ],
            "fieldConfig": {"defaults": {"unit": "s", "min": 0}},
        }
    )

    # Order metrics
    builder.add_panel(
        {
            "title": "Order Success Rate",
            "type": "stat",
            "targets": [
                {
                    "expr": f"{exchange_name.lower()}_orders_success_total / {exchange_name.lower()}_orders_total * 100",
                    "legendFormat": "Success Rate",
                    "refId": "A",
                }
            ],
            "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100}},
        }
    )

    # Connection errors
    builder.add_panel(
        {
            "title": "Connection Errors",
            "type": "graph",
            "targets": [
                {
                    "expr": f"rate({exchange_name.lower()}_connection_errors_total[5m])",
                    "legendFormat": "Errors/sec",
                    "refId": "A",
                }
            ],
            "fieldConfig": {"defaults": {"unit": "reqps", "min": 0}},
        }
    )

    return builder.build()


def save_dashboard_to_file(dashboard: dict[str, Any], filename: str) -> None:
    """Save dashboard to file."""
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(dashboard, f, indent=2)


def get_all_dashboard_configs() -> dict[str, dict[str, Any]]:
    """Get all dashboard configurations."""
    return {
        "trading": create_trading_dashboard(),
        "system": create_system_dashboard(),
        "binance": create_exchange_dashboard("binance"),
        "okx": create_exchange_dashboard("okx"),
    }
