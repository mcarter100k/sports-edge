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
2. **Compares with sharp odds, only before each kickoff.** Once an hour it
   pairs each Kalshi game with the same game on Pinnacle, using The Odds
   API's free listings. It covers only the owner's sports
   (`config/slate_sports.json`: NFL, college football, NBA, NHL, MLB and
   European soccer). A pairing is used only if both teams, the sport and the
   start time all agree, and only one game fits. Anything else is logged as
   `UNMATCHED` and never compared. Games starting within 30 minutes of each
   other form a time slot.
   - **When prices are pulled:** Pinnacle prices for a slot are pulled
     only shortly before it starts. With `ODDS_PLAN=free` that is once,
     about 30 minutes before, with the earliest slots first until the
     day's credits run out. With `ODDS_PLAN=paid` it is 60 and 15 minutes
     before.
   - **No credit on unconfirmed fees:** a game whose fee is not confirmed
     (MLB, today) never costs a credit.
3. **Logs price gaps, without trading.** At each of those checks, it
   assumes a $10 stake at Kalshi's best ask, walking the order book if the
   best price is too thin. If the price after Kalshi's fee is at least 3
   cents below the fair chance, it writes a row to
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

**Messages** (run after every 15-minute cycle, and at 8:00 AM):
- **A morning iMessage:** today's games, yesterday's results and the track
  record.
- **One iMessage per pick at each check.** The picks are the ledger rows that
  check wrote. When a two-team game offers two ways to back one team ("Yes on
  A" and "No on B"), only the cheaper is shown, but both are logged.
- **"Don't bet now"** when prices weren't checked.
- **A full sheet** in iCloud Drive at `sports-edge/sheet.txt`.

Every message ends with the stage: Practice, Early signal, Supported or
Confirmed. The bar for each is fixed in `docs/specs/slate_alerts_spec.md`.

**Daily cap.** Each bet you took today counts $10 while it is open, $10 if it
lost, and nothing if it won. At $30, alerts say "Daily cap reached — no more
bets today."

### Logging your bets (Apple Shortcuts)

The system reads your replies from `iCloud Drive/sports-edge/replies.txt`, one
per line, and never writes to that file. A line looks like:

```
2026-10-22T19:05:12-07:00 took 42
2026-10-22T19:06:40-07:00 skip 3
```

- **The date at the front** lets a reply be matched to the alert it answers.
  A line without one is timed when the Mac first reads it.
- **Reply forms:**
  - `took 42`, or `took 42.3` for an average price;
  - `took 3 42` names pick #3;
  - `skip`, or `skip 3` for pick #3.
- **When the number is needed:** the alert says so when one check showed
  more than one pick.

**Set it up once:**
1. On the Mac, create the folder `sports-edge` in iCloud Drive if it is not
   there. Then create an empty `replies.txt` in it.
2. On the iPhone, in Shortcuts, make a shortcut named **Log bet** with these
   actions:
   1. **Receive** `Text` input from `Share Sheet` (or **Ask for Input**,
      Text, prompt `took 42 / skip 3`).
   2. **Current Date**, then **Format Date**: Date Format `ISO 8601`,
      Include ISO 8601 Time on.
   3. **Text**: `Formatted Date` + one space + `Shortcut Input` (or
      `Provided Input`).
   4. **Append to Text File**: the text from step 3, File Path
      `sports-edge/replies.txt` in iCloud Drive, **Make New Line** on.
3. **To log replies automatically:** Shortcuts → Automation → New → Message.
   - Set **Message Contains** to `took`, choose **Run Immediately**, and use
     the same actions with the message's Content as the text.
   - Make a second automation the same way for `skip`.
   - Run **Log bet** by hand if the automation does not fire for messages you
     send to yourself.

**Checking it works:** the sheet's "Bet log" line reads `read` once the file
is there, and `unreadable (...)` with the reason when it is not. For example:
"not downloaded to this Mac yet" means iCloud has not synced the file. The
Shortcut steps above have not yet been tested on a phone.

### Rating a slip

Run `venv/bin/python -m sports_edge.rate slip.txt` on a slip in the
plain-text format of `tests/fixtures/slip_nba_9leg.txt`. It prints:
- each leg's sharp-book fair chance and an A–F grade after Kalshi's fee,
  where it can;
- the payout's markup over the legs' own prices.

It places nothing and stores nothing. A stored sharp-book read older than 15
minutes costs one odds credit per sport. The rules are in
`docs/specs/rate_slip_spec.md`.

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
scripts/launchd/install.sh  # loads the scheduled jobs
```

`.env` holds `ODDS_API_KEY` (from the-odds-api.com) and `IMESSAGE_TO` (your
own number). The free plan has 500 credits a month; `ODDS_PLAN=free` (the
default) keeps to 15 a day, and `ODDS_PLAN=paid` switches the schedule.
Without a key, prices are still recorded, but nothing is compared, and the
weekly heartbeat says so.

## Where things are

| Path | What |
|---|---|
| `data/sports_edge.duckdb` | prices, markets, settlements, reference odds, matches, run log |
| `data/shadow_ledger.csv` | gaps logged (append-only) |
| `data/calibration_grades.csv`, `data/shadow_grades.csv` | grading results (append-only) |
| `data/bet_log.csv` | your "took" and "skip" replies, as logged (append-only) |
| iCloud Drive `sports-edge/replies.txt` | your replies, appended by your Shortcut (read only) |
| iCloud Drive `sports-edge/sheet.txt` | today's slate, open picks and results (rewritten; a view) |
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
