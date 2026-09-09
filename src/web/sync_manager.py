import hashlib
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests

from src.config import ARCHIVE_DIR, RAW_DIR, DB_PATH
from src.db.repository import ArchiveRepository
from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector
from src.scraper.client import ScraperClient
from src.scraper.posters import fetch_poster_from_tmdb

SYNC_STATUS_FILE = ARCHIVE_DIR / "sync_status.json"
POSTERS_DIR = ARCHIVE_DIR / "posters"


class ArchiveSyncManager:
    """
    Manages background archive operations (incremental sync, poster backfill,
    and database optimization) for the DVDRewind web interface.
    """
    _instance: Optional["ArchiveSyncManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_state()
            return cls._instance

    def _init_state(self):
        self.is_running = False
        self.task_type = "idle"  # "sync", "posters", "vacuum", "idle"
        self.status_message = "Ready"
        self.current_action = ""
        self.cancel_requested = False
        self.start_time: Optional[float] = None
        self.log_lines: List[Dict[str, str]] = []
        self.stats = {
            "new_titles": 0,
            "revisions_updated": 0,
            "checked": 0,
            "errors": 0,
            "current_fid": 0,
        }
        self.thread: Optional[threading.Thread] = None

    def log(self, msg: str, style: str = ""):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {"ts": ts, "msg": msg, "style": style}
        self.log_lines.append(entry)
        if len(self.log_lines) > 200:
            self.log_lines = self.log_lines[-200:]

    def get_status(self) -> Dict[str, Any]:
        last_sync = None
        if SYNC_STATUS_FILE.exists():
            try:
                last_sync = json.loads(SYNC_STATUS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        db_titles = 0
        db_size_mb = 0.0
        try:
            repo = ArchiveRepository()
            db_titles = repo.conn.execute("SELECT COUNT(*) FROM titles WHERE is_missing = 0").fetchone()[0]
            repo.close()
            if DB_PATH.exists():
                db_size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 2)
        except Exception:
            pass

        elapsed = None
        if self.start_time and self.is_running:
            elapsed = round(time.time() - self.start_time, 1)

        return {
            "is_running": self.is_running,
            "task_type": self.task_type,
            "status_message": self.status_message,
            "current_action": self.current_action,
            "stats": self.stats,
            "elapsed_seconds": elapsed,
            "log_lines": self.log_lines[-60:],
            "last_sync": last_sync,
            "db_titles": db_titles,
            "db_size_mb": db_size_mb,
        }

    def start_sync(self, limit: Optional[int] = None) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.is_running = True
            self.task_type = "sync"
            self.cancel_requested = False
            self.status_message = "Starting incremental sync..."
            self.current_action = "Initializing"
            self.start_time = time.time()
            self.stats = {"new_titles": 0, "revisions_updated": 0, "checked": 0, "errors": 0, "current_fid": 0}
            self.log("🚀 Starting incremental sync with DVDCompare...", "cyan")

            self.thread = threading.Thread(target=self._run_sync, args=(limit,), daemon=True)
            self.thread.start()
            return True

    def start_posters_backfill(self, limit: Optional[int] = None) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.is_running = True
            self.task_type = "posters"
            self.cancel_requested = False
            self.status_message = "Starting poster backfill..."
            self.current_action = "Initializing"
            self.start_time = time.time()
            self.stats = {"new_titles": 0, "revisions_updated": 0, "checked": 0, "errors": 0, "current_fid": 0}
            self.log("🖼 Starting offline poster backfill pass...", "cyan")

            self.thread = threading.Thread(target=self._run_posters, args=(limit,), daemon=True)
            self.thread.start()
            return True

    def start_vacuum(self) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.is_running = True
            self.task_type = "vacuum"
            self.cancel_requested = False
            self.status_message = "Starting database optimization..."
            self.current_action = "Optimizing"
            self.start_time = time.time()
            self.stats = {"new_titles": 0, "revisions_updated": 0, "checked": 0, "errors": 0, "current_fid": 0}
            self.log("🧹 Starting SQLite database maintenance...", "cyan")

            self.thread = threading.Thread(target=self._run_vacuum_task, daemon=True)
            self.thread.start()
            return True

    def cancel(self) -> bool:
        if self.is_running:
            self.cancel_requested = True
            self.status_message = "Canceling operation..."
            self.log("⏹ Stop signal sent — waiting for current item to finish...", "yellow")
            return True
        return False

    def _run_sync(self, limit: Optional[int] = None):
        try:
            repo = ArchiveRepository()
            client = ScraperClient()
            collector = WarningCollector()
            parser = DVDCompareParser(collector)
            cur = repo.conn.cursor()

            # 1. Check DVDCompare homepage
            self.status_message = "Checking DVDCompare homepage for revisions..."
            self.current_action = "Fetching homepage"
            self.log("Checking DVDCompare homepage for recent reviews & revisions...", "cyan")

            homepage_fids = []
            try:
                r = requests.get(
                    "https://www.dvdcompare.net/",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DVDRewindArchive/1.0"},
                    timeout=15,
                )
                if r.status_code == 200:
                    found = set(int(m) for m in re.findall(r"film\.php\?fid=(\d+)", r.text))
                    homepage_fids = sorted(list(found))
                    if limit and limit > 0:
                        homepage_fids = homepage_fids[:limit]
                    self.log(f"Found {len(homepage_fids)} comparison links on homepage.", "dim")
            except Exception as e:
                self.log(f"Homepage fetch error: {e}", "red")

            for fid in homepage_fids:
                if self.cancel_requested:
                    break
                self.stats["checked"] += 1
                self.stats["current_fid"] = fid
                self.current_action = f"Checking FID {fid}"

                cur.execute("SELECT source_hash FROM titles WHERE fid = ?", (fid,))
                row = cur.fetchone()
                stored_hash = row[0] if row else None

                try:
                    status, content, meta = client.fetch_film(fid, save_to_raw=True)
                    if not content or meta.get("is_missing"):
                        continue
                    sha256 = meta.get("content_sha256")
                    if stored_hash is None or sha256 != stored_hash:
                        raw_path = RAW_DIR / f"{fid}.html"
                        parsed = parser.parse(content, fid=fid)
                        clean_title = parsed.get("clean_title", f"FID-{fid}")
                        num_releases = len(parsed.get("releases", []))
                        repo.save_parsed_comparison(parsed, source_hash=sha256, raw_html_path=str(raw_path))

                        # Check poster
                        cur.execute("SELECT poster_url FROM titles WHERE fid = ?", (fid,))
                        p_row = cur.fetchone()
                        if not p_row or not p_row[0] or p_row[0] == "/static/images/missing_poster.svg":
                            p_url = fetch_poster_from_tmdb(parsed.get("imdb_id"), clean_title, parsed.get("year"))
                            if p_url:
                                repo.update_poster_url(fid, p_url)

                        self.stats["revisions_updated"] += 1
                        action = "Ingested new" if stored_hash is None else "Updated revised"
                        self.log(f"✅ {action} FID {fid}: {clean_title} ({num_releases} releases)", "green")
                    time.sleep(1.0)
                except Exception as e:
                    self.stats["errors"] += 1
                    self.log(f"Error checking FID {fid}: {e}", "red")

            # 2. Probe frontier beyond MAX(fid)
            if not self.cancel_requested:
                cur.execute("SELECT MAX(fid) FROM titles WHERE is_missing = 0")
                max_row = cur.fetchone()
                max_db_fid = max_row[0] if max_row and max_row[0] else 0
                probe_start = max(max_db_fid + 1, 1)

                self.status_message = f"Probing for new titles starting at FID {probe_start}..."
                self.log(f"Probing frontier starting at FID {probe_start:,}...", "cyan")

                consecutive_misses = 0
                max_misses = limit if (limit and limit > 0 and limit < 15) else 15
                current_probe = probe_start
                existing_fids = set(r[0] for r in cur.execute("SELECT fid FROM titles").fetchall())

                while not self.cancel_requested and consecutive_misses < max_misses and (limit is None or self.stats["new_titles"] < limit):
                    self.stats["current_fid"] = current_probe
                    self.current_action = f"Probing FID {current_probe}"

                    if current_probe in existing_fids:
                        current_probe += 1
                        continue

                    try:
                        status, content, meta = client.fetch_film(current_probe, save_to_raw=True)
                        if meta.get("is_missing"):
                            consecutive_misses += 1
                            current_probe += 1
                            time.sleep(1.0)
                            continue

                        text_check = content[:4000].decode("latin1", errors="replace")
                        if "FILMID NOT FOUND" in text_check or "Unable to find film details" in text_check:
                            consecutive_misses += 1
                            current_probe += 1
                            time.sleep(1.0)
                            continue

                        sha256 = meta.get("content_sha256")
                        parsed = parser.parse(content, fid=current_probe)
                        clean_title = parsed.get("clean_title", f"FID-{current_probe}")
                        num_releases = len(parsed.get("releases", []))
                        fmt = parsed.get("format_category", "?")
                        raw_path = RAW_DIR / f"{current_probe}.html"

                        repo.save_parsed_comparison(parsed, source_hash=sha256, raw_html_path=str(raw_path))

                        p_url = fetch_poster_from_tmdb(parsed.get("imdb_id"), clean_title, parsed.get("year"))
                        if p_url:
                            repo.update_poster_url(current_probe, p_url)

                        existing_fids.add(current_probe)
                        self.stats["new_titles"] += 1
                        consecutive_misses = 0
                        self.log(f"🎉 New Title! FID {current_probe}: {clean_title} [{fmt}] ({num_releases} releases)", "green")
                        time.sleep(1.5)
                    except Exception as e:
                        consecutive_misses += 1
                        self.stats["errors"] += 1
                        self.log(f"Error probing FID {current_probe}: {e}", "red")

                    current_probe += 1

            # 3. Save sync status
            elapsed = round(time.time() - (self.start_time or time.time()), 1)
            total_db = cur.execute("SELECT COUNT(*) FROM titles WHERE is_missing = 0").fetchone()[0]
            status_data = {
                "last_sync": datetime.now().isoformat(),
                "status": "canceled" if self.cancel_requested else "success",
                "homepage_checked": len(homepage_fids),
                "revisions_updated": self.stats["revisions_updated"],
                "new_titles_ingested": self.stats["new_titles"],
                "total_db_titles": total_db,
                "elapsed_seconds": elapsed,
            }
            try:
                SYNC_STATUS_FILE.write_text(json.dumps(status_data, indent=2), encoding="utf-8")
            except Exception:
                pass

            repo.close()

            # 4. Auto-vacuum
            if not self.cancel_requested:
                self._run_vacuum_internal()

            final_status = "Sync canceled" if self.cancel_requested else "Sync completed successfully"
            self.log(f"🏁 {final_status} in {elapsed}s. Ingested: {self.stats['new_titles']}, Revised: {self.stats['revisions_updated']}, Total in DB: {total_db:,}", "bold")
            self.status_message = final_status

        except Exception as e:
            self.log(f"❌ Sync error: {e}", "red")
            self.status_message = f"Error: {e}"
        finally:
            self.is_running = False
            self.current_action = ""

    def _run_posters(self, limit: Optional[int] = None):
        try:
            repo = ArchiveRepository()
            cur = repo.conn.cursor()
            cur.execute(
                "SELECT fid, clean_title, year, imdb_id, format_category FROM titles "
                "WHERE (poster_url IS NULL OR poster_url = '' OR poster_url = '/static/images/missing_poster.svg') "
                "AND is_missing = 0 AND clean_title NOT LIKE '%NOT FOUND%' AND clean_title NOT LIKE 'FID-%' "
                "ORDER BY fid ASC"
            )
            missing = cur.fetchall()
            total_missing = len(missing)
            if limit and limit > 0:
                missing = missing[:limit]

            self.log(f"Found {total_missing:,} titles needing posters. Processing {len(missing):,}...", "cyan")

            fetched = 0
            for idx, (fid, title, year, imdb_id, fmt) in enumerate(missing, start=1):
                if self.cancel_requested:
                    break
                self.stats["checked"] += 1
                self.stats["current_fid"] = fid
                self.status_message = f"Fetching poster {idx}/{len(missing)}: {title}"
                self.current_action = f"Poster for FID {fid}"

                try:
                    p_url = fetch_poster_from_tmdb(imdb_id, title, year)
                    if p_url:
                        repo.update_poster_url(fid, p_url)
                        fetched += 1
                        self.stats["new_titles"] += 1
                        self.log(f"🖼 ({idx}/{len(missing)}) FID {fid}: Found poster for '{title}'", "green")
                    else:
                        self.log(f"• ({idx}/{len(missing)}) FID {fid}: No poster found for '{title}'", "dim")
                    time.sleep(1.0)
                except Exception as e:
                    self.stats["errors"] += 1
                    self.log(f"Error fetching poster for FID {fid}: {e}", "red")

            repo.close()
            final_status = "Poster backfill canceled" if self.cancel_requested else "Poster backfill complete"
            self.log(f"🏁 {final_status}. Successfully fetched: {fetched} posters.", "bold")
            self.status_message = final_status

        except Exception as e:
            self.log(f"❌ Poster backfill error: {e}", "red")
            self.status_message = f"Error: {e}"
        finally:
            self.is_running = False
            self.current_action = ""

    def _run_vacuum_internal(self):
        try:
            self.status_message = "Optimizing database & running VACUUM..."
            self.current_action = "Database VACUUM"
            self.log("🧹 Verifying SQLite integrity & defragmenting database...", "cyan")

            repo = ArchiveRepository()
            cur = repo.conn.cursor()

            cur.execute("PRAGMA integrity_check;")
            res = cur.fetchone()[0]
            if res == "ok":
                self.log("• Database integrity: OK", "green")
            else:
                self.log(f"• Integrity warning: {res}", "yellow")

            try:
                cur.execute("INSERT INTO titles_fts(titles_fts) VALUES('optimize');")
                repo.conn.commit()
                self.log("• Full-text search index (FTS5): Optimized", "green")
            except Exception as e:
                self.log(f"• FTS optimize note: {e}", "dim")

            size_before = DB_PATH.stat().st_size / (1024 * 1024)
            cur.execute("VACUUM;")
            repo.conn.commit()
            size_after = DB_PATH.stat().st_size / (1024 * 1024)
            saved_kb = (size_before - size_after) * 1024

            self.log(f"• VACUUM complete: {size_after:.2f} MB (reclaimed {saved_kb:.1f} KB)", "green")
            repo.close()
        except Exception as e:
            self.log(f"Database optimization error: {e}", "red")

    def _run_vacuum_task(self):
        try:
            self._run_vacuum_internal()
            self.status_message = "Database optimization complete"
        finally:
            self.is_running = False
            self.current_action = ""
