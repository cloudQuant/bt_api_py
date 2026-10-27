# Iteration 51: CTP commission request stall regression

The CTP package `dev` commit `f6e0a06da5fb28150c0e1faf650e7913c8c5c355`
adds a focused synthetic test for a synchronous commission request that does
not return before its deadline. The real TraderClient and session-lease logic
are exercised with a fake native API leaf held behind an event barrier.

The author and Root each ran the focused test successfully (1 passed; the
environment emitted its existing pytest configuration warning). An independent
static review confirmed the client bindings and thread cleanup. The test shows
that the deadline cannot interrupt the entered synchronous call: while it is
held, same-session queries and a managed write lease are refused. Releasing the
barrier allows the timed-out query to return and subsequent operations to
recover.

This is synthetic SDK evidence, not a vendor-native timing guarantee, a real
account acceptance, or proof of Backtrader refresh, shutdown, or order outcome
behavior. It changes no production SDK code. The synchronous native-call
limitation remains relevant to the Iteration 51 acceptance plan.
