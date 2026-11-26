"""Copy the DVDRewind database to another disk and keep the newest few copies.

Meant for a host crontab (weekly). It uses SQLite's online backup API, so the
site keeps running, and checks each copy before keeping it.

    DVDREWIND_DB_PATH      database to copy (default: archive/dvdrewind.db)
    DVDREWIND_BACKUP_DIR   where copies go (default: backups/); point it at a
                           different disk than the database
    DVDREWIND_BACKUP_KEEP  how many copies to keep (default 2)
"""
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(os.environ.get("DVDREWIND_DB_PATH") or PROJECT_ROOT / "archive" / "dvdrewind.db")
DEST_DIR = Path(os.environ.get("DVDREWIND_BACKUP_DIR") or PROJECT_ROOT / "backups")
KEEP = int(os.environ.get("DVDREWIND_BACKUP_KEEP", "2"))


def main() -> int:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    final = DEST_DIR / f"dvdrewind-{datetime.now():%Y%m%d-%H%M}.db"
    tmp = final.with_name(final.name + ".tmp")
    started = time.time()

    src = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        # One step: a single read snapshot, so writes by the site during the
        # copy can't restart it (WAL readers don't block writers).
        src.backup(dst)
        check = dst.execute("PRAGMA quick_check").fetchone()[0]
        titles = dst.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if check != "ok":
        tmp.unlink(missing_ok=True)
        print(f"{datetime.now():%F %T} FAILED quick_check on the copy: {check}", flush=True)
        return 1
    tmp.replace(final)

    copies = sorted(DEST_DIR.glob("dvdrewind-*.db"))
    for old in copies[:-KEEP]:
        old.unlink()
    size_mb = final.stat().st_size / 2**20
    print(f"{datetime.now():%F %T} saved {final.name}: {size_mb:.0f} MB, {titles} titles, "
          f"{time.time() - started:.0f} s; kept {min(len(copies), KEEP)} copies", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
