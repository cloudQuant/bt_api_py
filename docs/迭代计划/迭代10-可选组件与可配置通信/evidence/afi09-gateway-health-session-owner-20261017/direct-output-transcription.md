# AFI09 local synthetic output transcript

Evidence level: LOCAL_SYNTHETIC_AFI09_UI_HTTP_SDK_GATEWAY_OWNER_INTEGRATION_ONLY.
This is not a formal AFI09 PASS. No real credentials, provider, exchange/network, or cross-machine test was used.

The two browser records below are author-captured output from the original run: the parent Python-function stdout and its nested Chromium child transcript. They are preserved verbatim as separate fenced output sections and remain distinct from the later three-function WebSocket authorization-continuity delta transcript. Neither browser record is pytest output.

The browser-enabled Python integration function was invoked directly, not by pytest. The original run context recorded parent exit 0 in about 45 seconds; no standalone machine-readable exit receipt or exact invocation command/function name was retained. The complete raw stdout is now preserved below. The browser child output later in this file is the separate captured Playwright transcript. The chain used the actual frontend route/page, local Uvicorn HTTP/WS routers, signed synthetic JWT and persisted-owner dependency, SDK ServiceRead clients, and the production GatewayOperatorDeployment owner boundary.

## Full browser-enabled direct-invocation stdout

Capture: `D:\source_code\ai-for-investor\src\backend\src\backend\.codex-tmp\afi09-session-owner-local-qa-20261017\gateway-health-browser-chain-final-r2.txt`  
Captured size/SHA-256: 1,481 bytes / `54ADC0861354B90910A9AB5AC62975B1D89E946B9212E37A3EF75A6CB55EA493`  
Filesystem last-write time: 2026-10-17 00:39:59 (local time).

~~~text
C:\anaconda3\Lib\site-packages\zmq\_future.py:718: RuntimeWarning: Proactor event loop does not implement add_reader family of methods required for zmq. Registering an additional selector thread for add_reader support via tornado. Use `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` to avoid this warning.
  self._get_loop()
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
AFI09 browser / HTTP / SDK clients / production GatewayOperator / recovery chain: PASS
~~~

This stdout establishes the final success marker and emitted warnings, but is not a process/exit receipt. The candidate pins in this archive describe the checkout at archive time; the preserved stdout does not itself bind them to an invocation-time HEAD/source snapshot. Do not treat the 00:38:53 candidate-pin observation as invocation-time binding unless its separate source/command receipt is available.

## Chromium child output, one test passed.

Capture: D:\source_code\ai-for-investor\src\backend\tests\.codex-tmp\afi09-session-owner-local-qa-20261017\gateway-health-browser-final.txt

~~~text

Running 1 test using 1 worker

  ok 1 [afi09-chromium] › e2e\tests\afi09-gateway-health.spec.ts:3:1 › Gateway health page reads an authorized service projection only on submit (2.1s)

  1 passed (7.6s)
[WebServer] (node:16352) Warning: The 'NO_COLOR' env is ignored due to the 'FORCE_COLOR' env being set.
[WebServer] (Use `node --trace-warnings ...` to show where the warning was created)
(node:72684) Warning: The 'NO_COLOR' env is ignored due to the 'FORCE_COLOR' env being set.
(Use `node.EXE --trace-warnings ...` to show where the warning was created)
~~~

## Direct Python-function output for two AFI processes and owner recovery.

Capture: D:\source_code\ai-for-investor\src\backend\.codex-tmp\afi09-session-owner-local-qa-20261017\two-afi-processes-direct-final.txt

~~~text
C:\anaconda3\Lib\site-packages\zmq\_future.py:718: RuntimeWarning: Proactor event loop does not implement add_reader family of methods required for zmq. Registering an additional selector thread for add_reader support via tornado. Use `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` to avoid this warning.
  self._get_loop()
AFI09 session owner / two AFI processes / recovery: PASS
~~~

## Direct ServiceHost two-worker composition output.

Capture: D:\source_code\ai-for-investor\src\backend\.codex-tmp\afi09-session-owner-local-qa-20261017\servicehost-two-worker-direct.txt

~~~text
C:\anaconda3\Lib\site-packages\zmq\_future.py:718: RuntimeWarning: Proactor event loop does not implement add_reader family of methods required for zmq. Registering an additional selector thread for add_reader support via tornado. Use `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` to avoid this warning.
  self._get_loop()
AFI09 session owner / recovery / two-worker ServiceHost composition: PASS
~~~

## Direct authorized WebSocket projection output.

Capture: D:\source_code\ai-for-investor\src\backend\.codex-tmp\afi09-session-owner-local-qa-20261017\authorized-websocket-direct.txt

~~~text
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
AFI09 authorized Gateway health WebSocket projection: PASS
~~~

## Direct production GatewayOperator / two-client recovery output.

Capture: D:\source_code\ai-for-investor\src\backend\src\backend\.codex-tmp\afi09-session-owner-local-qa-20261017\two-afi-processes-production-operator-final-r3.txt  
Captured size/SHA-256: 1,244 bytes / `91CAE1C4F30C0B8F24D838AF43E75E35916A636CEC32BBB4C2DABE84A48EB465`.

~~~text
C:\anaconda3\Lib\site-packages\zmq\_future.py:718: RuntimeWarning: Proactor event loop does not implement add_reader family of methods required for zmq. Registering an additional selector thread for add_reader support via tornado. Use `asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())` to avoid this warning.
  self._get_loop()
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
AFI09 production GatewayOperator / two authenticated HTTP clients / AFI restart / explicit owner recovery: PASS
~~~

## Retained GatewaySessionOwner focused author pytest output.

Capture: D:\source_code\ai-for-investor\src\backend\.codex-tmp\afi09-session-owner-local-qa-20261017\sdk-session-owner-pytest.txt

~~~text
..........                                                               [100%]
10 passed in 0.29s
~~~

## Retained GatewaySessionOwner Ruff output.

Capture: D:\source_code\ai-for-investor\src\backend\.codex-tmp\afi09-session-owner-local-qa-20261017\sdk-session-owner-ruff.txt

~~~text
All checks passed!
~~~

## Limits

The browser page-opening check observed zero service-gateway requests; explicit submit issued the health HTTP read. The browser WS established an authenticated socket and observed two finite health projections (initial and explicit refresh). The separate AFI process check observed two distinct client PIDs and a replacement client using the same owner session; owner-thread recovery changed the session generation while resolver and provider-factory counts stayed at one.

Warnings in the exact captures are existing Windows PyZMQ Proactor selector-thread support, Pydantic protected-namespace, and Node color-environment warnings. They did not fail the captured runs.

The previously captured browser and owner integration runs were not rerun while assembling the initial archive; the separately requested narrow WebSocket authorization delta validation is recorded below.

## WebSocket authorization-continuity patch delta

Root review identified that the accepted socket rechecked persisted ownership but reused the decoded handshake `TokenPayload` indefinitely. The source patch now decodes the immutable original `access-token` handshake protocol before each projection, requires the exact accepted protocol and original subject, and ends the prior database read transaction with `AsyncSession.rollback()` before each persisted-owner lookup. Expiry, decode failure, subject/protocol mismatch, or persisted-owner revocation closes with policy code 1008 before another Gateway worker read. WebSocket messages still accept only `refresh` and `close`.

Only these two AFI source paths changed for this delta:

- `src/backend/app/api/service_gateway_ws.py`: 4,993 bytes / SHA-256 `96B9027EDB4A2F10FA75B967718B52644D3A0E3F96E47BB3BD4D694FE19A1335`.
- `src/backend/tests/test_afi09_gateway_health_ws.py`: 12,682 bytes / SHA-256 `4BFD191040A93944DD354B913022790CCF0DAAC14DE85BE2046763CB7E0575B8`.

The direct focused invocation was `python -X utf8 -c "import runpy; namespace = runpy.run_path('tests/test_afi09_gateway_health_ws.py'); namespace['test_afi09_gateway_health_ws_is_an_authorized_projection_only'](); namespace['test_afi09_gateway_health_ws_revalidates_expiry_before_next_worker_read'](); namespace['test_afi09_gateway_health_ws_rechecks_owner_after_fresh_read_transaction'](); print('AFI09 focused WebSocket authorization tests: 3 direct function calls passed')"`. It returned exit 0 with one existing Pydantic protected-namespace warning:

~~~text
C:\anaconda3\Lib\site-packages\pydantic\_internal\_fields.py:149: UserWarning: Field "model_id" has conflict with protected namespace "model_".

You may be able to resolve this warning by setting `model_config['protected_namespaces'] = ()`.
  warnings.warn(
AFI09 focused WebSocket authorization tests: 3 direct function calls passed
exit_code=0
~~~

This is a direct function invocation, not a pytest result. Two pytest attempts failed in repository `conftest.py` before collection: the default-encoding run hit a GBK `UnicodeDecodeError` while Starlette read configuration; the UTF-8 run then hit missing `purgedcv` while importing `app.main`. No test collection/pass is claimed for those attempts. The expiry regression uses a controlled clock/decoder and proves one first projection followed by 1008 with no second worker read; the revocation regression simulates an AsyncSession identity-map cache and proves rollback/fresh lookup rejects deactivation before another worker read. The previously archived Chromium/browser chain was not rerun.
