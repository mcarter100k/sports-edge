# Spec: nightly encrypted off-machine backup (`sports_edge/backup.py`, `scripts/run_backup.sh`)

Status: Milestone 1, owner-directed 2026-10-22. Not a protected file. The
backup only reads evidence. It never changes the live database, the CSV
ledgers or `logs/`.

**Why.** All evidence is one DuckDB file, a few CSV ledgers and `logs/`,
on one laptop. Nothing was copied off the machine. A lost or failed disk
would lose every observation.

## What a run does (`python -m sports_edge.backup run`)

1. **Database: an export, never a copy of the live file.** It opens the
   live database through `Store.open(read_only=True)`, under the same lock
   as the scheduled jobs, so it never reads a half-written run. It runs
   `EXPORT DATABASE … (FORMAT parquet)`, which writes `schema.sql`,
   `load.sql` and one Parquet file per table. It records each table's row
   count, read in the same locked session.
2. **CSV ledgers:** every `data/*.csv`, each read under a shared `flock`
   (the ledger writer takes an exclusive one), so no half-appended row is
   copied.
3. **Logs:** everything under `logs/`.
4. **Frozen research datasets:** `$SPORTS_EDGE_RESEARCH_DIR/datasets/`, if
   present. These are the inputs registered studies cite by id.
5. **Manifest** (`manifest.json`): every file's path, size and sha256; the
   table row counts; the DuckDB version; the time.
6. **Archive:** tar (paths sorted) then gzip.
7. **Encryption:**
   `/usr/bin/openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -salt -pass env:BACKUP_PASSPHRASE`.
   - The passphrase comes from `.env` (`BACKUP_PASSPHRASE`) and is never on
     a command line.
   - Any OpenSSL 1.1.1 or later, or LibreSSL 3.3 or later, decrypts it.
   - CBC has no built-in integrity check. Integrity comes from the
     ciphertext sha256 (step 8) and, after decryption, from the manifest
     hashes.
8. **Sidecar:** `<name>.sha256` holds the sha256 of the encrypted file.
9. **Upload** to `BACKUP_TARGET`:
   - `b2` (the default): Backblaze B2 native API. Authorize, get an upload
     URL, then upload the encrypted file and its sidecar. Each upload sends
     `X-Bz-Content-Sha1`, and the SHA-1 B2 returns must equal the local one.
   - `dir:<path>`: copy both files to a folder, for example an external
     drive. Used by the tests.
10. **Local copies:** kept in `BACKUP_DIR` (default `~/sports-edge-backups`,
    outside the repo). Only the newest 7 archives are kept there. These are
    copies; the evidence itself is never deleted.
11. **Output:** one line, `BACKUP OK name=… bytes=… sha256=… tables=… uploaded=<target>`.

**Archive name:** `sports-edge-<UTC yyyymmddThhmmssZ>.tar.gz.enc`.

## Failure is loud

The run exits nonzero, and the wrapper alerts loudly through
`scripts/common.sh`, if:
- `BACKUP_PASSPHRASE` is missing or shorter than 16 characters;
- the database lock is not obtained within the normal wait;
- any export, hash, encryption or upload step fails;
- B2 returns a SHA-1 that differs from the local one;
- `BACKUP_TARGET=b2` and the B2 settings are missing. In that case the
  local archive is still written, and the message says so.

## Weekly heartbeat

The Sunday proof-of-life lists `backup` next to `snapshot` and `grader`. It
needs attention if the backup is failing, has never succeeded, or last
succeeded more than 48 hours ago.

## Restore (`python -m sports_edge.backup restore <archive> <empty folder>`)

1. Check the archive's sha256 against its sidecar.
2. Decrypt, then unpack into the folder.
3. Check every file against the manifest.
4. `IMPORT DATABASE` into `<folder>/sports_edge.duckdb`, and compare every
   table's row count with the manifest.
5. Print `RESTORE OK` with the counts, or exit nonzero naming the first
   difference.

`python -m sports_edge.backup fetch <name> <folder>` downloads an archive and
its sidecar from B2 with a restore key (`BACKUP_B2_RESTORE_KEY_ID`,
`BACKUP_B2_RESTORE_APP_KEY`), for use on any machine.

## Schedule

`com.quis.sportsedge.backup` runs `scripts/run_backup.sh` at 04:15 local
time. That is after the 03:00 daily scan and the 03:30 grader, and between
snapshot runs; the lock serializes it with any job that overlaps.

## Off-machine target: recommendation and cost

**Recommended: Backblaze B2.**
- **Cost:** storage is $6 per TB per month, and the first 10 GB are free.
  An archive is a few MB today (the database is 19 MB, and Parquet
  compresses it). A year of nightly archives is under 10 GB, so the
  expected cost is **$0 a month**. These are published list prices; check
  them when signing up.
- **Bucket:** private, with **Object Lock** on. A default retention of 90
  days means no key, including one stolen from the laptop, can delete a
  recent backup.
- **Laptop key:** restricted to that bucket, with the `writeFiles`
  capability only. It can add backups but cannot read, list or delete them.
- **Restore key:** `readFiles` and `listFiles`, kept in a password manager,
  never on the laptop.

**Alternative: Azure Blob Storage.**
- **Cost:** about $0.02 per GB per month (Hot, LRS) at list price. Under
  10 GB that is roughly $0.20 a month or less.
- **Setup:** a SAS token limited to create and write on one container, with
  an immutability policy, gives the same guarantees.
- **Why not first:** it needs a subscription and SAS rotation. Supporting it
  means adding one target to `upload()`.

## Owner setup (once)

1. Create the B2 bucket and the two keys above.
2. In `.env`, set `BACKUP_PASSPHRASE` (at least 16 characters),
   `BACKUP_B2_KEY_ID`, `BACKUP_B2_APP_KEY` and `BACKUP_B2_BUCKET_ID`.
3. **Store the passphrase in a password manager.** Without it no backup can
   be decrypted, and a lost laptop takes the only copy with it.
4. Run `scripts/launchd/install.sh`.

## Tests

- **Gate 1:**
  - backup then restore round trip, with byte-equal files and equal row
    counts;
  - a tampered archive, a wrong passphrase, a missing passphrase and a
    missing B2 setting each fail loudly;
  - the B2 upload is checked against a fake transport (headers, SHA-1
    check, a SHA-1 mismatch failing);
  - retention keeps 7;
  - the live database is unchanged;
  - the wrapper runs under `/bin/bash`.
- **Gate 2:** a real export of the live database, encrypted, then restored
  into a temporary folder. Restored row counts must equal the live counts
  read under the lock, and every file hash must match the manifest. The B2
  upload stays PENDING until the owner creates the bucket and keys.
