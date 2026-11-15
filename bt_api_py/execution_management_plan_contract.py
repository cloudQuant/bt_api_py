"""Pure bounded disclosure of original built-in plan declarations.

No scheduler, extension, or strategy code is loaded or executed. This is
neither current-build attestation nor a recovery or dispatch authorization.
"""
import hashlib
import json
import re
from dataclasses import asdict

from bt_api_base.market_protocol import MarketInstrumentKey

MAX_PLAN_BYTES = 131072
MAX_PLAN_AUDIT_RESULT_BYTES = 2 * MAX_PLAN_BYTES + 32768
MAX_SLOTS = 4096
_DECIMAL = re.compile(r'(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z')
_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')


def _reference(value):
    try:
        return (type(value) is str and bool(value) and value == value.strip() and value.isprintable()
                and len(value.encode('utf-8', 'strict')) <= 256)
    except UnicodeError:
        return False


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError


def _decimal(value):
    if type(value) is not str or len(value) > 128 or not _DECIMAL.fullmatch(value):
        raise ValueError
    integer, _, fraction = value.partition('.')
    numerator = int(integer + fraction)
    if numerator <= 0:
        raise ValueError
    return numerator, len(fraction)


def _grid(value, step):
    number, places = _decimal(value)
    unit, unit_places = _decimal(step)
    scale = max(places, unit_places)
    return (number * 10**(scale - places)) % (unit * 10**(scale - unit_places)) == 0


def validate_plan_json(data, *, digest, version, build_id):
    """Validate and return the exact canonical ASCII representation."""
    if type(data) is not bytes or len(data) > MAX_PLAN_BYTES:
        raise ValueError
    text = data.decode('ascii', 'strict')
    plan = json.loads(text)
    if (type(plan) is not dict or set(plan) != {'schema_version', 'plan_version', 'build_id', 'schedule'}
            or type(plan['schema_version']) is not int or plan['schema_version'] != 1
            or not _reference(plan['plan_version']) or not _reference(plan['build_id'])
            or plan['plan_version'] != version or plan['build_id'] != build_id
            or json.dumps(plan, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False) != text
            or type(digest) is not str or _HEX64.fullmatch(digest) is None
            or hashlib.sha256(data).hexdigest() != digest):
        raise ValueError
    schedule = plan['schedule']
    if (type(schedule) is not dict or set(schedule) != {'class', 'version', 'binding', 'params'}
            or schedule['class'] not in {'PASS', 'SLICE', 'TWAP'}
            or type(schedule['version']) is not int or schedule['version'] != 1):
        raise ValueError
    binding, params = schedule['binding'], schedule['params']
    if (type(binding) is not dict or set(binding) != {
            'deadline_ns', 'quantity_step', 'parent_limit', 'price_step', 'retry_limit'}
            or type(params) is not dict):
        raise ValueError
    _integer(binding['deadline_ns'], 1)
    _integer(binding['retry_limit'])
    _decimal(binding['quantity_step'])
    if not _grid(binding['parent_limit'], binding['price_step']):
        raise ValueError
    if schedule['class'] == 'PASS':
        if params:
            raise ValueError
    elif schedule['class'] == 'SLICE':
        if set(params) != {'maximum'} or not _grid(params['maximum'], binding['quantity_step']):
            raise ValueError
    else:
        if set(params) != {'slots'} or type(params['slots']) is not list or not 1 <= len(params['slots']) <= MAX_SLOTS:
            raise ValueError
        previous_end = 0
        for slot in params['slots']:
            if type(slot) is not dict or set(slot) != {'start_ns', 'end_ns', 'quantity'}:
                raise ValueError
            _integer(slot['start_ns'])
            _integer(slot['end_ns'], 1)
            if (slot['start_ns'] < previous_end or slot['start_ns'] >= slot['end_ns']
                    or slot['end_ns'] > binding['deadline_ns']
                    or not _grid(slot['quantity'], binding['quantity_step'])):
                raise ValueError
            previous_end = slot['end_ns']
    return text


def plan_snapshot_payload(snapshot, *, parent_ref):
    """Create a detached finite wire/audit value from the exact local snapshot."""
    from bt_api_execution.local_parent_snapshot import LocalParentPlanSnapshot
    if type(snapshot) is not LocalParentPlanSnapshot or snapshot.source != 'LOCAL_JOURNAL':
        raise ValueError
    if snapshot.status == 'UNKNOWN':
        if snapshot != LocalParentPlanSnapshot('UNKNOWN'):
            raise ValueError
        result = asdict(snapshot)
        result.pop('request_sha256')
        return result
    if (snapshot.status != 'OBSERVED' or snapshot.parent_ref != parent_ref
            or type(snapshot.journal_incarnation) is not str
            or _HEX32.fullmatch(snapshot.journal_incarnation) is None
            or type(snapshot.instrument) is not MarketInstrumentKey
            or type(snapshot.request_sha256) is not str or _HEX64.fullmatch(snapshot.request_sha256) is None):
        raise ValueError
    text = validate_plan_json(snapshot.plan_json, digest=snapshot.plan_digest,
                              version=snapshot.plan_version, build_id=snapshot.declared_build_id)
    result = asdict(snapshot)
    result.pop('request_sha256')
    result['plan_json'] = text
    return result
