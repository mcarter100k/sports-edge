# sports-edge

A read-only research system for Kalshi sports markets. It never places an
order and never logs in to a trading account.

## What it does

Every 15 minutes it:

1. **Records prices.** Once a day (03:00 Los Angeles time) it reads every
   open sports market from Kalshi's public market data and stores the best
   bid, best ask, last price and volume. Sports and leagues are discovered
   from Kalshi, so nothing is hardcoded. Markets paired with a sharp-book
   line (see 2) are also recorded every hour, and every 15 minutes once they
   resolve within 48 hours. Every run prints how many paired markets each
   sport has. On Thursdays during Kalshi's maintenance (03:00–05:00 ET) runs
   are recorded as "exchange closed" and do not alert.
2. **Compares with sharp odds.** Once an hour it reads Pinnacle moneylines
   from The Odds API, strips out the bookmaker's margin to get a fair
   probability, and pairs each Kalshi game with the same game on Pinnacle.
   A pairing is used only if both teams, the sport and the start time all
   agree, and only one game fits. Anything else is logged as `UNMATCHED` and
   never compared.
3. **Logs gaps, without trading.** When Kalshi's price, after Kalshi's
   trading fee, is more than 3 cents per $1 contract away from the fair
   probability, before the game starts, it writes a row to
   `data/shadow_ledger.csv`. Fees come only from `config/fee_schedule.json`,
   which is built from Kalshi's fee schedule PDF in `docs/sources/`, with
   every value cited. If a series' fee is not confirmed, or Kalshi's API
   disagrees with the table, that market is skipped and the run says why.
   A fee is never guessed.

   When a sharp-book game starting within 24 hours has no Kalshi pair, it
   looks up that sport's Kalshi series directly, in case Kalshi listed the
   game after the daily read.

Every night at 03:30 Los Angeles time it **grades**: once markets settle,
it scores how well Kalshi's pre-game price and Pinnacle's fair price
predicted the result (Brier score and log loss), and works out what each
shadow row would have made or lost after fees.

Every Sunday at 09:00 it sends a **proof-of-life** notification, healthy or
not. If that notification doesn't arrive, something is wrong.

## What it does not do

- Place, size or manage trades.
- Model any sport. The only opinion it uses is Pinnacle's price.
- Claim an edge. With fewer than 20 independent games graded, every result
  is labelled INSUFFICIENT.

## Setup

```bash
cd /Users/quis/sports-edge
/opt/homebrew/bin/python3.14 -m venv venv
venv/bin/pip install -r requirements-dev.txt
cp .env.example .env        # then put your Odds API key in .env
venv/bin/python -m pytest -q
scripts/launchd/install.sh  # loads the three scheduled jobs
```

`.env` holds `ODDS_API_KEY` (from the-odds-api.com). The free plan has
500 credits a month, and the default budget of 15 a day stays inside it.
Without a key, prices are still recorded, but nothing is compared, and the
weekly heartbeat says so.

## Where things are

| Path | What |
|---|---|
| `data/sports_edge.duckdb` | prices, markets, settlements, reference odds, matches, run log |
| `data/shadow_ledger.csv` | gaps logged (append-only) |
| `data/calibration_grades.csv`, `data/shadow_grades.csv` | grading results (append-only) |
| `logs/<job>_<date>.log` | everything each run printed; dates are Los Angeles dates |
| `logs/alerts.log` | every notification sent |
| `docs/specs/` | how each piece is supposed to behave |
| `docs/validation_log.md` | what has been verified, and what is still waiting on reality |

All stored times are UTC. Anything meant for a person to read is in Los
Angeles time, with the zone shown.

## When something breaks

A failing job sends a notification with a sound, and then at most one
reminder an hour while it keeps failing. It sends a RECOVERED notice when
it works again. Each run's full output is in that day's log.

To test the alert path for real: `touch state/force_failure_once`. The
next 15-minute run fails on purpose and alerts, and the run after that
recovers.

## Changing it

Read `AGENTS.md` first. In short: work on a branch, write the spec before
the code, keep the tests green, and run `scripts/reality_run.py` against the
real APIs before merging. Never edit the data files by hand.
