import ast
from pathlib import Path

RETIRED_MODULE_PREFIXES = (
    "bt_api_py.containers.exchanges.curve_exchange_data",
    "bt_api_py.containers.exchanges.raydium_exchange_data",
    "bt_api_py.containers.exchanges.sushiswap_exchange_data",
    "bt_api_py.feeds.live_curve",
    "bt_api_py.feeds.live_raydium",
    "bt_api_py.feeds.live_sushiswap",
)
CACHE_DIRECTORY_NAMES = frozenset(
    {"__pycache__", ".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache", "cache"}
)


def _is_retired_module(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in RETIRED_MODULE_PREFIXES
    )


def _is_cache_file(source_path: Path, source_root: Path) -> bool:
    directory_parts = source_path.relative_to(source_root).parts[:-1]
    return any(
        directory in CACHE_DIRECTORY_NAMES or directory.endswith("_cache")
        for directory in directory_parts
    )


def _imported_module_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    if isinstance(node, ast.ImportFrom) and node.module:
        module_names = [node.module]
        if not _is_retired_module(node.module):
            module_names.extend(
                f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
            )
        return module_names

    return []


def test_network_examples_do_not_import_retired_dex_modules() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    source_root = repository_root / "examples" / "network_tests"
    assert source_root.is_dir(), f"Expected example source directory at {source_root}"

    violations = []
    for source_path in sorted(source_root.rglob("*.py")):
        if _is_cache_file(source_path, source_root):
            continue

        source_tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(source_tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for imported_module in _imported_module_names(node):
                if _is_retired_module(imported_module):
                    relative_path = source_path.relative_to(repository_root)
                    violations.append(f"{relative_path}:{node.lineno}: {imported_module}")

    assert not violations, (
        "Retired DEX modules are still imported by examples/network_tests:\n"
        + "\n".join(violations)
    )
