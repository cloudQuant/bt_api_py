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
