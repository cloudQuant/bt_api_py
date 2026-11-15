"""Unit-test fixture providers registered at collection start.

A few unit modules declare ``pytest_plugins`` on the test module itself and rely
on fixtures defined by another test module. Pytest imports a test module only
when its collection node is visited, and ``FixtureManager.parsefactories`` skips
a module object it has already parsed. When the provider module is collected
before the consumer, the later plugin registration is therefore a no-op and the
fixtures are invisible. Registering the providers here, from a conftest loaded
before any test module is collected, makes availability independent of file
collection order.
"""

pytest_plugins = (
    "tests.unit.test_trading_calendar_interruption_prepare_simulation",
    "tests.unit.test_iter64_gateway_risk_v2_direct_origin_source",
    "tests.unit.test_iter64_gateway_risk_control_port",
)
