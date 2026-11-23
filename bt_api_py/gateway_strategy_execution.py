"""Local, explicitly selected Execution composition; no files are opened."""
import hashlib
import sqlite3

from bt_api_py import _execution_route_journal as journal
from bt_api_py import execution_routing as routing
from bt_api_py.execution_parent_strategy_session import ExecutionParentStrategySession
from bt_api_py.service_runtime import BoundExecutionParentTarget, ClientCurveKeys

_RETAINED_FAILED_SESSIONS = []


def validate_database(db):
    try:
        if type(db) is not sqlite3.Connection or db.isolation_level is not None or db.in_transaction:
            raise ValueError
        for name, schema in ((routing._TABLE, routing._SCHEMA), (journal._TABLE, journal._SCHEMA)):
            row = db.execute("SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
            if row is None or tuple(row) != (schema,):
                raise ValueError
    except Exception:
        raise ValueError("INVALID_EXECUTION_DATABASE") from None


def compose_execution(
    selection, profiles, db, *, risk_selected, on_session, forbid_direct_path: bool = False
):
    route = selection.route
    try:
        keys = profiles[selection.security_profile_ref]
        if type(keys) is not ClientCurveKeys or type(keys.server_key) is not bytes or len(keys.server_key) != 40:
            raise ValueError
        target = BoundExecutionParentTarget(
            route.mount, "service.execution_parent", route.service_id, route.instance_id,
            route.scope_ref, route.scope, route.identity, route.scope.run_id,
            selection.transport_ref, selection.security_profile_ref, selection.endpoint,
            selection.peer, hashlib.sha256(keys.server_key).hexdigest(),
            "registration-placeholder", "epoch-placeholder")
        session = ExecutionParentStrategySession(selection.client_config, target=target,
                                                 security_profiles=profiles)
        on_session(session)
    except Exception:
        raise ValueError("INVALID_EXECUTION_CONFIGURATION") from None
    router = routing.ExecutionOrderRouter(
        db=db,
        config=selection.routing,
        execution_port=session.client,
        risk_selected=risk_selected,
        forbid_direct_path=forbid_direct_path,
    )
    return session, router
