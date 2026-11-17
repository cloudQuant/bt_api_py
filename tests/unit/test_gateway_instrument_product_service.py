"""SERVICE-only product queries share the exact synthetic CTP owner and trader."""

import time
from dataclasses import replace

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_instrument_product_codec import (
    BEGIN,
    CANCEL,
    OPERATIONS,
    STATUS,
    validate_response,
)
from bt_api_py.gateway_instrument_product_config import InstrumentProductGrant
from bt_api_py.gateway_instrument_product_service import GatewayInstrumentProductService
from bt_api_py.service_registry import ActiveRegistrationContext, ServiceCallContext
from tests.unit.test_iter63_gateway_instrument_service import (  # noqa: F401
    KEY,
    ctp_prepared,
    harness,
)


def make(h):
    account = h.service._journal.account_scope
    grant = InstrumentProductGrant("monitor", "monitor-actor", "domain", account, frozenset({KEY}))
    service = GatewayInstrumentProductService(
        h.service._owner, h.session, h.service._journal, frozenset({grant})
    )
    ctx = ServiceCallContext(
        WireIdentity(WireRole.SERVICE, "monitor", "monitor-actor"),
        WireScope("domain", account_ref=account),
        "registration",
        "monitor",
        lambda _: True,
    )
    active = ActiveRegistrationContext(
        ctx,
        "gateway",
        "service.instrument_product_read",
        "scope",
        OPERATIONS,
        frozenset(),
        "epoch",
        "policy",
        "requirements",
        time.time() + 60,
    )
    return service, ctx, active


def test_product_observation_uses_same_native_session_and_fences(harness, monkeypatch):  # noqa: F811
    h = harness
    service, ctx, active = make(h)
    opid = "a" * 32

    def call(op, **args):
        return service.handle(op, context=ctx, arguments={"operation_id": opid, **args})

    assert call(BEGIN, instrument=KEY.to_dict())["status"] == "pending"
    for _ in range(200):
        service.maintain_registrations((active,))
        for task in h.calls:
            task.execute()
        result = call(STATUS)
        if result["status"] == "ready":
            break
        time.sleep(0.002)
    else:
        pytest.fail(
            f"synthetic query did not finish: {result!r}; {service._jobs[(ctx.registration_id, opid)].observation!r}; {harness.requests!r}"
        )
    snap = validate_response(result, expected_operation_id=opid, expected_instrument=KEY)[
        "snapshot"
    ]
    assert snap["product_id"] == "rb"
    assert snap["session_generation"] == "g1" and 0 < snap["remaining_seconds"] <= 35
    assert h.requests == [("rb2701", "SHFE")] and h.streams == [] and h.md.instances == []
    assert call(STATUS)["status"] == "pending"
    for _ in range(3):
        service.maintain_registrations((active,))
        for task in h.calls:
            task.execute()
    job = service._jobs[(ctx.registration_id, opid)]
    monkeypatch.setattr(job.reader, "observation_current", lambda _: False)
    assert call(STATUS)["code"] == "SOURCE_UNAVAILABLE"
    service.maintain_registrations(())
    assert call(STATUS)["status"] == "failed"
    service.begin_close()


def test_separate_grants_context_and_jobs(harness):  # noqa: F811
    service, ctx, active = make(harness)
    for identity in (
        replace(ctx.identity, role=WireRole.STRATEGY_CLIENT),
        replace(ctx.identity, actor_ref="other"),
        replace(ctx.identity, delegate_ref="other"),
    ):
        with pytest.raises(ValueError, match="SCOPE_DENIED"):
            service.handle(
                BEGIN,
                context=replace(ctx, identity=identity),
                arguments={"operation_id": "a" * 32, "instrument": KEY.to_dict()},
            )
    for scope in (
        replace(ctx.scope, account_ref="other"),
        replace(ctx.scope, strategy_ref="other"),
        replace(ctx.scope, execution_domain_ref="other"),
    ):
        with pytest.raises(ValueError, match="SCOPE_DENIED"):
            service.handle(
                BEGIN,
                context=replace(ctx, scope=scope),
                arguments={"operation_id": "a" * 32, "instrument": KEY.to_dict()},
            )
    other = replace(KEY, instrument_id="other", native_symbol="other")
    with pytest.raises(ValueError, match="INSTRUMENT_DENIED"):
        service.handle(
            BEGIN, context=ctx, arguments={"operation_id": "a" * 32, "instrument": other.to_dict()}
        )
    assert not service._jobs
    assert (
        service.handle(CANCEL, context=ctx, arguments={"operation_id": "a" * 32})["status"]
        == "cancelled"
    )
    assert (
        service.handle(
            STATUS,
            context=replace(ctx, registration_id="another"),
            arguments={"operation_id": "a" * 32},
        )["status"]
        == "not_found"
    )
    service.begin_close()


def test_conflict_unregistration_and_owner_cleanup(harness):  # noqa: F811
    service, ctx, active = make(harness)
    other = replace(KEY, instrument_id="other", native_symbol="other")
    service._grants["monitor"] = replace(
        service._grants["monitor"], instruments=frozenset({KEY, other})
    )
    request = {"operation_id": "a" * 32, "instrument": KEY.to_dict()}
    assert service.handle(BEGIN, context=ctx, arguments=request)["status"] == "pending"
    with pytest.raises(ValueError, match="OPERATION_CONFLICT"):
        service.handle(BEGIN, context=ctx, arguments=dict(request, instrument=other.to_dict()))
    service.maintain_registrations((active,))
    queued = harness.calls[-1]
    service.maintain_registrations(())
    queued.execute()
    service.maintain_registrations(())
    assert harness.requests == []
    assert (
        service.handle(STATUS, context=ctx, arguments={"operation_id": "a" * 32})["status"]
        == "cancelled"
    )
    service.begin_close()
    assert service.cleanup_on_owner() is True


def test_close_retains_unconfirmed_native_handle(harness, monkeypatch):  # noqa: F811
    service, ctx, active = make(harness)
    service.handle(
        BEGIN, context=ctx, arguments={"operation_id": "b" * 32, "instrument": KEY.to_dict()}
    )
    service.maintain_registrations((active,))
    harness.calls[-1].execute()
    job = service._jobs[(ctx.registration_id, "b" * 32)]
    assert job.handle is not None
    original_handle = job.handle
    monkeypatch.setattr(job.reader, "cancel", lambda _: False)
    monkeypatch.setattr(job.reader, "release", lambda _: False)
    service.begin_close()
    assert service.cleanup_on_owner() is False and job.handle is original_handle
    monkeypatch.setattr(job.reader, "release", lambda _: True)
    assert service.cleanup_on_owner() is True and job.handle is None


def test_product_and_strategy_queries_share_the_original_trader(harness):  # noqa: F811
    product, ctx, active = make(harness)
    request = {"operation_id": "c" * 32, "instrument": KEY.to_dict()}
    product.handle(BEGIN, context=ctx, arguments=request)
    harness.service.handle("account.instrument_spec.begin", context=harness.ctx, arguments=request)
    for _ in range(400):
        product.maintain_registrations((active,))
        harness.service.maintain_registrations((harness.active,))
        for task in harness.calls:
            task.execute()
        a = product.handle(STATUS, context=ctx, arguments={"operation_id": "c" * 32})
        b = harness.service.handle(
            "account.instrument_spec.status",
            context=harness.ctx,
            arguments={"operation_id": "c" * 32},
        )
        if a["status"] == b["status"] == "ready":
            break
        time.sleep(0.002)
    else:
        pytest.fail("both synthetic observations did not finish")
    product_job = product._jobs[(ctx.registration_id, "c" * 32)]
    strategy_job = harness.service._jobs[(harness.ctx.registration_id, "c" * 32)]
    assert product_job.reader._trader is strategy_job.reader._trader is harness.native.client
    assert len(harness.requests) == 2
    product.begin_close()


@pytest.mark.parametrize("day", ["20261106", "20261107"])
def test_relogin_or_day_change_rejects_original_product_observation(harness, day):  # noqa: F811
    from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi

    service, ctx, active = make(harness)
    opid = "d" * 32
    service.handle(
        BEGIN, context=ctx, arguments={"operation_id": opid, "instrument": KEY.to_dict()}
    )
    for _ in range(200):
        service.maintain_registrations((active,))
        for task in harness.calls:
            task.execute()
        value = service.handle(STATUS, context=ctx, arguments={"operation_id": opid})
        if value["status"] == "ready":
            break
        time.sleep(0.002)
    assert value["status"] == "ready"
    _relogin_same_spi(harness.native, day, 20)
    harness.session.refresh_session()
    for _ in range(30):
        service.maintain_registrations((active,))
        for task in harness.calls:
            task.execute()
        value = service.handle(STATUS, context=ctx, arguments={"operation_id": opid})
        if value["status"] == "failed":
            break
    assert value["status"] == "failed" and value["snapshot"] is None
    service.begin_close()
