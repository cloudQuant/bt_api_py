Iteration 10 CTP quote-v2 market provider round

Scope: expose the CTP provider's actual held market-source identity through the existing provider-neutral `MarketSourceDescriptor` contract. The SDK `ServiceMarketClient` opts in with `require_source_descriptor=True`; ordinary clients retain the legacy subscribe request/reply shape. The CTP provider owns one native market stream for its active keys, binds the descriptor to that exact key and session, and fences ambiguous failed-add state until typed final shutdown.

Commits: Base descriptor protocol `6f314acda4cec224843d63ca5bc07d8cea5bdf46`; Gateway optional descriptor response `a60a1eb4331e0bf733164cf408d120f02487fc73`; CTP provider `4d277cbdee2068e7ddff24d95fd33d94f0ba2d94`. This SDK round is based on parent HEAD `a1502d0586b93936424993c92f965bc8227b2348` and updates only the CTP gitlink plus the listed ServiceMarket files and this record.

Author verification already completed for this candidate: CTP focused provider tests 8 passed; SDK ServiceMarket unit tests 13 passed; local synthetic GatewayOperator/ServiceHost/Curve integration 1 passed. Static source review returned GO_SOURCE_REVIEW_ONLY. These are local synthetic/source checks, not actual CTP login or exchange acceptance. Native `UnSubscribeMarketData` completion means the local API accepted the request; it does not prove an exchange-side unsubscribe acknowledgement. No Root QA or real account was run for this delivery.

This slice does not claim formal MIG01 or SCN02 completion.
