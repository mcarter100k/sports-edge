import json
import re
from decimal import Decimal
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
