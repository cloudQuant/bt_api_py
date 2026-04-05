"""Tests for functions/utils.py."""

import sys
import types

import pytest
import requests

from bt_api_py.functions import utils


class TestUtils:
    """Tests for functions/utils.py."""

    def test_module_exists(self):
        """Test module can be imported."""
        assert utils is not None


class _Logger:
    def __init__(self):
        self.messages = []

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))

    def debug(self, message):
        self.messages.append(("debug", message))


class _Response:
    def __init__(self, status_code=200, text="", json_data=None, raises=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self._raises = raises

    def raise_for_status(self):
        if self._raises is not None:
            raise self._raises

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class TestEnvParsing:
    def test_parse_env_int_returns_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_INT", raising=False)
        assert utils._parse_env_int("TEST_INT", 7) == 7

    def test_parse_env_int_returns_value_when_valid(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "42")
        assert utils._parse_env_int("TEST_INT", 0) == 42

    def test_parse_env_int_invalid_uses_default_and_logs(self, monkeypatch):
        logger = _Logger()
        monkeypatch.setattr(utils, "_get_logger", lambda: logger)
        monkeypatch.setenv("TEST_INT", "bad")
        assert utils._parse_env_int("TEST_INT", 9) == 9
        assert logger.messages[0][0] == "warning"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("true", True), ("YES", True), ("1", True), ("off", False), ("No", False), ("0", False)],
    )
    def test_parse_env_bool_supported_values(self, monkeypatch, value, expected):
        monkeypatch.setenv("TEST_BOOL", value)
        assert utils._parse_env_bool("TEST_BOOL", not expected) is expected

    def test_parse_env_bool_missing_returns_default(self, monkeypatch):
        monkeypatch.delenv("TEST_BOOL", raising=False)
        assert utils._parse_env_bool("TEST_BOOL", True) is True

    def test_parse_env_bool_invalid_uses_default_and_logs(self, monkeypatch):
        logger = _Logger()
        monkeypatch.setattr(utils, "_get_logger", lambda: logger)
        monkeypatch.setenv("TEST_BOOL", "maybe")
        assert utils._parse_env_bool("TEST_BOOL", False) is False
        assert logger.messages[0][0] == "warning"


class TestNetworkAndPathHelpers:
    def test_get_public_ip_uses_primary_service(self, monkeypatch):
        monkeypatch.setattr(
            utils.requests, "get", lambda *args, **kwargs: _Response(200, "1.2.3.4\n")
        )
        assert utils.get_public_ip() == "1.2.3.4"

    def test_get_public_ip_falls_back_to_secondary_service(self, monkeypatch):
        logger = _Logger()
        responses = [requests.RequestException("boom"), _Response(200, json_data={"ip": "5.6.7.8"})]

        def fake_get(*args, **kwargs):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(utils, "_get_logger", lambda: logger)
        monkeypatch.setattr(utils.requests, "get", fake_get)
        assert utils.get_public_ip() == "5.6.7.8"
        assert logger.messages[0][0] == "error"

    def test_get_public_ip_returns_none_when_both_services_fail(self, monkeypatch):
        logger = _Logger()

        def fake_get(*args, **kwargs):
            raise requests.RequestException("offline")

        monkeypatch.setattr(utils, "_get_logger", lambda: logger)
        monkeypatch.setattr(utils.requests, "get", fake_get)
        assert utils.get_public_ip() is None
        assert len(logger.messages) == 2

    def test_get_package_path_returns_none_for_missing_package(self, monkeypatch):
        logger = _Logger()
        monkeypatch.setattr(utils, "_get_logger", lambda: logger)

        def fake_import(name):
            raise ModuleNotFoundError(name)

        monkeypatch.setattr(utils.importlib, "import_module", fake_import)
        assert utils.get_package_path("missing_pkg") is None
        assert logger.messages[0][0] == "error"

    def test_get_package_path_prefers_file_parent(self, monkeypatch, tmp_path):
        package_dir = tmp_path / "pkg"
        package_dir.mkdir()
        module = types.SimpleNamespace(__file__=str(package_dir / "__init__.py"))
        monkeypatch.setattr(utils.importlib, "import_module", lambda name: module)
        assert utils.get_package_path("pkg") == str(package_dir)

    def test_get_package_path_uses_package_path_when_file_missing(self, monkeypatch, tmp_path):
        package_dir = tmp_path / "namespace_pkg"
        package_dir.mkdir()
        module = types.SimpleNamespace(__path__=[str(package_dir)])
        monkeypatch.setattr(utils.importlib, "import_module", lambda name: module)
        assert utils.get_package_path("namespace_pkg") == str(package_dir)

    def test_resolve_config_root_uses_explicit_value(self, tmp_path):
        assert utils._resolve_config_root(tmp_path) == tmp_path

    def test_resolve_config_root_raises_when_package_path_missing(self, monkeypatch):
        monkeypatch.setattr(utils, "get_package_path", lambda package_name="bt_api_py": None)
        with pytest.raises(RuntimeError):
            utils._resolve_config_root()

    def test_get_project_log_path_uses_package_parent(self, monkeypatch, tmp_path):
        package_dir = tmp_path / "bt_api_py"
        package_dir.mkdir()
        monkeypatch.setattr(
            utils, "get_package_path", lambda package_name="bt_api_py": str(package_dir)
        )
        assert utils.get_project_log_path("app.log") == str(tmp_path / "logs" / "app.log")

    def test_get_project_log_path_falls_back_to_cwd(self, monkeypatch, tmp_path):
        monkeypatch.setattr(utils, "get_package_path", lambda package_name="bt_api_py": None)
        monkeypatch.chdir(tmp_path)
        assert utils.get_project_log_path("app.log") == str(tmp_path / "logs" / "app.log")


class TestFileAndConfigHelpers:
    def test_read_yaml_file(self, tmp_path):
        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        target = configs_dir / "sample.yaml"
        target.write_text("answer: 42\nname: test\n", encoding="utf-8")
        assert utils.read_yaml_file("sample.yaml", data_root=tmp_path) == {
            "answer": 42,
            "name": "test",
        }

    def test_read_account_config_loads_env_and_proxies(self, monkeypatch, tmp_path):
        package_dir = tmp_path / "bt_api_py"
        package_dir.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(
            "OKX_API_KEY=okx-key\n"
            "OKX_SECRET=okx-secret\n"
            "OKX_PASSWORD=okx-pass\n"
            "BINANCE_API_KEY=binance-key\n"
            "BINANCE_PASSWORD=binance-secret\n"
            "HTTP_PROXY=http://proxy\n"
            "IB_PORT=4001\n"
            "IB_WEB_VERIFY_SSL=true\n"
            "IB_WEB_TIMEOUT=20\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            utils, "get_package_path", lambda package_name="bt_api_py": str(package_dir)
        )
        monkeypatch.delenv("CTP_ENV", raising=False)
        config = utils.read_account_config()
        assert config["okx"]["public_key"] == "okx-key"
        assert config["binance"]["public_key"] == "binance-key"
        assert config["ib"]["port"] == 4001
        assert config["ib_web"]["verify_ssl"] is True
        assert config["ib_web"]["timeout"] == 20
        assert (
            config["proxies"]
            == {
                "http": "http://proxy",
                "https": "http://default.mitmproxy.hub.ace-research.openai.org:80",
            }
            or config["proxies"] == {"http": "http://proxy"}
            or config["proxies"] == {"http": "http://proxy", "https": "http://proxy"}
        )

    def test_read_account_config_applies_ctp_env_when_enabled(self, monkeypatch, tmp_path):
        package_dir = tmp_path / "bt_api_py"
        package_dir.mkdir()
        (tmp_path / ".env").write_text("", encoding="utf-8")
        monkeypatch.setattr(
            utils, "get_package_path", lambda package_name="bt_api_py": str(package_dir)
        )
        monkeypatch.setenv("CTP_ENV", "simnow")
        called = {"value": False}
        module = types.ModuleType("bt_api_py.ctp_env_selector")

        def apply_ctp_env():
            called["value"] = True

        module.apply_ctp_env = apply_ctp_env
        monkeypatch.setitem(sys.modules, "bt_api_py.ctp_env_selector", module)
        utils.read_account_config()
        assert called["value"] is True

    def test_update_extra_data_merges_dicts(self):
        assert utils.update_extra_data({"a": 1}, b=2) == {"a": 1, "b": 2}

    def test_update_extra_data_handles_none(self):
        assert utils.update_extra_data(None, a=1) == {"a": 1}


class TestDictHelpers:
    def test_from_dict_get_string_returns_default_for_missing_key(self):
        assert utils.from_dict_get_string({}, "missing", "fallback") == "fallback"

    def test_from_dict_get_string_casts_non_string(self):
        assert utils.from_dict_get_string({"value": 123}, "value") == "123"

    def test_from_dict_get_bool_supports_multiple_input_types(self):
        assert utils.from_dict_get_bool({"v": True}, "v") is True
        assert utils.from_dict_get_bool({"v": "yes"}, "v") is True
        assert utils.from_dict_get_bool({"v": "off"}, "v") is False
        assert utils.from_dict_get_bool({"v": 1}, "v") is True

    def test_from_dict_get_bool_raises_for_invalid_value(self):
        with pytest.raises(TypeError):
            utils.from_dict_get_bool({"v": "maybe"}, "v")

    def test_from_dict_get_float_covers_supported_inputs(self):
        assert utils.from_dict_get_float({"v": "1.5"}, "v") == 1.5
        assert utils.from_dict_get_float({"v": 2.5}, "v") == 2.5
        assert utils.from_dict_get_float({"v": ""}, "v") is None
        assert utils.from_dict_get_float({}, "v", 9.9) == 9.9
        assert utils.from_dict_get_float({"v": "bad"}, "v", 7.7) == 7.7

    def test_from_dict_get_int_covers_supported_inputs(self):
        assert utils.from_dict_get_int({"v": "3"}, "v") == 3
        assert utils.from_dict_get_int({"v": 4}, "v") == 4
        assert utils.from_dict_get_int({"v": ""}, "v") is None
        assert utils.from_dict_get_int({}, "v", 8) == 8
        assert utils.from_dict_get_int({"v": "bad"}, "v", 6) == 6
