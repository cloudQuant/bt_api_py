# Runtime operator deployment templates

These files contain placeholders only. Copy them into an operator-controlled
deployment and replace every path, identity, route, delegation and key reference.
The example selects Gateway and Monitor; set `gateway` or `monitor` to `null`
only when the Runtime configuration has no Gateway units or disables Monitor,
respectively. An empty Runtime `units` list is permitted for management only.

`runtime_operator.example.json` is the strict deployment envelope. The referenced
Runtime file follows the existing `bt_api_runtime/v1` contract. Its operator
names, roles and unit scopes are the authority used for the token mapping;
`subject_tokens.example.json` shows the separate subject-token file. Generate
strong unique tokens independently, store the actual token file and each CURVE
key file as a regular owner-owned file with mode 0600, and use absolute paths.
The loader refuses symlinks in protected input paths. Give clients only their own
tokens. The template token, endpoints and identities are not credentials.

Each Gateway route must name exactly one configured Gateway unit and its real
strategy UUID. Obtain the delegation independently from the trusted Gateway
operator and install it at Gateway; putting its fields in this file does not
create a server-side grant. `client_config_fingerprint` is the SHA-256 returned
by `bt_api_py.service_config.parse_client_config(client_config).fingerprint()`
for that route's complete client config; recalculate it after any config edit.
Each `curve_keys` object supplies the server's public key and the runtime
client's public and secret key files. The Monitor config uses the same layout
but a service identity and a separate, explicitly bound account scope.

Run in the selected Python environment:

```sh
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m bt_api_py.runtime_operator_cli validate --config /absolute/path/runtime-operator.json
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m bt_api_py.runtime_operator_cli run --config /absolute/path/runtime-operator.json
```

`validate` reads only the public envelope and Runtime config; it does not read
keys or tokens, open services, or create state. `run` opens a loopback Runtime
control listener and does not start strategies. The listening message does not
assert Gateway, account, trading, or whole-system readiness. SIGTERM or SIGINT
requests close. For a deployment that needs cooperative shutdown without a
console, run with `--enable-owner-stop-file`. The `runtime_control_stop_file`
line announces the current marker's basename. Create an empty regular file
with that name under the configured Runtime `home`, with permissions restricted
to the Runtime OS user (mode `0600` on POSIX, owner-only DACL on Windows).
Wait for `runtime_control_closed` and exit status `0` to confirm cleanup.

If cleanup is uncertain, the process retains its resources and waits for
another explicit request. With file requests enabled, use the new basename
printed for that retry. Each marker applies to one request of that process.
`--enable-stop-file` separately enables the supervised strategy stop-file
channel. Monitor events are observational only.
