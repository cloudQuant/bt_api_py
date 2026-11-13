"""One isolated, explicitly fictional run through the normal observation entry."""

import json
import os
import stat
import tempfile

from bt_api_py import trading_calendar_mock as mock

try:
    import fcntl
except ImportError:  # Windows is explicitly unsupported for this workflow.
    fcntl = None

if fcntl is not None:
    from bt_api_py import trading_calendar_detail_collection as storage
    from bt_api_py import trading_calendar_observation_run as runs
    from bt_api_py import trading_calendar_review_bundle as review

_DECLARATION = "MOCK-SIMULATION_ONLY.json"
_RESERVED = frozenset(
    {
        ".lock",
        _DECLARATION,
        "CURRENT.json",
        "PENDING.json",
        "REVIEW_POLICY.json",
        "REVIEW_POLICY_INITIALIZED.json",
        "POLICY_CHANGE_PENDING.json",
        "POLICY_AUDIT_INITIALIZED.json",
        "versions",
        "publications",
        "receipts",
        "observation-runs",
        "review-bundles",
        "supplement-captures",
        "policy_changes",
        "association-review-candidates",
        "detail-collections",
        "hours-review-candidates",
        "hours-evidence-candidates",
        "holiday-evidence-candidates",
        "inbox",
        "akshare-observations",
        "source-evidence",
    }
)
_RESERVED_PREFIXES = ("MOCK-", ".CURRENT-", ".REVIEW_POLICY-", ".preview-", "preview-")


class _RootInputError(ValueError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def configured_mode(root, config):
    """Read only the calendar settings through the existing protected loader."""
    from bt_api_py.trading_calendar_check import _absolute_path, _read_config
    from bt_api_py.trading_calendar_config import parse_trading_calendar_config

    root_path, config_path = _absolute_path(root), _absolute_path(config)
    if str(root_path) != root or str(config_path) != config:
        raise ValueError("noncanonical path")
    if os.path.realpath(root) != root or os.path.realpath(config) != config:
        raise ValueError("symlink path")
    parsed = parse_trading_calendar_config(
        _read_config(config_path),
        config_file=config_path,
        gateway_enabled=False,
        direct_write_enabled=False,
    )
    if parsed.root_kind != "directory" or parsed.root_path != root_path:
        raise ValueError("calendar root mismatch")
    return parsed.review_mode


def persistent_declaration(root, run_id):
    """Read and validate the original local simulation declaration."""
    if fcntl is None:
        raise ValueError("unsupported filesystem")
    from bt_api_py.trading_calendar_preview_reports import _open_directory

    fds, chain = _open_directory(root)
    try:
        info = os.fstat(fds[-1])
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("root not private")
        raw = storage._read(fds[-1], chain, _DECLARATION, 8192)
        body, _ = storage._unwrap(raw)
        if (
            set(body)
            != {
                "schema",
                "environment",
                "data_origin",
                "root",
                "initial_run_id",
                "config",
                "year",
                "review_mode",
                "device",
                "inode",
            }
            or body["schema"] != "bt_api.calendar-mock-root/1"
            or body["environment"] != "SIMULATION_ONLY"
            or body["data_origin"] != "MOCK"
            or body["root"] != root
            or not runs._id(body["initial_run_id"])
            or not runs._id(run_id)
            or (body["device"], body["inode"]) != (info.st_dev, info.st_ino)
            or type(body["year"]) is not int
            or not 1901 <= body["year"] <= 9998
            or body["review_mode"] not in {"auto", "manual"}
            or configured_mode(root, body["config"]) != body["review_mode"]
        ):
            raise ValueError("simulation declaration invalid")
        return body
    finally:
        runs._close(fds)


def _declare_root(root, config, run_id, year, *, root_inputs=()):
    if fcntl is None:
        raise ValueError("unsupported filesystem")
    from bt_api_py.trading_calendar_preview_reports import _directory_matches, _open_directory

    mode = configured_mode(root, config)
    fds, chain = _open_directory(root)
    lock_fd = None
    locked = False
    marker_attempted = False
    children = []
    try:
        info = os.fstat(fds[-1])
        expected = {(): set()}
        files = set()
        for relative in root_inputs:
            parts = tuple(relative.split(os.sep))
            if (
                not parts
                or len(parts) > 128
                or any(
                    not part
                    or part in {".", ".."}
                    or part in _RESERVED
                    or part.startswith(_RESERVED_PREFIXES)
                    for part in parts
                )
            ):
                raise _RootInputError("INVALID_SUPPLEMENT_PATH")
            for index, part in enumerate(parts):
                parent = parts[:index]
                expected.setdefault(parent, set()).add(part)
                if index < len(parts) - 1:
                    expected.setdefault(parts[: index + 1], set())
            files.add(parts)
        if any(parts in expected for parts in files):
            raise _RootInputError("ROOT_INPUT_REJECTED")

        def names(directory):
            found = set()
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= 512:
                        raise _RootInputError("ROOT_INPUT_REJECTED")
                    found.add(entry.name)
            return found

        def directory_id(value):
            return value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid

        def file_id(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        dirs = {(): fds[-1]}
        dir_identities = {}
        input_identities = {}
        try:
            for parts in sorted(expected, key=len):
                if not parts:
                    continue
                parent = dirs[parts[:-1]]
                before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISDIR(before.st_mode):
                    raise _RootInputError("ROOT_INPUT_REJECTED")
                fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                children.append(fd)
                if directory_id(before) != directory_id(os.fstat(fd)):
                    raise _RootInputError("ROOT_INPUT_CHANGED")
                dirs[parts] = fd
                dir_identities[parts] = directory_id(before)
            for parts in files:
                source = os.stat(parts[-1], dir_fd=dirs[parts[:-1]], follow_symlinks=False)
                if not stat.S_ISREG(source.st_mode):
                    raise _RootInputError("ROOT_INPUT_REJECTED")
                input_identities[parts] = file_id(source)
            for parts, wanted in expected.items():
                actual = names(dirs[parts])
                if parts == ():
                    actual.discard(".lock")
                if actual != wanted:
                    raise _RootInputError("ROOT_INPUT_REJECTED")
        except FileNotFoundError:
            raise _RootInputError("SUPPLEMENT_MISSING") from None
        except OSError:
            raise _RootInputError("ROOT_INPUT_REJECTED") from None
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise _RootInputError("ROOT_INPUT_REJECTED")
        try:
            lock_fd = os.open(
                ".lock",
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=fds[-1],
            )
            created_lock = True
        except FileExistsError:
            lock_fd = os.open(".lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=fds[-1])
            created_lock = False
        lock_info = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_uid != os.geteuid()
            or stat.S_IMODE(lock_info.st_mode) != 0o600
            or lock_info.st_nlink != 1
            or lock_info.st_size != 0
        ):
            raise ValueError("existing lock is not pristine")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            return "NOT_COMMITTED"
        # A preexisting lock may be evidence of an earlier unknown attempt.
        if not created_lock:
            return "NOT_COMMITTED"

        def root_matches(root_extra):
            if not _directory_matches(chain):
                return False
            entry = os.stat(".lock", dir_fd=fds[-1], follow_symlinks=False)
            for parts, identity in dir_identities.items():
                if (
                    directory_id(os.fstat(dirs[parts])) != identity
                    or directory_id(
                        os.stat(parts[-1], dir_fd=dirs[parts[:-1]], follow_symlinks=False)
                    )
                    != identity
                ):
                    return False
            for parts, identity in input_identities.items():
                if (
                    file_id(os.stat(parts[-1], dir_fd=dirs[parts[:-1]], follow_symlinks=False))
                    != identity
                ):
                    return False
            for parts, wanted in expected.items():
                current = names(dirs[parts])
                if not parts:
                    current -= root_extra
                if current != wanted:
                    return False
            return (
                _directory_matches(chain)
                and (entry.st_dev, entry.st_ino) == (lock_info.st_dev, lock_info.st_ino)
                and entry.st_uid == lock_info.st_uid
                and entry.st_mode == lock_info.st_mode
                and entry.st_nlink == 1
                and entry.st_size == 0
                and os.fstat(lock_fd).st_size == 0
            )

        if not root_matches({".lock"}):
            return "NOT_COMMITTED"
        body = {
            "schema": "bt_api.calendar-mock-root/1",
            "environment": "SIMULATION_ONLY",
            "data_origin": "MOCK",
            "root": root,
            "initial_run_id": run_id,
            "config": config,
            "year": year,
            "review_mode": mode,
            "device": info.st_dev,
            "inode": info.st_ino,
        }
        marker_attempted = True
        state = storage._write(fds[-1], chain, _DECLARATION, storage._envelope(body), 8192)
        if state != "COMMITTED":
            return state
        if (
            not root_matches({".lock", _DECLARATION})
            or persistent_declaration(root, run_id) != body
        ):
            return "COMMIT_UNKNOWN"
        return "COMMITTED"
    except _RootInputError:
        if marker_attempted:
            return "COMMIT_UNKNOWN"
        raise
    finally:
        if locked:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        if lock_fd is not None:
            os.close(lock_fd)
        runs._close(children)
        runs._close(fds)


def read_audit(directory, chain, run_id, root):
    """Read the original declaration only; visibility does not settle a write."""
    unknown = {"state": "UNKNOWN", "environment": "SIMULATION_ONLY", "data_origin": "MOCK"}
    if fcntl is None:
        return unknown
    try:
        raw = runs._read_entry(directory, chain, f"mock-{run_id}.json")
        if raw is None:
            return None
        body, digest = storage._unwrap(raw)
        expected = {
            "schema",
            "data_origin",
            "environment",
            "publication",
            "source_authentication",
            "run_id",
            "capture_id",
            "parent_id",
            "derived_id",
            "state",
            "simulation_root",
            "year",
        }
        ids = [body[key] for key in ("run_id", "capture_id", "parent_id", "derived_id")]
        if (
            set(body) != expected
            or body["schema"] != "bt_api.calendar-mock-run/1"
            or body["run_id"] != run_id
            or body["simulation_root"] != root
            or any(not runs._id(value) for value in ids)
            or len(set(ids)) != 4
            or body["data_origin"] != "MOCK"
            or body["environment"] != "SIMULATION_ONLY"
            or body["publication"] != "NOT_AUTHORIZED"
            or body["source_authentication"] != "NOT_PROVEN"
            or body["state"] != "NOT_ATTEMPTED"
            or type(body["year"]) is not int
            or not 1901 <= body["year"] <= 9998
        ):
            return unknown
        return {
            "state": "VISIBLE_UNAUTHENTICATED",
            "data_origin": "MOCK",
            "environment": "SIMULATION_ONLY",
            "capture_id": body["capture_id"],
            "parent_id": body["parent_id"],
            "derived_id": body["derived_id"],
            "year": body["year"],
            "payload_sha256": digest,
        }
    except Exception:
        return unknown


def run_mock(
    *,
    root,
    config,
    supplement,
    budget_seconds,
    enable_shfe,
    filesystem,
    run_id,
    candidate_id,
    fetch_product_navigation,
    ine_evidence_ids,
    ine_retry_config,
    capture_id,
    parent_id,
    parent_sha256,
    derived_id,
    source_texts,
    decisions,
    year,
    emit,
):
    """Create a private temporary root or declare an empty configured mock root.

    Callers supply all four stable IDs and receive them before the first write.
    The resulting root is retained for read-only inspection and caller cleanup.
    This routine never writes CURRENT. It does not establish a security boundary
    against another process under the same OS identity or hostile file replacement.
    """
    result = {
        "data_origin": "MOCK",
        "environment": "SIMULATION_ONLY",
        "publication": "NOT_AUTHORIZED",
        "source_authentication": "NOT_PROVEN",
        "run_id": run_id,
        "capture_id": capture_id,
        "parent_id": parent_id,
        "derived_id": derived_id,
        "state": "NOT_ATTEMPTED",
        "overall": "INCOMPLETE",
        "simulation_publication": "NOT_ATTEMPTED",
        "supplement_input": "NOT_DETERMINED",
    }
    if fcntl is None:
        result["reason"] = "UNSUPPORTED_FILESYSTEM"
        return result
    ids = (run_id, capture_id, parent_id, derived_id)
    if (
        any(not runs._id(value) for value in ids)
        or len(set(ids)) != 4
        or type(year) is not int
        or not 1901 <= year <= 9998
        or filesystem != runs.TRUSTED_LOCAL_POSIX
        or not runs._supported()
        or ((root is None) != (config is None))
        or (root is None and supplement is not None)
        or enable_shfe is not False
        or candidate_id
        or fetch_product_navigation
        or ine_evidence_ids is not None
        or ine_retry_config is not None
        or parent_sha256
        or source_texts is not None
        or decisions is not None
        or not callable(emit)
        or not runs._valid_number(budget_seconds, 0.001, 86400.0)
    ):
        result["reason"] = "INVALID_ARGUMENTS"
        return result
    if supplement is not None:
        try:
            from bt_api_py.trading_calendar_check import _absolute_path

            if str(_absolute_path(supplement)) != supplement:
                raise ValueError("noncanonical supplement path")
        except (ValueError, TypeError, OSError):
            result["reason"] = "INVALID_SUPPLEMENT_PATH"
            return result
    try:
        value, addition = mock.build(year)
        temporary_root = root or os.path.join(
            os.path.realpath(tempfile.gettempdir()), f"bt-calendar-MOCK-SIMULATION_ONLY-{run_id}"
        )
        if root is not None:
            configured_mode(root, config)
        result["simulation_root"] = temporary_root
        emit({"event": "MOCK_IDS_ALLOCATED", **result})
    except (Exception, KeyboardInterrupt):
        result["reason"] = "ALLOCATION_NOT_CONFIRMED"
        return result
    try:
        selected_input = None
        if root is not None:
            from bt_api_py.trading_calendar_check import _absolute_path

            selected_path = str(
                _absolute_path(
                    supplement if supplement is not None else os.path.join(root, "supplement.yaml")
                )
            )
            result["supplement_input"] = "USER_SUPPLEMENT_REQUESTED"
            nested = (
                selected_path.startswith(root + os.sep) and os.path.dirname(selected_path) != root
            )
            if nested:
                # The descriptor walk below checks every component without following links.
                selected_input = selected_path
            else:
                try:
                    input_info = os.lstat(selected_path)
                except FileNotFoundError:
                    if supplement is not None:
                        result.update(
                            state="NOT_COMMITTED",
                            reason="SUPPLEMENT_MISSING",
                            supplement_status="MISSING",
                        )
                        return result
                    result["supplement_input"] = "SYNTHETIC_MOCK_NOT_USER_YAML"
                else:
                    if not stat.S_ISREG(input_info.st_mode):
                        result.update(
                            state="NOT_COMMITTED",
                            reason="SUPPLEMENT_NOT_REGULAR",
                            supplement_status="NOT_REGULAR",
                        )
                        return result
                    selected_input = selected_path
        else:
            result["supplement_input"] = "SYNTHETIC_MOCK_NOT_USER_YAML"
        if root is None:
            try:
                os.mkdir(temporary_root, 0o700)
            except FileExistsError:
                result.update(state="NOT_COMMITTED", reason="RUN_ID_EXISTS_RECONCILE_FIRST")
                return result
        else:
            allowed_inputs = set()
            if selected_input is not None and selected_input.startswith(root + os.sep):
                allowed_inputs.add(os.path.relpath(selected_input, root))
            try:
                os.lstat(os.path.join(root, "supplement.yaml"))
            except FileNotFoundError:
                pass
            else:
                allowed_inputs.add("supplement.yaml")
            try:
                state = _declare_root(
                    root,
                    config,
                    run_id,
                    year,
                    root_inputs=tuple(sorted(allowed_inputs)),
                )
            except _RootInputError as exc:
                result.update(state="NOT_COMMITTED", reason=exc.reason)
                return result
            if state != "COMMITTED":
                result.update(state=state, reason="DECLARATION_RECONCILE_FIRST")
                return result
        emit({"event": "MOCK_ROOT_CREATED", "simulation_root": temporary_root, **result})
        fds, chain = runs._open_runs(temporary_root, create=True)
        try:
            audit = {
                "schema": "bt_api.calendar-mock-run/1",
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"overall", "simulation_publication", "supplement_input"}
                },
                "year": year,
            }
            state, reason = runs._write_once(
                fds[-1], chain, f"mock-{run_id}.json", storage._envelope(audit)
            )
        finally:
            runs._close(fds)
        if state != "COMMITTED":
            result.update(state=state, reason=reason)
            return result
        parent = review.build_once(
            root=temporary_root,
            candidate_id=parent_id,
            input_data=value,
            filesystem=filesystem,
            emit=lambda _: None,
        )
        result["parent_state"] = parent["status"]
        if parent["status"] != "COMMITTED":
            result.update(state=parent["status"], reason="PARENT_" + parent["status"])
            return result
        shown = review.show(root=temporary_root, candidate_id=parent_id, filesystem=filesystem)
        if shown.get("status") != "VISIBLE_UNAUTHENTICATED":
            result.update(state="COMMIT_UNKNOWN", reason="PARENT_RECHECK_UNKNOWN")
            return result
        config_path = config or os.path.join(temporary_root, f"MOCK-config-{run_id}.json")
        if config is None:
            with open(config_path, "x", encoding="utf-8") as file:
                json.dump({"calendars": {"root": temporary_root}}, file)
        input_path = selected_input or os.path.join(
            temporary_root, f"MOCK-supplement-{run_id}.json"
        )
        if selected_input is None:
            with open(input_path, "x", encoding="utf-8") as file:
                json.dump(addition, file)
        observed = runs.run_once(
            root=temporary_root,
            config=config_path,
            supplement=input_path,
            budget_seconds=budget_seconds,
            enable_shfe=False,
            filesystem=filesystem,
            run_id=run_id,
            capture_id=capture_id,
            parent_id=parent_id,
            parent_sha256=shown["payload_sha256"],
            derived_id=derived_id,
            source_texts={},
            decisions=[],
        )
        result.update(observed)
        result.update(
            data_origin="MOCK",
            environment="SIMULATION_ONLY",
            publication="NOT_AUTHORIZED",
            source_authentication="NOT_PROVEN",
        )
        result["parent_sha256"] = shown["payload_sha256"]
        result["candidate_state"] = observed.get("derived_state", "NOT_ATTEMPTED")
        if selected_input is not None:
            if result["candidate_state"] != "COMMITTED":
                reread = runs.show_once(root=root, run_id=run_id, filesystem=filesystem)
                preview = (reread.get("observation") or {}).get("preview") or {}
                result["supplement_status"] = preview.get("supplement_status", "UNKNOWN")
                result["input_code"] = preview.get("input_code", "UNKNOWN")
                if observed.get("derived_reason") == "SUPPLEMENT_CONFLICT":
                    result["reason"] = "SUPPLEMENT_CONFLICT"
                    result["conflict_kind"] = observed.get("conflict_kind")
            else:
                from bt_api_py import trading_calendar_supplement_capture as capture

                captured = capture.read_capture(
                    root=root, capture_id=capture_id, filesystem=filesystem, include_content=True
                )
                if (
                    captured.get("status") != "VISIBLE_UNAUTHENTICATED"
                    or captured.get("run_id") != run_id
                    or type(captured.get("content")) is not dict
                    or type(captured["content"].get("sources")) is not list
                ):
                    result.update(
                        state="COMMIT_UNKNOWN",
                        reason="CAPTURE_RECONCILE_FIRST",
                        supplement_status="UNKNOWN",
                    )
                    return result
                if captured["content"]["sources"] != [mock.SOURCE]:
                    result.update(
                        state="NOT_COMMITTED",
                        reason="SUPPLEMENT_NOT_MOCK",
                        supplement_status="REJECTED",
                    )
                    return result
                result.update(
                    supplement_input="USER_SUPPLEMENT_CAPTURED",
                    supplement_status="CAPTURED_UNVERIFIED",
                    normalized_content_sha256=captured["normalized_content_sha256"],
                )
        result["state"] = (
            "INCOMPLETE" if result["candidate_state"] == "COMMITTED" else result["candidate_state"]
        )
        return result
    except (Exception, KeyboardInterrupt):
        result.update(state="COMMIT_UNKNOWN", reason="INTERRUPTED_OR_IO_UNKNOWN")
        return result
