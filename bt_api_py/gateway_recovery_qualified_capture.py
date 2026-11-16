"""Owner-local pairing of raw recovery pages and clock qualifications.

The original capture closure remains responsible for provider/session binding.
These immutable values provide neither source authentication nor a wire format.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from bt_api_base.market_recovery_observation import RecoveryObservationPage
from bt_api_gateway.recovery_observation_clock import RecoveryObservationQualification

from bt_api_py.gateway_recovery_clock_qualification import (
    GatewayRecoveryClockQualification,
    GatewayRecoveryClockQualifier,
)

_MAX_INT = (1 << 63) - 1


class GatewayQualifiedRecoveryCaptureError(RuntimeError):
    """Finite capture diagnostic without source or exception details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _validate_page(page):
    if type(page) is not RecoveryObservationPage:
        raise ValueError('INVALID_PAGE')
    page.__post_init__()
    for observation in page.observations:
        observation.__post_init__()
        for member in observation.members:
            member.__post_init__()
            member.key.__post_init__()
            member.key.instrument.__post_init__()


@dataclass(frozen=True, slots=True)
class GatewayQualifiedRecoveryPage:
    """One original page with exactly one immutable result per original row."""

    page: RecoveryObservationPage
    qualifications: tuple[RecoveryObservationQualification, ...]

    def __post_init__(self):
        try:
            _validate_page(self.page)
            if (type(self.qualifications) is not tuple
                    or len(self.qualifications) != len(self.page.observations)):
                raise ValueError
            for observation, qualification in zip(self.page.observations, self.qualifications, strict=True):
                if (type(qualification) is not RecoveryObservationQualification
                        or qualification.observation is not observation):
                    raise ValueError
                qualification.__post_init__()
        except Exception:
            raise ValueError('INVALID_QUALIFIED_RECOVERY_PAGE') from None


def _unknown(observation, reason):
    return RecoveryObservationQualification(observation, 'UNKNOWN', reason)


class GatewayQualifiedRecoveryCapture:
    """Capture and qualify on the original owner, without I/O or retries."""

    def __init__(self, capture, *, qualifier=None):
        if (not callable(capture) or (qualifier is not None
                and type(qualifier) is not GatewayRecoveryClockQualifier)):
            raise ValueError('INVALID_QUALIFIED_CAPTURE_CONFIGURATION')
        self._pid = os.getpid()
        self._owner = threading.current_thread()
        if qualifier is not None:
            try:
                if (qualifier._pid != self._pid or qualifier._owner is not self._owner
                        or type(qualifier._max_interval_ns) is not int
                        or not 1 <= qualifier._max_interval_ns <= _MAX_INT
                        or not qualifier._bound()):
                    raise ValueError
            except Exception:
                raise ValueError('INVALID_QUALIFIED_CAPTURE_CONFIGURATION') from None
        self._capture = self._original_capture = capture
        self._qualifier = self._original_qualifier = qualifier
        self._qualifier_max_interval = None if qualifier is None else qualifier._max_interval_ns
        self._source_epoch = None

    def _check_owner(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._owner:
            raise GatewayQualifiedRecoveryCaptureError('CAPTURE_OWNER_CHANGED')

    def _check_capture(self):
        if self._capture is not self._original_capture:
            raise GatewayQualifiedRecoveryCaptureError('CAPTURE_BINDING_CHANGED')

    def _qualifier_bound(self):
        qualifier = self._original_qualifier
        if self._qualifier is not qualifier:
            return False
        if qualifier is None:
            return True
        try:
            return (type(qualifier) is GatewayRecoveryClockQualifier
                    and qualifier._pid == self._pid and qualifier._owner is self._owner
                    and type(qualifier._max_interval_ns) is int
                    and qualifier._max_interval_ns == self._qualifier_max_interval
                    and qualifier._bound() is True)
        except Exception:
            return False

    def capture(self, after_sequence, limit):
        self._check_owner()
        self._check_capture()
        if (type(after_sequence) is not int or not 0 <= after_sequence <= _MAX_INT
                or type(limit) is not int or not 1 <= limit <= 256):
            raise GatewayQualifiedRecoveryCaptureError('CAPTURE_INVALID_ARGUMENT')
        try:
            page = self._original_capture(after_sequence, limit)
        except Exception:
            raise GatewayQualifiedRecoveryCaptureError('CAPTURE_FAILED') from None
        self._check_owner()
        self._check_capture()
        try:
            _validate_page(page)
            if (page.owner_pid != self._pid or page.after_sequence != after_sequence
                    or len(page.observations) > limit):
                raise ValueError
        except Exception:
            raise GatewayQualifiedRecoveryCaptureError('CAPTURE_INVALID_PAGE') from None
        if self._source_epoch is not None and page.source_epoch != self._source_epoch:
            raise GatewayQualifiedRecoveryCaptureError('CAPTURE_SOURCE_CHANGED')
        self._source_epoch = page.source_epoch
        results = []
        binding_failed = False
        qualification_failed = False
        for observation in page.observations:
            if observation.state == 'UNKNOWN':
                results.append(_unknown(observation, 'SOURCE_UNKNOWN'))
                continue
            if not self._qualifier_bound():
                binding_failed = True
                results.append(_unknown(observation, 'OWNER_CHANGED'))
                continue
            if self._original_qualifier is None:
                results.append(_unknown(observation, 'CLOCK_UNAVAILABLE'))
                continue
            try:
                result = self._original_qualifier.qualify(
                    observation.coverage_started_monotonic_ns,
                    observation.coverage_through_monotonic_ns)
                if type(result) is not GatewayRecoveryClockQualification:
                    raise ValueError
                results.append(RecoveryObservationQualification(
                    observation, result.status, result.reason,
                    result.start_utc_lower_ns, result.start_utc_upper_ns,
                    result.end_utc_lower_ns, result.end_utc_upper_ns,
                    result.source_epoch, result.first_sample_sequence, result.last_sample_sequence))
            except Exception:
                qualification_failed = True
                results.append(_unknown(observation, 'HISTORY_UNAVAILABLE'))
        self._check_owner()
        self._check_capture()
        if binding_failed or not self._qualifier_bound():
            results = [_unknown(observation, 'SOURCE_UNKNOWN' if observation.state == 'UNKNOWN'
                                else 'OWNER_CHANGED') for observation in page.observations]
        elif qualification_failed:
            results = [_unknown(observation, 'SOURCE_UNKNOWN' if observation.state == 'UNKNOWN'
                                else 'HISTORY_UNAVAILABLE') for observation in page.observations]
        return GatewayQualifiedRecoveryPage(page, tuple(results))

    __call__ = capture
