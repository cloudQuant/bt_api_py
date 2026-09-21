"""Offline behavior contracts for the Binance margin-doc scraper copies."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRAPER_PATHS = (
    "scripts/scrape_margin_docs.py",
    "scripts/tools/scrape_margin_docs.py",
)


class PlaywrightSyncApiModule(ModuleType):
    sync_playwright: Mock


class PlaywrightModule(ModuleType):
    sync_api: PlaywrightSyncApiModule


def _load_scraper(relative_path: str, monkeypatch: pytest.MonkeyPatch):
    playwright = PlaywrightModule("playwright")
    sync_api = PlaywrightSyncApiModule("playwright.sync_api")
    sync_api.sync_playwright = Mock()
    playwright.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    script_path = ROOT / relative_path
    module_name = "isolated_" + relative_path.removesuffix(".py").replace("/", "_")
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, sync_api


@pytest.mark.parametrize("relative_path", SCRAPER_PATHS)
def test_sidebar_discovery_failure_returns_empty_links_and_safe_warning(
    relative_path: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page_content = "private-page-content-sentinel"
    page_url = "https://private-url-sentinel.invalid/docs/private"
    filesystem_path = str(tmp_path / "private-path-sentinel")
    exception_text = f"failure: {page_content} {page_url} {filesystem_path}"
    page = SimpleNamespace(url=page_url, content=page_content, path=filesystem_path)
    logger = Mock()

    module, sync_api = _load_scraper(relative_path, monkeypatch)
    module.discover_sidebar_links = Mock(side_effect=RuntimeError(exception_text))
    monkeypatch.setattr(module, "logger", logger, raising=False)

    links = module._safe_discover_sidebar_links(page)

    assert links == []
    module.discover_sidebar_links.assert_called_once_with(page)
    logger.warning.assert_called_once_with("Sidebar link discovery failed (%s)", "RuntimeError")
    assert logger.warning.call_args.kwargs == {}
    diagnostic = repr(logger.warning.call_args)
    assert exception_text not in diagnostic
    assert page_content not in diagnostic
    assert page_url not in diagnostic
    assert filesystem_path not in diagnostic
    sync_api.sync_playwright.assert_not_called()
