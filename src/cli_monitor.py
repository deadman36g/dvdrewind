import json
import os
import select
import sys
import time

try:
    import termios
    import tty
except ImportError:  # Windows can still use one-shot/search/retry modes.
    termios = None
    tty = None
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.config import ARCHIVE_DIR, RAW_DIR
from src.db.repository import ArchiveRepository
from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector
from src.scraper.client import ScraperClient
from src.scraper.posters import fetch_poster_from_tmdb

INITIAL_MAX_FID = 76200
STATUS_URL = "http://127.0.0.1:8088/api/archive/status"
CONTROL_BASE_URL = "http://127.0.0.1:8088/api/archive"
WEB_URL = "http://192.168.50.39:8091"
FAILED_FIDS_FILE = ARCHIVE_DIR / "sync_failed_fids.json"
console = Console()

FORMAT_STYLES = {
    "4k": "bold black on bright_yellow",
    "uhd": "bold black on bright_yellow",
    "blu": "bold white on blue",
    "dvd": "bold white on red",
    "tv": "bold white on magenta",
}

PHASE_LABELS = {
    "starting": "STARTING",
    "homepage": "HOMEPAGE REVISIONS",
    "catchup": "ARCHIVE FRONTIER SCAN",
    "posters": "POSTER BACKFILL",
    "maintenance": "DATABASE MAINTENANCE",
    "complete": "COMPLETE",
    "idle": "IDLE",
}


def fetch_archive_status() -> Dict[str, Any]:
    response = requests.get(STATUS_URL, timeout=5)
    response.raise_for_status()
    return response.json()


def _fmt_duration(seconds: Any) -> str:
    try:
        seconds = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        seconds = 0
    return str(timedelta(seconds=seconds))


def _fmt_delta(value: Any, decimals: int = 0) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if decimals:
        return f"{number:+.{decimals}f}"
    return f"{int(number):+d}"


def _phase_progress(stats: Dict[str, Any], elapsed: float) -> tuple[int, int, float, float, Optional[float]]:
    current = int(stats.get("phase_current") or 0)
    total = int(stats.get("phase_total") or 0)
    pct = (current / total * 100.0) if total > 0 else 0.0
    speed = (current / elapsed) if elapsed > 0 and current > 0 else 0.0
    eta = ((total - current) / speed) if total > current and speed > 0 else None
    return current, total, pct, speed, eta


def _progress_bar(pct: float, width: int = 44) -> Text:
    pct = min(100.0, max(0.0, pct))
    filled = int(round(width * pct / 100.0))
    t = Text()
    t.append("█" * filled, style="bold cyan")
    t.append("░" * (width - filled), style="bright_black")
    return t


def _format_badge(fmt: Any) -> Text:
    label = str(fmt or "?")
    low = label.lower()
    style = "bold white on bright_black"
    for needle, candidate in FORMAT_STYLES.items():
        if needle in low:
            style = candidate
            break
    text = Text(" ")
    text.append(label[:12], style=style)
    text.append(" ")
    return text


def _counter_strip(stats: Dict[str, Any]) -> Text:
    strip = Text()
    strip.append(" +")
    strip.append(f"{int(stats.get('new_titles') or 0):,}", style="bold green")
    strip.append(" new   ")
    strip.append(f"{int(stats.get('revisions_updated') or 0):,}", style="bold cyan")
    strip.append(" revised   ")
    strip.append(f"{int(stats.get('posters_fetched') or 0):,}", style="bold magenta")
    strip.append(" posters   ")
    errors = int(stats.get("errors") or 0)
    strip.append(f"{errors:,}", style="bold red" if errors else "bold green")
    strip.append(" errors")
    return strip


def _current_spotlight(stats: Dict[str, Any], current_action: str) -> Panel:
    fid = int(stats.get("current_fid") or 0)
    title = str(stats.get("current_title") or "")
    year = stats.get("current_year")
    fmt = stats.get("current_format") or "?"
    poster = bool(stats.get("poster_found"))

    if title:
        body = Text()
        body.append(title, style="bold white")
        if year:
            body.append(f" ({year})", style="dim")
        body.append("  ")
        body.append_text(_format_badge(fmt))
        body.append(f"  FID {fid:,}", style="yellow")
        body.append("  •  ")
        body.append("poster ✓" if poster else "poster —", style="green" if poster else "dim")
    else:
        body = Text(f"{current_action or 'Waiting'}", style="bold white")
        if fid:
            body.append(f"  •  FID {fid:,}", style="yellow")

    return Panel(body, title="[bold]Now Processing[/]", border_style="bright_black", padding=(0, 1))


def _discoveries_table(items: Iterable[Dict[str, Any]], limit: int = 8) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, expand=True, show_header=True, header_style="bold")
    table.add_column("Time", width=8, style="dim")
    table.add_column("FID", width=8, justify="right")
    table.add_column("Title", ratio=1)
    table.add_column("Format", width=14)
    table.add_column("Rel.", width=5, justify="right")
    rows = list(items)[-limit:]
    if not rows:
        table.add_row("—", "—", "No new discoveries this run yet.", "—", "—")
        return table
    for item in reversed(rows):
        table.add_row(
            str(item.get("ts") or "—"),
            f"{int(item.get('fid') or 0):,}",
            f"{item.get('title') or 'Unknown'}" + (f" ({item.get('year')})" if item.get("year") else ""),
            _format_badge(item.get("format")),
            str(item.get("releases") or 0),
        )
    return table


def _errors_table(items: Iterable[Dict[str, Any]]) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, expand=True, header_style="bold red")
    table.add_column("FID", width=9, justify="right")
    table.add_column("Phase", width=12)
    table.add_column("Error", ratio=1)
    rows = list(items)[-12:]
    if not rows:
        table.add_row("—", "—", "No errors recorded for this run.")
        return table
    for item in reversed(rows):
        table.add_row(
            str(item.get("fid") or "—"),
            str(item.get("phase") or "—"),
            str(item.get("error") or "")[:120],
        )
    return table


def _growth_table(data: Dict[str, Any]) -> Table:
    metrics = data.get("metrics") or {}
    growth = dict(data.get("growth") or {})
    if not growth:
        # After a web-process restart, in-memory start_metrics are gone. The
        # completed sync persists its start/end snapshot so the last-run report
        # can still show real archive growth.
        last_sync = data.get("last_sync") or {}
        start_metrics = last_sync.get("start_metrics") or {}
        end_metrics = last_sync.get("end_metrics") or {}
        if start_metrics and end_metrics:
            for key, end_value in end_metrics.items():
                start_value = start_metrics.get(key)
                if isinstance(end_value, (int, float)) and isinstance(start_value, (int, float)):
                    growth[key] = round(end_value - start_value, 2) if isinstance(end_value, float) else end_value - start_value
    table = Table(box=box.SIMPLE, expand=True, show_header=False, padding=(0, 1))
    table.add_column("Metric", style="dim", width=16)
    table.add_column("Value", justify="right")
    table.add_column("Δ run", justify="right")
    table.add_row("Titles", f"{int(metrics.get('titles') or 0):,}", _fmt_delta(growth.get("titles")))
    table.add_row("Releases", f"{int(metrics.get('releases') or 0):,}", _fmt_delta(growth.get("releases")))
    table.add_row("Posters", f"{int(metrics.get('posters') or 0):,}", _fmt_delta(growth.get("posters")))
    table.add_row("Raw HTML", f"{int(metrics.get('raw_html') or 0):,}", _fmt_delta(growth.get("raw_html")))
    table.add_row("Database", f"{float(metrics.get('db_size_mb') or 0):.2f} MB", f"{_fmt_delta(growth.get('db_size_mb'), 2)} MB")
    return table


def _previous_run_line(last_sync: Dict[str, Any], stats: Dict[str, Any]) -> Text:
    t = Text("Previous run: ", style="dim")
    if not last_sync:
        t.append("no completed sync recorded", style="dim")
        return t
    prev_new = int(last_sync.get("new_titles_ingested") or 0)
    prev_rev = int(last_sync.get("revisions_updated") or 0)
    prev_err = int(last_sync.get("errors") or 0)
    prev_elapsed = float(last_sync.get("elapsed_seconds") or 0)
    t.append(f"+{prev_new} new", style="green")
    t.append(f" • {prev_rev} revised • {prev_err} errors • {_fmt_duration(prev_elapsed)}", style="dim")
    if stats:
        delta = int(stats.get("new_titles") or 0) - prev_new
        t.append(f"  |  current new-title delta {delta:+d}", style="cyan" if delta >= 0 else "yellow")
    return t


def build_archive_status_panel(data: Dict[str, Any], view: str = "main") -> Panel:
    """Render the compact DVD Rewind operations console."""
    running = bool(data.get("is_running"))
    stats = data.get("stats") or {}
    metrics = data.get("metrics") or {}
    last_sync = data.get("last_sync") or {}
    post_initial = data.get("post_initial") or {}
    elapsed = float(data.get("elapsed_seconds") or 0)
    phase_elapsed = float(data.get("phase_elapsed_seconds") or elapsed)
    phase = str(stats.get("phase") or ("idle" if not running else "starting"))
    phase_label = PHASE_LABELS.get(phase, phase.replace("_", " ").upper())
    current, total, pct, speed, eta = _phase_progress(stats, phase_elapsed)

    navy = "#071a2f"
    panel_blue = "#0c2747"
    line_blue = "#2f78b7"
    accent = "#63c7ff"
    green = "#55e39f"
    muted = "#8aa7c1"

    masthead = Text()
    masthead.append("DVD REWIND", style=f"bold white on {panel_blue}")
    masthead.append("  //  ARCHIVE OPERATIONS", style=f"bold {accent}")
    masthead.append("\n")
    if running:
        masthead.append("● LIVE ", style=f"bold {green}")
        masthead.append(phase_label, style="bold white")
        masthead.append(f"  ·  {data.get('status_message') or 'Working'}", style=muted)
    else:
        masthead.append("● IDLE ", style=muted)
        masthead.append("Archive worker is standing by", style="bold white")
        if last_sync:
            masthead.append(f"  ·  last run {str(last_sync.get('status') or 'complete').upper()}", style=muted)

    progress = Text()
    if running and total:
        progress.append_text(_progress_bar(pct, width=40))
        progress.append(f"  {current:,}/{total:,}  {pct:5.1f}%", style=f"bold {accent}")
        if speed:
            progress.append(f"  {speed:.2f}/s", style=muted)
        if eta is not None:
            progress.append(f"  ETA {_fmt_duration(eta)}", style=green)
    elif running:
        progress.append("Preparing phase…", style=muted)
    else:
        progress.append("Worker idle", style=muted)

    current_fid = int(stats.get("current_fid") or 0)
    next_fid = int(
        (stats.get("next_fid") if running else None)
        or post_initial.get("next_fid")
        or stats.get("next_fid")
        or INITIAL_MAX_FID + 1
    )

    telemetry = Table.grid(expand=True, padding=(0, 1))
    telemetry.add_column(ratio=1)
    telemetry.add_column(ratio=1)
    telemetry.add_column(ratio=1)
    telemetry.add_column(ratio=1)
    telemetry.add_row(
        Text(f"CURRENT FID\n{current_fid:,}" if running and current_fid else "CURRENT FID\n—", style="bold white"),
        Text(f"NEXT FID\n{next_fid:,}", style="bold white"),
        Text(f"ELAPSED\n{_fmt_duration(elapsed) if running else '—'}", style="bold white"),
        Text(f"ARCHIVE\n{int(metrics.get('titles') or data.get('db_titles') or 0):,} titles", style="bold white"),
    )

    counters = Table.grid(expand=True, padding=(0, 1))
    counters.add_column(ratio=1)
    counters.add_column(ratio=1)
    counters.add_column(ratio=1)
    counters.add_column(ratio=1)
    counters.add_row(
        Text(f"NEW\n{int(stats.get('new_titles') or 0):,}", style=green),
        Text(f"REVISED\n{int(stats.get('revisions_updated') or 0):,}", style=accent),
        Text(f"POSTERS\n{int(stats.get('posters_fetched') or 0):,}", style="#c7a7ff"),
        Text(f"ERRORS\n{int(stats.get('errors') or 0):,}", style="bold #ff7d8a" if int(stats.get("errors") or 0) else green),
    )

    activity = Table(box=None, expand=True, show_header=False, padding=(0, 1))
    activity.add_column("Time", width=9, style=muted)
    activity.add_column("Event", ratio=1)
    logs = list(data.get("log_lines") or [])[-6:]
    if logs:
        for item in reversed(logs):
            activity.add_row(str(item.get("ts") or "--:--:--"), str(item.get("msg") or "")[:110])
    else:
        activity.add_row("--:--:--", "No activity in this session yet.")

    if view == "errors":
        body = Group(masthead, Text(""), Panel(_errors_table(data.get("failed_fids") or []), title="ERROR LOG", border_style="#d85c69", style=f"on {navy}"))
        subtitle = "[dim]E dashboard • R refresh • Q back[/]"
    elif view == "help":
        help_table = Table(box=None, show_header=False, expand=True)
        help_table.add_column("Key", style=f"bold {accent}", width=8)
        help_table.add_column("Action")
        for key, desc in [("Q", "Back to Command Center / close monitor"), ("R", "Refresh now"), ("E", "Errors"), ("N", "New discoveries"), ("S", "Search archive"), ("H", "Help")]:
            help_table.add_row(key, desc)
        body = Group(masthead, Text(""), Panel(help_table, title="CONTROLS", border_style=line_blue, style=f"on {navy}"))
        subtitle = "[dim]H dashboard[/]"
    elif view == "new":
        body = Group(masthead, Text(""), counters, Text(""), Panel(_discoveries_table(data.get("recent_discoveries") or [], limit=14), title="RECENT DISCOVERIES", border_style=green, style=f"on {navy}"))
        subtitle = "[dim]N dashboard • S search • Q back[/]"
    elif view == "report":
        summary = Table(box=None, show_header=False, expand=True, padding=(0, 1))
        summary.add_column("Metric", style=muted, width=25)
        summary.add_column("Value", style="bold white")
        summary.add_row("Result", str(last_sync.get("status") or "—").upper())
        summary.add_row("Duration", _fmt_duration(last_sync.get("elapsed_seconds")))
        summary.add_row("Homepage checked", f"{int(last_sync.get('homepage_checked') or 0):,}")
        summary.add_row("Catch-up FIDs scanned", f"{int(last_sync.get('post_initial_scanned_fids') or 0):,}")
        summary.add_row("New titles", f"{int(last_sync.get('new_titles_ingested') or 0):,}")
        summary.add_row("Revisions", f"{int(last_sync.get('revisions_updated') or 0):,}")
        summary.add_row("Posters fetched", f"{int(last_sync.get('posters_fetched') or 0):,}")
        summary.add_row("Errors", f"{int(last_sync.get('errors') or 0):,}")
        summary.add_row("Next FID", f"{int(last_sync.get('post_initial_next_fid') or next_fid):,}")
        result_banner = Text("RUN COMPLETE", style=f"bold black on {green}" if str(last_sync.get("status") or "").lower() == "success" else "bold white on #a33b49")
        body = Group(result_banner, Text(""), Panel(summary, title="LAST RUN", border_style=line_blue, style=f"on {navy}"), Panel(_growth_table(data), title="ARCHIVE GROWTH", border_style=line_blue, style=f"on {navy}"), _previous_run_line(last_sync.get("previous_sync") or {}, {}))
        subtitle = "[dim]Q back • S search[/]"
    else:
        now_title = str(stats.get("current_title") or "")
        now_line = Text()
        now_line.append("NOW PROCESSING  ", style=f"bold {accent}")
        if running:
            now_line.append(now_title or data.get("current_action") or "Working…", style="bold white")
            if current_fid:
                now_line.append(f"  ·  FID {current_fid:,}", style=muted)
        else:
            now_line.append("No active task", style=muted)
            if last_sync:
                now_line.append(f"  ·  last completed in {_fmt_duration(last_sync.get('elapsed_seconds'))}", style=muted)

        body = Group(masthead, Text(""), progress, Text(""), Panel(telemetry, title="SYSTEM STATUS", border_style=line_blue, style=f"on {navy}"), Panel(counters, title="THIS RUN", border_style=line_blue, style=f"on {navy}"), now_line, Text(""), Panel(activity, title="RECENT ACTIVITY", border_style=line_blue, style=f"on {navy}"))
        subtitle = "[dim]Q back • R refresh • E errors • N discoveries • S search • H help[/]"

    return Panel(body, title=f"[bold {accent}]DVD REWIND // LIVE CONSOLE[/]", subtitle=subtitle, border_style=line_blue, box=box.SQUARE, padding=(1, 2), style=f"white on {navy}")


@contextmanager
def _cbreak_stdin():
    if os.name == "posix" and termios is not None and tty is not None and sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    else:
        yield


def _read_key_nonblocking() -> Optional[str]:
    if os.name != "posix" or termios is None or not sys.stdin.isatty():
        return None
    ready, _, _ = select.select([sys.stdin], [], [], 0)
    if not ready:
        return None
    return sys.stdin.read(1).lower()


def _interactive_search_from_monitor(live: Live) -> None:
    live.stop()
    try:
        if os.name == "posix" and termios is not None and sys.stdin.isatty():
            # Live monitor enters cbreak mode; temporarily restore normal line input.
            fd = sys.stdin.fileno()
            attrs = termios.tcgetattr(fd)
            attrs[3] |= termios.ICANON | termios.ECHO
            termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
        query = console.input("\n[bold cyan]Search DVDRewind:[/] ").strip()
        if query:
            search_archive(query)
            console.input("\n[dim]Press Enter to return to the monitor…[/]")
    finally:
        if os.name == "posix" and termios is not None and tty is not None and sys.stdin.isatty():
            tty.setcbreak(sys.stdin.fileno())
        live.start(refresh=True)


def run_archive_status_monitor(watch: bool = False, new_only: bool = False, exit_on_complete: bool = False) -> None:
    if not watch:
        try:
            console.print(build_archive_status_panel(fetch_archive_status(), view="new" if new_only else "main"))
        except Exception as exc:
            console.print(f"[bold red]Could not read DVDRewind status:[/] {exc}")
            raise SystemExit(1)
        return

    view = "new" if new_only else "main"
    last_data: Dict[str, Any] = {}
    saw_running = False
    next_refresh = 0.0

    try:
        with _cbreak_stdin():
            with Live(console=console, refresh_per_second=4, screen=True) as live:
                while True:
                    now = time.monotonic()
                    if now >= next_refresh:
                        try:
                            last_data = fetch_archive_status()
                            saw_running = saw_running or bool(last_data.get("is_running"))
                            live.update(build_archive_status_panel(last_data, view=view), refresh=True)
                            if exit_on_complete and saw_running and not last_data.get("is_running"):
                                break
                        except Exception as exc:
                            live.update(Panel(f"[red]Waiting for DVDRewind web service…[/]\n{exc}", border_style="red"), refresh=True)
                        next_refresh = now + 1.0

                    key = _read_key_nonblocking()
                    if key == "q":
                        break
                    if key == "r":
                        next_refresh = 0.0
                    elif key == "e":
                        view = "main" if view == "errors" else "errors"
                        next_refresh = 0.0
                    elif key == "n":
                        view = "main" if view == "new" else "new"
                        next_refresh = 0.0
                    elif key == "h":
                        view = "main" if view == "help" else "help"
                        next_refresh = 0.0
                    elif key == "s":
                        _interactive_search_from_monitor(live)
                        next_refresh = 0.0
                    time.sleep(0.08)
    except KeyboardInterrupt:
        pass

    completed = bool(exit_on_complete and saw_running and last_data and not last_data.get("is_running"))
    if completed:
        console.print("\n[bold green]DVDRewind task completed.[/]")
        raise SystemExit(20)
    console.print("\n[dim]Monitor closed. The DVDRewind task, if running, continues on the NAS.[/]")


def _post_archive_action(endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = requests.post(
        f"{CONTROL_BASE_URL}/{endpoint}",
        json=payload or {},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def build_command_center_panel(data: Dict[str, Any]) -> Panel:
    """Main DVD Rewind menu: compact, status-first, and intentionally console-like."""
    running = bool(data.get("is_running"))
    stats = data.get("stats") or {}
    metrics = data.get("metrics") or {}
    last_sync = data.get("last_sync") or {}
    post_initial = data.get("post_initial") or {}
    elapsed = float(data.get("elapsed_seconds") or 0)
    phase_elapsed = float(data.get("phase_elapsed_seconds") or elapsed)
    phase = str(stats.get("phase") or ("idle" if not running else "starting"))
    phase_label = PHASE_LABELS.get(phase, phase.replace("_", " ").upper())
    phase_current, phase_total, pct, speed, eta = _phase_progress(stats, phase_elapsed)

    navy = "#071a2f"
    panel_blue = "#0c2747"
    line_blue = "#2f78b7"
    accent = "#63c7ff"
    green = "#55e39f"
    muted = "#8aa7c1"
    warning = "#ffd166"

    next_fid = int(
        (stats.get("next_fid") if running else None)
        or post_initial.get("next_fid")
        or stats.get("next_fid")
        or INITIAL_MAX_FID + 1
    )

    header = Text()
    header.append("DVD REWIND", style=f"bold white on {panel_blue}")
    header.append("  //  PRIVATE DISC ARCHIVE", style=f"bold {accent}")
    header.append("\n")
    header.append("ARCHIVE CONTROL CONSOLE", style="bold white")
    header.append("  ·  one terminal, one archive, no command memorization", style=muted)

    menu = Table(box=None, expand=True, show_header=False, padding=(0, 1))
    menu.add_column("Key", width=4, justify="center")
    menu.add_column("Action", ratio=1)
    menu.add_column("State", width=12, justify="right")
    options = [
        ("1", "Update to Latest", "BUSY" if running else "READY"),
        ("2", "Deep Verify Historical Tail", "BUSY" if running else "READY"),
        ("3", "Watch Live Run", "LIVE" if running else "IDLE"),
        ("4", "Recent Discoveries", "VIEW"),
        ("5", "Search Archive", "SEARCH"),
        ("6", "Errors / Retry", f"{int(stats.get('errors') or 0)} ERR"),
        ("7", "Last Run Report", "VIEW"),
        ("8", "Open Web Interface", "OPEN"),
    ]
    for key, action, state in options:
        key_text = Text(key, style=f"bold {accent}")
        action_style = "dim" if running and key in {"1", "2"} else "bold white"
        state_style = warning if state == "BUSY" else green if state in {"READY", "LIVE"} else muted
        menu.add_row(key_text, Text(action, style=action_style), Text(state, style=state_style))

    maintenance = Text()
    maintenance.append("P", style=f"bold {accent}")
    maintenance.append("  Posters   ", style="white")
    maintenance.append("D", style=f"bold {accent}")
    maintenance.append("  Database maintenance   ", style="white")
    maintenance.append("Q", style=f"bold {accent}")
    maintenance.append("  Quit", style="white")

    state_panel = Table(box=None, expand=True, show_header=False, padding=(0, 1))
    state_panel.add_column("Label", width=17, style=muted)
    state_panel.add_column("Value", ratio=1, style="bold white")
    if running:
        state_panel.add_row("STATUS", Text("● RUNNING", style=green))
        state_panel.add_row("PHASE", phase_label)
        state_panel.add_row("CURRENT FID", f"{int(stats.get('current_fid') or 0):,}")
        state_panel.add_row("PHASE PROGRESS", f"{phase_current:,}/{phase_total:,}  ({pct:.1f}%)" if phase_total else "starting…")
        state_panel.add_row("ELAPSED", _fmt_duration(elapsed))
        state_panel.add_row("ETA", _fmt_duration(eta) if eta is not None else "calculating…")
        state_panel.add_row("THIS RUN", f"{int(stats.get('new_titles') or 0)} new  /  {int(stats.get('revisions_updated') or 0)} revised  /  {int(stats.get('errors') or 0)} errors")
    else:
        state_panel.add_row("STATUS", Text("● IDLE", style=muted))
        state_panel.add_row("LAST RESULT", str(last_sync.get("status") or "—").upper())
        state_panel.add_row("LAST DURATION", _fmt_duration(last_sync.get("elapsed_seconds")) if last_sync else "—")
        state_panel.add_row("LAST CHANGES", f"{int(last_sync.get('new_titles_ingested') or 0)} new  /  {int(last_sync.get('revisions_updated') or 0)} revised  /  {int(last_sync.get('errors') or 0)} errors" if last_sync else "—")
        state_panel.add_row("NEXT FID", f"{next_fid:,}")
    state_panel.add_row("ARCHIVE", f"{int(metrics.get('titles') or data.get('db_titles') or 0):,} titles  /  {int(metrics.get('releases') or 0):,} releases")
    state_panel.add_row("DATABASE", f"{float(metrics.get('db_size_mb') or data.get('db_size_mb') or 0):.2f} MB")

    if running and phase_total:
        state_panel.add_row("PROGRESS", _progress_bar(pct, width=28))

    activity = Table(box=None, expand=True, show_header=False, padding=(0, 1))
    activity.add_column("Time", width=9, style=muted)
    activity.add_column("Event", ratio=1)
    logs = list(data.get("log_lines") or [])[-4:]
    if logs:
        for item in reversed(logs):
            activity.add_row(str(item.get("ts") or "--:--:--"), str(item.get("msg") or "")[:82])
    else:
        activity.add_row("--:--:--", "No active-session events yet.")

    split = Table.grid(expand=True, padding=(0, 1))
    split.add_column(ratio=5)
    split.add_column(ratio=6)
    split.add_row(
        Panel(Group(menu, Text(""), maintenance), title="OPERATIONS", border_style=line_blue, box=box.SQUARE, style=f"on {navy}"),
        Panel(state_panel, title="SYSTEM STATUS", border_style=line_blue, box=box.SQUARE, style=f"on {navy}"),
    )

    body = Group(
        header,
        Text(""),
        split,
        Panel(activity, title="RECENT ACTIVITY", border_style=line_blue, box=box.SQUARE, style=f"on {navy}"),
    )

    return Panel(
        body,
        title=f"[bold {accent}]DVD REWIND // ARCHIVE CONSOLE[/]",
        subtitle="[dim]Select a key • active jobs keep running on the NAS[/]",
        border_style=line_blue,
        box=box.SQUARE,
        padding=(1, 2),
        style=f"white on {navy}",
    )


def _read_menu_key() -> str:
    if os.name == "posix" and termios is not None and tty is not None and sys.stdin.isatty():
        console.print("[bold cyan]Select:[/] ", end="")
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            key = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        console.print(key)
        return key.strip().lower()
    try:
        return console.input("[bold cyan]Select:[/] ").strip().lower()
    except EOFError:
        return "q"


def _pause_command_center(message: str = "Press Enter to return to the Command Center…") -> None:
    try:
        console.input(f"\n[dim]{message}[/]")
    except EOFError:
        pass


def _watch_after_start() -> int:
    try:
        run_archive_status_monitor(watch=True, exit_on_complete=True)
    except SystemExit as exc:
        if exc.code == 20:
            console.clear()
            try:
                completed = fetch_archive_status()
                console.print(build_archive_status_panel(completed, view="report"))
            except Exception:
                console.print("[bold green]RUN COMPLETE[/]")
            _pause_command_center("Run complete. Press Enter to return to DVD Rewind…")
            return 20
        raise
    return 0


def run_command_center() -> int:
    while True:
        try:
            data = fetch_archive_status()
        except Exception as exc:
            console.clear()
            console.print(Panel(f"[bold red]DVD Rewind web service is unavailable.[/]\n{exc}", border_style="red"))
            if not sys.stdin.isatty():
                return 1
            _pause_command_center()
            continue

        console.clear()
        console.print(build_command_center_panel(data))
        choice = _read_menu_key()

        if choice == "q":
            return 0
        if choice == "1":
            if data.get("is_running"):
                run_archive_status_monitor(watch=True)
                continue
            result = _post_archive_action("sync", {"since_initial": False})
            if not result.get("ok"):
                console.print(f"[yellow]{result.get('message') or 'A task is already running.'}[/]")
                _pause_command_center()
                continue
            code = _watch_after_start()
            if code == 20:
                return 20
            continue
        if choice == "2":
            if data.get("is_running"):
                run_archive_status_monitor(watch=True)
                continue
            result = _post_archive_action("sync", {"since_initial": True})
            if not result.get("ok"):
                console.print(f"[yellow]{result.get('message') or 'A task is already running.'}[/]")
                _pause_command_center()
                continue
            code = _watch_after_start()
            if code == 20:
                return 20
            continue
        if choice == "3":
            run_archive_status_monitor(watch=True)
            continue
        if choice == "4":
            if data.get("is_running"):
                run_archive_status_monitor(watch=True, new_only=True)
            else:
                console.clear()
                console.print(build_archive_status_panel(data, view="new"))
                _pause_command_center()
            continue
        if choice == "5":
            query = console.input("\n[bold cyan]Search DVD Rewind:[/] ").strip()
            if query:
                search_archive(query)
            _pause_command_center()
            continue
        if choice == "6":
            console.clear()
            console.print(build_archive_status_panel(data, view="errors"))
            retry = console.input("\n[bold cyan]R[/] retry failed FIDs  •  [bold cyan]Enter[/] back: ").strip().lower()
            if retry == "r":
                retry_failed_fids()
                _pause_command_center()
            continue
        if choice == "7":
            console.clear()
            console.print(build_archive_status_panel(data, view="report"))
            _pause_command_center()
            continue
        if choice == "8":
            console.print(f"[cyan]Opening {WEB_URL}…[/]")
            return 81
        if choice == "p":
            if data.get("is_running"):
                run_archive_status_monitor(watch=True)
                continue
            result = _post_archive_action("posters")
            if not result.get("ok"):
                console.print(f"[yellow]{result.get('message') or 'A task is already running.'}[/]")
                _pause_command_center()
                continue
            code = _watch_after_start()
            if code == 20:
                return 20
            continue
        if choice == "d":
            if data.get("is_running"):
                run_archive_status_monitor(watch=True)
                continue
            result = _post_archive_action("vacuum")
            if not result.get("ok"):
                console.print(f"[yellow]{result.get('message') or 'A task is already running.'}[/]")
                _pause_command_center()
                continue
            code = _watch_after_start()
            if code == 20:
                return 20
            continue


def search_archive(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    repo = ArchiveRepository()
    try:
        results = repo.search_fts(query, limit=limit)
    finally:
        repo.close()

    table = Table(title=f"DVDRewind Search — {query}", box=box.ROUNDED, expand=True)
    table.add_column("FID", justify="right", width=8)
    table.add_column("Title", ratio=1)
    table.add_column("Year", width=6)
    table.add_column("Format", width=14)
    table.add_column("Releases", width=8, justify="right")
    for row in results:
        table.add_row(
            f"{int(row.get('fid') or 0):,}",
            str(row.get("clean_title") or ""),
            str(row.get("year") or "—"),
            _format_badge(row.get("format_category")),
            str(row.get("release_count") or 0),
        )
    if not results:
        table.add_row("—", "No matching titles.", "—", "—", "—")
    console.print(table)
    console.print("[dim]Movie URL pattern: http://192.168.50.39:8091/film/<FID>[/]")
    return results


def retry_failed_fids() -> int:
    try:
        live_status = fetch_archive_status()
        if live_status.get("is_running"):
            console.print("[bold yellow]A DVDRewind archive task is already running. Retry is disabled until it finishes.[/]")
            return 2
    except Exception:
        pass

    if not FAILED_FIDS_FILE.exists():
        console.print("[green]No saved failed FIDs to retry.[/]")
        return 0
    try:
        entries = json.loads(FAILED_FIDS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Could not read failed FID list:[/] {exc}")
        return 1

    fids = []
    for item in entries if isinstance(entries, list) else []:
        try:
            fid = int(item.get("fid") if isinstance(item, dict) else item)
        except (TypeError, ValueError):
            continue
        if fid not in fids:
            fids.append(fid)
    if not fids:
        console.print("[green]No saved failed FIDs to retry.[/]")
        return 0

    repo = ArchiveRepository()
    client = ScraperClient()
    parser = DVDCompareParser(WarningCollector())
    remaining: List[Dict[str, Any]] = []
    success = 0
    table = Table(title=f"Retrying {len(fids)} failed FIDs", box=box.ROUNDED, expand=True)
    table.add_column("FID", justify="right")
    table.add_column("Result", ratio=1)

    for fid in fids:
        try:
            _, content, meta = client.fetch_film(fid, save_to_raw=True)
            if not content or meta.get("is_missing"):
                raise RuntimeError("comparison is still unavailable")
            parsed = parser.parse(content, fid=fid)
            raw_path = RAW_DIR / f"{fid}.html"
            repo.save_parsed_comparison(parsed, source_hash=meta.get("content_sha256"), raw_html_path=str(raw_path))
            title = parsed.get("clean_title") or f"FID-{fid}"
            try:
                poster = fetch_poster_from_tmdb(parsed.get("imdb_id"), title, parsed.get("year"))
                if poster:
                    repo.update_poster_url(fid, poster)
            except Exception:
                pass
            table.add_row(str(fid), f"[green]Recovered[/] {title}")
            success += 1
        except Exception as exc:
            remaining.append({"fid": fid, "phase": "retry", "error": str(exc), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
            table.add_row(str(fid), f"[red]Still failed[/] {str(exc)[:100]}")

    repo.close()
    FAILED_FIDS_FILE.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
    console.print(table)
    console.print(f"[bold]Recovered {success}/{len(fids)}.[/] Remaining failures: {len(remaining)}")
    return 0 if not remaining else 1


def completion_message() -> str:
    try:
        data = fetch_archive_status()
    except Exception:
        return "DVD Rewind task finished."
    last = data.get("last_sync") or {}
    if not last:
        return "DVD Rewind task finished."
    return (
        f"DVD Rewind finished: {int(last.get('new_titles_ingested') or 0)} new titles, "
        f"{int(last.get('revisions_updated') or 0)} revisions, "
        f"{int(last.get('errors') or 0)} errors."
    )
