#!/usr/bin/env python
"""
╔══════════════════════════════════════════════════════════════╗
║          DVDRewind — Full Catalog Population Script          ║
║                                                              ║
║  Interactive live dashboard that crawls DVDCompare and        ║
║  ingests comparisons into your local archive.                ║
║                                                              ║
║  Phase 1: Interleaved Priority Lists                         ║
║           • Selected Directors (Carpenter, Craven, etc.)     ║
║           • Selected Franchises (Star Wars, Indy, etc.)      ║
║           • Boutique Labels (Criterion, Arrow, Eureka, etc.) ║
║                                                              ║
║  Phase 2: Full Catalog Sweep (FIDs 1 -> 76,200)              ║
║  Phase 3: Catch Up New Comparisons Added After 76,200         ║
║                                                              ║
║  Usage:   python populate_all.py                              ║
║  Stop:    Ctrl+C (safely saves progress, resume anytime)      ║
║  Resume:  Just run it again — skips already-ingested FIDs     ║
╚══════════════════════════════════════════════════════════════╝
"""

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Project setup — resolve from this file so the same script works on Windows,
# NAS/Docker deployments, and development workspaces.
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from rich import box

from src.config import ARCHIVE_DIR, RAW_DIR, DB_PATH
from src.db.repository import ArchiveRepository
from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector
from src.scraper.client import ScraperClient
from src.cli_monitor import (
    completion_message as cli_completion_message,
    retry_failed_fids as cli_retry_failed_fids,
    run_archive_status_monitor as cli_run_archive_status_monitor,
    search_archive as cli_search_archive,
)
from src.tui_app import run_tui_command_center

import requests

# ── Configuration ──────────────────────────────────────────────
INITIAL_MAX_FID = 76200   # Historical endpoint of the original full-catalog sweep
MAX_FID = INITIAL_MAX_FID # Dashboard compatibility for the original sweep
CRAWL_DELAY = 2.0        # Polite crawl delay
POSTER_DELAY = 1.0       # Poster fetch delay
POST_INITIAL_TAIL_FIDS = 100       # Keep looking this far beyond the newest known FID
POST_INITIAL_FALLBACK_WINDOW = 500 # Used when the homepage cannot reveal a high-water mark
PROGRESS_FILE = ARCHIVE_DIR / "populate_progress.json"
PRIORITY_FILE = ARCHIVE_DIR / "priority_queue.json"
SYNC_STATUS_FILE = ARCHIVE_DIR / "sync_status.json"
POST_INITIAL_STATE_FILE = ARCHIVE_DIR / "post_initial_sync.json"
POSTERS_DIR = ARCHIVE_DIR / "posters"
POSTERS_DIR.mkdir(parents=True, exist_ok=True)

console = Console()

# ── State ──────────────────────────────────────────────────────
class CrawlState:
    def __init__(self):
        self.phase = "Phase 1: Curated Priorities"
        self.priority_index = 0
        self.last_fid = 0
        self.ingested = 0
        self.skipped_existing = 0
        self.skipped_missing = 0
        self.errors = 0
        self.total_releases = 0
        self.posters_fetched = 0
        self.db_count = 0
        self.priority_total = 0
        self.current_fid = 0
        self.current_title = ""
        self.current_format = ""
        self.current_tag = ""
        self.current_status = "Starting..."
        self.start_time = time.time()
        self.log_lines = []
        self.running = True
        self.paused = False
        self.headless = False

    def log(self, msg, style=""):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append((ts, msg, style))
        if len(self.log_lines) > 18:
            self.log_lines = self.log_lines[-18:]
        if getattr(self, "headless", False):
            try:
                clean = Text.from_markup(msg).plain
            except Exception:
                clean = re.sub(r"\[/?(?:bold|dim|italic|underline|green|cyan|yellow|red|magenta|white|black|#\w+)[^\]]*\]", "", msg)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {clean}", flush=True)

    def save_progress(self):
        data = {
            "phase": self.phase,
            "priority_index": self.priority_index,
            "last_fid": self.last_fid,
            "ingested": self.ingested,
            "skipped_existing": self.skipped_existing,
            "skipped_missing": self.skipped_missing,
            "errors": self.errors,
            "total_releases": self.total_releases,
            "posters_fetched": self.posters_fetched,
            "saved_at": datetime.now().isoformat(),
        }
        PROGRESS_FILE.write_text(json.dumps(data, indent=2))

    def load_progress(self):
        if PROGRESS_FILE.exists():
            try:
                data = json.loads(PROGRESS_FILE.read_text())
                self.phase = data.get("phase", "Phase 1: Curated Priorities")
                self.priority_index = data.get("priority_index", 0)
                self.last_fid = data.get("last_fid", 0)
                self.current_fid = self.last_fid
                self.ingested = data.get("ingested", 0)
                self.skipped_existing = data.get("skipped_existing", 0)
                self.skipped_missing = data.get("skipped_missing", 0)
                self.errors = data.get("errors", 0)
                self.total_releases = data.get("total_releases", 0)
                self.posters_fetched = data.get("posters_fetched", 0)
                return True
            except Exception:
                pass
        return False

    @property
    def elapsed(self):
        return time.time() - self.start_time

    @property
    def speed(self):
        if self.elapsed < 1:
            return 0
        return (self.ingested + self.skipped_missing) / (self.elapsed / 3600)

    @property
    def eta_str(self):
        if self.speed < 1:
            return "calculating..."
        remaining = MAX_FID - self.current_fid
        hours = remaining / self.speed
        if hours > 24:
            return f"{hours/24:.1f} days"
        elif hours > 1:
            return f"{hours:.1f} hours"
        else:
            return f"{hours*60:.0f} min"


state = CrawlState()

# ── Graceful Shutdown ──────────────────────────────────────────
def signal_handler(sig, frame):
    state.running = False
    state.current_status = "Shutting down..."
    state.log("Ctrl+C received — saving progress and shutting down...", "bold yellow")

signal.signal(signal.SIGINT, signal_handler)

# ── Dashboard Rendering ───────────────────────────────────────
def build_dashboard():
    elapsed_str = str(timedelta(seconds=int(state.elapsed)))
    pct = (state.current_fid / MAX_FID * 100) if MAX_FID > 0 else 0

    # Header with current compared to final number
    header = Text()
    header.append("🎬 DVDRewind ", style="bold cyan")
    header.append("Catalog Population Engine", style="bold white")
    header.append(f"  •  FID {state.current_fid:,} / {MAX_FID:,} ({pct:.1f}%)", style="bold green")
    header.append(f"  •  {state.phase}", style="bold yellow")

    # Stats table
    stats = Table(box=box.SIMPLE, show_header=False, padding=(0, 2), expand=True)
    stats.add_column("label", style="dim", width=16)
    stats.add_column("value", style="bold", width=28)
    stats.add_column("label2", style="dim", width=16)
    stats.add_column("value2", style="bold")

    stats.add_row(
        "📥 Downloaded:", f"[yellow]{state.current_fid:,} / {MAX_FID:,}[/] [dim]({pct:.1f}%)[/]",
        "⏱ Elapsed:", elapsed_str,
    )
    db_str = f"[green]{state.ingested:,}[/] [dim]new ({state.db_count:,} in archive)[/]" if state.db_count else f"[green]{state.ingested:,}[/] [dim]new films[/]"
    stats.add_row(
        "✅ Ingested:", db_str,
        "⚡ Speed:", f"{state.speed:,.0f} FIDs/hr",
    )
    stats.add_row(
        "📀 Releases:", f"[cyan]{state.total_releases:,}[/]",
        "⏳ ETA:", state.eta_str,
    )
    stats.add_row(
        "🖼 Posters:", f"[magenta]{state.posters_fetched:,}[/]",
        "🎯 Focus:", f"[bold]{state.current_tag}[/]" if state.current_tag else "[dim]General sweep[/]",
    )
    stats.add_row(
        "⏭ Skipped:", f"[dim]{state.skipped_existing + state.skipped_missing:,}[/]",
        "❌ Errors:", f"[red]{state.errors}[/]" if state.errors else f"[green]0[/]",
    )

    # Progress bar showing current compared to final number
    bar_width = 44
    filled = int(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)
    progress_text = Text()
    progress_text.append(f"  [{bar}] ", style="cyan")
    progress_text.append(f"{state.current_fid:,} / {MAX_FID:,} ", style="bold yellow")
    progress_text.append(f"({pct:.1f}%)", style="bold green")
    if state.phase.startswith("Phase 1") and state.priority_total > 0:
        p_pct = (state.priority_index / state.priority_total * 100)
        progress_text.append(f"  •  Priority: {state.priority_index:,} / {state.priority_total:,} ({p_pct:.1f}%)", style="dim yellow")

    # Activity log
    log_text = Text()
    for ts, msg, style in state.log_lines[-15:]:
        log_text.append(f"  {ts} ", style="dim")
        log_text.append(f"{msg}\n", style=style or "")

    # Current status
    status_text = Text()
    if state.paused:
        status_text.append("  ⏸ PAUSED ", style="bold yellow on dark_red")
        status_text.append(f" at FID {state.current_fid:,} / {MAX_FID:,}. Press Enter to resume, Ctrl+C to quit", style="dim")
    else:
        status_text.append(f"  ▶ [{state.current_fid:,} / {MAX_FID:,}] {state.current_status}", style="bold green")

    return Panel(
        Group(
            header,
            progress_text,
            Text(""),
            stats,
            Text("─" * 64, style="dim"),
            Text("  📋 Activity Log\n", style="bold"),
            log_text,
            Text("─" * 64, style="dim"),
            status_text,
        ),
        title="[bold cyan]🎬 DVDRewind Population & Ingestion Dashboard[/]",
        subtitle=f"[dim]Progress: {state.current_fid:,} / {MAX_FID:,} ({pct:.1f}%) • Ctrl+C to stop safely • Resume anytime[/]",
        border_style="cyan",
        box=box.DOUBLE,
        padding=(1, 2),
    )


# ── Poster Fetcher ─────────────────────────────────────────────
WIKI_CACHE = {}

def try_fetch_poster(clean_title, imdb_id=None, year=None):
    if not clean_title:
        return None

    title_key = (clean_title.lower().strip(), imdb_id or "")
    if title_key in WIKI_CACHE:
        return WIKI_CACHE[title_key]

    try:
        from src.scraper.posters import fetch_poster_from_tmdb
        res = fetch_poster_from_tmdb(imdb_id=imdb_id, clean_title=clean_title, year=year)
        WIKI_CACHE[title_key] = res
        if res:
            state.posters_fetched += 1
        return res
    except Exception:
        WIKI_CACHE[title_key] = None
        return None


# ── Ingestion Worker ───────────────────────────────────────────
def ingest_one(fid, client, parser, repo, existing_fids, group_tag="", live=None):
    state.current_fid = fid
    state.current_tag = group_tag
    state.current_status = f"Checking FID {fid:,}..."

    if fid in existing_fids:
        state.skipped_existing += 1
        state.last_fid = fid
        return False

    raw_path = RAW_DIR / f"{fid}.html"

    try:
        if raw_path.exists():
            with open(raw_path, "rb") as f:
                content = f.read()
            sha256 = hashlib.sha256(content).hexdigest()
        else:
            state.current_status = f"Fetching FID {fid:,}..."
            if live: live.update(build_dashboard())
            status, content, meta = client.fetch_film(fid, save_to_raw=True)
            sha256 = meta["content_sha256"]

            if meta.get("is_missing"):
                state.skipped_missing += 1
                state.last_fid = fid
                return False

        text_check = content[:4000].decode("latin1", errors="replace")
        if "FILMID NOT FOUND" in text_check or "Unable to find film details" in text_check:
            state.skipped_missing += 1
            state.last_fid = fid
            return False

        state.current_status = f"Parsing FID {fid:,}..."
        if live: live.update(build_dashboard())
        parsed = parser.parse(content, fid=fid)
        clean_title = parsed.get("clean_title", f"FID-{fid}")
        fmt = parsed.get("format_category", "?")
        num_releases = len(parsed.get("releases", []))
        year = parsed.get("year")
        imdb_id = parsed.get("imdb_id")

        state.current_title = clean_title
        state.current_format = fmt

        repo.save_parsed_comparison(parsed, source_hash=sha256, raw_html_path=str(raw_path))

        poster_path = try_fetch_poster(clean_title, imdb_id, year)
        if poster_path:
            repo.update_poster_url(fid, poster_path)
            if imdb_id:
                repo.conn.cursor().execute(
                    "UPDATE titles SET imdb_id = ? WHERE fid = ? AND (imdb_id IS NULL OR imdb_id = '')",
                    (imdb_id, fid)
                )
                repo.conn.commit()

        existing_fids.add(fid)
        state.db_count = len(existing_fids)
        state.ingested += 1
        state.total_releases += num_releases
        state.last_fid = fid

        tag_str = f"[{group_tag[:12]}] " if group_tag else ""
        poster_icon = "🖼" if poster_path else "  "
        state.log(
            f"{poster_icon} FID {fid:>5}: {tag_str}{clean_title[:30]:<30} [{fmt:<7}] {num_releases:>2} releases",
            "green"
        )

        if state.ingested % 20 == 0:
            state.save_progress()

        return True

    except Exception as e:
        state.errors += 1
        state.last_fid = fid
        err_msg = str(e)[:55]
        state.log(f"❌ FID {fid}: {err_msg}", "red")
        return False


# ── Maintenance & Incremental Sync Tasks ───────────────────────────
def run_vacuum(headless=False):
    """Performs SQLite database integrity check, FTS5 index optimization, and VACUUM."""
    tag = "[VACUUM]"
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} Starting database maintenance...", flush=True)
    else:
        console.print(f"[bold cyan]{tag} Starting database maintenance...[/]")

    repo = ArchiveRepository()
    cur = repo.conn.cursor()

    # 1. Integrity check
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} Checking database integrity...", flush=True)
    else:
        console.print(f"  [yellow]• Running PRAGMA integrity_check...[/]")
    cur.execute("PRAGMA integrity_check;")
    result = cur.fetchone()[0]
    if result != "ok":
        msg = f"{tag} Integrity check warning/error: {result}"
        if headless:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
        else:
            console.print(f"  [red]{msg}[/]")
    else:
        if headless:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} Integrity check: OK", flush=True)
        else:
            console.print(f"  [green]• Integrity check: OK[/]")

    # 2. FTS5 index optimize
    try:
        if headless:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} Optimizing full-text search index (titles_fts)...", flush=True)
        else:
            console.print(f"  [yellow]• Optimizing FTS5 index (titles_fts)...[/]")
        cur.execute("INSERT INTO titles_fts(titles_fts) VALUES('optimize');")
        repo.conn.commit()
    except Exception as e:
        if headless:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} FTS optimize note: {e}", flush=True)
        else:
            console.print(f"  [dim]• FTS optimize note: {e}[/]")

    # 3. VACUUM
    size_before = DB_PATH.stat().st_size / (1024 * 1024)
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} Running VACUUM (current size: {size_before:.2f} MB)...", flush=True)
    else:
        console.print(f"  [yellow]• Running VACUUM (current size: {size_before:.2f} MB)...[/]")
    
    cur.execute("VACUUM;")
    repo.conn.commit()
    size_after = DB_PATH.stat().st_size / (1024 * 1024)
    saved_kb = (size_before - size_after) * 1024

    msg_done = f"{tag} Database maintenance complete! Size: {size_after:.2f} MB (reclaimed {saved_kb:.1f} KB)"
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg_done}", flush=True)
    else:
        console.print(f"  [bold green]• {msg_done}[/]\n")
    repo.close()


def run_posters_backfill(headless=False, limit=None):
    """Backfills missing posters for existing titles in the database."""
    tag = "[POSTERS]"
    repo = ArchiveRepository()
    cur = repo.conn.cursor()
    cur.execute(
        "SELECT fid, clean_title, year, imdb_id, format_category FROM titles "
        "WHERE (poster_url IS NULL OR poster_url = '' OR poster_url = '/static/images/missing_poster.svg') "
        "AND is_missing = 0 "
        "AND clean_title NOT LIKE '%NOT FOUND%' "
        "AND clean_title NOT LIKE 'FID-%' "
        "ORDER BY fid ASC"
    )
    missing = cur.fetchall()
    total_missing = len(missing)

    if limit and limit > 0:
        missing = missing[:limit]

    start_msg = f"{tag} Found {total_missing:,} titles needing posters. Processing {len(missing):,}..."
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {start_msg}", flush=True)
    else:
        console.print(f"[bold cyan]{start_msg}[/]")

    fetched = 0
    failed = 0

    for idx, (fid, title, year, imdb_id, fmt) in enumerate(missing, start=1):
        if not state.running:
            break
        try:
            poster_url = try_fetch_poster(title, imdb_id, year)
            if poster_url:
                repo.update_poster_url(fid, poster_url)
                fetched += 1
                msg = f"{tag} ({idx}/{len(missing)}) FID {fid}: Found poster for '{title}' -> {poster_url}"
                if headless:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
                else:
                    console.print(f"  [green]🖼 ({idx}/{len(missing)}) FID {fid}:[/] {title} [dim]-> {poster_url}[/]")
            else:
                failed += 1
                msg = f"{tag} ({idx}/{len(missing)}) FID {fid}: No poster found for '{title}'"
                if headless:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
                else:
                    console.print(f"  [dim]• ({idx}/{len(missing)}) FID {fid}: No poster for {title}[/]")
            time.sleep(POSTER_DELAY)
        except Exception as e:
            failed += 1
            err = f"{tag} FID {fid} error: {e}"
            if headless:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {err}", flush=True)
            else:
                console.print(f"  [red]{err}[/]")

    summary = f"{tag} Backfill complete. Fetched: {fetched}, Not found: {failed}, Remaining missing: {total_missing - fetched}"
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {summary}", flush=True)
    else:
        console.print(f"[bold green]{summary}[/]\n")
    repo.close()


def _fetch_archive_status():
    """Read the live Archive Control Center status from the local DVDRewind web app."""
    response = requests.get("http://127.0.0.1:8088/api/archive/status", timeout=5)
    response.raise_for_status()
    return response.json()


def _build_archive_status_panel(data):
    running = bool(data.get("is_running"))
    stats = data.get("stats") or {}
    post_initial = data.get("post_initial") or {}
    last_sync = data.get("last_sync") or {}

    status = Table(box=box.SIMPLE, show_header=False, expand=True, padding=(0, 1))
    status.add_column("label", style="dim", width=18)
    status.add_column("value", style="bold")
    status.add_row("Task", data.get("task_type", "idle").upper() if running else "IDLE")
    status.add_row("Status", data.get("status_message") or "Ready")
    status.add_row("Current action", data.get("current_action") or "—")
    status.add_row("Current FID", f"{int(stats.get('current_fid') or 0):,}")
    status.add_row("Next catch-up FID", f"{int(stats.get('next_fid') or post_initial.get('next_fid') or INITIAL_MAX_FID + 1):,}")
    status.add_row("FIDs scanned", f"{int(stats.get('scanned_fids') or 0):,}")
    status.add_row("New titles", f"{int(stats.get('new_titles') or 0):,}")
    status.add_row("Revisions", f"{int(stats.get('revisions_updated') or 0):,}")
    status.add_row("Errors", f"{int(stats.get('errors') or 0):,}")
    status.add_row("Archive titles", f"{int(data.get('db_titles') or 0):,}")
    if data.get("elapsed_seconds") is not None:
        status.add_row("Elapsed", f"{float(data.get('elapsed_seconds') or 0):.1f}s")
    if last_sync.get("last_sync"):
        status.add_row("Last completed sync", str(last_sync.get("last_sync")))

    log_text = Text()
    logs = data.get("log_lines") or []
    for line in logs[-12:]:
        ts = str(line.get("ts") or "--:--:--")
        log_text.append(f"{ts} ", style="dim")
        log_text.append(str(line.get("msg") or ""))
        log_text.append("\n")
    if not logs:
        log_text.append("No live activity yet.", style="dim")

    title_style = "bold green" if running else "bold cyan"
    return Panel(
        Group(
            Text("DVDRewind NAS Archive Monitor", style=title_style),
            Text(""),
            status,
            Text("─" * 64, style="dim"),
            Text("Recent activity", style="bold"),
            log_text,
        ),
        title="[bold cyan]DVDRewind Live CLI[/]",
        subtitle="[dim]Read-only monitor • Ctrl+C exits the monitor only[/]",
        border_style="green" if running else "cyan",
        box=box.DOUBLE,
        padding=(1, 2),
    )


def run_archive_status_monitor(watch=False):
    """Show or continuously watch the web-managed archive sync without starting another crawler."""
    if not watch:
        try:
            console.print(_build_archive_status_panel(_fetch_archive_status()))
        except Exception as exc:
            console.print(f"[bold red]Could not read DVDRewind status:[/] {exc}")
            raise SystemExit(1)
        return

    try:
        with Live(console=console, refresh_per_second=2, screen=False) as live:
            while True:
                try:
                    live.update(_build_archive_status_panel(_fetch_archive_status()))
                except Exception as exc:
                    live.update(Panel(f"[red]Waiting for DVDRewind web service...[/]\n{exc}", border_style="red"))
                time.sleep(1.5)
    except KeyboardInterrupt:
        console.print("\n[dim]Monitor closed. The DVDRewind sync, if running, continues on the NAS.[/]")


def run_incremental_sync(headless=False, limit=None, force_from_initial=False):
    """
    Checks DVDCompare for newly updated/reviewed comparisons and catches up every
    FID added after the original 1..76,200 catalog sweep. Progress beyond the
    original cutoff is persisted separately so future syncs resume where the
    previous one stopped instead of relying on a fragile consecutive-miss rule.
    """
    tag = "[SYNC]"
    start_ts = time.time()
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} Starting incremental sync...", flush=True)
    else:
        console.print(f"[bold cyan]{tag} Starting incremental sync...[/]")

    repo = ArchiveRepository()
    client = ScraperClient()
    collector = WarningCollector()
    parser = DVDCompareParser(collector)

    # 1. Fetch homepage to get active/spotlighted/recent reviews
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} Checking DVDCompare homepage for recent reviews & updates...", flush=True)
    else:
        console.print(f"  [yellow]• Checking DVDCompare homepage for recent updates...[/]")

    homepage_fids = []
    try:
        r = requests.get(
            "https://www.dvdcompare.net/",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DVDRewindArchive/1.0"},
            timeout=15
        )
        if r.status_code == 200:
            found = set(int(m) for m in re.findall(r'film\.php\?fid=(\d+)', r.text))
            homepage_fids = sorted(list(found))
            msg = f"{tag} Found {len(homepage_fids)} comparison links to check on homepage."
            if headless:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
            else:
                console.print(f"    Found [bold]{len(homepage_fids)}[/] comparison links on homepage.")
    except Exception as e:
        msg = f"{tag} Could not fetch homepage: {e}"
        if headless:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
        else:
            console.print(f"    [red]{msg}[/]")

    # Check for revisions or missing homepage titles
    revisions_updated = 0
    cur = repo.conn.cursor()
    for fid in homepage_fids:
        if not state.running:
            break
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

                # Also ensure poster is present
                cur.execute("SELECT poster_url FROM titles WHERE fid = ?", (fid,))
                p_row = cur.fetchone()
                if not p_row or not p_row[0] or p_row[0] == '/static/images/missing_poster.svg':
                    p_url = try_fetch_poster(clean_title, parsed.get("imdb_id"), parsed.get("year"))
                    if p_url:
                        repo.update_poster_url(fid, p_url)

                revisions_updated += 1
                action_word = "Ingested new" if stored_hash is None else "Updated revised"
                msg = f"{tag} {action_word} FID {fid}: {clean_title} ({num_releases} releases)"
                if headless:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
                else:
                    console.print(f"    [green]• {msg}[/]")
            time.sleep(CRAWL_DELAY)
        except Exception as e:
            if headless:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {tag} Error checking FID {fid}: {e}", flush=True)
            else:
                console.print(f"    [red]• Error checking FID {fid}: {e}[/]")

    # 2. Catch up every FID added after the original catalog cutoff.
    # The old implementation stopped after only 15 misses, which could leave a
    # permanent hole if DVDCompare assigned IDs sparsely. The new cursor is
    # durable and scans through the newest FID observed on the homepage, plus a
    # polite tail window for titles not linked there yet.
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

    probe_start = INITIAL_MAX_FID + 1 if force_from_initial else max(INITIAL_MAX_FID + 1, saved_next_fid)
    known_high_fid = max(INITIAL_MAX_FID, max_db_fid, homepage_high_fid)
    if known_high_fid >= probe_start:
        probe_end = known_high_fid + POST_INITIAL_TAIL_FIDS
    else:
        probe_end = probe_start + POST_INITIAL_FALLBACK_WINDOW - 1

    if limit and limit > 0:
        probe_end = min(probe_end, probe_start + limit - 1)

    msg = (
        f"{tag} Post-initial catch-up scanning FIDs {probe_start:,} -> {probe_end:,} "
        f"(newest known: {known_high_fid:,})..."
    )
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
    else:
        console.print(f"  [yellow]• {msg}[/]")

    new_titles_ingested = 0
    scanned_fids = 0
    try:
        saved_highest_seen = int(sync_state.get("highest_seen_fid", INITIAL_MAX_FID) or INITIAL_MAX_FID)
    except (TypeError, ValueError):
        saved_highest_seen = INITIAL_MAX_FID
    highest_seen_fid = max(known_high_fid, saved_highest_seen)
    current_probe = probe_start
    existing_fids = set(r[0] for r in cur.execute("SELECT fid FROM titles").fetchall())

    while state.running and current_probe <= probe_end:
        already_present = current_probe in existing_fids
        if already_present:
            highest_seen_fid = max(highest_seen_fid, current_probe)
        else:
            res = ingest_one(current_probe, client, parser, repo, existing_fids, group_tag="Sync", live=None)
            if res:
                new_titles_ingested += 1
                highest_seen_fid = max(highest_seen_fid, current_probe)
                if not limit:
                    probe_end = max(probe_end, current_probe + POST_INITIAL_TAIL_FIDS)
            time.sleep(CRAWL_DELAY)

        scanned_fids += 1
        current_probe += 1

        if scanned_fids % 25 == 0:
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

    elapsed = time.time() - start_ts
    total_db = len(existing_fids)

    # 3. Save sync status file
    status_data = {
        "last_sync": datetime.now().isoformat(),
        "status": "success" if state.running else "aborted",
        "homepage_checked": len(homepage_fids),
        "revisions_updated": revisions_updated,
        "new_titles_ingested": new_titles_ingested,
        "post_initial_scanned_fids": scanned_fids,
        "post_initial_next_fid": current_probe,
        "highest_seen_fid": highest_seen_fid,
        "total_db_titles": total_db,
        "elapsed_seconds": round(elapsed, 1),
    }
    try:
        SYNC_STATUS_FILE.write_text(json.dumps(status_data, indent=2), encoding="utf-8")
    except Exception as e:
        pass

    summary = (
        f"{tag} Sync completed in {elapsed:.1f}s. "
        f"Homepage checked: {len(homepage_fids)}, Revisions updated: {revisions_updated}, "
        f"Post-initial FIDs scanned: {scanned_fids}, New titles: {new_titles_ingested}, "
        f"Next FID: {current_probe:,}, Total in DB: {total_db:,}"
    )
    if headless:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {summary}", flush=True)
    else:
        console.print(f"\n[bold green]{summary}[/]\n")

    repo.close()

    # 4. Auto-optimize database after sync
    run_vacuum(headless=headless)


# ── Main Crawl Controller ──────────────────────────────────────
def populate(headless=False):
    state.headless = headless
    resumed = state.load_progress()

    repo = ArchiveRepository()
    existing_fids = set(r[0] for r in repo.conn.cursor().execute("SELECT fid FROM titles").fetchall())
    state.db_count = len(existing_fids)
    if not state.current_fid and state.last_fid:
        state.current_fid = state.last_fid

    client = ScraperClient()
    collector = WarningCollector()
    parser = DVDCompareParser(collector)

    # Load priority queue if present
    priority_queue = []
    if PRIORITY_FILE.exists():
        try:
            priority_queue = json.loads(PRIORITY_FILE.read_text(encoding="utf-8"))
            state.priority_total = len(priority_queue)
        except Exception:
            pass

    def run_phases(live=None):
        # ── PHASE 1: Priority Queue (Interleaved) ─────────────────────
        if state.priority_index < len(priority_queue) and state.phase.startswith("Phase 1"):
            state.phase = "Phase 1: Curated Priorities"
            state.log(f"Starting Phase 1: {len(priority_queue)} curated comparisons...", "bold cyan")

            while state.priority_index < len(priority_queue) and state.running:
                item = priority_queue[state.priority_index]
                fid = item["fid"]
                group = item.get("group", "")
                
                ingest_one(fid, client, parser, repo, existing_fids, group_tag=group, live=live)
                state.priority_index += 1
                if live: live.update(build_dashboard())
                time.sleep(0.3)

            if state.running:
                state.log("Phase 1 Complete! Transitioning to Phase 2: Full Catalog Sweep...", "bold green")
                state.phase = "Phase 2: Full Catalog Sweep"
                state.save_progress()

        # ── PHASE 2: Sequential Sweep (1 to MAX_FID) ──────────────────
        if state.running:
            state.phase = "Phase 2: Full Catalog Sweep"
            start_fid = max(1, state.last_fid + 1)
            state.log(f"Phase 2 Sweep starting from FID {start_fid:,} -> {MAX_FID:,}", "bold cyan")

            for fid in range(start_fid, MAX_FID + 1):
                if not state.running:
                    break
                ingest_one(fid, client, parser, repo, existing_fids, group_tag="", live=live)
                if live: live.update(build_dashboard())

        # Final cleanup
        state.save_progress()
        state.current_status = "Finished!" if state.current_fid >= MAX_FID else "Stopped (progress saved)"
        state.log(f"Progress saved to {PROGRESS_FILE.name}. Total in DB: {len(existing_fids)}", "bold yellow")
        if live:
            live.update(build_dashboard())
            time.sleep(2)

    if headless:
        run_phases(live=None)
    else:
        with Live(build_dashboard(), console=console, refresh_per_second=2, screen=True) as live:
            run_phases(live=live)

    repo.close()


def main():
    parser = argparse.ArgumentParser(
        description="DVDRewind Ingestion & Archive Maintenance Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python populate_all.py                     Interactive full catalog sweep (dashboard)
  python populate_all.py --sync              Resume post-76,200 catch-up + check current revisions
  python populate_all.py --since-initial     Re-scan everything added after the original 76,200 cutoff
  python populate_all.py --command-center    Open the unified DVD Rewind terminal command center
  python populate_all.py --watch             Watch the live web-managed sync with hotkeys and live dashboard
  python populate_all.py --watch-new         Watch only newly discovered titles
  python populate_all.py --status            Show one live Archive Control Center status snapshot
  python populate_all.py --search "The Thing" Search the local DVDRewind archive
  python populate_all.py --retry-failed      Retry only FIDs that failed on the previous web sync
  python populate_all.py --cron --sync       Automated headless daily sync (ideal for NAS crontab)
  python populate_all.py --posters-only      Backfill missing movie posters from TMDB / Wikipedia
  python populate_all.py --vacuum            Check DB integrity, optimize search index & VACUUM
        """
    )
    parser.add_argument("--command-center", action="store_true", help="Open the unified DVD Rewind interactive terminal interface")
    parser.add_argument("--sync", "--update", action="store_true", help="Incremental sync: check current revisions and resume scanning FIDs added after the original catalog cutoff")
    parser.add_argument("--since-initial", action="store_true", help="Force a complete catch-up scan from FID 76,201 onward, skipping titles already in the database")
    parser.add_argument("--status", action="store_true", help="Show a one-time status snapshot of the web-managed archive task")
    parser.add_argument("--watch", action="store_true", help="Continuously watch the web-managed archive task in the enhanced live dashboard")
    parser.add_argument("--watch-new", action="store_true", help="Continuously watch only newly discovered titles")
    parser.add_argument("--exit-on-complete", action="store_true", help="When watching, exit after the active NAS task finishes")
    parser.add_argument("--search", type=str, default=None, metavar="QUERY", help="Search the local DVDRewind archive by title/metadata")
    parser.add_argument("--search-b64", type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--retry-failed", action="store_true", help="Retry only FIDs saved as failures from the previous web-managed sync")
    parser.add_argument("--completion-message", action="store_true", help="Print a one-line summary suitable for a Windows completion notification")
    parser.add_argument("--cron", "--headless", action="store_true", help="Run in headless logging mode (auto-detected if no TTY)")
    parser.add_argument("--posters-only", action="store_true", help="Backfill missing posters for existing titles without re-scraping HTML")
    parser.add_argument("--vacuum", "--health", action="store_true", help="Run database integrity check, FTS5 index optimization, and VACUUM")
    parser.add_argument("--limit", type=int, default=None, help="Maximum items/FIDs to process (poster backfill or bounded sync testing)")

    args = parser.parse_args()

    # Auto-detect headless mode when running in cron or piped stdout
    is_headless = args.cron or (not sys.stdout.isatty())
    state.headless = is_headless

    if args.command_center:
        raise SystemExit(run_tui_command_center())
    elif args.completion_message:
        print(cli_completion_message())
    elif args.search or args.search_b64:
        query = args.search
        if args.search_b64:
            try:
                query = base64.b64decode(args.search_b64).decode("utf-8")
            except Exception as exc:
                raise SystemExit(f"Invalid encoded search query: {exc}")
        cli_search_archive(query or "")
    elif args.retry_failed:
        raise SystemExit(cli_retry_failed_fids())
    elif args.watch or args.watch_new:
        cli_run_archive_status_monitor(
            watch=True,
            new_only=args.watch_new,
            exit_on_complete=args.exit_on_complete,
        )
    elif args.status:
        cli_run_archive_status_monitor(watch=False)
    elif args.vacuum:
        run_vacuum(headless=is_headless)
    elif args.posters_only:
        run_posters_backfill(headless=is_headless, limit=args.limit)
    elif args.sync or args.since_initial:
        run_incremental_sync(
            headless=is_headless,
            limit=args.limit,
            force_from_initial=args.since_initial,
        )
    else:
        if not is_headless:
            console.clear()
            console.print(Panel(
                "[bold cyan]🎬 DVDRewind Priority & Full Catalog Ingestion[/]\n\n"
                "Phase 1: Curated Priority Lists (Directors, Franchises & Boutique Labels)\n"
                "Phase 2: Full Catalog Sweep (FIDs 1 -> 76,200)\n"
                "Phase 3: Catch Up Everything Added Since the Original Sweep\n\n"
                "[dim]• Press Ctrl+C at any time to safely stop and save progress\n"
                "• Resume anytime — skips all existing titles\n"
                "• Every new title is immediately live at http://127.0.0.1:8088[/]",
                border_style="cyan",
                box=box.DOUBLE,
            ))
            time.sleep(1.5)
        populate(headless=is_headless)
        if state.running and state.current_fid >= INITIAL_MAX_FID:
            run_incremental_sync(headless=is_headless, limit=args.limit)


if __name__ == "__main__":
    main()
