"""Tests for monitoring/grafana.py."""

import pytest
import json

from bt_api_py.monitoring import grafana


class TestGrafana:
    """Tests for Grafana integration."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import grafana
        assert grafana is not None


class TestGrafanaDashboardBuilder:
    """Tests for Grafana integration."""

    def test_add_panel_assigns_ids_and_grid_positions(self):
        builder = grafana.GrafanaDashboardBuilder("Custom Dashboard")

        builder.add_panel({"title": "Panel A", "type": "stat", "targets": []})
        builder.add_panel({"title": "Panel B", "type": "graph", "targets": []})
        builder.add_panel({"title": "Panel C", "type": "graph", "targets": []})

        panels = builder.build()["dashboard"]["panels"]

        assert builder.build()["dashboard"]["title"] == "Custom Dashboard"
        assert [panel["id"] for panel in panels] == [1, 2, 3]
        assert panels[0]["gridPos"] == {"h": 8, "w": 12, "x": 0, "y": 0}
        assert panels[1]["gridPos"] == {"h": 8, "w": 12, "x": 12, "y": 0}
        assert panels[2]["gridPos"] == {"h": 8, "w": 12, "x": 0, "y": 8}

    def test_row_helpers_and_to_json(self):
        builder = grafana.GrafanaDashboardBuilder()
        builder.add_system_metrics_row().add_trading_metrics_row().add_latency_metrics_row()

        dashboard = builder.build()["dashboard"]
        panel_titles = [panel["title"] for panel in dashboard["panels"]]
        payload = json.loads(builder.to_json())

        assert len(dashboard["panels"]) == 6
        assert panel_titles[:2] == ["CPU Usage (%)", "Memory Usage (%)"]
        assert payload["dashboard"]["title"] == "BT API Py Dashboard"


class TestGrafanaFactories:
    def test_create_trading_dashboard_contains_all_rows(self):
        dashboard = grafana.create_trading_dashboard()["dashboard"]

        assert dashboard["title"] == "BT API Py Trading Dashboard"
        assert len(dashboard["panels"]) == 12
        assert dashboard["panels"][0]["title"] == "CPU Usage (%)"
        assert dashboard["panels"][-1]["title"] == "Network Bytes Received"

    def test_create_system_dashboard_and_exchange_dashboard(self):
        system_dashboard = grafana.create_system_dashboard()["dashboard"]
        exchange_dashboard = grafana.create_exchange_dashboard("BINANCE")["dashboard"]

        assert len(system_dashboard["panels"]) == 4
        assert system_dashboard["panels"][2]["title"] == "Process Memory"
        assert exchange_dashboard["title"] == "BINANCE Exchange Dashboard"
        assert len(exchange_dashboard["panels"]) == 4
        assert exchange_dashboard["panels"][0]["targets"][0]["expr"] == "exchange_binance_health_status"
        assert "binance_orders_success_total" in exchange_dashboard["panels"][2]["targets"][0]["expr"]

    def test_save_dashboard_to_file_and_get_all_configs(self, tmp_path):
        dashboard = grafana.create_system_dashboard()
        output = tmp_path / "nested" / "dashboard.json"

        grafana.save_dashboard_to_file(dashboard, str(output))
        all_configs = grafana.get_all_dashboard_configs()

        assert output.exists()
        assert json.loads(output.read_text(encoding="utf-8"))["dashboard"]["title"] == "BT API Py System Dashboard"
        assert set(all_configs.keys()) == {"trading", "system", "binance", "okx"}
        assert all_configs["okx"]["dashboard"]["title"] == "okx Exchange Dashboard"
