"""Template for a separately installed, trusted CTP Gateway profile factory.

Register the exported ``profile_factory`` in the plugin distribution's
``bt_api_py.gateway_operator_profile_factories`` entry-point group with name
``ctp-simnow``. Its loader executes on the Gateway owner thread and must
resolve CtpGatewayTrustedResources from the operator's protected authority,
credentials and explicit grants. Never place secret values in the public YAML.
This intentionally incomplete example fails closed until an operator supplies
those trusted resources; it does not open an account or authorize trading.
"""
from bt_api_py.ctp_gateway_operator_profile import create_ctp_gateway_profile_factory


def load_trusted_resources(service_config):
    """Resolve trusted authority, settings, grants and provider factories here."""
    raise RuntimeError("CTP_TRUSTED_RESOURCES_NOT_CONFIGURED")


profile_factory = create_ctp_gateway_profile_factory(
    "ctp-simnow", resource_loader=load_trusted_resources
)
