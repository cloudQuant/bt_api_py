"""The v2 origin adapter reuses a real Registry scope without widening it."""

import threading

import pytest
from bt_api_gateway.risk_v2_binding import RiskV2BindingError, _install_direct_issuer

from bt_api_py.gateway_risk_v2_direct_origin import (
    GatewayRiskV2DirectAdmission,
    GatewayRiskV2DirectOriginError,
    risk_v2_direct_existing_scope,
)

pytest_plugins = ("tests.unit.test_iter64_gateway_risk_v2_direct_origin_source",)


def test_existing_scope_is_reused_without_nested_scope_and_permit_replay(harness):
    holder, _, owner, reserve = harness
    holder["capture"] = True
    assert reserve("a")["ok"]
    context, arguments = holder["captures"][0]
    admission = context.dispatch_admission

    def check():
        authority = holder["authority"]
        issuer = _install_direct_issuer(
            holder["journal"], holder["guard"], GatewayRiskV2DirectAdmission,
            authority.validate, admission_identity=authority.admission_identity,
        )
        kwargs = {"context": context, "arguments": arguments, "admission": admission,
                      "journal": holder["journal"], "owner": owner, "authority": authority}
        with admission.scope() as recheck:
            with risk_v2_direct_existing_scope(**kwargs, recheck=recheck) as origin:
                binding = origin.binding
                issuer.issue(origin, binding)
                assert authority.validate(origin, binding)
            assert not authority.validate(origin, binding)
            recheck()  # the caller still owns the Registry scope
            with (risk_v2_direct_existing_scope(**kwargs, recheck=recheck) as replay,
                  pytest.raises(RiskV2BindingError)):
                issuer.issue(replay, replay.binding)
        with (pytest.raises(GatewayRiskV2DirectOriginError),
              risk_v2_direct_existing_scope(**kwargs, recheck=recheck)):
            pytest.fail("accepted expired scope")
        return True

    try:
        assert owner.submit_service_call(check).result(timeout_seconds=2)
    finally:
        admission.close()


def test_existing_scope_rejects_forged_cross_admission_and_other_thread(harness):
    holder, _, owner, reserve = harness
    holder["capture"] = True
    assert reserve("a")["ok"] and reserve("b")["ok"]
    (context_a, args_a), (context_b, args_b) = holder["captures"]
    admission_a, admission_b = context_a.dispatch_admission, context_b.dispatch_admission

    def check():
        kwargs = {"context": context_a, "arguments": args_a, "admission": admission_a,
                      "journal": holder["journal"], "owner": owner, "authority": holder["authority"]}
        with admission_b.scope() as other_recheck:
            pass
        with admission_a.scope() as recheck:
            for wrong in (lambda: None, other_recheck):
                with (pytest.raises(GatewayRiskV2DirectOriginError),
                      risk_v2_direct_existing_scope(**kwargs, recheck=wrong)):
                    pytest.fail("accepted an unrelated callback")
            for changed in ({"context": context_b}, {"arguments": args_b},
                            {"admission": admission_b}):
                with (pytest.raises(GatewayRiskV2DirectOriginError),
                      risk_v2_direct_existing_scope(**(kwargs | changed), recheck=recheck)):
                    pytest.fail("accepted a different request")
            outcomes = []

            def on_other_thread():
                try:
                    with risk_v2_direct_existing_scope(**kwargs, recheck=recheck):
                        outcomes.append("accepted")
                except GatewayRiskV2DirectOriginError:
                    outcomes.append("rejected")

            thread = threading.Thread(target=on_other_thread)
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive()
            assert outcomes == ["rejected"]
            with risk_v2_direct_existing_scope(**kwargs, recheck=recheck) as origin:
                assert holder["authority"].validate(origin, origin.binding)
        return True

    try:
        assert owner.submit_service_call(check).result(timeout_seconds=2)
    finally:
        admission_a.close()
        admission_b.close()


def test_existing_scope_rejects_admission_closed_during_scope(harness):
    holder, _, owner, reserve = harness
    holder["capture"] = True
    assert reserve("a")["ok"]
    context, arguments = holder["captures"][0]
    admission = context.dispatch_admission

    def check():
        kwargs = {"context": context, "arguments": arguments, "admission": admission,
                      "journal": holder["journal"], "owner": owner, "authority": holder["authority"]}
        with admission.scope() as recheck:
            with risk_v2_direct_existing_scope(**kwargs, recheck=recheck) as origin:
                binding = origin.binding
                admission.close()
                assert not holder["authority"].validate(origin, binding)
            with (pytest.raises(GatewayRiskV2DirectOriginError),
                  risk_v2_direct_existing_scope(**kwargs, recheck=recheck)):
                pytest.fail("accepted closed admission")
        return True

    assert owner.submit_service_call(check).result(timeout_seconds=2)


def test_existing_scope_exception_closes_origin_and_reentry_rejects_old_callback(harness):
    holder, _, owner, reserve = harness
    holder["capture"] = True
    assert reserve("a")["ok"]
    context, arguments = holder["captures"][0]
    admission = context.dispatch_admission

    def check():
        authority = holder["authority"]
        kwargs = {"context": context, "arguments": arguments, "admission": admission,
                  "journal": holder["journal"], "owner": owner, "authority": authority}
        with admission.scope() as old_recheck:
            with (pytest.raises(ValueError, match="inner failure"),
                  risk_v2_direct_existing_scope(**kwargs, recheck=old_recheck) as origin):
                binding = origin.binding
                raise ValueError("inner failure")
            assert not authority.validate(origin, binding)
            old_recheck()
        with admission.scope() as current_recheck:
            assert current_recheck is not old_recheck
            with (pytest.raises(GatewayRiskV2DirectOriginError),
                  risk_v2_direct_existing_scope(**kwargs, recheck=old_recheck)):
                pytest.fail("accepted the previous scope callback")
            with risk_v2_direct_existing_scope(**kwargs, recheck=current_recheck) as current:
                assert authority.validate(current, current.binding)
        return True

    try:
        assert owner.submit_service_call(check).result(timeout_seconds=2)
    finally:
        admission.close()
