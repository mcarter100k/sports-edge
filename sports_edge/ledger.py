"""Append-only CSV ledgers (docs/specs/shadow_ledger_spec.md).

Every ledger write in the package goes through append_rows. It locks the
file, refuses a header mismatch, appends, and fsyncs. Nothing opens a
ledger for overwrite.
"""
import csv
import fcntl
import io
import os
from pathlib import Path


class LedgerSchemaMismatch(Exception):
    pass


def read_header(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    with open(path, newline="") as fh:
        return next(csv.reader(fh), None)


def append_rows(path, header, rows):
    """Append dict rows to a CSV ledger. Returns the number written.

    Writes the header only when the file is new or empty. If the file
    already has a different header, raises LedgerSchemaMismatch and
    writes nothing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    for row in rows:
        extra = set(row) - set(header)
        if extra:
            raise LedgerSchemaMismatch(f"{path.name}: unknown fields {sorted(extra)}")
    with open(path, "a+", newline="") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.seek(0)
            first = fh.readline()
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
            if first == "":
                writer.writeheader()
            else:
                existing = next(csv.reader([first]))
                if existing != list(header):
                    raise LedgerSchemaMismatch(
                        f"{path.name}: header on disk {existing} != expected {list(header)}")
            for row in rows:
                writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in header})
            fh.seek(0, os.SEEK_END)
            fh.write(buf.getvalue())
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return len(rows)


def read_rows(path):
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))
