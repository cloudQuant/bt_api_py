"""Own host-generation fencing for SDK-issued identity vector bindings."""

from __future__ import annotations

import threading
from typing import Any, Callable, List, Tuple, Type


class ExecutionIdentityFence:
    """Retain opaque SDK identity scopes across Store generations.

    The SDK captures and validates provider assurance, route/session state, and
    the canonical identity-vector digest. This SDK component sees only
    the exact vector binding and its opaque per-route scope references; it
    applies the Store-local generation/fencing-epoch rule and retains those
    references strongly for as long as their history is needed.
    """

    def __init__(self, *, error_type: Type[Exception]) -> None:
        if not callable(error_type):
            raise TypeError("SDK execution identity fence requires an error type")
        self._error_type = error_type
        self._identity_bindings = {}
        self._fence_history: List[Tuple[Tuple[str, Any], int, int]] = []
        self._lock = threading.Lock()

    def clear_session(self) -> None:
        """Clear current-session pins while retaining cross-generation history."""
        with self._lock:
            self._identity_bindings.clear()

    @staticmethod
    def _binding_contract(binding: Any, expected_routes: Tuple[str, ...]):
        try:
            from bt_api_py.execution_identity_assurance_composition import (
                execution_identity_vector_contract,
            )
        except Exception:
            return None
        return execution_identity_vector_contract(binding, expected_routes)

    def bind_vector(
        self,
        api: Any,
        binding: Any,
        *,
        expected_routes: Tuple[str, ...],
        stream_generation: int,
        is_current_api: Callable[[Any], bool],
    ) -> None:
        """Apply the prior Store-generation fence to one SDK-issued vector."""
        if api is None or not callable(is_current_api) or not is_current_api(api):
            raise self._error_type("execution_identity_snapshot_route_changed")
        if type(stream_generation) is not int or stream_generation < 0:
            raise self._error_type("execution_identity_snapshot_projection_invalid")
        contract = self._binding_contract(binding, expected_routes)
        if contract is None:
            raise self._error_type("execution_identity_vector_projection_invalid")
        scope_refs, session_generations, fencing_epoch = contract

        routes_and_keys = tuple(
            (route, (route, scope_ref), session_generation)
            for route, scope_ref, session_generation in zip(
                expected_routes, scope_refs, session_generations
            )
        )
        with self._lock:
            if not is_current_api(api):
                raise self._error_type("execution_identity_snapshot_route_changed")
            for route, identity_key, session_generation in routes_and_keys:
                prior = self._identity_bindings.get(route)
                if prior is not None:
                    prior_api, prior_scope, prior_generation, prior_session, prior_epoch = prior
                    if (
                        prior_api is not api
                        or prior_scope is not identity_key[1]
                        or prior_generation != stream_generation
                        or prior_session != session_generation
                        or prior_epoch != fencing_epoch
                    ):
                        raise self._error_type("execution_identity_snapshot_route_changed")

                for prior_key, prior_generation, prior_epoch in self._fence_history:
                    if prior_key != identity_key:
                        continue
                    if prior_generation == stream_generation and prior_epoch != fencing_epoch:
                        raise self._error_type("execution_identity_snapshot_route_changed")
                    if prior_generation != stream_generation and fencing_epoch <= prior_epoch:
                        raise self._error_type(
                            "execution_identity_snapshot_fence_not_advanced"
                        )

            updated_history = list(self._fence_history)
            for _route, identity_key, _session_generation in routes_and_keys:
                updated_history = [
                    row for row in updated_history if row[0] != identity_key
                ]
                updated_history.append((identity_key, stream_generation, fencing_epoch))

            for route, identity_key, session_generation in routes_and_keys:
                self._identity_bindings[route] = (
                    api,
                    identity_key[1],
                    stream_generation,
                    session_generation,
                    fencing_epoch,
                )
            self._fence_history = updated_history

    def validate_vector(
        self,
        api: Any,
        binding: Any,
        *,
        expected_routes: Tuple[str, ...],
        stream_generation: int,
        is_current_api: Callable[[Any], bool],
    ) -> bool:
        """Require the exact current Store API, scope refs, generation, and epoch."""
        if (
            api is None
            or not callable(is_current_api)
            or not is_current_api(api)
            or type(stream_generation) is not int
            or stream_generation < 0
        ):
            return False
        contract = self._binding_contract(binding, expected_routes)
        if contract is None:
            return False
        scope_refs, session_generations, fencing_epoch = contract
        with self._lock:
            for route, scope_ref, session_generation in zip(
                expected_routes, scope_refs, session_generations
            ):
                pinned = self._identity_bindings.get(route)
                if (
                    pinned is None
                    or pinned[0] is not api
                    or pinned[1] is not scope_ref
                    or pinned[2] != stream_generation
                    or pinned[3] != session_generation
                    or pinned[4] != fencing_epoch
                ):
                    return False
        return is_current_api(api)


__all__ = ("ExecutionIdentityFence",)
