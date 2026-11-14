"""Private online snapshots of both databases, with an isolated restore check."""
import argparse
import os
import sqlite3
from pathlib import Path

from .config import settings


def snapshot(source, destination):
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        src = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
            restored = sqlite3.connect(":memory:")
            try:
                dst.backup(restored)
                if restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Restore integrity check failed")
                for (name,) in dst.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    quoted = '"' + name.replace('"', '""') + '"'
                    query = "SELECT count(*) FROM " + quoted
                    if dst.execute(query).fetchone() != restored.execute(query).fetchone():
                        raise RuntimeError("Restored table differs")
            finally:
                restored.close()
        finally:
            src.close()
            dst.close()
    except BaseException:
        Path(destination).unlink(missing_ok=True)
        raise


def backup(destination, state_dir):
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ("room.sqlite3", "r2-guard.sqlite3"):
        snapshot(Path(state_dir) / name, destination / name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(args.destination, settings().state_dir)
    print("Database backups and isolated restore checks passed.")
