"""HTTPS GET shared by the Kalshi and odds clients.

Certificate verification is always on. Homebrew's Python on this machine has
no default CA file (its openssl@3/cert.pem link is missing), so when neither
the interpreter nor SSL_CERT_FILE provides one, the first existing bundle in
CA_FALLBACKS is used.
"""
import os
import ssl
import time
import urllib.error
import urllib.request

# A whole GET, not one socket read. `timeout` restarts with every byte received, so a body that
# keeps trickling in never trips it (the snapshot of 2026-10-24 3:38 AM PT). Slowest real GET
# seen: 42 s for the 6.4 MB /series listing (2026-10-24).
DEADLINE_S = 120
READ_CHUNK = 65536

CA_FALLBACKS = (
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    "/etc/ssl/cert.pem",
)


def ssl_context():
    paths = ssl.get_default_verify_paths()
    if os.environ.get("SSL_CERT_FILE") or (paths.cafile and os.path.exists(paths.cafile)):
        return ssl.create_default_context()
    for candidate in CA_FALLBACKS:
        if os.path.exists(candidate):
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


_CONTEXT = None


def https_get(url, timeout, headers=None, deadline_s=None, clock=time.monotonic):
    """One GET. Returns (status, response_headers, body). HTTP errors return their status.
    `timeout` bounds each socket operation; the body must be complete within DEADLINE_S, or
    TimeoutError (whose message never carries the URL: the odds key is in its query)."""
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = ssl_context()
    deadline_s = DEADLINE_S if deadline_s is None else deadline_s
    give_up = clock() + deadline_s
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/json", "User-Agent": "sports-edge/phase1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CONTEXT) as resp:
            chunks = []
            while chunk := resp.read1(READ_CHUNK):
                chunks.append(chunk)
                if clock() > give_up:
                    raise TimeoutError(f"response not complete within {deadline_s:g} s")
            return resp.status, dict(resp.headers), b"".join(chunks)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


def is_permanent(exc):
    """Errors that retrying cannot fix."""
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError)
