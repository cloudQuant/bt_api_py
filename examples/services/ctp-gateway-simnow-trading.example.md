# SimNow CTP Gateway profile (native writes enabled, SimNow only)

`ctp-simnow-trading` is the installed operator profile for the shared, account-level Gateway that strategies reach through their own `config.yaml` (iteration 63, D63-08). It uses the same protected resource bundle as `ctp-local-readonly` (see that document for the file, permission and identity rules) with two differences:

- `settings.write_enabled` may be `true`. Writes are refused at startup with `GATEWAY_WRITE_REQUIRES_BROKER_CONFIRMATION` unless the account key's `venue` and `environment` are both `simnow` (INV63-08: no Gateway writes on other accounts until the broker has confirmed the programme-trading reporting for a shared terminal, D63-22).
- The bundle must contain a `returns` object: `{"instruments": [<MarketInstrumentKey>...], "query_interval_seconds": <seconds>}`. Order dispatch is ready only while these return queries are healthy.
- When `market` is configured, the bundle must also contain a `clock` object: `{"servers": [<SNTP host>...], "max_error_bound_ms": <0-500>, "max_sample_validity_seconds": <seconds>, "refresh_interval_seconds": <seconds>}`. Every dispatch is qualified against a fresh quote and the Gateway's own SNTP-verified clock (decision B), so a trading bundle with markets but no clock is refused at startup.
- Optional `controls` object for emergency stop (FREEZE, STOP_NEW, DRAIN and resume): `{"operators": [{"identity": <application_client identity>, "curve_public_key": ..., "resources": [<service.direct_write grant limited to direct.control.* operations>], "account_scope_allowed": <bool>, "strategies": [<strategy refs>]}], "upgrade_controls_from_v9": <bool>}`. Operators receive no trading grants. The service config must then set `control_enabled: true`. `upgrade_controls_from_v9` permits the one-way journal upgrade to schema v10, which cannot be reverted.
  - Optional `controls.drain`, `{"upgrade_journal": true}`, together with `account_extensions_enabled`, `strategy_origin_enabled` and `control_cancel_enabled` (and the dispatch and cancel options) set to `true` in the operator config. Applying DRAIN already blocks new orders at any journal schema; cancelling one working order of a DRAIN cause on the operator's request (`direct.control.drain_target`) needs journal schema 17 and a trusted instrument identity for every strategy order. With this section the journal is explicitly upgraded step by step to 17 when asked (the features of the intermediate schemas stay off), and every strategy order must name one of the bundle's `returns.instruments` by `exchange_id` and `symbol`; any other order is refused with `STRATEGY_ORIGIN_UNAVAILABLE`. The journal never downgrades.
  - Optional `controls.lease_drain`, `{"strategies": [<strategy refs>], "expiry_seconds": <number>, "automatic_resume": true}` (off unless present; the control and cancel options must be enabled). `expiry_seconds` is mandatory and has no default: once the lease of a listed strategy has stayed expired that long (its process died or lost the Gateway), the Gateway applies a DRAIN cause for that strategy, fences its new orders and, at journal schema 17, cancels its working orders. A strategy that registers again before the duration has passed gets no cause. A reconnection alone never clears a cause: it is released only when the strategy has registered again under the same run id, has read and acknowledged its whole event stream, has no unresolved order or cancel, and the Gateway's return stream of the current session is complete and quiet (no gap, no waiting callback, no unattributed return on the strategy's instruments). With `automatic_resume` true (the default when the key is absent) the Gateway releases the cause itself; with false an operator's `resume` does, under the same conditions (otherwise it answers `RECOVERY_REQUIRED`).

- Optional `strategy_ledger` object, `{"upgrade_from_v10": true}`, together with `strategy_ledger_enabled: true` in the operator config. It explicitly upgrades the journal from v10 to v11; the journal never downgrades. Closing orders (`close`, `close_today`, `close_yesterday`) first read the strategy's own close capacity from the Gateway (`direct.strategy_position`, `direct.strategy_close_capacity`). Without the ledger every close is refused before sending.
  - With the ledger enabled the Gateway admits no order (opens included) until it has a baseline: it reads the complete account at a cut it can prove and records an empty baseline (fresh journal, flat account), a replayed one (the journal's own fills explain the account exactly), or the operator's declaration. `strategy_ledger.external_baseline` (`{"operator": ..., "operation_id": ..., "positions": [{"instrument": {...}, "direction": "long"|"short", "today_quantity": "0", "yesterday_quantity": "1"}]}`) declares holdings that predate the journal; it is written only when it equals the account read, on an empty journal. While there is no baseline the operator log names the reason.
  - Optional `strategy_ledger.account_comparison`, `{"upgrade_from_v11": true, "refresh_seconds": 30}`, together with `account_extensions_enabled: true` in the operator config. The journal is at schema 12 (explicitly upgraded from 11 when asked). At most every `refresh_seconds` (1 to 120) and only at a cut it can prove, the Gateway compares per instrument the strategies' ledgers plus the declared external holdings with the account, and logs every change of the outcome: `MATCH`, `MISMATCH` with the difference (a position nobody in the journal explains, for example a manual trade), or not comparable. It reads the account once per refresh; it does not stop trading on a mismatch.
- Optional `strategy_financial` object: the Gateway computes each strategy's own cash and value and serves them over `direct.strategy_financial` (`broker.get_strategy_cash(strategy)` / `get_strategy_value(strategy)` in a strategy whose config sets `strategy_finance_currency`). It needs `account_facts_financial_enabled: true` in the operator config (journal schema 14 or later). Shape: `{"rates_file": "/absolute/path/strategy-rates.yaml", "currency": "CNY", "budgets": {"<strategy ref>": "600000.00"}, "adjustments": [{"id": "2026-11-12-a", "strategy": "<strategy ref>", "amount": "-100000.00"}], "refresh_seconds": 60, "price_max_age_seconds": 180}`; the last three keys are optional.
  - `budgets` is the fixed initial amount of each strategy (two decimals at most). A strategy that is not listed has no cash figure; there is no default and no share of the account balance. Every key must be the strategy of a client of this bundle.
  - The initial amount of a strategy cannot be changed later: the Gateway refuses to continue the strategy's record with another initial amount and reports `INITIAL_BUDGET_CHANGED`. To give a strategy more or less money, add an entry to `adjustments` (its own `id`, the strategy, a signed amount) and restart the Gateway. Entries stay in the list; removing one takes its amount back at the next start.
  - `rates_file` is one local file for the whole account with the contract multiplier, the margin ratio per side and the commission per kind of trade, by product and optionally by contract (template: `strategy-rates.example.yaml`). It is read once at start; a changed file takes effect at the next start. The numbers are yours: the Gateway does not read the broker's rates, so cash and value are estimates that are as good as this file.
  - Value = initial amount + adjustments + realized profit (each close against the strategy's own average open price, long and short kept apart) + unrealized profit at the last price − commissions. Cash = value − margin of what is held − margin and opening commission of working opening orders at their limit price.
  - The last price is the one the Gateway's own market connection received, so it exists only for instruments that some strategy subscribes to through this Gateway. A strategy that holds a position gets a new figure at least every `refresh_seconds`; a price older than `price_max_age_seconds` is not used.
  - The figure is withheld (the strategy's `get_strategy_cash` raises `GATEWAY_STRATEGY_CASH_UNAVAILABLE`) rather than guessed whenever something is missing; the Gateway log names the reason once per change: no rate for a traded product (`RATES_UNAVAILABLE`), no usable price for a held instrument (`VALUATION_PRICE_UNAVAILABLE`), a trade on one of the strategy's instruments that is not attributed to any strategy (`UNATTRIBUTED_TRADE_ON_INSTRUMENT`), an incomplete return stream (`RETURNS_NOT_CONTINUOUS`), a working opening order without a limit price, or a history that does not add up (`POSITION_UNDERFLOW`: a close of something the strategy's own recorded trades never opened, for example a position that was declared in the ledger baseline instead of traded).
  - Each change of a strategy's figure stores one row in the journal; with positions held, that is one row per strategy and `refresh_seconds` while prices move. Keep `refresh_seconds` no lower than the strategies need.
- `bt-gateway-operator --log-level INFO` writes the Gateway packages' log lines to stderr with a timestamp. Without the option only warnings and errors appear: a missing ledger baseline or an account mismatch is shown, a ready baseline or a matching comparison is not. The lines carry codes, counts and references, never credentials or account values.
- Optional `market.dispatch_max_latency_seconds` (default 2) bounds the age of the quote a dispatch may use. Values above 2 s, up to 5 s, are accepted only for a SimNow account (user decision 2026-11-10: SimNow ticks lag 1–3.5 s); any other account keeps the 2 s limit.
- Optional `returns.query_timeout_seconds` (default 0.5, at most 5). On SimNow, order queries take longer than 0.5 s once the account has orders that day; use 3.

Order dispatch follows user decision B (an admitted order is in flight once its single claim succeeds; a revocation that precedes the claim stops it) and settlement confirmation follows decision A (one durable daily attempt, success only on a trusted readback, an unclear result is read back and never resent). Neither profile proves that the configured fronts, credentials, grants or journal are genuine, and these files do not authorise any account operation.

Storage trust (user decision, 2026-11-09: OS-enforced isolation). Run the Gateway as its own OS user, and run every strategy as a different user. Before the Registry, session guard or journal opens, startup refuses with `GatewayStorageTrustError` in these cases:

- A storage directory (guard root, journal, CTP flow root, Registry, account-read and identity databases) or any entry in it is not owned by the Gateway user, is writable by group or others, or is a symbolic link.
- An ancestor directory is writable by others without the sticky bit, or is owned by a user other than root or the Gateway user.

Keep strategy state directories outside the Gateway's storage directories, and use `umask 077` for the Gateway process. The check cannot detect a second process running as the Gateway user or a privileged actor. Those remain deployment responsibilities.

Offline check (parses the YAML and resolves the installed profile; it does not read the resource file, credentials or network):

```bash
bt-gateway-operator --config ctp-gateway-simnow-trading.example.yaml --dry-run
```

Start (after provisioning the protected bundle; connects to the configured SimNow fronts):

```bash
BT_API_CTP_GATEWAY_RESOURCES_FILE=/PRIVATE/DIRECTORY/resource.json \
  bt-gateway-operator --config ctp-gateway-simnow-trading.example.yaml
```

SIGINT/SIGTERM stop the Gateway through its owner; stopping a strategy never stops the Gateway, and stopping the Gateway never cancels or re-sends a strategy's orders. While the Gateway is unreachable, strategies wait or refuse; they never fall back to direct mode.

## Restart after an unclean exit (session reconciliation)

If the Gateway process dies without closing (SIGKILL, power loss or a crash), its account-session record stays `active`. Every later start then fails with `SESSION_RECONCILIATION_REQUIRED`. Waiting does not clear it, because elapsed time never grants a takeover. Reconcile explicitly before restarting (user decision, 2026-11-10):

```bash
BT_API_CTP_GATEWAY_RESOURCES_FILE=/PRIVATE/DIRECTORY/resource.json \
  bt-gateway-operator --config ctp-gateway-simnow-trading.example.yaml \
  --reconcile-session --operator <your operator id> --reason "<what happened>"
```

The command never starts the Gateway or connects to the venue. It works in this order:

1. It runs the same storage-isolation check as startup.
2. It takes the account-session OS lock. If another Gateway still holds the lock, it refuses with `SESSION_ALREADY_OWNED`.
3. It reads the command journal. It refuses with `SESSION_RECONCILIATION_BLOCKED` while any place or cancel command is `DISPATCHING`/`UNKNOWN`, or any settlement confirmation is `ATTEMPTING`/`UNKNOWN`. The refusal prints the counts.
4. It appends an audit entry to `account-<digest>.reconciliations.jsonl` in the guard directory (mode 0600). The entry records operator, reason, time, the abandoned generation and those counts.
5. Only then does it close the record.

It prints `RECONCILED` (exit 0) when it closed a record and `NOT_REQUIRED` (exit 0) when nothing was recorded active. A refusal exits with 3 and a configuration error with 2.

A blocked reconciliation means a command may have reached the venue without a recorded outcome. Check the account at the venue before deciding what to do. This command offers no override.

## Emergency: cancelling working orders when the Gateway cannot serve (cancel-only start)

Use this when strategies cannot reach the Gateway, or it must not accept orders, and the account still has working orders that this Gateway placed (user decision D63-58, 2026-11-11).

```bash
BT_API_CTP_GATEWAY_RESOURCES_FILE=/PRIVATE/DIRECTORY/resource.json \
  bt-gateway-operator --config ctp-gateway-simnow-trading.example.yaml \
  --cancel-only --operator <your operator id> --reason "<what happened>" --log-level INFO
```

What it does, in this order:

1. It starts with the same storage checks, account-session lock, identity and journal as a normal start. If the previous Gateway is still alive, the lock refuses it; after an unclean exit run `--reconcile-session` first, as above.
2. Before it serves any request it stores an account-scope DRAIN cause (`cancel-only:<run id>`). From then on every new order of every strategy is refused with `CONTROL_DRAIN`. Strategies may still cancel their own orders.
3. It waits until the return stream of the session is complete, so that orders that already ended are known.
4. It cancels, one at a time, every order of this journal that is still working. An order that already ended is not cancelled. A cancel that was sent is never sent twice.
5. It prints one line `{"cancel_only": {...}}` with the counts and stops. Exit code 0 means every order of the journal was observed terminal (`COMPLETE`). Exit code 3 means it was not (`INCOMPLETE`, after 120 seconds) or the mode was not available (`UNAVAILABLE`).

Limits you must know before relying on it:

- It needs journal schema 17 (`controls.drain`), and the control and cancel options enabled. Below that it prints `UNAVAILABLE` with `CANCEL_ONLY_REQUIRES_JOURNAL_17`: the DRAIN cause is stored, nothing is cancelled.
- It only knows orders recorded in this journal. Orders placed by hand or by another program are not touched.
- An order whose dispatch outcome is unknown (`DISPATCHING`/`UNKNOWN`) is not cancelled blindly. It keeps the run `INCOMPLETE`.
- The DRAIN cause stays active after the run, also across restarts. A later normal start admits no new order until an operator resumes the cause (`direct.control.resume` at account scope). This is deliberate: trading resumes only by an explicit decision.
- A start that fails after step 2 also leaves the cause active.

Manual procedure, for every case the command does not cover (it refuses to start, it reports `UNAVAILABLE` or `INCOMPLETE`, the journal is damaged, or the orders were not placed through this Gateway):

1. Make sure no Gateway process for this account is running, so that two parties do not act on the same orders.
2. Log in to the futures company's own client with the same account and cancel the working orders there. Note the time and the orders you cancelled.
3. If you also close positions by hand, note them too: the strategy ledger does not assign manual trades to any strategy.
4. Before the next normal start, run `--reconcile-session` if the previous exit was unclean. After the start, check the account comparison in the log (with `strategy_ledger.account_comparison` enabled) and resume the DRAIN cause only when the orders and positions are as you expect.

The command authorizes nothing new at the venue: it uses the same account session as a normal start and sends cancel requests only.
