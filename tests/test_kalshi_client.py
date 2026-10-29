import http.client
import json
import re
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sports_edge import kalshi, net
from sports_edge.kalshi import KalshiClient, KalshiError, KalshiSchemaError, PaginationLimit, dec

PKG = Path(kalshi.__file__).parent


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        status, body = self.responses.pop(0)
        return status, json.dumps(body).encode() if not isinstance(body, bytes) else body


def client(responses, **kw):
    sleeps = []
    t = Transport(responses)
    c = KalshiClient(base_url="https://example.test/v2", max_rps=1000, transport=t,
                     sleep=sleeps.append, **kw)
    return c, t, sleeps


def test_read_only_by_construction():
    for name in ("kalshi.py", "net.py"):
        src = (PKG / name).read_text()
        assert not re.search(r"\b(POST|PUT|DELETE|PATCH)\b", src), name
        assert "KALSHI-ACCESS" not in src.upper().replace("_", "-"), name
    assert 'method="GET"' in (PKG / "net.py").read_text()
    assert [m for m in dir(KalshiClient) if m.lower() in ("post", "put", "delete", "order",
                                                          "create_order")] == []


def test_retries_429_then_succeeds():
    c, t, sleeps = client([(429, b'{"error":"too many requests"}'), (503, b""), (200, {"ok": 1})])
    assert c.get("/x") == {"ok": 1}
    assert [x for x in sleeps if x >= 1] == [1, 2] and len(t.urls) == 3


def test_gives_up_after_six_attempts():
    c, t, sleeps = client([(503, b"")] * 6)
    with pytest.raises(KalshiError, match="after 6 attempts"):
        c.get("/x")
    assert [s for s in sleeps if s >= 1] == [1, 2, 4, 8, 16]


def test_client_error_not_retried():
    c, t, _ = client([(404, b'{"error":"not found"}')])
    with pytest.raises(KalshiError, match="HTTP 404"):
        c.get("/x")
    assert len(t.urls) == 1


def test_connection_error_retried():
    calls = []

    def flaky(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("connection reset")
        return 200, b'{"a": 1}'

    c = KalshiClient(base_url="https://e.test", max_rps=1000, transport=flaky, sleep=lambda s: None)
    assert c.get("/x") == {"a": 1} and len(calls) == 3


def test_certificate_failure_is_not_retried():
    import ssl
    import urllib.error
    calls = []

    def bad_cert(url, timeout):
        calls.append(url)
        raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer"))

    c = KalshiClient(base_url="https://e.test", max_rps=1000, transport=bad_cert, sleep=lambda s: None)
    with pytest.raises(KalshiError):
        c.get("/x")
    assert len(calls) == 1


def test_pagination_follows_cursor_and_sends_params():
    c, t, _ = client([(200, {"events": [{"event_ticker": "A", "series_ticker": "S"}], "cursor": "c1"}),
                      (200, {"events": [{"event_ticker": "B", "series_ticker": "S"}], "cursor": ""})])
    pages = list(c.open_events_with_markets())
    assert [e["event_ticker"] for _, p in pages for e in p] == ["A", "B"]
    assert "with_nested_markets=true" in t.urls[0] and "status=open" in t.urls[0]
    assert "cursor=c1" in t.urls[1]


def test_pagination_cap_raises_not_truncates(monkeypatch):
    monkeypatch.setattr(kalshi, "MAX_PAGES", 3)
    c, _, _ = client([(200, {"markets": [], "cursor": "again"})] * 3)
    with pytest.raises(PaginationLimit):
        list(c.settled_markets(0))


def test_missing_required_field_raises():
    c, _, _ = client([(200, {"cursor": ""})])
    with pytest.raises(KalshiSchemaError):
        list(c.open_events_with_markets())
    c, _, _ = client([(200, {"events": [{"event_ticker": "A", "series_ticker": "S",
                                         "markets": [{"status": "active", "close_time": "x"}]}],
                             "cursor": ""})])
    with pytest.raises(KalshiSchemaError, match="ticker"):
        list(c.open_events_with_markets())


def test_dec_never_defaults_to_zero():
    assert dec("") is None and dec(None) is None
    assert dec("0.4100") == Decimal("0.41")
    with pytest.raises(KalshiSchemaError):
        dec("n/a")


def test_market_lookup_falls_back_to_historical_then_none():
    c, t, _ = client([(404, b"{}"), (200, {"market": {"ticker": "T"}})])
    assert c.market("T")["ticker"] == "T"
    assert "/historical/markets/T" in t.urls[1]
    c, _, _ = client([(404, b"{}"), (404, b"{}")])
    assert c.market("T") is None


def test_throttle_spaces_requests():
    now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    c = KalshiClient(base_url="https://e.test", max_rps=5, transport=lambda u, t: (200, b"{}"),
                     sleep=sleep, clock=lambda: now[0])
    c.get("/a"); c.get("/b"); c.get("/c")
    assert sleeps == [pytest.approx(0.2), pytest.approx(0.2)]


def test_default_base_url_and_override(monkeypatch):
    assert KalshiClient().base_url == "https://external-api.kalshi.com/trade-api/v2"
    monkeypatch.setenv("KALSHI_BASE_URL", "https://other.test/v2")
    assert KalshiClient().base_url == "https://other.test/v2"


def test_ssl_context_uses_fallback_bundle_when_none_configured(monkeypatch, tmp_path):
    import ssl
    bundle = tmp_path / "cert.pem"
    bundle.write_text("")
    monkeypatch.setattr(ssl, "get_default_verify_paths",
                        lambda: type("P", (), {"cafile": None})())
    monkeypatch.setattr(net, "CA_FALLBACKS", (str(tmp_path / "missing.pem"), str(bundle)))
    used = {}
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: used.setdefault("cafile", cafile))
    net.ssl_context()
    assert used["cafile"] == str(bundle)


def test_orderbook_parses_both_sides_best_first():
    body = {"orderbook_fp": {"yes_dollars": [["0.3900", "9632.89"], ["0.4100", "178.61"]],
                             "no_dollars": [["0.5400", "500.00"], ["0.5800", "570333.51"]]}}
    c, t, _ = client([(200, body)])
    book = c.orderbook("KXNBAGAME-26OCT22CLEPHI-CLE", depth=5)
    assert book["yes"] == [(Decimal("0.41"), Decimal("178.61")), (Decimal("0.39"), Decimal("9632.89"))]
    assert book["no"][0] == (Decimal("0.58"), Decimal("570333.51"))
    assert t.urls == ["https://example.test/v2/markets/KXNBAGAME-26OCT22CLEPHI-CLE/orderbook?depth=5"]


def test_orderbook_without_its_field_raises():
    c, _, _ = client([(200, {"orderbook": {}})])
    with pytest.raises(KalshiSchemaError):
        c.orderbook("T")


# A broken HTTP response is retried (kalshi_client_spec.md, amendment 2026-10-25) --------------

@pytest.mark.parametrize("error", [http.client.IncompleteRead(b'{"ser', 88), http.client.BadStatusLine("")])
def test_a_broken_response_is_retried_then_succeeds(error):
    calls = []

    def broken_once(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise error
        return 200, b'{"a": 1}'

    sleeps = []
    c = KalshiClient(base_url="https://e.test", max_rps=1000, transport=broken_once, sleep=sleeps.append)
    assert c.get("/x") == {"a": 1} and len(calls) == 2 and 1 in sleeps


class _CutOff(BaseHTTPRequestHandler):
    """Sends a chunk header for 100 bytes, 12 bytes of it, then closes: the 2026-10-24 grader failure.
    Requests after the first `broken` get a whole body."""
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.server.hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        if self.server.hits <= self.server.broken:
            self.wfile.write(b'64\r\n{"series": [')
            self.wfile.flush()
            self.close_connection = True
            return
        body = b'{"series": []}'
        self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")

    def log_message(self, *args):
        pass


def cut_off_server(broken):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CutOff)
    server.hits, server.broken = 0, broken
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_real_cut_off_response_is_retried_and_recovers():
    server = cut_off_server(broken=1)
    try:
        c = KalshiClient(base_url=f"http://127.0.0.1:{server.server_address[1]}", max_rps=1000, sleep=lambda s: None)
        assert c.get("/series") == {"series": []}
        assert server.hits == 2 and c.retries == 1 and c.requests == 2
    finally:
        server.shutdown()
        server.server_close()


def test_a_real_cut_off_response_every_time_ends_in_kalshi_error():
    server = cut_off_server(broken=99)
    try:
        c = KalshiClient(base_url=f"http://127.0.0.1:{server.server_address[1]}", max_rps=1000, sleep=lambda s: None)
        with pytest.raises(KalshiError, match="after 6 attempts: IncompleteRead"):
            c.get("/series")
        assert server.hits == 6
        assert c.retries == 5           # the sixth failure has no retry after it
    finally:
        server.shutdown()
        server.server_close()

