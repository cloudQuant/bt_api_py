"""Operator-composed Atomic parent listener; readiness is independently fenced.

The default journal mode is quarantined. The opt-in single-host cooperative
mode assumes a sole fixed local root and original journal, cooperative writers,
operator exclusion of old and remote writers, and no file rollback. The setting
does not prove these assumptions or confer account authority; all independent
source and readiness gates remain in force.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.algorithm_config import ScheduleRegistry
from bt_api_execution.algorithm_policy import AlgorithmPolicy, AllowedSchedule
from bt_api_execution.atomic_submission_intake import AtomicSubmissionIntake
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.service_atomic_intake import AtomicIntakeService

from ._owner_stop_file import marker_requested, new_marker
from .execution_action_grant import ExecutionActionGrant
from .execution_parent_grant import ExecutionParentGrant, OPERATIONS, PORT
from .execution_read_service_host import _check_existing, _directory, _endpoint, _identity, _path, _text
from .monitor_service_config import read_bounded
from .service_components import ComponentResources
from .service_config import parse_service_config
from .service_host import ServiceHost, TrustedServiceAuthority
from .service_registry import PrincipalGrant, ResourceGrant

SCHEMA = 'bt_api_execution.parent_service.v1'


class ParentServiceError(ValueError):
    pass


def _map(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ParentServiceError('INVALID_CONFIGURATION')
    return value


def _list(value, limit=128):
    if type(value) is not list or not 1 <= len(value) <= limit:
        raise ParentServiceError('INVALID_CONFIGURATION')
    return value


def _unique_strings(value):
    rows = _list(value)
    result = frozenset(_text(item) for item in rows)
    if len(result) != len(rows):
        raise ParentServiceError('INVALID_CONFIGURATION')
    return result


def _instruments(value, local):
    rows = _list(value, 1024)
    result = frozenset(MarketInstrumentKey.from_dict(item) for item in rows)
    if (len(result) != len(rows)
            or any(i.provider != local.provider or i.environment != local.environment for i in result)):
        raise ParentServiceError('INVALID_CONFIGURATION')
    return result


@dataclass(frozen=True)
class ParentConfig:
    service: object
    identity: WireIdentity
    local_scope: ExecutionScope
    wire_scope: WireScope
    journal_root: Path
    registry_path: Path
    storage_id: str
    server_public: Path
    server_secret: Path
    principal_keys: tuple
    parent_grants: tuple
    action_grant: ExecutionActionGrant
    policies: tuple
    build_id: str
    lease_owner: str
    lease_ttl_ns: int
    versions: tuple
    writer_mode: str = 'quarantined'
    require_existing: bool = False
    initial_claim: tuple | None = None
    dispatch: object | None = None


def parse_config(raw):
    try:
        required_top = ('schema', 'service', 'service_identity', 'authority_versions',
                        'execution_scope', 'wire_scope', 'storage', 'server_keys',
                        'strategy_grants', 'action_grant', 'schedule_build_id',
                        'algorithm_policies', 'lease')
        if (type(raw) is not dict or not set(required_top) <= set(raw)
                or set(raw) - set(required_top) - {'dispatch'}):
            raise ParentServiceError('INVALID_CONFIGURATION')
        top = raw
        if top['schema'] != SCHEMA:
            raise ValueError
        service = parse_service_config(top['service'])
        manifest = service.to_public_dict()
        if service.clients or set(service.components) != {'execution'}:
            raise ValueError
        settings = manifest['components']['execution']['settings']
        if settings['bind_ports'] != [PORT] or settings['options'] != {'role': 'atomic_intake'}:
            raise ValueError
        instance_id = settings['instance_id']
        profile = manifest['communication']['profiles'][service.transport_ref]
        if (set(manifest['communication']['profiles']) != {service.transport_ref}
                or profile['backend'] != 'zmq' or set(profile['endpoints']) != {'rpc_bind'}):
            raise ValueError
        _endpoint(profile['endpoints']['rpc_bind'])
        identity = _identity(top['service_identity'], WireRole.SERVICE)
        if identity.actor_ref != service.service_id:
            raise ValueError
        versions = _map(top['authority_versions'], ('scope', 'security', 'registration_policy', 'requirements'))
        versions = tuple(_text(versions[k]) for k in ('scope', 'security', 'registration_policy', 'requirements'))
        local = _map(top['execution_scope'], ('provider', 'environment', 'account_ref', 'strategy_id'))
        local = ExecutionScope(**{k: _text(v) for k, v in local.items()})
        wire = _map(top['wire_scope'], ('execution_domain_ref', 'account_ref', 'strategy_ref', 'run_id'))
        wire = WireScope(_text(wire['execution_domain_ref']), account_ref=_text(wire['account_ref']),
                         strategy_ref=_text(wire['strategy_ref']), run_id=_text(wire['run_id']))
        if (wire.account_ref, wire.strategy_ref) != (local.account_ref, local.strategy_id):
            raise ValueError
        storage = top['storage']
        required = {'journal_root', 'registry_path', 'storage_id'}
        if (type(storage) is not dict or not required <= set(storage)
                or set(storage) - required - {'writer_mode', 'require_existing'}):
            raise ValueError
        writer_mode = storage.get('writer_mode', 'quarantined')
        require_existing = storage.get('require_existing', False)
        if (type(writer_mode) is not str
                or writer_mode not in {'quarantined', 'single_host_cooperative'}
                or type(require_existing) is not bool
                or (writer_mode == 'single_host_cooperative' and not require_existing)):
            raise ValueError
        lease = top['lease']
        if (type(lease) is not dict or not {'owner', 'ttl_ns'} <= set(lease)
                or set(lease) - {'owner', 'ttl_ns', 'initial_claim'}):
            raise ValueError
        ttl = lease['ttl_ns']
        if type(ttl) is not int or not 1_000_000 <= ttl <= 300_000_000_000:
            raise ValueError
        initial_claim = None
        if 'initial_claim' in lease:
            claim = _map(lease['initial_claim'], ('claim_id', 'expected_incarnation', 'history_assertion'))
            claim_id, incarnation, assertion = (claim['claim_id'], claim['expected_incarnation'],
                                                claim['history_assertion'])
            if (writer_mode != 'single_host_cooperative' or require_existing is not True
                    or type(claim_id) is not str or str(uuid.UUID(claim_id)) != claim_id
                    or type(incarnation) is not str or re.fullmatch(r'[0-9a-f]{32}', incarnation) is None
                    or assertion != 'management_read_only_no_prior_intake' or type(assertion) is not str):
                raise ValueError
            initial_claim = (claim_id, incarnation, assertion)
        journal_root, registry_path = _path(storage['journal_root']), _path(storage['registry_path'])
        if (journal_root == registry_path.parent or journal_root in registry_path.parents
                or registry_path.parent in journal_root.parents):
            raise ValueError
        storage_id = _text(storage['storage_id'])
        if str(uuid.UUID(storage_id)) != storage_id:
            raise ValueError
        keys = _map(top['server_keys'], ('public_key_file', 'secret_key_file'))
        server_public, server_secret = _path(keys['public_key_file']), _path(keys['secret_key_file'])
        action = _map(top['action_grant'], ('revision', 'instruments', 'plan_versions', 'actions'))
        action_grant = ExecutionActionGrant(instance_id, identity, wire.account_ref, wire.strategy_ref,
            wire.execution_domain_ref, _text(action['revision']), _instruments(action['instruments'], local),
            _unique_strings(action['plan_versions']), _unique_strings(action['actions']))
        if not action_grant.actions <= {'accept', 'query'}:
            raise ValueError
        grants, principal_keys = [], []
        for row in _list(top['strategy_grants']):
            row = _map(row, ('identity', 'scope_ref', 'revision', 'instruments', 'operations', 'public_key_file'))
            caller = _identity(row['identity'], WireRole.STRATEGY_CLIENT)
            if caller.actor_ref != wire.strategy_ref or caller.principal_ref == identity.principal_ref:
                raise ValueError
            operations = _unique_strings(row['operations'])
            instruments = _instruments(row['instruments'], local)
            if operations != OPERATIONS or not instruments <= action_grant.instruments:
                raise ValueError
            grants.append(ExecutionParentGrant(instance_id, _text(row['scope_ref']), caller,
                wire.account_ref, wire.strategy_ref, wire.execution_domain_ref, _text(row['revision']),
                instruments, operations))
            principal_keys.append((caller.principal_ref, _path(row['public_key_file'])))
        if len({g.identity.principal_ref for g in grants}) != len(grants):
            raise ValueError
        build_id = _text(top['schedule_build_id'])
        registry = ScheduleRegistry()
        policies, seen = [], set()
        for row in _list(top['algorithm_policies']):
            row = _map(row, ('plan_version', 'schedules'))
            plan_version = _text(row['plan_version'])
            if plan_version in seen or plan_version not in action_grant.plan_versions:
                raise ValueError
            seen.add(plan_version)
            rules = []
            for rule in _list(row['schedules']):
                rule = _map(rule, ('name', 'version', 'params', 'max_retry'))
                allowed = AllowedSchedule.from_params(rule['name'], rule['version'], rule['params'], rule['max_retry'])
                if (allowed.name, allowed.version) not in registry._definitions or allowed in rules:
                    raise ValueError
                rules.append(allowed)
            policies.append(AlgorithmPolicy(wire.account_ref, wire.strategy_ref, wire.execution_domain_ref,
                                            plan_version, build_id, tuple(rules)))
        dispatch_cfg = None
        dispatch_key_paths: tuple[Path, ...] = ()
        if 'dispatch' in top:
            from .execution_parent_dispatcher import ParentDispatchConfig

            d_raw = top['dispatch']
            d_req = {'route', 'risk_endpoint', 'risk_service_id', 'risk_instance_id',
                     'risk_scope_ref', 'risk_peer', 'risk_server_public',
                     'client_public', 'client_secret'}
            if type(d_raw) is not dict or not d_req <= set(d_raw) or set(d_raw) - d_req - {'poll_interval_seconds'}:
                raise ValueError
            _endpoint(d_raw['risk_endpoint'])
            poll_interval = float(d_raw.get('poll_interval_seconds', 0.2))
            if not 0.02 <= poll_interval <= 10.0:
                raise ValueError
            risk_peer = _identity(d_raw['risk_peer'], WireRole.SERVICE)
            risk_srv_pub = _path(d_raw['risk_server_public'])
            cli_pub = _path(d_raw['client_public'])
            cli_sec = _path(d_raw['client_secret'])
            dispatch_key_paths = (risk_srv_pub, cli_pub, cli_sec)
            if any(p == journal_root or journal_root in p.parents for p in dispatch_key_paths):
                raise ValueError
            dispatch_cfg = ParentDispatchConfig(
                route=_text(d_raw['route']),
                risk_endpoint=_text(d_raw['risk_endpoint']),
                risk_service_id=_text(d_raw['risk_service_id']),
                risk_instance_id=_text(d_raw['risk_instance_id']),
                risk_scope_ref=_text(d_raw['risk_scope_ref']),
                risk_peer=risk_peer,
                risk_server_public=risk_srv_pub,
                client_public=cli_pub,
                client_secret=cli_sec,
                poll_interval_seconds=poll_interval,
            )
        paths = [server_public, server_secret, *(p for _, p in principal_keys), registry_path]
        if len(set(paths)) != len(paths) or any(p == journal_root or journal_root in p.parents for p in paths):
            raise ValueError
        return ParentConfig(service, identity, local, wire, journal_root, registry_path, storage_id,
                            server_public, server_secret, tuple(principal_keys), tuple(grants), action_grant,
                            tuple(policies), build_id, _text(lease['owner']), ttl, versions,
                            writer_mode, require_existing, initial_claim, dispatch_cfg)
    except Exception:
        raise ParentServiceError('INVALID_CONFIGURATION') from None


def load_config(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        config_path = _path(path)
        config = parse_config(json.loads(read_bounded(config_path, 1024 * 1024, protected=True).decode('utf-8'),
            object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())))
        if (config_path in (config.registry_path, config.server_public, config.server_secret,
                            *(key for _, key in config.principal_keys))
                or config_path == config.journal_root or config.journal_root in config_path.parents):
            raise ValueError
        return config
    except Exception:
        raise ParentServiceError('INVALID_CONFIGURATION') from None


def _load_keys(config):
    import zmq
    from zmq.utils import z85
    paths = (config.server_public, config.server_secret, *(p for _, p in config.principal_keys))
    result, inodes = [], set()
    for path in paths:
        data = read_bounded(path, 4096, protected=True).strip()
        info = path.stat(follow_symlinks=False)
        if (info.st_dev, info.st_ino) in inodes or len(data) != 40 or len(z85.decode(data)) != 32:
            raise ParentServiceError('INVALID_KEYS')
        inodes.add((info.st_dev, info.st_ino))
        result.append(data)
    if zmq.curve_public(result[1]) != result[0] or len(set([result[0], *result[2:]])) != len(result) - 1:
        raise ParentServiceError('INVALID_KEYS')
    return result


def _check_paths(config):
    _directory(config.journal_root)
    _directory(config.registry_path.parent)
    try:
        info = config.registry_path.stat(follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if os.name == "nt":
            # Windows parity of the POSIX regular/private/single-link check.
            from bt_api_py._win32_security import (
                Win32SecurityUnavailable,
                inspect_owner,
                is_reparse_point,
            )

            try:
                info = config.registry_path.stat(follow_symlinks=False)
                owner = inspect_owner(config.registry_path)
                if (is_reparse_point(config.registry_path) or not stat.S_ISREG(info.st_mode)
                        or not owner.is_current_user or owner.others_writable
                        or info.st_nlink != 1):
                    raise ValueError
            except (OSError, ValueError, Win32SecurityUnavailable):
                raise ParentServiceError('INVALID_STORAGE') from None
        elif (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ParentServiceError('INVALID_STORAGE')
    _check_existing(config)


def _prepare_registry(config):
    """Ensure a new SQLite registry has private permissions from its first open."""
    if os.name == "nt":
        # O_NOFOLLOW does not exist on Windows.  CREATE_NEW (O_CREAT|O_EXCL)
        # fails when the path already exists, including a reparse point; refuse
        # an existing reparse point explicitly and verify the new file's owner
        # and DACL with the same rules as the protected reads.
        from bt_api_py._win32_security import (
            Win32SecurityUnavailable,
            inspect_owner,
            is_reparse_point,
        )

        try:
            fd = os.open(config.registry_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                if is_reparse_point(config.registry_path):
                    raise ParentServiceError('INVALID_STORAGE')
            except Win32SecurityUnavailable:
                raise ParentServiceError('INVALID_STORAGE') from None
            return
        except OSError:
            raise ParentServiceError('INVALID_STORAGE') from None
        try:
            # os.open's mode argument does not set a Windows DACL.  Apply the
            # POSIX 0600 equivalent (owner only, plus the privileged SIDs the
            # compat layer treats like POSIX root) before accepting the file.
            import win32con
            import win32security

            sd = win32security.GetFileSecurity(
                os.fspath(config.registry_path), win32security.OWNER_SECURITY_INFORMATION)
            dacl = win32security.ACL()
            for text in ('S-1-5-18', 'S-1-5-32-544'):
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL,
                                         win32security.ConvertStringSidToSid(text))
            dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL,
                                     sd.GetSecurityDescriptorOwner())
            sd.SetSecurityDescriptorDacl(1, dacl, 0)
            win32security.SetFileSecurity(
                os.fspath(config.registry_path), win32security.DACL_SECURITY_INFORMATION, sd)
            owner = inspect_owner(config.registry_path)
            if not owner.is_current_user or owner.others_writable:
                raise ParentServiceError('INVALID_STORAGE')
        except (OSError, Win32SecurityUnavailable):
            raise ParentServiceError('INVALID_STORAGE') from None
        finally:
            os.close(fd)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(config.registry_path, flags, 0o600)
    except FileExistsError:
        return
    else:
        os.close(fd)


class ParentServiceHost:
    """Single-use owner of the original Host and journal; close is retryable."""
    def __init__(self, config, *, risk_port=None):
        if type(config) is not ParentConfig:
            raise ParentServiceError('INVALID_CONFIGURATION')
        if risk_port is not None and not callable(getattr(risk_port, 'reserve_parent', None)):
            raise ParentServiceError('INVALID_CONFIGURATION')
        self.config, self.host, self.journal = config, None, None
        self.risk_port = risk_port
        self.dispatcher = None
        self.state = 'new'

    def start(self):
        if self.state != 'new':
            raise ParentServiceError('INVALID_STATE')
        self.state = 'close_uncertain'
        try:
            config = self.config
            _check_paths(config)
            keys = _load_keys(config)
            _prepare_registry(config)
            grants = {g.identity.principal_ref: (PrincipalGrant(g.identity, (ResourceGrant(
                g.instance_id, PORT, g.scope_ref, replace(config.wire_scope, run_id=None), g.operations),)),)
                for g in config.parent_grants}
            principal_map = {key: principal for (principal, _), key in zip(config.principal_keys, keys[2:])}
            service = config.service
            profile = service.communication['profiles'][service.transport_ref]
            authority = TrustedServiceAuthority(config.identity, config.wire_scope, service.scope_ref,
                config.versions[0], profile['security_profile_ref'], config.versions[1],
                service.registration_policy_ref, config.versions[2], service.service_requirements_ref,
                config.versions[3], keys[0], keys[1], principal_map, grants)

            def provider(*_args):
                options = {}
                if config.writer_mode != 'quarantined' or config.require_existing:
                    options = {'writer_mode': config.writer_mode, 'require_existing': config.require_existing}
                journal = AccountJournalHost(root=str(config.journal_root), scope=config.local_scope,
                                             storage_id=config.storage_id, **options)
                self.journal = journal
                try:
                    lease_options = {'ttl_ns': config.lease_ttl_ns}
                    if config.initial_claim is not None:
                        from bt_api_execution.account_journal_host import InitialIntakeClaim
                        claim_id, incarnation, assertion = config.initial_claim
                        lease_options['initial_claim'] = InitialIntakeClaim(
                            claim_id=claim_id, expected_incarnation=incarnation, history_assertion=assertion)
                    lease = journal.acquire_intake_lease(config.local_scope, config.lease_owner, **lease_options)
                    intake = AtomicSubmissionIntake(host=journal, targets={config.wire_scope: config.local_scope},
                        lease=lease, schedule_registry=ScheduleRegistry(), schedule_build_id=config.build_id,
                        algorithm_policies=config.policies)
                    adapter = AtomicIntakeService(config.action_grant.instance_id, intake,
                        config.parent_grants, (config.action_grant,), config.identity,
                        risk_port=self.risk_port)
                    return ComponentResources.owned({'service': adapter}, ready=lambda: True, close=journal.close)
                except BaseException:
                    journal.close()
                    self.journal = None
                    raise

            self.host = ServiceHost(service, authority, config.registry_path, provider)
            self.host.start()
            if config.dispatch is not None:
                from .execution_parent_dispatcher import ExecutionParentDispatcher

                dispatcher = ExecutionParentDispatcher(
                    dispatch_config=config.dispatch,
                    service_id=service.service_id,
                    identity=config.identity,
                    wire_scope=config.wire_scope,
                    local_scope=config.local_scope,
                    journal=self.journal,
                )
                dispatcher.start()
                self.dispatcher = dispatcher
            self.state = 'running'
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise ParentServiceError('START_FAILED') from None

    def poll(self):
        if self.state != 'running' or self.host is None:
            raise ParentServiceError('LISTENER_UNAVAILABLE')
        self.host.serve_once(100)
        if self.dispatcher is not None and self.host.healthy:
            self.dispatcher.step()
        return 'READY' if self.host.healthy else 'UNREADY'

    def close(self):
        if self.state == 'closed':
            return
        self.state = 'close_uncertain'
        try:
            if self.dispatcher is not None:
                self.dispatcher.close()
                self.dispatcher = None
            if self.host is not None:
                self.host.close()
                self.host = None
            if self.journal is not None:
                self.journal.close()
                self.journal = None
        except BaseException:
            raise ParentServiceError('CLEANUP_UNKNOWN') from None
        self.state = 'closed'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--enable-owner-stop-file', action='store_true',
                        help='allow local cooperative shutdown with a protected empty marker')
    args = parser.parse_args(argv)
    try:
        host = ParentServiceHost(load_config(args.config))
        signals, failed = 0, False
        def announce_stop_file():
            if not args.enable_owner_stop_file:
                return None
            marker = new_marker(Path(args.config).parent, 'execution-parent')
            print('EXECUTION_PARENT_STOP_FILE ' + marker.name, flush=True)
            return marker
        def stop(_signum, _frame):
            nonlocal signals
            signals += 1
        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            try:
                host.start()
                print('EXECUTION_PARENT_LISTENER_UNREADY' if not host.host.healthy else
                      'EXECUTION_PARENT_LISTENER_READY', flush=True)
                stop_file = announce_stop_file()
                while not signals and not marker_requested(stop_file):
                    host.poll()
            except Exception:
                failed = True
            finally:
                while True:
                    before = signals
                    try:
                        host.close()
                        break
                    except Exception:
                        failed = True
                        print('EXECUTION_PARENT_CLEANUP_PENDING', flush=True)
                        retry_file = announce_stop_file()
                        while signals == before and not marker_requested(retry_file):
                            time.sleep(0.1)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        print('EXECUTION_PARENT_FAILED' if failed else 'EXECUTION_PARENT_STOPPED', flush=True)
        return int(failed)
    except Exception:
        print('EXECUTION_PARENT_FAILED', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
