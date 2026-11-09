"""Actual CURVE ServiceHost routes control lookups with per-operation grants."""
import queue
import socket
import threading
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayControlGrant, GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ServiceRuntimeError, ZmqTransportFactory
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime


def test_control_identity_and_target_status_over_real_curve(tmp_path):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        endpoint = f'tcp://127.0.0.1:{probe.getsockname()[1]}'
    server_keys, client_keys = zmq.curve_keypair(), zmq.curve_keypair()
    service = WireIdentity(WireRole.SERVICE,'service-principal','service')
    client = WireIdentity(WireRole.APPLICATION_CLIENT,'operator','actor')
    scope = WireScope('domain',account_ref='acct')
    ops = frozenset({'direct.control.identity','direct.control.target_status'})
    service_config = parse_service_config({'service_config_schema_version':1,'process_role':'service',
        'service_id':'service','scope_ref':'scope','transport_ref':'server',
        'registration_policy_ref':'policy','service_requirements_ref':'requirements',
        'components':{'gateway':{'config_schema_version':2,'settings':{'instance_id':'gateway',
            'bind_ports':['service.direct_write'],'options':{'role':'trading_owner','dispatch_enabled':True,'control_enabled':True}}}},
        'communication':{'config_schema_version':1,'profiles':{'server':{'backend':'zmq','config_schema_version':1,
            'endpoints':{'rpc_bind':endpoint},'security_profile_ref':'key'}}}})
    client_config = parse_client_config({'client_config_schema_version':1,'process_role':'application_client',
        'registration':{'application_ref':'actor'},'clients':{'gateway':{'config_schema_version':1,'settings':{
        'bindings':[{'mount':'application.direct_write','service_id':'service','instance_id':'gateway',
            'scope_ref':'scope','account_ref':'acct','transport_ref':'client'}],'options':{'request_timeout_ms':2000}}}},
        'communication':{'config_schema_version':1,'profiles':{'client':{'backend':'zmq','config_schema_version':1,
            'endpoints':{'rpc_connect':endpoint},'security_profile_ref':'key'}}}})
    grant=ResourceGrant('gateway','service.direct_write','scope',scope,ops)
    authority=TrustedServiceAuthority(identity=service,scope=WireScope('domain'),scope_ref='scope',scope_version='v1',
        security_ref='key',security_version='v1',registration_policy_ref='policy',registration_policy_version='v1',
        service_requirements_ref='requirements',service_requirements_version='v1',curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],principals={client_keys[0]:'operator'},
        grants={'operator':(PrincipalGrant(client,(grant,)),)})
    ready=queue.Queue(); stop=threading.Event(); (tmp_path/'guard').mkdir()
    def serve():
        owner=host=None
        try:
            state=[]
            def factory():
                guard=LocalAccountSessionGuard(tmp_path/'guard','acct','owner')
                journal=GatewayDirectCommandJournal.create(tmp_path/'journal','acct',generation=guard.acquire())
                dispatcher=GatewayDirectDispatcher(journal,'TEST',guard,lambda *_:None,lambda _:(),lambda _:True)
                state.append((journal,guard))
                return GatewayDirectOwnerResources(dispatcher,journal,guard.release)
            owner=GatewayDirectOwner(factory,queue_size=4,max_queue_wait_seconds=10,busy_timeout_seconds=10)
            assert owner.start(10)
            journal,guard=state[0]
            control=owner.submit_service_call(lambda:journal.enable_controls(guard)).result(timeout_seconds=5)
            binding=GatewayDirectServiceBinding(journal,{'strategy':GatewayDirectGrant('s',frozenset({'X'}))},
                owner=owner,control=control,control_grants={'operator':GatewayControlGrant('actor',True,frozenset(),ops)})
            host=ServiceHost(service_config,authority,tmp_path/'registry',
                lambda *_:ComponentResources.borrowed({'binding':binding},ready=lambda:True))
            host.start(); ready.put(('ready',journal.journal_id,host._registry))
            while not stop.is_set(): host.serve_once(10)
        except BaseException as error:
            ready.put(('error',type(error).__name__,str(error)))
        finally:
            if host is not None and host.state=='started': host.close()
            if owner is not None: owner.close(10)
    server=threading.Thread(target=serve,daemon=True); server.start(); runtime=None
    try:
        started=ready.get(timeout=20); assert started[0]=='ready',started
        runtime=ThreadedServiceClientRuntime(lambda:ServiceClientRuntime(client_config,identity=client,
            scope_resolver=lambda _:scope,peer_resolver=lambda _:service,
            transport_factory=ZmqTransportFactory({'key':ClientCurveKeys(server_keys[0],client_keys[0],client_keys[1])}),
            requested_operations={'application.direct_write':ops}))
        snapshot = runtime.start(timeout_seconds=10)
        assert snapshot.healthy
        def call(op,args):
            return runtime.call('application.direct_write',op,args,account_ref='acct',scope_ref='scope',timeout_seconds=5)
        assert call('direct.control.identity',{})['journal_id']==started[1]
        assert call('direct.control.target_status',{'cause_id':'absent','operation_id':'op'}) == {
            'status':'NOT_FOUND','cause_id':'absent','operation_id':'op'}
        import pytest
        with pytest.raises(ServiceRuntimeError, match='AMBIGUOUS_OR_MISSING_ROUTE'):
            call('direct.control.status',{'cause_id':'absent'})
        from tests.unit.test_service_registry import _next_request
        registered = snapshot.bindings[0]
        denied = started[2].handle('operator',_next_request('direct.control.status',{
            'registration_id':registered.registration_id,'service_epoch':registered.service_epoch,
            'instance_id':'gateway','arguments':{'cause_id':'absent'}},identity=client,scope=scope))
        assert not denied['ok'] and denied['code'] in {'SCOPE_DENIED','CAPABILITY_UNAVAILABLE'}, denied
    finally:
        if runtime is not None: runtime.close(timeout_seconds=10)
        stop.set(); server.join(15); assert not server.is_alive()
