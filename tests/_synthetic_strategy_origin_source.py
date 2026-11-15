"""Test-only dynamic source for exercising consumers' refusal paths.

This fake cannot establish claim coverage or serve as a production source.
Tests must explicitly install it in the SDK module under test.
"""


class SyntheticStrategyOriginSource:
    def __init__(self, *, resolve, recheck):
        self._resolve = resolve
        self._recheck = recheck
        self._closed = False

    def resolve(self, context, order):
        if self._closed:
            raise RuntimeError('SYNTHETIC_SOURCE_CLOSED')
        return self._resolve(context, order)

    def recheck(self, instrument):
        return not self._closed and self._recheck(instrument) is True

    def close(self):
        self._closed = True
