"""Tests for dependency injection container registration behavior."""

from __future__ import annotations

from bt_api_py.core.dependency_injection import DIContainer


class _ServiceInterface:
    pass


class _FirstService:
    def __init__(self) -> None:
        self.name = "first"


class _SecondService:
    def __init__(self) -> None:
        self.name = "second"


def test_register_singleton_replaces_cached_instance() -> None:
    container = DIContainer()
    container.register_singleton(_ServiceInterface, _FirstService)
    first = container.resolve(_ServiceInterface)

    container.register_singleton(_ServiceInterface, _SecondService)
    second = container.resolve(_ServiceInterface)

    assert first is not second
    assert second.name == "second"


def test_register_instance_overrides_previous_registration() -> None:
    container = DIContainer()
    container.register_transient(_ServiceInterface, _FirstService)

    replacement = _SecondService()
    container.register_instance(_ServiceInterface, replacement)

    resolved = container.resolve(_ServiceInterface)
    assert resolved is replacement


def test_register_scoped_clears_previous_scope_instance() -> None:
    container = DIContainer()
    container.register_scoped(_ServiceInterface, _FirstService)

    with container.create_scope():
        first = container.resolve(_ServiceInterface)
        container.register_scoped(_ServiceInterface, _SecondService)
        second = container.resolve(_ServiceInterface)

        assert first is not second
        assert second.name == "second"
