"""Explicit, bounded YAML loading for optional SDK clients and services.

Loading a configuration never starts a component, discovers a plugin, resolves
a secret/policy reference, or expands an environment variable. Run
``python -m bt_api_py.service_config_io --role client path.yaml`` for an offline
validation summary. Use ``--role service`` for a deployment configuration.
"""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from bt_api_py.service_config import (
    ServiceConfigError,
    parse_client_config,
    parse_service_config,
)

if TYPE_CHECKING:
    from bt_api_py.service_config import ClientConfig, ServiceConfig

MAX_CONFIG_BYTES = 262_144
MAX_CONFIG_DEPTH = 32
MAX_CONFIG_NODES = 16_384


def _read_yaml(path: str | Path) -> dict:
    """Read one plain mapping, without aliases, merge keys or duplicate keys."""
    import yaml

    try:
        with Path(path).open("rb") as source:
            # Check the opened file, so a replaced path cannot change this check.
            import os

            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ServiceConfigError("CONFIG_NOT_REGULAR_FILE", "$")
            raw = source.read(MAX_CONFIG_BYTES + 1)
    except OSError:
        raise ServiceConfigError("CONFIG_READ_FAILED", "$") from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise ServiceConfigError("CONFIG_TOO_LARGE", "$")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ServiceConfigError("CONFIG_ENCODING_INVALID", "$") from None

    class UniqueSafeLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            # Reject merges before construction rather than let SafeLoader
            # silently resolve overriding fields from another configuration.
            if key_node.tag != "tag:yaml.org,2002:str":
                raise ServiceConfigError("CONFIG_KEY_INVALID", "$")
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise ServiceConfigError("CONFIG_DUPLICATE_KEY", "$")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueSafeLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )
    try:
        depth = 0
        for nodes, event in enumerate(yaml.parse(text, Loader=yaml.SafeLoader), start=1):
            if nodes > MAX_CONFIG_NODES:
                raise ServiceConfigError("CONFIG_TOO_COMPLEX", "$")
            if isinstance(event, yaml.AliasEvent):
                raise ServiceConfigError("CONFIG_ALIAS_FORBIDDEN", "$")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > MAX_CONFIG_DEPTH:
                    raise ServiceConfigError("CONFIG_TOO_DEEP", "$")
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
        value = yaml.load(text, Loader=UniqueSafeLoader)
    except yaml.YAMLError:
        # YAML exceptions may contain a line copied from a secret-bearing file.
        raise ServiceConfigError("CONFIG_YAML_INVALID", "$") from None
    if not isinstance(value, dict):
        raise ServiceConfigError("CONFIG_ROOT_INVALID", "$")
    return value


def load_client_config(path: str | Path) -> ClientConfig:
    """Validate an SDK client file without importing optional service packages."""
    return parse_client_config(_read_yaml(path))


def load_service_config(path: str | Path) -> ServiceConfig:
    """Validate a deployment file; this deliberately does not activate it."""
    return parse_service_config(_read_yaml(path))


def main(argv: list[str] | None = None) -> int:
    """Print finite diagnostics without echoing configuration values or paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("client", "service"), required=True)
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)
    loader = load_client_config if args.role == "client" else load_service_config
    try:
        config = loader(args.config)
    except ServiceConfigError as error:
        print(json.dumps({"status": "invalid", "code": error.code, "path": error.path}))
        return 2
    public = config.to_public_dict()
    print(
        json.dumps(
            {
                "status": "valid",
                "process_role": public.get("process_role"),
                "client_count": len(public.get("clients", {})),
                "component_count": len(public.get("components", {})),
                "fingerprint": config.fingerprint(),
                "activated": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
