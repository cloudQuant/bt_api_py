from __future__ import annotations

from pathlib import Path

import pytest

from bt_api_py.config_loader import load_all_exchange_configs, load_exchange_config
from bt_api_py.exceptions import ConfigurationError


def test_load_exchange_config_rejects_non_mapping_yaml(tmp_path: Path):
    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("- item\n- item2\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="mapping object"):
        load_exchange_config(str(config_path))


def test_load_all_exchange_configs_skips_invalid_non_mapping_yaml_and_keeps_valid(tmp_path: Path):
    valid_path = tmp_path / "b_valid.yaml"
    valid_path.write_text(
        "id: valid_exchange\n"
        "display_name: Valid Exchange\n"
        "venue_type: broker\n"
        "connection:\n"
        "  type: http\n",
        encoding="utf-8",
    )
    invalid_path = tmp_path / "a_invalid.yaml"
    invalid_path.write_text("- invalid\n", encoding="utf-8")

    configs = load_all_exchange_configs(str(tmp_path))

    assert list(configs) == ["valid_exchange"]
    assert configs["valid_exchange"].display_name == "Valid Exchange"


def test_load_all_exchange_configs_uses_deterministic_filename_order(tmp_path: Path):
    for name, exchange_id in [("b_second.yaml", "second"), ("a_first.yaml", "first")]:
        (tmp_path / name).write_text(
            f"id: {exchange_id}\n"
            f"display_name: {exchange_id.title()}\n"
            "venue_type: broker\n"
            "connection:\n"
            "  type: http\n",
            encoding="utf-8",
        )

    configs = load_all_exchange_configs(str(tmp_path))

    assert list(configs.keys()) == ["first", "second"]
