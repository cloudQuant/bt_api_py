# Root review: AFI09 WebSocket authorization continuity

Review basis: direct instruction from Root in this task, received after inspecting the exact two-path delta.

> Root已读WS新增两路径：原握手JWT每投影重新decode、same-subject、rollback再active owner check且拒绝无额外read，接受窄修复。3directfunctions证据准确。可立即精确18path AFI09提交(当前parent50f6)，排除AFI05新routerhunk与其他WIP。给FXowner排队下一个AFIindex。新DB回归是faithful fake，勿标actualSQLAlchemyDBtest；全browser原证据保留不重复。

The reviewed delta is limited to `src/backend/app/api/service_gateway_ws.py` and `src/backend/tests/test_afi09_gateway_health_ws.py`. The original handshake `access-token` is decoded before every projection, the subject and accepted protocol remain fixed, and the prior read transaction is rolled back before checking the active persisted owner. Invalid/expired/revoked authorization closes with policy code 1008 before another Gateway worker read. The three focused function calls returned exit 0; they were direct `runpy` calls, not pytest. The revocation regression uses a faithful AsyncSession identity-map fake, not an actual SQLAlchemy database test. The browser chain was preserved and not rerun.

This is Root's review decision for the narrow authorization delta, not independent review and not a formal AFI09 PASS.
