"""sports_edge/net.py: a GET gives up when its body is not complete within DEADLINE_S, even though
every socket read succeeds (the snapshot of 2026-10-24 3:38 AM PT; docs/specs/heartbeat_spec.md)."""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sports_edge import kalshi, net


class Trickle:
    """A slow response: one byte per read1, the clock moving 1 s per byte, for 100 bytes."""
    status, headers = 200, {"Transfer-Encoding": "chunked"}

    def __init__(self, clock, body=None):
        self.clock, self.body = clock, body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read1(self, n):
        if self.body is not None:
            out, self.body = self.body[:n], self.body[n:]
            return out
        if self.clock.t >= 100:
            return b""
        self.clock.t += 1
        return b"x"


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_a_body_that_keeps_trickling_gives_up_at_the_deadline_without_naming_the_url(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda req, timeout, context: Trickle(clock))
    with pytest.raises(TimeoutError) as exc:
        net.https_get("https://api.example/v4/odds?apiKey=SECRET", 30, deadline_s=5, clock=clock)
    assert str(exc.value) == "response not complete within 5 s" and 5 < clock.t <= 6
    assert "SECRET" not in str(exc.value)


def test_a_complete_body_is_returned_whole(monkeypatch):
    body = b'{"series": []}' * 10000
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda req, timeout, context: Trickle(Clock(), body))
    assert net.https_get("https://x/series", 30) == (200, {"Transfer-Encoding": "chunked"}, body)


def test_the_deadline_is_120_s():
    assert net.DEADLINE_S == 120 and kalshi.TIMEOUT_S == 30


# A real socket on localhost: every read gets a byte well inside the socket timeout. -------------------

class _Slow(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for _ in range(self.server.chunks):
                self.wfile.write(b"1\r\n \r\n")
                self.wfile.flush()
                time.sleep(0.2)
            self.wfile.write(b"2\r\n{}\r\n0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def slow_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
    server.chunks = 20                     # about 4 s of body, 0.2 s between bytes
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.integration   # localhost only; the marker lets the test open a socket
def test_the_socket_timeout_alone_does_not_bound_a_trickling_response(slow_server):
    """The bug: a 1 s socket timeout, yet the call takes about 4 s and succeeds."""
    start = time.monotonic()
    status, _, body = net.https_get(slow_server + "/series", 1, deadline_s=60)
    assert status == 200 and body.strip() == b"{}" and time.monotonic() - start > 3


@pytest.mark.integration
def test_the_deadline_bounds_a_trickling_response(slow_server):
    start = time.monotonic()
    with pytest.raises(TimeoutError, match="response not complete within 1 s"):
        net.https_get(slow_server + "/series", 1, deadline_s=1)
    assert time.monotonic() - start < 2


@pytest.mark.integration
def test_the_kalshi_client_gives_up_on_a_trickling_response(slow_server, monkeypatch):
    """The real KalshiClient and transport: each attempt stops at the deadline, then the call fails."""
    monkeypatch.setattr(net, "DEADLINE_S", 0.5)
    client = kalshi.KalshiClient(base_url=slow_server, max_rps=1000, sleep=lambda s: None)
    start = time.monotonic()
    with pytest.raises(kalshi.KalshiError, match="after 6 attempts: TimeoutError: response not complete within 0.5 s"):
        client.sports_series()
    assert client.requests == 6 and time.monotonic() - start < 6
