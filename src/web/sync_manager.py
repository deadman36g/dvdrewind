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
from src.scraper.posters import fetch_poster_from_tmdb, find_imdb_match

INITIAL_MAX_FID = 76200
POST_INITIAL_TAIL_FIDS = 100
POST_INITIAL_FALLBACK_WINDOW = 500
SYNC_STATUS_FILE = ARCHIVE_DIR / "sync_status.json"
POST_INITIAL_STATE_FILE = ARCHIVE_DIR / "post_initial_sync.json"
FAILED_FIDS_FILE = ARCHIVE_DIR / "sync_failed_fids.json"
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
        self.task_type = "idle"  # "sync", "posters", "imdb", "vacuum", "idle"
        self.status_message = "Ready"
        self.current_action = ""
        self.cancel_requested = False
        self.start_time: Optional[float] = None
        self.log_lines: List[Dict[str, str]] = []
        self.recent_discoveries: List[Dict[str, Any]] = []
        self.failed_fids: List[Dict[str, Any]] = []
        self.start_metrics: Dict[str, Any] = {}
        self.phase_start_time: Optional[float] = None
        self._metrics_cache: Dict[str, Any] = {}
        self._metrics_cache_at = 0.0
        self.stats = self._blank_stats()
        self.thread: Optional[threading.Thread] = None

    def _blank_stats(self) -> Dict[str, Any]:
        return {
            "new_titles": 0,
            "revisions_updated": 0,
            "posters_fetched": 0,
            "matches_found": 0,
            "unmatched": 0,
            "checked": 0,
            "scanned_fids": 0,
            "errors": 0,
            "current_fid": 0,
            "next_fid": INITIAL_MAX_FID + 1,
            "phase": "idle",
            "phase_current": 0,
            "phase_total": 0,
            "current_title": "",
            "current_format": "",
            "current_year": None,
            "poster_found": False,
        }

    def _snapshot_metrics(self, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if not force and self._metrics_cache and (now - self._metrics_cache_at) < 5:
            return dict(self._metrics_cache)

        metrics = {
            "titles": 0,
            "releases": 0,
            "posters": 0,
            "raw_html": 0,
            "db_size_mb": 0.0,
        }
        try:
            repo = ArchiveRepository()
            metrics["titles"] = repo.conn.execute("SELECT COUNT(*) FROM titles WHERE is_missing = 0").fetchone()[0]
            metrics["releases"] = repo.conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
            metrics["posters"] = repo.conn.execute(
                "SELECT COUNT(*) FROM titles WHERE is_missing = 0 AND poster_url IS NOT NULL "
                "AND poster_url != '' AND poster_url != '/static/images/missing_poster.svg'"
            ).fetchone()[0]
            # Avoid walking tens of thousands of files on mergerfs for every
            # status refresh. Every imported comparison records its raw archive
            # path in SQLite, which is the fast authoritative count here.
            metrics["raw_html"] = repo.conn.execute(
                "SELECT COUNT(*) FROM titles WHERE raw_html_path IS NOT NULL AND raw_html_path != ''"
            ).fetchone()[0]
            repo.close()
        except Exception:
            pass
        try:
            if DB_PATH.exists():
                metrics["db_size_mb"] = round(DB_PATH.stat().st_size / (1024 * 1024), 2)
        except Exception:
            pass

        self._metrics_cache = metrics
        self._metrics_cache_at = now
        return dict(metrics)

    def _reset_task_state(self, phase: str = "starting", clear_failures: bool = False) -> None:
        self.stats = self._blank_stats()
        self.stats["phase"] = phase
        self.phase_start_time = time.time()
        self.recent_discoveries = []
        self.start_metrics = self._snapshot_metrics(force=True)
        if clear_failures:
            self.failed_fids = []
            try:
                FAILED_FIDS_FILE.write_text("[]\n", encoding="utf-8")
            except Exception:
                pass

    def _set_current_title(self, fid: int, parsed: Dict[str, Any], poster_found: bool = False) -> None:
        self.stats["current_fid"] = fid
        self.stats["current_title"] = parsed.get("clean_title") or f"FID-{fid}"
        self.stats["current_format"] = parsed.get("format_category") or "?"
        self.stats["current_year"] = parsed.get("year")
        self.stats["poster_found"] = bool(poster_found)

    def _record_discovery(self, fid: int, parsed: Dict[str, Any], poster_found: bool = False) -> None:
        entry = {
            "fid": fid,
            "title": parsed.get("clean_title") or f"FID-{fid}",
            "year": parsed.get("year"),
            "format": parsed.get("format_category") or "?",
            "releases": len(parsed.get("releases", [])),
            "poster_found": bool(poster_found),
            "ts": datetime.now().strftime("%H:%M:%S"),
        }
        self.recent_discoveries.append(entry)
        self.recent_discoveries = self.recent_discoveries[-20:]

    def _record_error(self, fid: int, error: Exception | str, phase: str) -> None:
        self.stats["errors"] += 1
        entry = {
            "fid": int(fid),
            "phase": phase,
            "error": str(error)[:500],
            "ts": datetime.now().isoformat(),
        }
        self.failed_fids.append(entry)
        self.failed_fids = self.failed_fids[-200:]
        try:
            FAILED_FIDS_FILE.write_text(json.dumps(self.failed_fids, indent=2), encoding="utf-8")
        except Exception:
            pass

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

        metrics = self._snapshot_metrics()
        elapsed = None
        phase_elapsed = None
        if self.start_time and self.is_running:
            elapsed = round(time.time() - self.start_time, 1)
        if self.phase_start_time and self.is_running:
            phase_elapsed = round(time.time() - self.phase_start_time, 1)

        post_initial = None
        if POST_INITIAL_STATE_FILE.exists():
            try:
                post_initial = json.loads(POST_INITIAL_STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        growth = {}
        if self.start_metrics:
            for key, value in metrics.items():
                start_value = self.start_metrics.get(key)
                if isinstance(value, (int, float)) and isinstance(start_value, (int, float)):
                    growth[key] = round(value - start_value, 2) if isinstance(value, float) else value - start_value

        return {
            "is_running": self.is_running,
            "task_type": self.task_type,
            "status_message": self.status_message,
            "current_action": self.current_action,
            "stats": dict(self.stats),
            "elapsed_seconds": elapsed,
            "phase_elapsed_seconds": phase_elapsed,
            "log_lines": self.log_lines[-80:],
            "recent_discoveries": list(self.recent_discoveries[-12:]),
            "failed_fids": list(self.failed_fids[-25:]),
            "last_sync": last_sync,
            "post_initial": post_initial,
            "metrics": metrics,
            "start_metrics": dict(self.start_metrics),
            "growth": growth,
            "db_titles": metrics.get("titles", 0),
            "db_size_mb": metrics.get("db_size_mb", 0.0),
        }

    def start_sync(self, limit: Optional[int] = None, force_from_initial: bool = False) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.is_running = True
            self.task_type = "sync"
            self.cancel_requested = False
            self.status_message = (
                "Starting full post-76,200 catch-up..."
                if force_from_initial
                else "Starting incremental sync..."
            )
            self.current_action = "Initializing"
            self.start_time = time.time()
            self._reset_task_state("starting", clear_failures=True)
            if force_from_initial:
                self.log("🚀 Starting complete catch-up from FID 76,201...", "cyan")
            else:
                self.log("🚀 Starting incremental sync with DVDCompare...", "cyan")

            self.thread = threading.Thread(target=self._run_sync, args=(limit, force_from_initial), daemon=True)
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
            self._reset_task_state("posters")
            self.log("🖼 Starting offline poster backfill pass...", "cyan")

            self.thread = threading.Thread(target=self._run_posters, args=(limit,), daemon=True)
            self.thread.start()
            return True

    def start_imdb_match(self, limit: Optional[int] = None, fid: Optional[int] = None) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self.is_running = True
            self.task_type = "imdb"
            self.cancel_requested = False
            self.status_message = "Starting IMDb repair..."
            self.current_action = "Initializing"
            self.start_time = time.time()
            self._reset_task_state("imdb")
            self.log("Starting conservative IMDb matching pass...", "cyan")

            self.thread = threading.Thread(target=self._run_imdb_matches, args=(limit, fid), daemon=True)
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
            self._reset_task_state("maintenance")
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

    def _run_sync(self, limit: Optional[int] = None, force_from_initial: bool = False):
        try:
            repo = ArchiveRepository()
            client = ScraperClient()
            collector = WarningCollector()
            parser = DVDCompareParser(collector)
            cur = repo.conn.cursor()

            # 1. Check DVDCompare homepage
            self.stats["phase"] = "homepage"
            self.phase_start_time = time.time()
            self.stats["phase_current"] = 0
            self.stats["phase_total"] = 0
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
                    self.stats["phase_total"] = len(homepage_fids)
                    self.log(f"Found {len(homepage_fids)} comparison links on homepage.", "dim")
            except Exception as e:
                self.log(f"Homepage fetch error: {e}", "red")

            for homepage_index, fid in enumerate(homepage_fids, start=1):
                if self.cancel_requested:
                    break
                self.stats["checked"] += 1
                self.stats["phase_current"] = homepage_index
                self.stats["current_fid"] = fid
                self.stats["current_title"] = ""
                self.stats["current_format"] = ""
                self.stats["current_year"] = None
                self.stats["poster_found"] = False
                self.current_action = f"Checking FID {fid}"

                cur.execute(
                    "SELECT source_hash, clean_title, year, format_category, poster_url FROM titles WHERE fid = ?",
                    (fid,),
                )
                row = cur.fetchone()
                stored_hash = row[0] if row else None
                if row:
                    self.stats["current_title"] = row[1] or ""
                    self.stats["current_year"] = row[2]
                    self.stats["current_format"] = row[3] or "?"
                    self.stats["poster_found"] = bool(row[4] and row[4] != "/static/images/missing_poster.svg")

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
                        p_url = None
                        cur.execute("SELECT poster_url FROM titles WHERE fid = ?", (fid,))
                        p_row = cur.fetchone()
                        if not p_row or not p_row[0] or p_row[0] == "/static/images/missing_poster.svg":
                            p_url = fetch_poster_from_tmdb(parsed.get("imdb_id"), clean_title, parsed.get("year"))
                            if p_url:
                                repo.update_poster_url(fid, p_url)
                                self.stats["posters_fetched"] += 1
                        else:
                            p_url = p_row[0]

                        self._set_current_title(fid, parsed, poster_found=bool(p_url))
                        action = "Ingested new" if stored_hash is None else "Updated revised"
                        if stored_hash is None:
                            self.stats["new_titles"] += 1
                            self._record_discovery(fid, parsed, poster_found=bool(p_url))
                        else:
                            self.stats["revisions_updated"] += 1
                        self.log(f"✅ {action} FID {fid}: {clean_title} ({num_releases} releases)", "green")
                    time.sleep(1.0)
                except Exception as e:
                    self._record_error(fid, e, "homepage")
                    self.log(f"Error checking FID {fid}: {e}", "red")

            # 2. Resume the durable post-initial catch-up scan.
            # The original web updater stopped after 15 misses. That was unsafe for a
            # sparse ID space, so the web UI now shares the CLI's persisted cursor.
            current_probe = INITIAL_MAX_FID + 1
            highest_seen_fid = INITIAL_MAX_FID
            existing_fids = set(r[0] for r in cur.execute("SELECT fid FROM titles").fetchall())

            if not self.cancel_requested:
                cur.execute("SELECT MAX(fid) FROM titles WHERE is_missing = 0")
                max_row = cur.fetchone()
                max_db_fid = max_row[0] if max_row and max_row[0] else 0
                homepage_high_fid = max(homepage_fids) if homepage_fids else 0

                sync_state = {}
                if POST_INITIAL_STATE_FILE.exists() and not force_from_initial:
                    try:
                        sync_state = json.loads(POST_INITIAL_STATE_FILE.read_text(encoding="utf-8"))
                    except Exception:
                        sync_state = {}

                saved_next_fid = sync_state.get("next_fid", INITIAL_MAX_FID + 1)
                try:
                    saved_next_fid = int(saved_next_fid)
                except (TypeError, ValueError):
                    saved_next_fid = INITIAL_MAX_FID + 1

                current_probe = INITIAL_MAX_FID + 1 if force_from_initial else max(INITIAL_MAX_FID + 1, saved_next_fid)
                known_high_fid = max(INITIAL_MAX_FID, max_db_fid, homepage_high_fid)
                try:
                    saved_highest_fid = int(sync_state.get("highest_seen_fid", INITIAL_MAX_FID) or INITIAL_MAX_FID)
                except (TypeError, ValueError):
                    saved_highest_fid = INITIAL_MAX_FID
                highest_seen_fid = max(known_high_fid, saved_highest_fid)

                if known_high_fid >= current_probe:
                    probe_end = known_high_fid + POST_INITIAL_TAIL_FIDS
                else:
                    probe_end = current_probe + POST_INITIAL_FALLBACK_WINDOW - 1
                if limit and limit > 0:
                    probe_end = min(probe_end, current_probe + limit - 1)

                catchup_start = current_probe
                self.stats["phase"] = "catchup"
                self.phase_start_time = time.time()
                self.stats["phase_current"] = 0
                self.stats["phase_total"] = max(0, probe_end - catchup_start + 1)
                self.status_message = f"Catching up FIDs {current_probe:,} through {probe_end:,}..."
                self.log(
                    f"Post-initial catch-up: scanning FIDs {current_probe:,} -> {probe_end:,} "
                    f"(newest known: {known_high_fid:,}).",
                    "cyan",
                )

                while not self.cancel_requested and current_probe <= probe_end:
                    self.stats["current_fid"] = current_probe
                    self.stats["next_fid"] = current_probe
                    self.stats["phase_current"] = self.stats["scanned_fids"] + 1
                    self.stats["phase_total"] = max(self.stats["phase_total"], probe_end - catchup_start + 1)
                    self.stats["current_title"] = ""
                    self.stats["current_format"] = ""
                    self.stats["current_year"] = None
                    self.stats["poster_found"] = False
                    self.current_action = f"Scanning FID {current_probe}"

                    if current_probe in existing_fids:
                        highest_seen_fid = max(highest_seen_fid, current_probe)
                    else:
                        try:
                            status, content, meta = client.fetch_film(current_probe, save_to_raw=True)
                            if not meta.get("is_missing"):
                                text_check = content[:4000].decode("latin1", errors="replace")
                                if "FILMID NOT FOUND" not in text_check and "Unable to find film details" not in text_check:
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
                                        self.stats["posters_fetched"] += 1

                                    existing_fids.add(current_probe)
                                    self.stats["new_titles"] += 1
                                    self._set_current_title(current_probe, parsed, poster_found=bool(p_url))
                                    self._record_discovery(current_probe, parsed, poster_found=bool(p_url))
                                    highest_seen_fid = max(highest_seen_fid, current_probe)
                                    if not limit:
                                        probe_end = max(probe_end, current_probe + POST_INITIAL_TAIL_FIDS)
                                        self.stats["phase_total"] = max(self.stats["phase_total"], probe_end - catchup_start + 1)
                                    self.log(
                                        f"🎉 New Title! FID {current_probe}: {clean_title} [{fmt}] ({num_releases} releases)",
                                        "green",
                                    )
                            time.sleep(1.0)
                        except Exception as e:
                            self._record_error(current_probe, e, "catchup")
                            self.log(f"Error scanning FID {current_probe}: {e}", "red")

                    self.stats["scanned_fids"] += 1
                    current_probe += 1
                    self.stats["next_fid"] = current_probe

                    if self.stats["scanned_fids"] % 25 == 0:
                        POST_INITIAL_STATE_FILE.write_text(json.dumps({
                            "next_fid": current_probe,
                            "highest_seen_fid": highest_seen_fid,
                            "updated_at": datetime.now().isoformat(),
                        }, indent=2), encoding="utf-8")

                POST_INITIAL_STATE_FILE.write_text(json.dumps({
                    "next_fid": current_probe,
                    "highest_seen_fid": highest_seen_fid,
                    "updated_at": datetime.now().isoformat(),
                }, indent=2), encoding="utf-8")

            # 3. Save sync status. Preserve one prior completed run so the CLI
            # can compare this run against the previous one without building an
            # unbounded history chain inside sync_status.json.
            elapsed = round(time.time() - (self.start_time or time.time()), 1)
            total_db = cur.execute("SELECT COUNT(*) FROM titles WHERE is_missing = 0").fetchone()[0]
            previous_sync = {}
            if SYNC_STATUS_FILE.exists():
                try:
                    previous_sync = json.loads(SYNC_STATUS_FILE.read_text(encoding="utf-8"))
                    if isinstance(previous_sync, dict):
                        previous_sync.pop("previous_sync", None)
                    else:
                        previous_sync = {}
                except Exception:
                    previous_sync = {}
            status_data = {
                "last_sync": datetime.now().isoformat(),
                "status": "canceled" if self.cancel_requested else "success",
                "homepage_checked": len(homepage_fids),
                "revisions_updated": self.stats["revisions_updated"],
                "new_titles_ingested": self.stats["new_titles"],
                "posters_fetched": self.stats["posters_fetched"],
                "errors": self.stats["errors"],
                "post_initial_scanned_fids": self.stats["scanned_fids"],
                "post_initial_next_fid": current_probe,
                "highest_seen_fid": highest_seen_fid,
                "total_db_titles": total_db,
                "elapsed_seconds": elapsed,
                "start_metrics": dict(self.start_metrics),
                "end_metrics": self._snapshot_metrics(force=True),
                "previous_sync": previous_sync,
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
            self.log(
                f"🏁 {final_status} in {elapsed}s. Scanned: {self.stats['scanned_fids']}, "
                f"Ingested: {self.stats['new_titles']}, Revised: {self.stats['revisions_updated']}, "
                f"Next FID: {current_probe:,}, Total in DB: {total_db:,}",
                "bold",
            )
            self.status_message = final_status

        except Exception as e:
            self.log(f"❌ Sync error: {e}", "red")
            self.status_message = f"Error: {e}"
        finally:
            self.is_running = False
            self.current_action = ""

    def _run_imdb_matches(self, limit: Optional[int] = None, fid: Optional[int] = None):
        repo = None
        try:
            repo = ArchiveRepository()
            cur = repo.conn.cursor()
            params: list[Any] = []
            sql = (
                "SELECT fid, clean_title, year, format_category, poster_url FROM titles "
                "WHERE is_missing = 0 AND (imdb_id IS NULL OR TRIM(imdb_id) = '') "
            )
            if fid is not None:
                sql += "AND fid = ? "
                params.append(int(fid))
            sql += "ORDER BY fid DESC"
            rows = cur.execute(sql, params).fetchall()
            total_missing = len(rows)
            if limit and limit > 0:
                rows = rows[:limit]

            self.stats["phase"] = "imdb"
            self.phase_start_time = time.time()
            self.stats["phase_current"] = 0
            self.stats["phase_total"] = len(rows)
            self.log(f"Found {total_missing:,} titles needing IMDb IDs. Processing {len(rows):,}...", "cyan")

            for idx, (row_fid, title, year, fmt, poster_url) in enumerate(rows, start=1):
                if self.cancel_requested:
                    break
                self.stats["checked"] += 1
                self.stats["phase_current"] = idx
                self.stats["current_fid"] = int(row_fid)
                self.stats["current_title"] = title or f"FID-{row_fid}"
                self.stats["current_format"] = fmt or "?"
                self.stats["current_year"] = year
                self.stats["poster_found"] = bool(poster_url and poster_url != "/static/images/missing_poster.svg")
                self.status_message = f"Matching IMDb {idx}/{len(rows)}: {title}"
                self.current_action = f"IMDb match for FID {row_fid}"

                try:
                    current_row = cur.execute("SELECT imdb_id FROM titles WHERE fid = ?", (row_fid,)).fetchone()
                    if current_row and str(current_row[0] or "").strip():
                        # A previous same-title/year match may already have
                        # propagated to this format sibling.
                        continue

                    match = find_imdb_match(title, year)
                    if not match:
                        self.stats["unmatched"] += 1
                        self.log(f"No confident IMDb match for FID {row_fid}: {title}", "dim")
                        time.sleep(0.25)
                        continue

                    imdb_id = str(match["imdb_id"])
                    affected = repo.update_imdb_id(int(row_fid), imdb_id)
                    self.stats["matches_found"] += max(1, affected)
                    self.log(
                        f"IMDb matched FID {row_fid}: {title} -> {imdb_id} ({match.get('confidence', 'verified')})",
                        "green",
                    )

                    if not poster_url or poster_url == "/static/images/missing_poster.svg":
                        p_url = fetch_poster_from_tmdb(imdb_id, title, year)
                        if p_url:
                            repo.update_poster_url(int(row_fid), p_url)
                            self.stats["posters_fetched"] += 1
                            self.stats["poster_found"] = True
                            self.log(f"Artwork filled from matched IMDb record for FID {row_fid}: {title}", "green")
                except Exception as exc:
                    self._record_error(int(row_fid), exc, "imdb")
                    self.log(f"IMDb repair error for FID {row_fid}: {exc}", "red")

                time.sleep(0.35)

            final_status = "IMDb repair canceled" if self.cancel_requested else "IMDb repair complete"
            self.stats["phase"] = "complete"
            self.log(
                f"{final_status}. Matched: {self.stats['matches_found']}, "
                f"unmatched: {self.stats['unmatched']}, errors: {self.stats['errors']}.",
                "bold",
            )
            self.status_message = final_status
        except Exception as exc:
            self.log(f"IMDb repair error: {exc}", "red")
            self.status_message = f"Error: {exc}"
        finally:
            if repo is not None:
                repo.close()
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

            self.stats["phase"] = "posters"
            self.stats["phase_current"] = 0
            self.stats["phase_total"] = len(missing)
            self.log(f"Found {total_missing:,} titles needing posters. Processing {len(missing):,}...", "cyan")

            fetched = 0
            for idx, (fid, title, year, imdb_id, fmt) in enumerate(missing, start=1):
                if self.cancel_requested:
                    break
                self.stats["checked"] += 1
                self.stats["phase_current"] = idx
                self.stats["current_fid"] = fid
                self.stats["current_title"] = title
                self.stats["current_format"] = fmt or "?"
                self.stats["current_year"] = year
                self.stats["poster_found"] = False
                self.status_message = f"Fetching poster {idx}/{len(missing)}: {title}"
                self.current_action = f"Poster for FID {fid}"

                try:
                    p_url = fetch_poster_from_tmdb(imdb_id, title, year)
                    if p_url:
                        repo.update_poster_url(fid, p_url)
                        fetched += 1
                        self.stats["posters_fetched"] += 1
                        self.stats["poster_found"] = True
                        self.log(f"🖼 ({idx}/{len(missing)}) FID {fid}: Found poster for '{title}'", "green")
                    else:
                        self.log(f"• ({idx}/{len(missing)}) FID {fid}: No poster found for '{title}'", "dim")
                    time.sleep(1.0)
                except Exception as e:
                    self._record_error(fid, e, "posters")
                    self.log(f"Error fetching poster for FID {fid}: {e}", "red")

            repo.close()
            final_status = "Poster backfill canceled" if self.cancel_requested else "Poster backfill complete"
            self.stats["phase"] = "complete"
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
            self.stats["phase"] = "maintenance"
            self.phase_start_time = time.time()
            self.stats["phase_current"] = 1
            self.stats["phase_total"] = 1
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
