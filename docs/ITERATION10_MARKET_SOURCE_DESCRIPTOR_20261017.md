# Iteration 10 market source descriptor

This round updates the Base and Gateway gitlinks for optional authenticated
market-source identity negotiation. Existing subscribe requests and responses
retain their prior shape. A caller requesting `require_source_descriptor=true`
receives a strict descriptor bound to its key, stream, Hub epoch and active
provider connection generation; unsupported capability fails explicitly.

The descriptor reports provider source and clock identity. It does not certify
clock quality, calibration, freshness, or a real-provider acceptance result.

- Base dev: `6f314acda4cec224843d63ca5bc07d8cea5bdf46`, protocol and focused test only.
- Gateway dev: `a60a1eb4331e0bf733164cf408d120f02487fc73`, Hub and focused test only.
- Author checks: Base 36 passed with one existing configuration warning;
  Gateway Hub/history 51 passed without warnings. A final Gateway test formatting
  change was reviewed without repeating tests.
- Independent static review: GO_SOURCE_DESCRIPTOR_STATIC; the four source/test
  file pins matched. The reviewer did not run tests.

CTP shared-session lifecycle, SDK consumer negotiation and the normal Backtrader
three-leg service path are separate pending rounds. This component delivery
does not add a formal Iteration 10 PASS.
