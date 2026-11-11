"""The local Web host with the account read registration, as a separate process (63.14).

A synthetic Gateway with the account read port runs in this test process (synthetic
journal, account read model fed directly, CURVE keys); no venue or account. The host
runs from its module entry point in an isolated interpreter that cannot import AFI.
"""
import signal

import pytest
from test_iter63_gateway_web_gateway_authority import ACCOUNT, Gateway, cli_call

from bt_api_py.gateway_web_local_host import LocalGatewayWebHost
from tests.integration.test_iter63_gateway_web_host_process import (
    Browser,
    OrdinaryClient,
    _free_port,
    _host_config,
    _registrations,
    _start_host,
    _wait_ready,
    _write_protected,
)

TYPED = frozenset({'account.snapshot.typed'})


def _with_account_read(config):
    config['operators'][0]['account_operations'] = sorted(TYPED)
    return config


def _snapshot(browser):
    response = browser.client.get('/api/v1/scopes/desk/snapshot', headers=browser.headers)
    return response.status_code, response.json()


def test_host_shows_the_account_view_and_its_crash_leaves_the_owner_alone(tmp_path):
    with Gateway(tmp_path, account_reads={'account': TYPED}) as gateway:
        gateway.publish()
        cli = gateway.runtime('cli')
        journal_id = cli_call(cli, 'cli', 'direct.control.identity', {}, gateway)['journal_id']
        owner_generation = gateway.on_owner(lambda: gateway.journal.generation)
        client = OrdinaryClient(gateway, cli)
        client.start()
        token = 'd' * 64
        port = _free_port()
        origin = f'http://127.0.0.1:{port}'
        config = _write_protected(tmp_path / 'web-host.json',
                                  _with_account_read(_host_config(gateway, 'account', port, token)))
        log = tmp_path / 'web-host.log'
        processes = []
        try:
            before = _registrations(gateway)
            first = _start_host(config, log)
            processes.append(first)
            _wait_ready(first, origin)
            # Two registrations of the operator: the control mount and the account read.
            assert len(_registrations(gateway) - before) == 2
            browser = Browser(origin, token)
            code, view = _snapshot(browser)
            assert code == 200 and view['status'] == 'OBSERVED', view
            assert (view['view'], view['account_ref']) == ('account', ACCOUNT)
            assert view['source']['journal_id'] == journal_id
            assert view['source']['owner_generation'] == owner_generation
            browser.close()

            first.send_signal(signal.SIGKILL)
            assert first.wait(10) == -signal.SIGKILL
            # The owner did not notice: the account read model and the journal are as they were.
            gateway.publish(available='70')
            before = _registrations(gateway)
            second = _start_host(config, log)
            processes.append(second)
            _wait_ready(second, origin)
            own = _registrations(gateway) - before
            assert len(own) == 2
            browser = Browser(origin, token)
            code, view = _snapshot(browser)
            assert view['status'] == 'OBSERVED' and view['account']['balances'][0]['available'] == '70', view
            browser.close()
            second.send_signal(signal.SIGTERM)
            assert second.wait(20) == 0
            # A clean stop closes both of its own registrations (the killed host's leases expire later).
            assert not own & _registrations(gateway)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(10)
            client.stop.set()
            client.join(10)
        assert len(client.results) > 10
        assert set(client.results) == {('ok', 'OBSERVED', journal_id)}
        assert gateway.on_owner(lambda: gateway.journal.journal_id) == journal_id


def test_host_starts_without_the_account_read_when_the_gateway_does_not_grant_it(tmp_path):
    with Gateway(tmp_path) as gateway:  # no account read port
        token = 'e' * 64
        port = _free_port()
        origin = f'http://127.0.0.1:{port}'
        config = _write_protected(tmp_path / 'web-host.json',
                                  _with_account_read(_host_config(gateway, 'account', port, token)))
        process = _start_host(config, tmp_path / 'web-host.log')
        try:
            _wait_ready(process, origin)
            browser = Browser(origin, token)
            assert _snapshot(browser) == (403, {'detail': 'GATEWAY_SCOPE_OR_GRANT_UNAVAILABLE'})
            assert browser.causes()['status'] == 'OBSERVED'
            browser.close()
            process.send_signal(signal.SIGTERM)
            assert process.wait(20) == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(10)


@pytest.mark.parametrize('change', [
    {'account_operations': ['account.snapshot']},
    {'account_operations': ['account.read_after']},
    {'account_operations': ['account.snapshot.typed', 'account.snapshot.typed']},
    {'account_operations': 'account.snapshot.typed'},
    {'account_operations': ['account.snapshot.typed'], 'strategy_ref': 'strategy-a'},
])
def test_account_read_in_the_host_config_is_strict(tmp_path, change):
    with Gateway(tmp_path) as gateway:
        config = _host_config(gateway, 'account', _free_port(), 'f' * 64)
        config['operators'][0].update(change)
        with pytest.raises(ValueError, match='LOCAL_GATEWAY_WEB_CONFIG_INVALID'):
            LocalGatewayWebHost(_write_protected(tmp_path / 'web-host.json', config))
        # The same file without the change is accepted.
        config = _with_account_read(_host_config(gateway, 'account', _free_port(), 'f' * 64))
        host = LocalGatewayWebHost(_write_protected(tmp_path / 'web-host-ok.json', config))
        host.close()
