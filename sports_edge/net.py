"""HTTPS GET shared by the Kalshi and odds clients.

Certificate verification is always on. Homebrew's Python on this machine has
no default CA file (its openssl@3/cert.pem link is missing), so when neither
the interpreter nor SSL_CERT_FILE provides one, the first existing bundle in
CA_FALLBACKS is used.
"""
import os
import ssl
import urllib.error
import urllib.request

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


def https_get(url, timeout, headers=None):
    """One GET. Returns (status, response_headers, body). HTTP errors return their status."""
    global _CONTEXT
    if _CONTEXT is None:
        _CONTEXT = ssl_context()
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/json", "User-Agent": "sports-edge/phase1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_CONTEXT) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


def is_permanent(exc):
    """Errors that retrying cannot fix."""
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError)
