# Iteration 51 query origins and deadlines

The CTP package on existing dev is a7f69954a6e898d03044b3e8ff6ba67ea3b41a7b
(parent 574cd2dd495a3c675d7cd23fcbf23a797a08c04d). This parent round updates only
that gitlink and this record; unrelated gateway/evidence changes remain intact.

The package records whitelisted first/latest query completion origins and
accepts an optional absolute deadline for instrument/depth queries. Legacy
error outcomes remain compatible. Managed lock/rate waits are bounded; native
request calls can only be checked before and after return, not interrupted.

Root source QA: 40 passed, zero failed/errors/skips, exit zero, six source pins
unchanged, one existing pytest configuration warning. Independent static review
returned GO. The Backtrader composition independently passed 78 source tests.
These checks use real SDK/SPI with synthetic request leaves and deny network
connections. They establish no provider/account or formal acceptance.

The initial SDK QA ran zero tests because a relative spec path was invalid after
changing the child's cwd. The successful call used its absolute path without
changing candidate code or the runner. Original results remain preserved.

Detailed package QA is docs/iteration51-query-origins-20261027.json in that
package commit. Parent base is 1607bed4a23f6a0b414f59b5f71c8c34b37b1aaf.
No push or new development worktree was performed.
