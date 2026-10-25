# Spec: Kalshi read-only client (`sports_edge/kalshi.py`)

Status: Phase 1. Protected file.

## Sources (read 2026-10-21)

- OpenAPI spec `https://docs.kalshi.com/openapi.yaml` (version 3.34.0).
  Its `servers` list names `https://external-api.kalshi.com/trade-api/v2` as
  the "Production Trade API server" and `api.elections.kalshi.com` as "also
  supported".
- "Making Unauthenticated Requests" guide (docs.kalshi.com): market data
  endpoints need no API key on `https://external-api.kalshi.com/trade-api/v2`.
- In the OpenAPI spec, `/series`, `/events`, `/markets` and
  `/markets/{ticker}` carry no `security` block. Only portfolio, order and
  account endpoints do.
- Rate limits page (`/getting_started/rate_limits`): budgets are in tokens
  per second for **authenticated** requests (Basic read = 200 tokens/s, and
  most requests cost 10 tokens, so 20 requests/s). A 429 returns
  `{"error": "too many requests"}` with **no** `Retry-After` header. The
  docs recommend exponential backoff. Limits for unauthenticated requests
  are not documented.

## Behaviour

1. **Base URL:** `https://external-api.kalshi.com/trade-api/v2`, overridable
   with `KALSHI_BASE_URL` (used by the forced-failure test).
2. **GET only.** The client has one request method, `get(path, params)`.
   There is no code path that sends POST/PUT/DELETE, no signing code, and no
   `KALSHI-ACCESS-*` header. A test asserts this from the module source.
3. **Self rate limit:** at most `KALSHI_MAX_RPS` requests per second
   (default 5, a quarter of the documented Basic read rate, since the
   unauthenticated limit is unknown).
4. **Retries:** on 429, 5xx and connection or timeout errors, retry with
   backoff of 1, 2, 4, 8 and 16 seconds (5 retries). After the last retry the
   error is raised. Any other 4xx is raised at once. The timeout is 30 s
   per request.
5. **Pagination:** follow `cursor` until it is empty. A hard cap of 2,000
   pages raises `PaginationLimit` instead of returning a truncated list.
6. **Validation:** a response missing a field the spec marks required (for
   example `markets`, `cursor`, or a market's `ticker`) raises
   `KalshiSchemaError`. Missing data is never defaulted.
7. **Prices and counts:** `*_dollars` strings are parsed to `Decimal`, and
   `*_fp` strings to `Decimal`. An empty string or null becomes `None`, never
   0.

## Endpoints used

| Purpose | Call |
|---|---|
| Sports discovery | `GET /series?category=Sports` (matches any entry in a series' `categories`) |
| Open events with markets | `GET /events?status=open&with_nested_markets=true&limit=200` (paginated) |
| Settled markets | `GET /markets?status=settled&min_settled_ts=<unix>&mve_filter=exclude&limit=1000` (paginated) |
| One market (settlement fallback) | `GET /markets/{ticker}`, then `GET /historical/markets/{ticker}` if not found |

## Sports discovery: no hardcoded leagues

A series is a sports series if and only if the `/series?category=Sports`
response returns it. A market's **sport** is the first entry of its series'
`tags` (for example `Basketball` or `Soccer`), or `Untagged` when there are
none. Its **competition** is the event's `product_metadata.competition`
when present. Both come from the API at run time.

Observed 2026-10-21: 4,029 sports series, 8,325 open sports events and
93,241 open sports markets. Combo (multivariate) events are excluded by
`/events`.

## Amendment 2026-10-25: a broken HTTP response is retried like a connection error

The grader of 2026-10-24 (3:38 AM PT) died on `http.client.IncompleteRead`, a
chunked response cut off part-way. Rule 4 retries connection and timeout
errors, but the client caught only `URLError`, `TimeoutError`,
`ConnectionError` and `OSError`. `IncompleteRead` is none of those: it is an
`http.client.HTTPException`, so one broken response ended the whole run.
Reproduced 2026-10-25 1:36 AM PT on `main`: a local server that sends a chunk
header for 100 bytes, then 12 bytes, then closes, made `KalshiClient.get`
raise `IncompleteRead` after 1 attempt.

**Change:** rule 4 now covers every `http.client.HTTPException`
(`IncompleteRead`, `BadStatusLine`, `RemoteDisconnected` and the rest of that
class), with the same backoff. After the last retry the call fails with
`KalshiError`, naming the last error. A certificate failure is still never
retried. The odds client is unchanged: it has no retries, and any error there
already fails the run loudly.
