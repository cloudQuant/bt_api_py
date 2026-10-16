"""Direct probe: production quote builder rejects a real parsed v3 request (fail-closed)."""
import pathlib, sys, tempfile
sys.path.insert(0, r"D:\bt_api_py")
from tests.integration import _dim09_three_leg_fixtures as fx
from bt_api_execution import build_instrument_risk_group_budget_quote
from bt_api_execution.errors import ContractValidationError
from bt_api_risk import InstrumentRiskRegistry
class Shim:
    def setitem(self, mapping, key, value):
        mapping[key] = value
td = tempfile.mkdtemp(dir=r"D:/temp/it10dim09-step2-review-20261016-r1/wire-probe")
cluster = fx.Dim09ThreeLegCluster(pathlib.Path(td), Shim())
try:
    body = cluster.v3_body("g-builder-probe")
    request = cluster.coordinator_service._parse_request(
        cluster.strategy_registration, cluster.coordinator_target, body)
    print("parsed request version:", request.version, "legs:", len(request.legs))
    registry = InstrumentRiskRegistry((cluster.metadata,))
    try:
        build_instrument_risk_group_budget_quote(
            request, cluster.portfolio_budget_policy, registry=registry,
            now_ns=__import__("time").time_ns())
        print("FAIL | production builder ACCEPTED v3 request (silent acceptance!)")
        sys.exit(1)
    except ContractValidationError as e:
        print("PASS | production builder rejects v3:", e)
    # and the service-side mapping around it (read-verified 704-705)
finally:
    cluster.close()
