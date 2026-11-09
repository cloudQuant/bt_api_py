"""Offline packet-level SNTP validation; no real network is used."""

import struct

import pytest

from bt_api_py import clock_verification as clock


class FakeSocket:
    def __init__(self, mutation):
        self.mutation = mutation
        self.peer = ('192.0.2.1', 123)
        self.request = None

    def settimeout(self, _timeout):
        pass

    def connect(self, address):
        assert address == self.peer

    def getpeername(self):
        return self.peer

    def send(self, packet):
        self.request = packet
        return len(packet)

    def recvfrom(self, _size):
        packet = bytearray(48)
        packet[0], packet[1] = 0x24, 2  # NTPv4 server, synchronized
        packet[24:32] = packet[32:40] = packet[40:48] = self.request[40:48]
        source = self.peer
        if self.mutation == 'origin':
            packet[24:32] = b'\0' * 8
            # An unrelated forged response offering a 120-second offset.
            seconds, fraction = struct.unpack('!II', packet[32:40])
            packet[32:40] = packet[40:48] = struct.pack('!II', seconds + 120, fraction)
        elif self.mutation == 'source':
            source = ('192.0.2.2', 123)
        elif self.mutation == 'port':
            source = ('192.0.2.1', 124)
        elif self.mutation == 'mode':
            packet[0] = 0x23
        elif self.mutation == 'version':
            packet[0] = 0x14
        elif self.mutation == 'leap':
            packet[0] |= 0xc0
        elif self.mutation == 'stratum':
            packet[1] = 0
        elif self.mutation == 'zero':
            packet[32:40] = b'\0' * 8
        elif self.mutation == 'reversed':
            seconds, fraction = struct.unpack('!II', packet[40:48])
            packet[40:48] = struct.pack('!II', seconds - 1, fraction)
        elif self.mutation == 'processing':
            seconds, fraction = struct.unpack('!II', packet[40:48])
            packet[40:48] = struct.pack('!II', seconds + 1, fraction)
        elif self.mutation == 'short':
            packet = packet[:47]
        return bytes(packet), source

    def close(self):
        pass


def setup_socket(monkeypatch, mutation):
    fake = FakeSocket(mutation)
    monkeypatch.setattr(clock.socket, 'getaddrinfo', lambda *_: [(None, None, None, None, fake.peer)])
    monkeypatch.setattr(clock.socket, 'socket', lambda *_: fake)
    return fake


def test_valid_reply_uses_nonzero_request_transmit(monkeypatch):
    fake = setup_socket(monkeypatch, None)
    offset, bound = clock._sntp_query('synthetic', 2)
    assert fake.request[40:48] != b'\0' * 8
    assert abs(offset) < 1_000_000_000
    assert 0 <= bound < 1_000_000_000


@pytest.mark.parametrize('mutation', [
    'origin', 'source', 'port', 'mode', 'version', 'leap', 'stratum',
    'zero', 'reversed', 'processing', 'short',
])
def test_invalid_or_unrelated_reply_is_rejected(monkeypatch, mutation):
    setup_socket(monkeypatch, mutation)
    with pytest.raises(OSError):
        clock._sntp_query('synthetic', 2)


def test_replayed_response_cannot_match_fresh_request(monkeypatch):
    fake = setup_socket(monkeypatch, None)
    monkeypatch.setattr(clock.secrets, 'randbits', lambda _bits: 1)
    clock._sntp_query('synthetic', 2)
    old_origin = fake.request[40:48]
    original_recv = fake.recvfrom

    def replay(size):
        response, source = original_recv(size)
        return response[:24] + old_origin + response[32:], source

    fake.recvfrom = replay
    monkeypatch.setattr(clock.secrets, 'randbits', lambda _bits: 2)
    with pytest.raises(OSError, match='unmatched'):
        clock._sntp_query('synthetic', 2)


def test_wall_clock_jump_during_request_is_rejected(monkeypatch):
    setup_socket(monkeypatch, None)
    wall = iter((1_800_000_000_000_000_000, 1_800_000_010_000_000_000))
    mono = iter((5_000_000_000, 5_001_000_000))
    monkeypatch.setattr(clock.time, 'time_ns', lambda: next(wall))
    monkeypatch.setattr(clock.time, 'monotonic_ns', lambda: next(mono))
    with pytest.raises(OSError, match='timing'):
        clock._sntp_query('synthetic', 2)


@pytest.mark.parametrize('jump_ns', [-10_000_000, -9_000_000, 9_000_000, 10_000_000])
def test_small_wall_clock_jumps_are_included_in_error_bound(monkeypatch, jump_ns):
    setup_socket(monkeypatch, None)
    wall = iter((1_800_000_000_000_000_000, 1_800_000_000_001_000_000 + jump_ns))
    mono = iter((5_000_000_000, 5_001_000_000))
    monkeypatch.setattr(clock.time, 'time_ns', lambda: next(wall))
    monkeypatch.setattr(clock.time, 'monotonic_ns', lambda: next(mono))
    monkeypatch.setattr(clock.secrets, 'randbits', lambda _bits: 0)
    _, bound = clock._sntp_query('synthetic', 2)
    assert bound >= abs(jump_ns)


@pytest.mark.parametrize('jump_ns', [-10_000_001, 10_000_001])
def test_wall_clock_jump_over_tolerance_is_rejected(monkeypatch, jump_ns):
    setup_socket(monkeypatch, None)
    wall = iter((1_800_000_000_000_000_000, 1_800_000_000_001_000_000 + jump_ns))
    mono = iter((5_000_000_000, 5_001_000_000))
    monkeypatch.setattr(clock.time, 'time_ns', lambda: next(wall))
    monkeypatch.setattr(clock.time, 'monotonic_ns', lambda: next(mono))
    with pytest.raises(OSError, match='timing'):
        clock._sntp_query('synthetic', 2)
