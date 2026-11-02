"""SDK entry point for the CTP provider's isolated SimNow trader probe.

Importing this module does not load the provider or start a connection.
"""

from __future__ import annotations


def probe_simnow_trader_connection(
    *,
    td_front: str,
    broker_id: str,
    user_id: str,
    password: str,
    app_id: str,
    auth_code: str,
) -> tuple[bool, str]:
    """Delegate the explicit probe request to the selected CTP provider."""
    from bt_api_ctp.simnow_probe import probe_simnow_trader_connection as probe

    return probe(
        td_front=td_front,
        broker_id=broker_id,
        user_id=user_id,
        password=password,
        app_id=app_id,
        auth_code=auth_code,
    )
