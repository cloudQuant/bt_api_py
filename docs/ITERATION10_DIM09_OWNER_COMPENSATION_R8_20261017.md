# DIM09 owner-managed compensation close source round

This round delivers the selected owner-managed compensation source closure on SDK `dev`, based on parent `19ba958015da2706ffa6ad0ad1f61ba66db269f7`. The accepted Execution dependency remains pinned by the SDK gitlink at `fdf53a416f7a7243e987fc46b267fc11ff2272a6`; the live Execution checkout is ahead and is not part of this SDK delivery.

The focused R8 evidence was consumed once, without rerun: `tests/integration/test_dim09_compensation_owner_service.py::test_dim09_owner_compensation_recovers_lost_submit_and_closes_only_from_receipt`, output `D:\temp\dim09-compensation-owner-r8-20261017`, 1 passed in 13.44 seconds. The frozen spec is `.codex-tmp/dim09-compensation-owner-host-spec-r8-full-20261017.json` (SHA-256 `5e46cd3a5a94fa8e6e3e56d9cd79d4779f1d9367cb98c1ef107c39414bc9f285`); raw result `fc1bf717a7d89230e4925536d500c73b974dffcd8335934f8d736a63e62d1a16`, stdout `97b78257b90495a3e9126dfedf69337a5dfad819fc642bcee352c5912615e57c`, and JUnit `b922e14dbd364dedf510531375d25748a00ee4c4b15b286bbc65244d81a1a90f`.

R8 exercises one synthetic three-leg group: the first leg fills, the second is rejected, and the third is not dispatched. The service records a durable compensation plan before its one submit; after the submit reply is dropped, recovery queries the original action rather than resubmitting. The close follows the singleton Risk order path. Closure requires the exact terminal CLOSE receipt, independently checked signed exposure at zero, settled compensation budget, and released reservation/latches. The business intent ID remains distinct from the trusted Risk intent ID. The test asserts one CLOSE provider write and normal shutdown.

Independent review `review_mig03_atomicity` returned GO_STATIC for the selected integrated candidate. R8 is local synthetic evidence; it does not complete formal DIM09. Third-leg zero-fill and partial-fill terminal outcomes, plus non-1 ratios and different multipliers, remain for the next focused matrix round. No formal tracker PASS is claimed here.

The selected SDK source and test paths are:

- `bt_api_py/service_components.py`
- `bt_api_py/service_execution_group.py`
- `bt_api_py/service_execution_group_client.py`
- `bt_api_py/service_execution_group_compensation_source.py`
- `bt_api_py/service_group_compensation_contracts.py`
- `bt_api_py/service_host.py`
- `bt_api_py/service_registry.py`
- `bt_api_py/service_risk_order_entry.py`
- `bt_api_py/service_runtime.py`
- `tests/integration/_dim09_three_leg_fixtures.py`
- `tests/integration/test_dim09_compensation_owner_service.py`
