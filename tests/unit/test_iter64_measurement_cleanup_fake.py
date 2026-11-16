"""In-memory fakes for measurement cleanup; no service or process is started."""

import importlib.util
import json
import multiprocessing
import sys
import threading
from collections import deque
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from bt_api_py.service_runtime_thread import RuntimeSnapshot

_script = Path(__file__).resolve().parents[2] / "scripts/measure_iter64_risk_service.py"
_spec = importlib.util.spec_from_file_location("iter64_measurement_cleanup", _script)
measure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)


def _snapshot(*, strict=True):
    return RuntimeSnapshot("closed", False, False, None, (), 0, 0,
                           owner_cleanup_uncertainties=0, unknown_registration_count=0,
                           strict_unregister_ack=strict)


class Client:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def close(self, **_kwargs):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _rig(*, clients=(), operator=None, sessions=(), risk=None, gateway=None):
    rig = measure.Rig.__new__(measure.Rig)
    rig._cleanup_unknown = False
    rig._workers = []
    rig._worker_resources = []
    rig.clients = list(clients)
    rig.operator = operator
    rig.sessions = list(sessions)
    rig.risk = risk
    rig.gateway = gateway
    rig.gateway_owner = None
    rig.risk_stop_observations = []
    return rig


def test_strict_success_discards_only_confirmed_client():
    clean = Client(_snapshot())
    unknown = Client(_snapshot(strict=False))
    rig = _rig(clients=[clean, unknown])
    with pytest.raises(measure.MeasurementCleanupError):
        rig.close_risk_clients()
    assert rig.clients == [unknown]
    assert rig._cleanup_unknown is True
    with pytest.raises(measure.MeasurementResourceError):
        rig.close_risk_clients()
    # One strict receipt check plus one bounded escalation, then unproven.
    assert unknown.calls == 2


def test_failed_client_blocks_downstream_close_and_keeps_handle():
    client = Client(TimeoutError())
    risk = SimpleNamespace(send_signal=lambda *_: pytest.fail("must not signal"))
    rig = _rig(clients=[client], risk=risk)
    with pytest.raises(measure.MeasurementCleanupError):
        rig.close()
    assert rig.clients == [client] and rig.risk is risk
    assert rig._cleanup_unknown is True


def test_interrupted_client_close_preserves_exception_and_handle():
    client = Client(KeyboardInterrupt())
    rig = _rig(clients=[client])
    with pytest.raises(KeyboardInterrupt):
        rig.close_risk_clients()
    assert rig.clients == [client] and rig._cleanup_unknown is True
    with pytest.raises(measure.MeasurementResourceError):
        rig.close_risk_clients()
    assert client.calls == 1


@pytest.mark.parametrize("primary_type", [ValueError, KeyboardInterrupt])
@pytest.mark.parametrize("cleanup_type", [RuntimeError, KeyboardInterrupt])
def test_client_start_error_survives_failed_close(primary_type, cleanup_type):
    primary, cleanup_error = primary_type(), cleanup_type()
    client = Client(cleanup_error)

    def fail_start(**_kwargs):
        raise primary

    client.start = fail_start
    rig = _rig()
    rig.strategy_count = 1
    rig.support = SimpleNamespace(risk_client=lambda *_args: client)
    rig.risk_endpoint, rig.keys = "fake", {}
    with pytest.raises(type(primary)) as caught:
        measure._guard(rig.open_risk_clients)
    assert caught.value is primary
    expected_calls = 1 if cleanup_type is KeyboardInterrupt else 2
    assert rig.clients == [client] and rig._cleanup_unknown
    assert client.calls == expected_calls
    assert measure._cleanup_detail(primary) == {
        "status": "INCOMPLETE", "categories": ["risk_clients_close_failed"]
    }
    measure._record_cleanup_error(primary, {"status": "UNKNOWN", "categories": ["cleanup_unknown"]})
    assert measure._cleanup_detail(primary)["categories"] == ["risk_clients_close_failed", "cleanup_unknown"]


def test_clean_independent_components_can_close():
    client, session = Client(_snapshot()), Client(_snapshot())
    rig = _rig(clients=[client], sessions=[session])
    measure._RETAINED_RIGS.append(rig)
    rig.close()
    assert not rig.clients and not rig.sessions
    assert rig not in measure._RETAINED_RIGS


def test_gateway_without_bound_owner_never_implies_cleanup_or_forced_kill():
    gateway = SimpleNamespace(join=lambda timeout: pytest.fail("join"),
                              terminate=lambda: pytest.fail("terminate"),
                              kill=lambda: pytest.fail("kill"))
    rig = _rig(gateway=gateway)
    with pytest.raises(measure.MeasurementResourceError):
        rig.stop_gateway()
    assert rig.gateway is gateway and rig._cleanup_unknown


def test_risk_stop_uses_one_bounded_escalation_and_keeps_the_handle():
    calls = []
    process = SimpleNamespace()
    rig = _rig(risk=process)
    rig.support = SimpleNamespace(stop_risk=lambda child: calls.append(child) or None)
    with pytest.raises(measure.MeasurementResourceError, match="risk_host_stop_not_clean"):
        rig.stop_risk()
    assert calls == [process]
    assert rig.risk is process and rig._cleanup_unknown
    assert rig.risk_stop_observations == [{
        "operation": "stop", "result": "timeout_forced_kill", "exit_code": None,
        "after_forced_kill": False,
    }]
    # A phase-level stop never retries from an uncertain owner; only close()
    # runs the bounded cleanup escalation.
    with pytest.raises(measure.MeasurementResourceError):
        rig.stop_risk()
    assert calls == [process]


def test_kill_fault_is_refused_and_guard_propagates():
    rig = _rig(risk=SimpleNamespace(kill=lambda: pytest.fail("kill")))
    with pytest.raises(measure.MeasurementResourceError):
        measure._guard(lambda: rig.stop_risk(kill=True))


def test_gateway_start_interruption_retains_same_handle_and_blocks_cleanup():
    def interrupted():
        raise KeyboardInterrupt()

    process = SimpleNamespace(start=interrupted)
    rig = _rig()
    rig.context = SimpleNamespace(Process=lambda **_kwargs: process)
    rig.support = SimpleNamespace(gateway_main=object(), gateway_publics=lambda *_: (),
                                  SyntheticGatewayStop=lambda *_: object())
    rig.gateway_root = "synthetic"
    rig.gateway_endpoint = "synthetic"
    rig.keys = {"gateway_server": object()}
    rig.strategy_count = 1
    rig.messages, rig.controls = object(), object()
    with pytest.raises(KeyboardInterrupt):
        rig.start_gateway()
    assert rig.gateway is process and rig._cleanup_unknown is True
    with pytest.raises(measure.MeasurementCleanupError):
        rig.close()


@pytest.mark.parametrize("receipt", ["complete", "wrong_generation", "missing", "bad_exit"])
def test_gateway_uses_bound_owner_receipt_and_retains_uncertainty(monkeypatch, receipt):
    from tests.integration import _iter64_recovery_support as support

    retained, actions = {}, []
    monkeypatch.setattr(support, "_GATEWAY_CHILD_RETAINED", retained)
    monkeypatch.setattr(support.secrets, "token_hex", lambda _: "c" * 32)
    messages = deque([("ready", 123, "journal", "a" * 32, "b" * 32)])

    def get(**_kwargs):
        if not messages:
            raise TimeoutError()
        return messages.popleft()

    def put(command):
        actions.append(command)
        if receipt != "missing":
            _, run_id, generation, stop_id = command
            if receipt == "wrong_generation":
                generation = "d" * 32
            messages.append(("gateway_close", 123, run_id, generation, stop_id, "COMPLETE"))

    process = SimpleNamespace(pid=123, exitcode=1 if receipt == "bad_exit" else 0,
                              start=lambda: actions.append("start"),
                              join=lambda timeout: actions.append("join"),
                              is_alive=lambda: False,
                              kill=lambda: pytest.fail("kill"),
                              terminate=lambda: pytest.fail("terminate"))
    rig = _rig()
    rig.context = SimpleNamespace(Process=lambda **_kwargs: process)
    rig.support = support
    rig.gateway_root = rig.gateway_endpoint = "synthetic"
    rig.keys = {"gateway_server": object(), "strategies": []}
    rig.strategy_count = 0
    rig.messages, rig.controls = SimpleNamespace(get=get), SimpleNamespace(put=put)
    monkeypatch.setattr(support, "gateway_publics", lambda *_: ())
    rig.start_gateway()
    owner = rig.gateway_owner
    assert owner.process is process and rig.journal_id == "journal"
    if receipt == "complete":
        rig.stop_gateway()
        assert rig.gateway is None and rig.gateway_owner is None and not rig._cleanup_unknown
        assert not retained and actions[-1] == "join"
    else:
        with pytest.raises(RuntimeError):
            rig.stop_gateway()
        assert rig.gateway is process and rig.gateway_owner is owner and rig._cleanup_unknown
        assert retained[id(owner)] is owner
        with pytest.raises(measure.MeasurementResourceError):
            rig.stop_gateway()
    assert len([item for item in actions if type(item) is tuple]) == 1


def test_gateway_bad_ready_identity_retains_owner_and_process(monkeypatch):
    from tests.integration import _iter64_recovery_support as support

    retained = {}
    monkeypatch.setattr(support, "_GATEWAY_CHILD_RETAINED", retained)
    monkeypatch.setattr(support, "gateway_publics", lambda *_: ())
    process = SimpleNamespace(pid=123, start=lambda: None)
    rig = _rig()
    rig.context = SimpleNamespace(Process=lambda **_kwargs: process)
    rig.support = support
    rig.gateway_root = rig.gateway_endpoint = "synthetic"
    rig.keys = {"gateway_server": object()}
    rig.strategy_count = 0
    rig.messages = SimpleNamespace(get=lambda **_: ("ready", 124, "journal", "a" * 32, "b" * 32))
    rig.controls = object()
    with pytest.raises(ValueError):
        rig.start_gateway()
    assert rig.gateway is process and rig._cleanup_unknown
    assert retained[id(rig.gateway_owner)] is rig.gateway_owner
    with pytest.raises(measure.MeasurementResourceError):
        rig.stop_gateway()


@pytest.mark.parametrize("stage", ["ready", "bind", "close"])
@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_gateway_uncertainty_propagates_original_and_stops_guard(stage, error_type):
    primary = error_type("private detail")
    actions = []
    process = SimpleNamespace(start=lambda: None)

    def fail():
        actions.append(stage)
        raise primary

    owner = SimpleNamespace(process=process,
                            bind_ready=lambda _ready: fail() if stage == "bind" else None,
                            close=lambda: fail() if stage == "close" else None)
    rig = _rig()
    rig.context = SimpleNamespace(Process=lambda **_kwargs: process)
    rig.support = SimpleNamespace(gateway_main=object(), gateway_publics=lambda *_: (),
                                  SyntheticGatewayStop=lambda *_: owner,
                                  next_message=lambda *_: fail() if stage == "ready"
                                  else ("ready", 1, "journal", "generation", "run"))
    rig.gateway_root = rig.gateway_endpoint = "synthetic"
    rig.keys = {"gateway_server": object()}
    rig.strategy_count = 0
    rig.messages = rig.controls = object()
    if stage == "close":
        rig.start_gateway()
    phase = rig.stop_gateway if stage == "close" else rig.start_gateway
    with pytest.raises(error_type) as caught:
        measure._guard(phase)
    assert caught.value is primary
    assert measure._cleanup_detail(primary) == {
        "status": "INCOMPLETE" if stage == "close" else "UNKNOWN",
        "categories": ["gateway_stop_failed" if stage == "close" else "cleanup_unknown"],
    }
    assert rig.gateway is process and rig.gateway_owner is owner and rig._cleanup_unknown
    with pytest.raises(measure.MeasurementResourceError):
        rig.stop_gateway()
    assert actions == [stage]


def test_arbitrary_worker_handles_are_never_forced(monkeypatch):
    calls = []
    worker = SimpleNamespace(join=lambda _: None, is_alive=lambda: True,
                             terminate=lambda: calls.append("terminate"),
                             kill=lambda: calls.append("kill"))
    remaining, forced = measure._cleanup_workers([worker])
    assert remaining == [worker] and forced == 0
    assert calls == []


def test_work_location_saved_before_create_even_if_create_fails():
    events, result = [], {}

    class FakePath:
        def __truediv__(self, name):
            assert name == "risk_service_measurements.work"
            return self

        def __str__(self):
            return "synthetic-work"

        def mkdir(self, **kwargs):
            events.append(("mkdir", kwargs))
            raise InterruptedError()

    def save():
        events.append(("save", dict(result)))

    with pytest.raises(InterruptedError):
        measure._prepare_workdir(FakePath(), result, save)
    assert events[0][0] == "save" and events[1][0] == "mkdir"
    assert events[0][1]["working_directory"] == "synthetic-work"
    assert events[0][1]["working_directory_retained"] is True
    assert events[0][1]["cleanup"]["status"] == "UNKNOWN"


def _claim_rig():
    rig = _rig()
    worker = SimpleNamespace(start=lambda: None)
    queues = [object(), object()]
    flags = {"aborted": False, "released": False}

    def event(flag):
        return SimpleNamespace(set=lambda: flags.__setitem__(flag, True),
                               is_set=lambda: flags[flag])

    # abort, start barrier and one-way release state share the phase lock.
    rig.events = [event("aborted"), SimpleNamespace(set=lambda: None), event("released")]
    events = list(rig.events)
    rig.context = SimpleNamespace(Queue=lambda: queues.pop(0), Event=lambda: events.pop(0),
                                  Lock=lambda: threading.Lock(),
                                  Process=lambda **_kwargs: worker)
    rig.working = lambda: {}
    rig.permit_evidence = lambda: []
    rig.gateway_endpoint = rig.risk_endpoint = rig.journal_id = "synthetic"
    rig.keys = {}
    return rig, worker


@pytest.mark.parametrize("primary_type", [KeyboardInterrupt, ValueError])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_claim_primary_error_survives_worker_cleanup(monkeypatch, primary_type, cleanup_fails):
    primary = primary_type()
    rig, worker = _claim_rig()

    def fail_phase(*_args):
        raise primary

    def cleanup(_workers):
        if cleanup_fails:
            raise RuntimeError("private cleanup details")
        return [], 0

    monkeypatch.setattr(measure, "_queue_until", fail_phase)
    monkeypatch.setattr(measure, "_cleanup_workers", cleanup)
    with pytest.raises(type(primary)) as caught:
        measure._guard(lambda: measure.phase_claim_processes(rig, 1, 1))
    assert caught.value is primary
    assert rig._workers == [worker] and rig._cleanup_unknown
    assert len(rig._worker_resources) == 6
    assert primary.measurement_cleanup_error == {
        "status": "UNKNOWN",
        "categories": (["unknown_cleanup_failure"] if cleanup_fails else [])
                      + ["worker_cleanup_unknown"],
    }


def test_claim_unknown_cleanup_alone_fails(monkeypatch):
    rig, worker = _claim_rig()
    monkeypatch.setattr(measure, "_queue_until", lambda *_args: [(0, "READY")])
    monkeypatch.setattr(measure, "_cleanup_workers", lambda _workers: ([], 0))
    with pytest.raises(measure.MeasurementCleanupError) as caught:
        measure.phase_claim_processes(rig, 1, 1)
    assert caught.value.categories == ["worker_cleanup_unknown"]
    assert rig._workers == [worker] and rig._cleanup_unknown


def test_claim_cleanup_interruption_alone_survives_and_reports_unknown(monkeypatch):
    rig, worker = _claim_rig()
    interruption = KeyboardInterrupt()
    monkeypatch.setattr(measure, "_queue_until", lambda *_args: [(0, "READY")])

    def interrupt(_workers):
        raise interruption

    monkeypatch.setattr(measure, "_cleanup_workers", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        measure.phase_claim_processes(rig, 1, 1)
    assert caught.value is interruption
    assert interruption.measurement_cleanup_error == {
        "status": "UNKNOWN", "categories": ["worker_cleanup_unknown"]
    }
    assert rig._workers == [worker] and rig._cleanup_unknown


def test_claim_start_interruption_keeps_unstarted_handle(monkeypatch):
    rig, worker = _claim_rig()
    interruption = KeyboardInterrupt()

    def interrupt():
        raise interruption

    worker.start = interrupt
    monkeypatch.setattr(measure, "_cleanup_workers", lambda workers: ([], 0))
    with pytest.raises(KeyboardInterrupt) as caught:
        measure.phase_claim_processes(rig, 1, 1)
    assert caught.value is interruption
    assert interruption.measurement_cleanup_error["categories"] == ["worker_cleanup_unknown"]
    assert rig._workers == [worker] and rig._cleanup_unknown


def test_main_reports_phase_and_final_cleanup_without_masking_primary(monkeypatch):
    writes = []

    class FakePath:
        def __init__(self, value):
            self.value = str(value)

        def __truediv__(self, name):
            return FakePath(self.value + "/" + name)

        def __str__(self):
            return self.value

        def mkdir(self, **_kwargs):
            pass

        def exists(self):
            return False

        def write_text(self, content, **_kwargs):
            writes.append(json.loads(content))

    primary = KeyboardInterrupt()
    primary.measurement_cleanup_error = {
        "status": "UNKNOWN", "categories": ["worker_cleanup_unknown"]
    }

    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            self._worker_result_evidence = {
                "retention": "PROCESS_MEMORY_ONLY", "expected_count": 2,
                "received_count": 1, "unreceived_or_unverified": "UNKNOWN",
            }

        def start_risk(self):
            raise primary

        def close(self):
            raise measure.MeasurementCleanupError(["cleanup_unknown"])

    monkeypatch.setattr(measure, "Path", FakePath)
    monkeypatch.setattr(measure, "Rig", FakeRig)
    monkeypatch.setattr(measure, "_load", lambda: {})
    monkeypatch.setattr(measure.platform, "platform", lambda: "fake")
    monkeypatch.setattr(measure.platform, "machine", lambda: "fake")
    monkeypatch.setattr(measure.platform, "python_version", lambda: "fake")
    monkeypatch.setattr(measure.os, "cpu_count", lambda: 1)
    with pytest.raises(KeyboardInterrupt) as caught:
        measure.main(["--out", "fake"])
    assert caught.value is primary
    assert writes[-1]["phase_cleanup"] == {
        "status": "UNKNOWN", "categories": ["worker_cleanup_unknown"]
    }
    assert primary.measurement_cleanup_error == {
        "status": "UNKNOWN", "categories": ["worker_cleanup_unknown", "cleanup_unknown"]
    }
    assert writes[-1]["cleanup"] == {"status": "UNKNOWN", "categories": ["cleanup_unknown"]}
    assert writes[-1]["worker_result_evidence"] == {
        "retention": "PROCESS_MEMORY_ONLY", "expected_count": 2,
        "received_count": 1, "unreceived_or_unverified": "UNKNOWN",
    }
    assert writes[-1]["measurement_status"] == "INCOMPLETE"


@pytest.mark.parametrize("cleanup_error", [
    measure.MeasurementCleanupError(["cleanup_unknown"]),
    RuntimeError("private cleanup detail"),
])
def test_phase_cleanup_and_report_failures_attach_to_original(monkeypatch, capsys, cleanup_error):
    primary = ValueError("private phase detail")
    report_failures, cleanup_failures, actions = [], [], []

    class FakePath:
        def __init__(self, value):
            self.value = str(value)

        def __truediv__(self, name):
            return FakePath(self.value + "/" + name)

        def __str__(self):
            return self.value

        def mkdir(self, **_kwargs):
            pass

        def exists(self):
            return False

        def write_text(self, content, **_kwargs):
            if "failure" in json.loads(content):
                actions.append("report")
                raise OSError("private filesystem detail")

    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_risk(self):
            raise primary

        def close(self):
            actions.append("close")
            raise cleanup_error

    monkeypatch.setattr(measure, "Path", FakePath)
    monkeypatch.setattr(measure, "Rig", FakeRig)
    monkeypatch.setattr(measure, "_load", lambda: {})
    monkeypatch.setattr(measure, "_MEASUREMENT_REPORT_FAILURES", report_failures)
    monkeypatch.setattr(measure, "_MEASUREMENT_CLEANUP_FAILURES", cleanup_failures)
    monkeypatch.setattr(measure.platform, "platform", lambda: "fake")
    monkeypatch.setattr(measure.platform, "machine", lambda: "fake")
    monkeypatch.setattr(measure.platform, "python_version", lambda: "fake")
    monkeypatch.setattr(measure.os, "cpu_count", lambda: 1)
    with pytest.raises(ValueError) as caught:
        measure.main(["--out", "fake"])
    assert caught.value is primary and actions == ["close", "report"]
    category = "cleanup_unknown" if type(cleanup_error) is measure.MeasurementCleanupError else "unknown_cleanup_failure"
    assert measure._cleanup_detail(primary) == {"status": "UNKNOWN", "categories": [category]}
    assert primary.measurement_report_error == {"status": "UNKNOWN", "category": "report_write_failed"}
    assert report_failures == [(primary, primary.measurement_report_error)]
    assert capsys.readouterr().err == "measurement_report_write_failed\n"


@pytest.mark.parametrize("stage", ["phase", "close"])
@pytest.mark.parametrize("primary_type", [ValueError, KeyboardInterrupt])
@pytest.mark.parametrize("save_type", [OSError, KeyboardInterrupt])
def test_main_preserves_primary_when_failure_report_write_fails(
        monkeypatch, capsys, stage, primary_type, save_type):
    writes, actions, report_failures = [], [], []
    primary = primary_type("private primary")

    class FakePath:
        def __init__(self, value):
            self.value = str(value)

        def __truediv__(self, name):
            return FakePath(self.value + "/" + name)

        def __str__(self):
            return self.value

        def mkdir(self, **_kwargs):
            pass

        def exists(self):
            return False

        def write_text(self, content, **_kwargs):
            row = json.loads(content)
            if "failure" in row:
                actions.append("report_attempt")
                raise save_type("private filesystem detail")
            writes.append(row)

    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_risk(self):
            if stage == "phase":
                raise primary
            return 0

        def prime(self):
            pass

        def close(self):
            actions.append("close")
            if stage == "close":
                raise primary

    monkeypatch.setattr(measure, "Path", FakePath)
    monkeypatch.setattr(measure, "Rig", FakeRig)
    monkeypatch.setattr(measure, "_load", lambda: {})
    monkeypatch.setattr(measure, "_guard", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(measure, "_MEASUREMENT_REPORT_FAILURES", report_failures)
    monkeypatch.setattr(measure.platform, "platform", lambda: "fake")
    monkeypatch.setattr(measure.platform, "machine", lambda: "fake")
    monkeypatch.setattr(measure.platform, "python_version", lambda: "fake")
    monkeypatch.setattr(measure.os, "cpu_count", lambda: 1)
    with pytest.raises(primary_type) as caught:
        measure.main(["--out", "fake"])
    assert caught.value is primary
    detail = {"status": "UNKNOWN", "category": "report_write_failed"}
    assert primary.measurement_report_error == detail
    assert report_failures == [(primary, detail)]
    assert actions == ["close", "report_attempt"]
    assert writes and all("failure" not in row for row in writes)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "measurement_report_write_failed\n"


def test_failed_failure_report_survives_unwritable_exception_and_stderr(monkeypatch):
    class RestrictedError(Exception):
        def __setattr__(self, _name, _value):
            raise RuntimeError("no attributes")

    primary, records = RestrictedError(), []
    monkeypatch.setattr(measure, "_MEASUREMENT_REPORT_FAILURES", records)
    monkeypatch.setattr(measure.sys, "stderr", SimpleNamespace(write=lambda _: 1 / 0))

    def fail():
        raise KeyboardInterrupt()

    measure._save_failure_report(fail, primary)
    assert records == [(primary, {"status": "UNKNOWN", "category": "report_write_failed"})]


@pytest.mark.parametrize("save_type", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("cleanup", ["complete", "unknown", "interrupted"])
def test_preclose_report_failure_closes_once_and_preserves_original(
        monkeypatch, capsys, save_type, cleanup):
    primary = save_type("private write failure")
    actions, report_failures, cleanup_failures = [], [], []

    class FakePath:
        def __init__(self, value):
            self.value = str(value)

        def __truediv__(self, name):
            return FakePath(self.value + "/" + name)

        def __str__(self):
            return self.value

        def mkdir(self, **_kwargs):
            pass

        def exists(self):
            return False

        def write_text(self, content, **_kwargs):
            row = json.loads(content)
            if row.get("active_stage") == "close":
                actions.append("save_failed")
                raise primary

    class FakeRig:
        def __init__(self, *_args, **_kwargs):
            pass

        def start_risk(self):
            return 0

        def prime(self):
            pass

        def close(self):
            actions.append("close")
            if cleanup == "unknown":
                raise measure.MeasurementCleanupError(["cleanup_unknown"])
            if cleanup == "interrupted":
                raise KeyboardInterrupt("private cleanup failure")

    monkeypatch.setattr(measure, "Path", FakePath)
    monkeypatch.setattr(measure, "Rig", FakeRig)
    monkeypatch.setattr(measure, "_load", lambda: {})
    monkeypatch.setattr(measure, "_guard", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(measure, "_MEASUREMENT_REPORT_FAILURES", report_failures)
    monkeypatch.setattr(measure, "_MEASUREMENT_CLEANUP_FAILURES", cleanup_failures)
    monkeypatch.setattr(measure.platform, "platform", lambda: "fake")
    monkeypatch.setattr(measure.platform, "machine", lambda: "fake")
    monkeypatch.setattr(measure.platform, "python_version", lambda: "fake")
    monkeypatch.setattr(measure.os, "cpu_count", lambda: 1)
    with pytest.raises(save_type) as caught:
        measure.main(["--out", "fake"])
    assert caught.value is primary and actions == ["save_failed", "close"]
    report = {"status": "UNKNOWN", "category": "report_write_failed"}
    assert primary.measurement_report_error == report
    assert report_failures == [(primary, report)]
    if cleanup == "complete":
        assert cleanup_failures == []
    else:
        category = "cleanup_unknown" if cleanup == "unknown" else "unknown_cleanup_failure"
        assert measure._cleanup_detail(primary) == {"status": "UNKNOWN", "categories": [category]}
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "measurement_report_write_failed\n"


def test_interrupted_claim_aborts_then_wakes_before_join(monkeypatch):
    rig, worker = _claim_rig()
    actions = []
    rig.events[0].set = lambda: actions.append("abort")
    rig.events[1].set = lambda: actions.append("wake")
    primary = KeyboardInterrupt()

    def fail(*_args):
        raise primary

    monkeypatch.setattr(measure, "_queue_until", fail)
    monkeypatch.setattr(measure, "_cleanup_workers", lambda workers: (actions.append("join") or [], 0))
    with pytest.raises(KeyboardInterrupt) as caught:
        measure.phase_claim_processes(rig, 1, 1)
    assert caught.value is primary
    assert actions == ["abort", "wake", "join"]
    assert rig._workers == [worker] and rig._cleanup_unknown


def test_interruption_after_release_reports_refused_abort_without_releasing_twice(monkeypatch):
    rig, worker = _claim_rig()
    actions = []
    rig.events[0].set = lambda: actions.append("abort")
    rig.events[1].set = lambda: actions.append("wake")
    primary = KeyboardInterrupt()
    calls = iter([False, True])

    def collect(*_args):
        if next(calls):
            raise primary
        return [(0, "READY")]

    monkeypatch.setattr(measure, "_queue_until", collect)
    monkeypatch.setattr(measure, "_cleanup_workers", lambda workers: (actions.append("join") or [], 0))
    with pytest.raises(KeyboardInterrupt) as caught:
        measure.phase_claim_processes(rig, 1, 1)
    assert caught.value is primary
    # The measured window was already released; the late abort is refused and
    # recorded instead of being applied to a non-atomic check.
    assert actions == ["wake", "join"]
    assert rig._worker_gate_evidence["decision"] == "RELEASED"
    assert rig._worker_gate_evidence["abort_requests"] == ["RELEASED"]
    assert rig._workers == [worker] and rig._cleanup_unknown


@pytest.mark.parametrize("short_stage", ["ready", "results"])
def test_partial_claim_collection_aborts_before_cleanup(monkeypatch, short_stage):
    rig, worker = _claim_rig()
    actions = []
    rig.events[0].set = lambda: actions.append("abort")
    rig.events[1].set = lambda: actions.append("wake")
    partial_row = {"index": 0, "keys": ["synthetic-key"]}
    calls = []

    def collect(*args):
        calls.append(1)
        if len(calls) == 1:
            return [] if short_stage == "ready" else [(0, "READY"), (1, "READY")]
        args[3].append(partial_row)
        return args[3]

    # Two fake workers allow one received result to remain incomplete.
    second = SimpleNamespace(start=lambda: None)
    workers = iter([worker, second])
    rig.context.Process = lambda **_kwargs: next(workers)
    monkeypatch.setattr(measure, "_queue_until", collect)
    monkeypatch.setattr(measure, "_cleanup_workers", lambda workers: (actions.append("join") or [], 0))
    with pytest.raises(measure.MeasurementCleanupError):
        measure.phase_claim_processes(rig, 2, 1)
    assert actions == (["abort", "wake", "join"] if short_stage == "ready"
                       else ["wake", "join"])
    assert rig._worker_gate_evidence["decision"] == (
        "ABORTED" if short_stage == "ready" else "RELEASED")
    assert rig._worker_gate_evidence["abort_requests"] == (
        [] if short_stage == "ready" else ["RELEASED"])
    assert rig._worker_result_rows == [partial_row]
    assert rig._worker_result_evidence == {
        "retention": "PROCESS_MEMORY_ONLY", "expected_count": 2,
        "received_count": 1, "unreceived_or_unverified": "UNKNOWN",
    }
    assert rig._workers == [worker, second] and rig._cleanup_unknown


def test_result_rows_consumed_before_interruption_remain_on_rig(monkeypatch):
    rig, worker = _claim_rig()
    primary = KeyboardInterrupt()
    partial_row = {"index": 0, "keys": ["synthetic-key"]}
    calls = []

    def collect(*args):
        calls.append(1)
        if len(calls) == 1:
            return [(0, "READY")]
        args[3].append(partial_row)
        raise primary

    monkeypatch.setattr(measure, "_queue_until", collect)
    monkeypatch.setattr(measure, "_cleanup_workers", lambda _workers: ([], 0))
    with pytest.raises(KeyboardInterrupt) as caught:
        measure.phase_claim_processes(rig, 1, 1)
    assert caught.value is primary
    assert rig._worker_result_rows == [partial_row]
    assert rig._worker_result_evidence["received_count"] == 1
    assert rig._worker_result_evidence["unreceived_or_unverified"] == "UNKNOWN"
    assert rig._workers == [worker] and rig._cleanup_unknown


def test_queue_collector_keeps_rows_consumed_before_interruption():
    partial_row, rows = {"index": 0}, []
    values = iter([partial_row, KeyboardInterrupt()])

    def get(**_kwargs):
        value = next(values)
        if isinstance(value, BaseException):
            raise value
        return value

    with pytest.raises(KeyboardInterrupt):
        measure._queue_until(SimpleNamespace(get=get), 2, 60, rows)
    assert rows == [partial_row]


def test_ready_timeout_failed_abort_does_not_release_or_retry(monkeypatch):
    rig, worker = _claim_rig()
    actions = []
    primary = RuntimeError("private abort failure")

    def abort():
        actions.append("abort")
        raise primary

    rig.events[0].set = abort
    rig.events[1].set = lambda: actions.append("wake")
    monkeypatch.setattr(measure, "_queue_until", lambda *_args: [])
    monkeypatch.setattr(measure, "_cleanup_workers", lambda _workers: (actions.append("join") or [], 0))
    with pytest.raises(RuntimeError) as caught:
        measure._guard(lambda: measure.phase_claim_processes(rig, 1, 1))
    assert caught.value is primary
    assert actions == ["abort", "join"]
    assert measure._cleanup_detail(primary) == {
        "status": "UNKNOWN", "categories": ["worker_wakeup_failed", "worker_cleanup_unknown"]
    }
    assert rig._workers == [worker] and rig._cleanup_unknown


@pytest.mark.parametrize("rows", [
    [(0, "READY"), (1, "FAILED")],
    [(0, "READY"), (0, "READY")],
    [(0, "READY"), (2, "READY")],
    [(False, "READY"), (1, "READY")],
    [[0, "READY"], (1, "READY")],
    [(0, "READY"), (1, "UNKNOWN")],
    [(0, "READY")],
])
def test_readiness_requires_one_valid_success_per_worker(rows):
    assert not measure._all_workers_ready(rows, 2)
    assert measure._all_workers_ready([(1, "READY"), (0, "READY")], 2)


def test_failed_readiness_stops_collection_immediately():
    calls = []

    def get(**_kwargs):
        calls.append(1)
        if len(calls) != 1:
            pytest.fail("must not wait for another worker")
        return (0, "FAILED")

    rows = measure._queue_until(
        SimpleNamespace(get=get), 2, 60, None,
        lambda items: not measure._valid_readiness_prefix(items, 2),
    )
    assert rows == [(0, "FAILED")] and calls == [1]


def test_failed_worker_readiness_aborts_before_other_worker_can_dispatch(monkeypatch):
    rig, worker = _claim_rig()
    second = SimpleNamespace(start=lambda: None)
    workers = iter([worker, second])
    rig.context.Process = lambda **_kwargs: next(workers)
    state = {"abort": False}
    actions = []

    def abort():
        actions.append("abort")
        state["abort"] = True

    def wake():
        actions.append("wake")
        if not state["abort"]:
            actions.append("dispatch")

    rig.events[0].set = abort
    rig.events[1].set = wake
    calls = []

    def collect(*args):
        calls.append(1)
        return [(0, "FAILED"), (1, "READY")] if len(calls) == 1 else args[3]

    monkeypatch.setattr(measure, "_queue_until", collect)
    monkeypatch.setattr(measure, "_cleanup_workers", lambda _workers: (actions.append("join") or [], 0))
    with pytest.raises(measure.MeasurementCleanupError):
        measure.phase_claim_processes(rig, 2, 1)
    assert actions == ["abort", "wake", "join"]
    assert rig._workers == [worker, second] and rig._cleanup_unknown


def test_worker_reports_failure_before_readiness(monkeypatch):
    ready_rows, results = [], []

    def fail(*_args):
        raise ValueError("private failure")

    support = SimpleNamespace(strategy_session=fail)
    fake_integration = ModuleType("tests.integration")
    fake_integration._iter64_recovery_support = support
    monkeypatch.setitem(sys.modules, "tests.integration", fake_integration)
    measure._claim_worker(0, "fake", "fake", {}, "fake", 1,
                          SimpleNamespace(put=ready_rows.append), object(),
                          SimpleNamespace(put=results.append))
    assert ready_rows == [(0, "FAILED")]
    assert results[0]["error"] == "ValueError"


@pytest.mark.parametrize("fails", ["abort", "wake"])
def test_wakeup_failure_keeps_original_even_if_exception_rejects_attributes(monkeypatch, fails):
    class RestrictedError(Exception):
        def __setattr__(self, _name, _value):
            raise RuntimeError("attribute write failed")

        def __getattribute__(self, name):
            if name == "measurement_cleanup_error":
                raise RuntimeError("attribute read failed")
            return super().__getattribute__(name)

    rig, worker = _claim_rig()
    actions = []
    primary = RestrictedError("original")

    def step(name):
        actions.append(name)
        if name == fails:
            raise RuntimeError("private wakeup failure")

    rig.events[0].set = lambda: step("abort")
    rig.events[1].set = lambda: step("wake")

    def fail(*_args):
        raise primary

    monkeypatch.setattr(measure, "_queue_until", fail)
    monkeypatch.setattr(measure, "_cleanup_workers", lambda workers: (actions.append("join") or [], 0))
    with pytest.raises(RestrictedError) as caught:
        measure._guard(lambda: measure.phase_claim_processes(rig, 1, 1))
    assert caught.value is primary
    assert actions == (["abort", "join"] if fails == "abort" else ["abort", "wake", "join"])
    assert measure._cleanup_detail(primary) == {
        "status": "UNKNOWN", "categories": ["worker_wakeup_failed", "worker_cleanup_unknown"]
    }
    assert rig._workers == [worker] and rig._cleanup_unknown


def test_aborted_worker_does_not_dispatch_prepared_claims(monkeypatch):
    support = SimpleNamespace()
    dispatched, rows = [], []

    class Client:
        def start(self, **_kwargs):
            pass

        def reserve(self, *_args, **_kwargs):
            answer = SimpleNamespace(outcome="RESERVED", permit_id="p", reservation_id="r",
                                     view_digest="digest")
            return SimpleNamespace(outcome="ANSWERED", answer=answer)

        def dispatch(self, key, **_kwargs):
            dispatched.append(key)
            return SimpleNamespace(outcome="ANSWERED", answer=SimpleNamespace(outcome="DISPATCHED"))

        def close(self, **_kwargs):
            pass

    support.strategy_session = lambda *_args: Client()
    support.risk_client = lambda *_args: Client()
    support.order_request = lambda *_args: object()
    fake_integration = ModuleType("tests.integration")
    fake_integration._iter64_recovery_support = support
    monkeypatch.setitem(sys.modules, "tests.integration", fake_integration)
    ready_rows = []
    ready = SimpleNamespace(put=ready_rows.append)
    state = {"aborted": False}
    start = SimpleNamespace(wait=lambda _timeout: state.update(aborted=True) or True)
    abort = SimpleNamespace(is_set=lambda: state["aborted"], set=lambda: state.update(aborted=True))
    results = SimpleNamespace(put=rows.append)
    measure._claim_worker(0, "fake", "fake", {}, "fake", 2, ready, start, results, abort)
    assert ready_rows == [(0, "READY"), (0, "FAILED")]
    assert len(dispatched) == 1  # warmup precedes the barrier
    assert rows[0]["keys"] == [] and rows[0]["error"] == "RuntimeError"


def test_ready_worker_timeout_aborts_before_parent_wakes_other_worker(monkeypatch):
    dispatched, ready_rows, worker_results, actions = [], [], [], []
    state = {"aborted": False}

    class Client:
        def start(self, **_kwargs):
            pass

        def reserve(self, *_args, **_kwargs):
            answer = SimpleNamespace(outcome="RESERVED", permit_id="p", reservation_id="r",
                                     view_digest="digest")
            return SimpleNamespace(outcome="ANSWERED", answer=answer)

        def dispatch(self, key, **_kwargs):
            dispatched.append(key)
            return SimpleNamespace(outcome="ANSWERED", answer=SimpleNamespace(outcome="DISPATCHED"))

        def close(self, **_kwargs):
            pass

    support = SimpleNamespace(strategy_session=lambda *_args: Client(),
                              risk_client=lambda *_args: Client(), order_request=lambda *_args: object())
    fake_integration = ModuleType("tests.integration")
    fake_integration._iter64_recovery_support = support
    monkeypatch.setitem(sys.modules, "tests.integration", fake_integration)
    rig, worker = _claim_rig()
    abort = rig.events[0]
    abort.set = lambda: state.update(aborted=True)
    abort.is_set = lambda: state["aborted"]
    measure._claim_worker(0, "fake", "fake", {}, "fake", 1,
                          SimpleNamespace(put=ready_rows.append),
                          SimpleNamespace(wait=lambda _timeout: False),
                          SimpleNamespace(put=worker_results.append), abort)
    assert ready_rows == [(0, "READY"), (0, "FAILED")]
    assert worker_results[0]["error"] == "TimeoutError"
    assert worker_results[0]["abort_status"] == "SET"
    assert len(dispatched) == 1  # A's warmup was already sent before timeout

    second = SimpleNamespace(start=lambda: None)
    workers = iter([worker, second])
    rig.context.Process = lambda **_kwargs: next(workers)

    def wake():
        actions.append("wake")
        if not state["aborted"]:
            actions.append("dispatch")

    rig.events[1].set = wake
    calls = []

    def collect(*args):
        calls.append(1)
        # The FAILED row is delayed behind B's READY; the shared state wins.
        return [ready_rows[0], (1, "READY")] if len(calls) == 1 else args[3]

    monkeypatch.setattr(measure, "_queue_until", collect)
    monkeypatch.setattr(measure, "_cleanup_workers", lambda _workers: (actions.append("join") or [], 0))
    with pytest.raises(measure.MeasurementCleanupError):
        measure.phase_claim_processes(rig, 2, 1)
    assert actions == ["wake", "join"]
    assert len(dispatched) == 1 and rig._cleanup_unknown


def test_partial_reserve_batch_reports_failed_without_warmup_dispatch(monkeypatch):
    reserve_calls, dispatched, ready_rows, results = [], [], [], []
    state = {"aborted": False}

    class Client:
        def start(self, **_kwargs):
            pass

        def reserve(self, *_args, **kwargs):
            reserve_calls.append(kwargs["request_key"])
            answer = SimpleNamespace(outcome="RESERVED" if len(reserve_calls) == 1 else "DENIED")
            return SimpleNamespace(outcome="ANSWERED", answer=answer)

        def dispatch(self, *_args, **_kwargs):
            dispatched.append(True)

        def close(self, **_kwargs):
            pass

    support = SimpleNamespace(strategy_session=lambda *_args: Client(),
                              risk_client=lambda *_args: Client(), order_request=lambda *_args: object())
    fake_integration = ModuleType("tests.integration")
    fake_integration._iter64_recovery_support = support
    monkeypatch.setitem(sys.modules, "tests.integration", fake_integration)
    abort = SimpleNamespace(is_set=lambda: state["aborted"], set=lambda: state.update(aborted=True))
    measure._claim_worker(0, "fake", "fake", {}, "fake", 2,
                          SimpleNamespace(put=ready_rows.append), object(),
                          SimpleNamespace(put=results.append), abort)
    assert ready_rows == [(0, "FAILED")] and not dispatched and state["aborted"]
    assert [row["request_key"] for row in results[0]["reserve_attempts"]] == reserve_calls
    assert [row["outcome"] for row in results[0]["reserve_attempts"]] == ["RESERVED", "DENIED"]
    assert results[0]["abort_status"] == "SET"


def test_worker_abort_set_failure_is_reported_unknown(monkeypatch):
    ready_rows, results = [], []

    def fail(*_args):
        raise ValueError("private")

    def fail_abort():
        raise KeyboardInterrupt()

    support = SimpleNamespace(strategy_session=fail)
    fake_integration = ModuleType("tests.integration")
    fake_integration._iter64_recovery_support = support
    monkeypatch.setitem(sys.modules, "tests.integration", fake_integration)
    abort = SimpleNamespace(is_set=lambda: False, set=fail_abort)
    measure._claim_worker(0, "fake", "fake", {}, "fake", 1,
                          SimpleNamespace(put=ready_rows.append), object(),
                          SimpleNamespace(put=results.append), abort)
    assert ready_rows == [(0, "FAILED")]
    assert results[0]["error"] == "ValueError" and results[0]["abort_status"] == "UNKNOWN"


def test_interruption_after_ready_enqueue_sets_abort(monkeypatch):
    ready_rows, results = [], []
    state = {"aborted": False}

    class Client:
        def start(self, **_kwargs):
            pass

        def reserve(self, *_args, **_kwargs):
            answer = SimpleNamespace(outcome="RESERVED", permit_id="p", reservation_id="r",
                                     view_digest="digest")
            return SimpleNamespace(outcome="ANSWERED", answer=answer)

        def dispatch(self, *_args, **_kwargs):
            return SimpleNamespace(outcome="ANSWERED", answer=SimpleNamespace(outcome="DISPATCHED"))

        def close(self, **_kwargs):
            pass

    support = SimpleNamespace(strategy_session=lambda *_args: Client(),
                              risk_client=lambda *_args: Client(), order_request=lambda *_args: object())
    fake_integration = ModuleType("tests.integration")
    fake_integration._iter64_recovery_support = support
    monkeypatch.setitem(sys.modules, "tests.integration", fake_integration)

    def put_ready(row):
        ready_rows.append(row)
        if row[1] == "READY":
            raise KeyboardInterrupt()

    abort = SimpleNamespace(is_set=lambda: state["aborted"], set=lambda: state.update(aborted=True))
    measure._claim_worker(0, "fake", "fake", {}, "fake", 1,
                          SimpleNamespace(put=put_ready), object(),
                          SimpleNamespace(put=results.append), abort)
    assert ready_rows == [(0, "READY"), (0, "FAILED")]
    assert state["aborted"] and results[0]["abort_status"] == "SET"
    assert results[0]["error"] == "KeyboardInterrupt"


class _RecordingLock:
    """Real lock that counts acquisitions, so removing a guard is visible."""

    def __init__(self):
        self._lock = threading.Lock()
        self.entries = 0

    def __enter__(self):
        self._lock.acquire()
        self.entries += 1
        return self

    def __exit__(self, *_exc):
        self._lock.release()
        return False


def _gate():
    abort, release = threading.Event(), threading.Event()
    lock = _RecordingLock()
    return measure._ClaimGate(lock, abort, release), abort, release, lock


def test_claim_gate_release_is_one_way_and_refuses_late_abort():
    gate, abort, release, lock = _gate()
    assert gate.state() == "PENDING"
    assert gate.admit() == "PENDING"
    assert gate.decide(True) == "RELEASED"
    assert gate.admit() == "RELEASED"
    # A set/abort after the release decision is refused, not applied late.
    assert gate.abort() == "RELEASED"
    assert abort.is_set() is False
    assert gate.decide(False) == "RELEASED"
    # Every transition (state, admit, decide, abort, decide) used the phase lock.
    assert lock.entries == 6


def test_claim_gate_abort_wins_before_release_and_is_one_way():
    gate, abort, release, lock = _gate()
    assert gate.abort() == "ABORTED"
    assert gate.decide(True) == "ABORTED"
    assert gate.admit() == "ABORTED"
    assert release.is_set() is False
    assert lock.entries == 3


def test_claim_gate_is_shared_with_a_spawned_worker_process():
    context = multiprocessing.get_context("spawn")
    lock, abort, release = context.Lock(), context.Event(), context.Event()
    gate = measure._ClaimGate(lock, abort, release)
    results = context.Queue()
    assert gate.decide(True) == "RELEASED"
    child = context.Process(target=_gate_child, args=(lock, abort, release, results))
    child.start()
    try:
        child.join(60)
        assert child.exitcode == 0
        # The settled one-way state is visible in another process, so a
        # worker cannot observe a stale PENDING after the release decision.
        assert results.get(timeout=10) == "RELEASED"
    finally:
        if child.is_alive():
            child.terminate()
            child.join(10)
        results.close()
        results.cancel_join_thread()


def _gate_child(lock, abort, release, results):
    import importlib.util
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2]
              / "scripts" / "measure_iter64_risk_service.py")
    spec = importlib.util.spec_from_file_location("iter64_gate_child", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results.put(module._ClaimGate(lock, abort, release).admit())


def test_claim_gate_decision_and_abort_never_both_win():
    for _ in range(50):
        gate, abort, release, _lock = _gate()
        barrier = threading.Barrier(2)
        outcomes = {}

        def decision(gate=gate, barrier=barrier, outcomes=outcomes):
            barrier.wait()
            outcomes["decide"] = gate.decide(True)

        def aborting(gate=gate, barrier=barrier, outcomes=outcomes):
            barrier.wait()
            outcomes["abort"] = gate.abort()

        threads = [threading.Thread(target=decision), threading.Thread(target=aborting)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert outcomes in ({"decide": "RELEASED", "abort": "RELEASED"},
                            {"decide": "ABORTED", "abort": "ABORTED"})
        assert not (abort.is_set() and release.is_set())
        assert abort.is_set() is (outcomes["decide"] == "ABORTED")
        assert release.is_set() is (outcomes["decide"] == "RELEASED")


class _WorkerClient:
    def __init__(self, dispatched, *, fail_after_warmup=False):
        self.dispatched = dispatched
        self.fail_after_warmup = fail_after_warmup
        self.calls = 0

    def start(self, **_kwargs):
        pass

    def reserve(self, *_args, **_kwargs):
        answer = SimpleNamespace(outcome="RESERVED", permit_id="p", reservation_id="r",
                                 view_digest="digest")
        return SimpleNamespace(outcome="ANSWERED", answer=answer)

    def dispatch(self, key, **_kwargs):
        self.calls += 1
        if self.fail_after_warmup and self.calls > 1:
            raise RuntimeError("private dispatch failure")
        self.dispatched.append(key)
        return SimpleNamespace(outcome="ANSWERED", answer=SimpleNamespace(outcome="DISPATCHED"))

    def close(self, **_kwargs):
        pass


def _worker_support(monkeypatch, client):
    support = SimpleNamespace(strategy_session=lambda *_args: client,
                              risk_client=lambda *_args: client,
                              order_request=lambda *_args: object())
    fake_integration = ModuleType("tests.integration")
    fake_integration._iter64_recovery_support = support
    monkeypatch.setitem(sys.modules, "tests.integration", fake_integration)


@pytest.mark.parametrize("admission", ["ABORTED", "PENDING"])
def test_worker_does_not_dispatch_without_a_guarded_admission(monkeypatch, admission):
    dispatched, ready_rows, rows = [], [], []
    _worker_support(monkeypatch, _WorkerClient(dispatched))
    gate = SimpleNamespace(admit=lambda: admission, abort=lambda: "ABORTED")
    abort = SimpleNamespace(is_set=lambda: False, set=lambda: None)
    measure._claim_worker(0, "fake", "fake", {}, "fake", 1,
                          SimpleNamespace(put=ready_rows.append),
                          SimpleNamespace(wait=lambda _timeout: True),
                          SimpleNamespace(put=rows.append), abort, gate)
    assert ready_rows == [(0, "READY"), (0, "FAILED")]
    # Only the warm-up dispatch preceded the barrier; no measured claim was sent.
    assert len(dispatched) == 1
    assert rows[0]["keys"] == []
    assert rows[0]["admission_status"] == admission
    assert rows[0]["error"] == "RuntimeError"


def test_worker_dispatches_only_after_guarded_release(monkeypatch):
    dispatched, ready_rows, rows = [], [], []
    _worker_support(monkeypatch, _WorkerClient(dispatched))
    gate = SimpleNamespace(admit=lambda: "RELEASED", abort=lambda: "RELEASED")
    abort = SimpleNamespace(is_set=lambda: False, set=lambda: None)
    measure._claim_worker(0, "fake", "fake", {}, "fake", 2,
                          SimpleNamespace(put=ready_rows.append),
                          SimpleNamespace(wait=lambda _timeout: True),
                          SimpleNamespace(put=rows.append), abort, gate)
    assert ready_rows == [(0, "READY")]
    assert rows[0]["admission_status"] == "RELEASED"
    assert len(rows[0]["keys"]) == 2 and len(dispatched) == 3


def test_worker_records_late_abort_refused_after_release(monkeypatch):
    dispatched, ready_rows, rows = [], [], []
    _worker_support(monkeypatch, _WorkerClient(dispatched, fail_after_warmup=True))
    gate = SimpleNamespace(admit=lambda: "RELEASED", abort=lambda: "RELEASED")
    abort = SimpleNamespace(is_set=lambda: False, set=lambda: pytest.fail("late abort applied"))
    measure._claim_worker(0, "fake", "fake", {}, "fake", 2,
                          SimpleNamespace(put=ready_rows.append),
                          SimpleNamespace(wait=lambda _timeout: True),
                          SimpleNamespace(put=rows.append), abort, gate)
    assert ready_rows == [(0, "READY"), (0, "FAILED")]
    assert rows[0]["admission_status"] == "RELEASED"
    assert rows[0]["abort_status"] == "RELEASE_ALREADY_DECIDED"
    assert rows[0]["error"] == "RuntimeError"
    assert len(rows[0]["keys"]) == 1
    assert rows[0]["keys"][0].startswith("tp-0-1-")


@pytest.mark.parametrize(("error", "expected"), [
    (None, {"status": "COMPLETE", "categories": []}),
    (measure.MeasurementCleanupError(["gateway_stop_failed"]),
     {"status": "INCOMPLETE", "categories": ["gateway_stop_failed"]}),
    (measure.MeasurementCleanupError(["risk_host_stop_not_clean"]),
     {"status": "INCOMPLETE", "categories": ["risk_host_stop_not_clean"]}),
    (measure.MeasurementCleanupError(["risk_clients_close_failed", "worker_cleanup_unknown"]),
     {"status": "UNKNOWN", "categories": ["risk_clients_close_failed", "worker_cleanup_unknown"]}),
    (measure.MeasurementCleanupError(["cleanup_unknown"]),
     {"status": "UNKNOWN", "categories": ["cleanup_unknown"]}),
    (measure.MeasurementResourceError("risk_host_stop_not_clean"),
     {"status": "INCOMPLETE", "categories": ["risk_host_stop_not_clean"]}),
    (measure.MeasurementResourceError("cleanup_unknown"),
     {"status": "UNKNOWN", "categories": ["cleanup_unknown"]}),
    (RuntimeError("private cleanup detail"),
     {"status": "UNKNOWN", "categories": ["unknown_cleanup_failure"]}),
])
def test_cleanup_result_distinguishes_deterministic_and_unknown(error, expected):
    result = measure._cleanup_result(error)
    assert result == expected
    assert "private" not in str(result)


def test_closed_client_escalates_once_then_confirms():
    snapshots = iter([_snapshot(strict=False), _snapshot()])

    class Client:
        calls = 0

        def close(self, **_kwargs):
            Client.calls += 1
            return next(snapshots)

    assert measure._closed_client(Client()) == {"receipt": "CONFIRMED", "attempts": 2}
    assert Client.calls == 2


def test_closed_client_stops_after_one_bounded_escalation():
    class Client:
        calls = 0

        def close(self, **_kwargs):
            Client.calls += 1
            return _snapshot(strict=False)

    with pytest.raises(RuntimeError, match="not confirmed"):
        measure._closed_client(Client())
    assert Client.calls == 2


def test_closed_client_never_swallows_interruption():
    class Client:
        calls = 0

        def close(self, **_kwargs):
            Client.calls += 1
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        measure._closed_client(Client())
    assert Client.calls == 1


def test_risk_stop_exception_records_unknown_and_keeps_handle():
    rig = _rig(risk=object())
    error = RuntimeError("private stop detail")
    rig.support = SimpleNamespace(stop_risk=lambda _child: (_ for _ in ()).throw(error))
    with pytest.raises(RuntimeError) as caught:
        rig.stop_risk()
    assert caught.value is error
    assert rig.risk is not None and rig._cleanup_unknown
    assert rig.risk_stop_observations == [{
        "operation": "stop", "result": "unknown", "exit_code": None,
        "after_forced_kill": False,
    }]
    assert "private" not in str(rig.risk_stop_observations)
