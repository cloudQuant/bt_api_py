# SimNow CTP Gateway profile (native writes enabled, SimNow only)

`ctp-simnow-trading` is the installed operator profile for the shared, account-level Gateway that strategies reach through their own `config.yaml` (iteration 63, D63-08). It uses the same protected resource bundle as `ctp-local-readonly` (see that document for the file, permission and identity rules) with two differences:

- `settings.write_enabled` may be `true`. Writes are refused at startup with `GATEWAY_WRITE_REQUIRES_BROKER_CONFIRMATION` unless the account key's `venue` and `environment` are both `simnow` (INV63-08: no Gateway writes on other accounts until the broker has confirmed the programme-trading reporting for a shared terminal, D63-22).
- The bundle must contain a `returns` object: `{"instruments": [<MarketInstrumentKey>...], "query_interval_seconds": <seconds>}`. Order dispatch is ready only while these return queries are healthy.
- When `market` is configured, the bundle must also contain a `clock` object: `{"servers": [<SNTP host>...], "max_error_bound_ms": <0-500>, "max_sample_validity_seconds": <seconds>, "refresh_interval_seconds": <seconds>}`. Every dispatch is qualified against a fresh quote and the Gateway's own SNTP-verified clock (decision B), so a trading bundle with markets but no clock is refused at startup.
- Optional `controls` object for emergency stop (FREEZE, STOP_NEW, DRAIN and resume): `{"operators": [{"identity": <application_client identity>, "curve_public_key": ..., "resources": [<service.direct_write grant limited to direct.control.* operations>], "account_scope_allowed": <bool>, "strategies": [<strategy refs>]}], "upgrade_controls_from_v9": <bool>}`. Operators receive no trading grants. The service config must then set `control_enabled: true`. `upgrade_controls_from_v9` permits the one-way journal upgrade to schema v10, which cannot be reverted.

- Optional `strategy_ledger` object, `{"upgrade_from_v10": true}`, together with `strategy_ledger_enabled: true` in the operator config. It explicitly upgrades the journal from v10 to v11; the journal never downgrades. Closing orders (`close`, `close_today`, `close_yesterday`) first read the strategy's own close capacity from the Gateway (`direct.strategy_position`, `direct.strategy_close_capacity`). Without the ledger every close is refused before sending.
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
