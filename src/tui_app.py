from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, ListItem, ListView, Static

from src.cli_monitor import retry_failed_fids
from src.config import ARCHIVE_DIR
from src.db.repository import ArchiveRepository

STATUS_URL = "http://127.0.0.1:8088/api/archive/status"
CONTROL_BASE_URL = "http://127.0.0.1:8088/api/archive"
STATE_FILE = ARCHIVE_DIR / "tui_state.json"
WEB_URL = "http://192.168.50.39:8091"

SECTIONS = [
    ("dashboard", "Dashboard"),
    ("movies", "Movies"),
    ("population", "Population"),
    ("discoveries", "Discoveries"),
    ("failures", "Failures"),
    ("maintenance", "Maintenance"),
]

BADGE = {
    "READY": "[black on #e0b85c] READY [/black on #e0b85c]",
    "LIVE": "[black on #55e39f] LIVE [/black on #55e39f]",
    "IDLE": "[white on #344a60] IDLE [/white on #344a60]",
    "COMPLETE": "[black on #55e39f] COMPLETE [/black on #55e39f]",
    "FAILED": "[white on #b94a57] FAILED [/white on #b94a57]",
    "NEEDS ART": "[black on #e0b85c] NEEDS ART [/black on #e0b85c]",
    "NEEDS MATCH": "[black on #e0b85c] NEEDS MATCH [/black on #e0b85c]",
    "LOCAL MEDIA": "[white on #6b4d8c] LOCAL MEDIA [/white on #6b4d8c]",
    "EXTERNAL LINK": "[black on #63c7ff] EXTERNAL LINK [/black on #63c7ff]",
}


def _fetch_status() -> Dict[str, Any]:
    response = requests.get(STATUS_URL, timeout=5)
    response.raise_for_status()
    return response.json()


def _post_action(endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = requests.post(f"{CONTROL_BASE_URL}/{endpoint}", json=payload or {}, timeout=10)
    response.raise_for_status()
    return response.json()


def _duration(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _fmt_ts(value: Any) -> str:
    if not value:
        return "Never"
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%b %d %H:%M")
    except Exception:
        return text[:16]


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def _library_insights() -> Dict[str, Any]:
    repo = ArchiveRepository()
    try:
        conn = repo.conn
        one = lambda sql: int(conn.execute(sql).fetchone()[0] or 0)
        missing_art = one(
            "SELECT COUNT(*) FROM titles WHERE is_missing=0 AND "
            "(poster_url IS NULL OR poster_url='' OR poster_url='/static/images/missing_poster.svg')"
        )
        missing_imdb = one("SELECT COUNT(*) FROM titles WHERE is_missing=0 AND (imdb_id IS NULL OR imdb_id='')")
        missing_records = one("SELECT COUNT(*) FROM titles WHERE is_missing=1")
        newest = conn.execute(
            "SELECT fid, clean_title, year, format_category, poster_url, imdb_id, scraped_at "
            "FROM titles WHERE is_missing=0 ORDER BY scraped_at DESC, fid DESC LIMIT 30"
        ).fetchall()
        return {
            "missing_art": missing_art,
            "missing_imdb": missing_imdb,
            "missing_records": missing_records,
            "newest": [dict(row) for row in newest],
        }
    finally:
        repo.close()


class KeysScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("h", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    CSS = """
    KeysScreen { align: center middle; background: rgba(2, 10, 20, 0.86); }
    #keys_box {
        width: 78;
        height: 33;
        border: solid #2f78b7;
        background: #071a2f;
        padding: 1 2;
    }
    #keys_title { height: 2; color: #63c7ff; text-style: bold; }
    #keys_body { height: 1fr; color: #d7e4ef; }
    #keys_hint { height: 2; color: #8aa7c1; text-align: center; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="keys_box"):
            yield Static("DVD REWIND // KEYS", id="keys_title")
            yield Static(
                "[bold #63c7ff]Browse[/]\n"
                "  Up/Down        Move through sections or rows\n"
                "  Enter          Open / run selected row action\n"
                "  F              Find movies\n"
                "  N              Show Best Next recommendation\n\n"
                "[bold #63c7ff]Population[/]\n"
                "  U              Run normal update\n"
                "  C              Catch up since 76,200\n"
                "  P              Poster backfill\n"
                "  M              Database maintenance\n"
                "  E              Retry saved failed FIDs\n\n"
                "[bold #63c7ff]App controls[/]\n"
                "  R              Refresh data only\n"
                "  F5             Reload updated application code\n"
                "  O              Open DVD Rewind web UI\n"
                "  H              This shortcut sheet\n"
                "  Q              Quit\n\n"
                "[dim]Refresh keeps your place. F5 restarts only the CLI process and restores your section/search.[/dim]",
                id="keys_body",
                markup=True,
            )
            yield Static("H / Esc / Q  Close", id="keys_hint")

    def action_close(self) -> None:
        self.dismiss(None)


class DVDRewindTUI(App[None]):
    TITLE = "DVD Rewind"
    SUB_TITLE = "Archive Console"

    BINDINGS = [
        Binding("q", "quit_app", "Quit", show=False),
        Binding("r", "refresh_data", "Refresh", show=False),
        Binding("f", "find", "Find", show=False),
        Binding("n", "best_next", "Best Next", show=False),
        Binding("h", "show_keys", "Keys", show=False),
        Binding("f5", "reload_code", "Reload", show=False),
        Binding("u", "run_update", "Update", show=False),
        Binding("c", "run_catchup", "Catch-up", show=False),
        Binding("p", "poster_backfill", "Posters", show=False),
        Binding("m", "maintenance", "Maintenance", show=False),
        Binding("e", "retry_failures", "Retry errors", show=False),
        Binding("o", "open_web", "Web", show=False),
    ]

    CSS = """
    Screen {
        background: #061525;
        color: #d7e4ef;
    }
    #brand {
        height: 4;
        border: solid #2f78b7;
        background: #0a213c;
        color: #f4f7fb;
        padding: 0 2;
    }
    #workspace { height: 1fr; }
    #left {
        width: 24;
        min-width: 21;
        border: solid #2f78b7;
        background: #071a2f;
        margin-right: 1;
    }
    #center {
        width: 1fr;
        min-width: 56;
        border: solid #2f78b7;
        background: #071a2f;
        margin-right: 1;
    }
    #right {
        width: 34;
        min-width: 30;
        border: solid #2f78b7;
        background: #071a2f;
    }
    .panel-title {
        height: 2;
        padding: 0 1;
        color: #63c7ff;
        text-style: bold;
        background: #0c2747;
    }
    #sections { height: 1fr; padding: 1 0; }
    ListItem { height: 3; padding: 0 1; color: #c7d6e4; }
    ListItem.--highlight { background: #153b62; color: #ffffff; text-style: bold; }
    #work_title { height: 3; padding: 0 1; color: #ffffff; text-style: bold; }
    #search_input { height: 3; margin: 0 1; display: none; }
    #work_table { height: 1fr; margin: 0 1; }
    DataTable > .datatable--header { background: #0c2747; color: #63c7ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #244e74; color: #ffffff; }
    #detail {
        height: 9;
        min-height: 7;
        border-top: solid #2f78b7;
        padding: 1 2;
        color: #d7e4ef;
    }
    #best_next { height: 12; padding: 1 2; border-bottom: solid #2f78b7; }
    #recent_activity { height: 1fr; padding: 1 2; border-bottom: solid #2f78b7; }
    #warnings { height: 10; padding: 1 2; }
    #status_line {
        height: 2;
        border: solid #2f78b7;
        background: #0a213c;
        padding: 0 2;
        color: #8aa7c1;
    }
    #footer_keys {
        height: 2;
        background: #071a2f;
        color: #8aa7c1;
        text-align: center;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        saved = _load_state()
        self.section = saved.get("section") if saved.get("section") in {s[0] for s in SECTIONS} else "dashboard"
        self.search_query = str(saved.get("search_query") or "")
        self.row_details: Dict[str, Dict[str, Any]] = {}
        self.status: Dict[str, Any] = {}
        self.insights: Dict[str, Any] = {}
        self.search_results: List[Dict[str, Any]] = []
        self._last_insights_refresh = 0.0
        project_root = Path(__file__).resolve().parent.parent
        self._code_paths = [Path(__file__).resolve(), project_root / "populate_all.py", project_root / "src" / "cli_monitor.py"]
        self._startup_mtimes = {path: path.stat().st_mtime for path in self._code_paths if path.exists()}
        self._update_ready = False
        self._saved_cursor_row = max(0, int(saved.get("cursor_row") or 0))
        self._restored_cursor = False
        self._was_running: Optional[bool] = None
        self._completion_timer_started = False

    def compose(self) -> ComposeResult:
        yield Static("DVD REWIND", id="brand", markup=True)
        with Horizontal(id="workspace"):
            with Vertical(id="left"):
                yield Static("LIBRARY / SECTIONS", classes="panel-title")
                yield ListView(
                    *[ListItem(Label(label), id=f"section-{key}") for key, label in SECTIONS],
                    id="sections",
                )
            with Vertical(id="center"):
                yield Static("CURRENT WORK AREA", id="work_title")
                yield Input(placeholder="Find a title…", value=self.search_query, id="search_input")
                yield DataTable(id="work_table", cursor_type="row", zebra_stripes=True)
                yield Static("Select an item for details.", id="detail", markup=True)
            with Vertical(id="right"):
                yield Static("BEST NEXT\nLoading…", id="best_next", markup=True)
                yield Static("RECENT ACTIVITY\nLoading…", id="recent_activity", markup=True)
                yield Static("WARNINGS / NOTICES\nLoading…", id="warnings", markup=True)
        yield Static("Connecting to archive…", id="status_line", markup=True)
        yield Static("N Best Next   F Find   H Keys   F5 Reload   Q Quit", id="footer_keys", markup=True)

    async def on_mount(self) -> None:
        table = self.query_one("#work_table", DataTable)
        table.cursor_type = "row"
        section_list = self.query_one("#sections", ListView)
        section_list.index = [key for key, _ in SECTIONS].index(self.section)
        self.set_interval(1.5, self.refresh_data)
        self.set_interval(2.0, self.check_code_update)
        await self.refresh_data()
        self.render_section()

    def save_place(self) -> None:
        try:
            table = self.query_one("#work_table", DataTable)
            cursor_row = table.cursor_row
        except Exception:
            cursor_row = 0
        _save_state(
            {
                "section": self.section,
                "search_query": self.search_query,
                "cursor_row": cursor_row,
                "saved_at": datetime.now().isoformat(),
            }
        )

    def on_unmount(self) -> None:
        self.save_place()

    async def refresh_data(self) -> None:
        try:
            status = await asyncio.to_thread(_fetch_status)
        except Exception as exc:
            self.query_one("#status_line", Static).update(f"[bold #ff7d8a]OFFLINE[/]  {exc}")
            return

        now = asyncio.get_running_loop().time()
        if not self.insights or now - self._last_insights_refresh > 8:
            try:
                self.insights = await asyncio.to_thread(_library_insights)
                self._last_insights_refresh = now
            except Exception:
                pass

        was_running = self._was_running
        self._was_running = bool(status.get("is_running"))
        self.status = status
        self.update_brand()
        self.update_intelligence()
        self.update_status_line()
        self.render_section(preserve_cursor=True)

        if was_running is True and not self._was_running:
            self.show_completion_state()

    def check_code_update(self) -> None:
        changed = False
        for path, started_mtime in self._startup_mtimes.items():
            try:
                if path.stat().st_mtime > started_mtime + 0.5:
                    changed = True
                    break
            except OSError:
                continue
        if changed != self._update_ready:
            self._update_ready = changed
            self.update_brand()
            self.update_status_line()

    def update_brand(self) -> None:
        running = bool(self.status.get("is_running"))
        stats = self.status.get("stats") or {}
        phase = str(stats.get("phase") or "idle").replace("_", " ").upper()
        marker = "[black on #55e39f] LIVE [/black on #55e39f]" if running else "[white on #344a60] IDLE [/white on #344a60]"
        update = "   [black on #e0b85c] UPDATE READY - F5 [/black on #e0b85c]" if self._update_ready else ""
        title = (
            "[bold #f4f7fb]DVD REWIND[/bold #f4f7fb]  [#63c7ff]// VIDEO STORE ARCHIVE TERMINAL[/#63c7ff]"
            f"{update}\n{marker}  [#8aa7c1]{phase}[/#8aa7c1]"
        )
        if running:
            title += f"  [white]{self.status.get('status_message') or 'Working'}[/white]"
        else:
            title += "  [#8aa7c1]Archive worker standing by[/#8aa7c1]"
        self.query_one("#brand", Static).update(title)

    def update_status_line(self) -> None:
        stats = self.status.get("stats") or {}
        metrics = self.status.get("metrics") or {}
        post = self.status.get("post_initial") or {}
        running = bool(self.status.get("is_running"))
        current = int(stats.get("current_fid") or 0)
        next_fid = int(stats.get("next_fid") or post.get("next_fid") or 76201)
        prefix = "[#55e39f]RUNNING[/#55e39f]" if running else "[#8aa7c1]IDLE[/#8aa7c1]"
        text = (
            f"{prefix}  |  FID {current:,} -> {next_fid:,}  |  "
            f"{int(metrics.get('titles') or self.status.get('db_titles') or 0):,} titles  |  "
            f"{int(stats.get('errors') or 0)} errors"
        )
        if self._update_ready:
            text += "  |  [black on #e0b85c] UPDATE READY - F5 [/black on #e0b85c]"
        self.query_one("#status_line", Static).update(text)

    def _best_next_items(self) -> List[str]:
        running = bool(self.status.get("is_running"))
        stats = self.status.get("stats") or {}
        last = self.status.get("last_sync") or {}
        missing_art = int(self.insights.get("missing_art") or 0)
        items: List[str] = []
        if running:
            items.append("[black on #55e39f] LIVE [/black on #55e39f] Keep the current population job visible until it completes.")
            if int(stats.get("errors") or 0):
                items.append(f"[white on #b94a57] FAILED [/white on #b94a57] Review {int(stats.get('errors') or 0)} current fetch errors after the run.")
        else:
            failures = int(last.get("errors") or stats.get("errors") or 0)
            if failures:
                items.append(f"[white on #b94a57] FAILED [/white on #b94a57] Retry {failures} failed FIDs from the last run.")
            if missing_art:
                items.append(f"[black on #e0b85c] NEEDS ART [/black on #e0b85c] Backfill artwork for {missing_art:,} titles.")
            items.append("[black on #e0b85c] READY [/black on #e0b85c] Run population update to check revisions and new comparisons.")
        return items[:3]

    def update_intelligence(self) -> None:
        best = self._best_next_items()
        self.query_one("#best_next", Static).update(
            "[bold #63c7ff]BEST NEXT[/bold #63c7ff]\n\n" + "\n\n".join(best or ["[#8aa7c1]Nothing urgent right now.[/#8aa7c1]"])
        )

        logs = list(self.status.get("log_lines") or [])[-5:]
        activity = ["[bold #63c7ff]RECENT ACTIVITY[/bold #63c7ff]", ""]
        if logs:
            for item in reversed(logs):
                ts = str(item.get("ts") or "--:--")
                msg = str(item.get("msg") or "")
                prefix = "[white on #b94a57] ! [/white on #b94a57]" if "error" in msg.lower() else "[#55e39f]+[/#55e39f]"
                activity.append(f"{prefix} [#8aa7c1]{ts}[/#8aa7c1] {msg[:58]}")
        else:
            activity.append("[#8aa7c1]No live activity right now.[/#8aa7c1]")
        self.query_one("#recent_activity", Static).update("\n".join(activity))

        warnings = ["[bold #63c7ff]WARNINGS / NOTICES[/bold #63c7ff]", ""]
        missing_imdb = int(self.insights.get("missing_imdb") or 0)
        missing_art = int(self.insights.get("missing_art") or 0)
        if int((self.status.get("stats") or {}).get("errors") or 0):
            warnings.append(f"[white on #b94a57] FAILED [/white on #b94a57] {(self.status.get('stats') or {}).get('errors')} current errors")
        if missing_art:
            warnings.append(f"[black on #e0b85c] NEEDS ART [/black on #e0b85c] {missing_art:,} titles")
        if missing_imdb:
            warnings.append(f"[black on #e0b85c] NEEDS MATCH [/black on #e0b85c] {missing_imdb:,} IMDb IDs")
        if len(warnings) == 2:
            warnings.append("[#55e39f]No failures right now.[/#55e39f]")
        self.query_one("#warnings", Static).update("\n".join(warnings))

    def _clear_table(self, columns: List[str]) -> DataTable:
        table = self.query_one("#work_table", DataTable)
        old_row = table.cursor_row
        table.clear(columns=True)
        table.add_columns(*columns)
        self.row_details = {}
        if old_row < 0:
            old_row = 0
        return table

    def _add_row(self, table: DataTable, key: str, values: List[Any], detail: Dict[str, Any]) -> None:
        if values:
            state = str(values[0])
            state_styles = {
                "READY": "bold black on #e0b85c",
                "LIVE": "bold black on #55e39f",
                "IDLE": "bold white on #344a60",
                "COMPLETE": "bold black on #55e39f",
                "FAILED": "bold white on #b94a57",
                "NEEDS ART": "bold black on #e0b85c",
                "NEEDS MATCH": "bold black on #e0b85c",
            }
            if state in state_styles:
                values = [Text(f" {state} ", style=state_styles[state]), *values[1:]]
        table.add_row(*values, key=key)
        self.row_details[key] = detail

    def render_section(self, preserve_cursor: bool = False) -> None:
        if not self.is_mounted:
            return
        table = self.query_one("#work_table", DataTable)
        if preserve_cursor:
            previous = table.cursor_row
        elif not self._restored_cursor:
            previous = self._saved_cursor_row
        else:
            previous = 0
        title = self.query_one("#work_title", Static)

        if self.section == "dashboard":
            title.update("CURRENT WORK AREA  //  Dashboard")
            table = self._clear_table(["State", "Work Item", "Value", "Next Step"])
            running = bool(self.status.get("is_running"))
            stats = self.status.get("stats") or {}
            post = self.status.get("post_initial") or {}
            last = self.status.get("last_sync") or {}
            self._add_row(table, "worker", ["LIVE" if running else "IDLE", "Archive Worker", str(self.status.get("status_message") or "Ready"), "Watch" if running else "Run Update"], {"title": "Archive Worker", "state": "LIVE" if running else "IDLE", "body": "The NAS worker handles sync, catch-up, poster backfill, and database maintenance. Leaving the TUI does not stop an active job."})
            self._add_row(table, "cursor", ["READY", "Population Cursor", f"FID {int(stats.get('next_fid') or post.get('next_fid') or 76201):,}", "Continue"], {"title": "Population Cursor", "state": "READY", "body": "The next post-76,200 FID that the catch-up worker will inspect."})
            self._add_row(table, "last", ["COMPLETE" if last else "IDLE", "Last Run", _fmt_ts(last.get("last_sync")) if last else "No completed run", f"{int(last.get('new_titles_ingested') or 0)} new / {int(last.get('errors') or 0)} errors"], {"title": "Last Run", "state": "COMPLETE" if last else "IDLE", "body": f"Duration {_duration(last.get('elapsed_seconds'))}. {int(last.get('revisions_updated') or 0)} revisions, {int(last.get('posters_fetched') or 0)} posters, {int(last.get('errors') or 0)} errors."})
            self._add_row(table, "best", ["READY", "Best Next", self._best_next_items()[0] if self._best_next_items() else "Nothing urgent", "Press N"], {"title": "Best Next", "state": "READY", "body": "Press N at any time for the current recommended action and why it matters."})

        elif self.section == "movies":
            title.update(f"CURRENT WORK AREA  //  Movies{'  //  ' + self.search_query if self.search_query else ''}")
            table = self._clear_table(["Status", "Title", "Year", "Source", "Next Step"])
            rows = self.search_results or list(self.insights.get("newest") or [])
            if not rows:
                self._add_row(table, "empty", ["IDLE", "Nothing to show", "—", "—", "Press F to find"], {"title": "Movies", "state": "IDLE", "body": "No movie rows are loaded. Press F and type a title."})
            else:
                for row in rows[:30]:
                    fid = int(row.get("fid") or 0)
                    title_text = str(row.get("clean_title") or "Unknown")
                    year = str(row.get("year") or "—")
                    source = str(row.get("format_category") or "Archive")
                    state = "COMPLETE" if row.get("poster_url") else "NEEDS ART"
                    self._add_row(table, f"movie-{fid}", [state, title_text, year, source, "Inspect"], {"title": title_text, "state": state, "fid": fid, "body": f"FID {fid:,}  |  {source}  |  IMDb: {row.get('imdb_id') or 'Source not checked yet'}  |  Artwork: {'available' if row.get('poster_url') else 'not downloaded'}."})

        elif self.section == "population":
            title.update("CURRENT WORK AREA  //  Population")
            table = self._clear_table(["Status", "Task", "Scope", "Next Step"])
            busy = bool(self.status.get("is_running"))
            state = "LIVE" if busy else "READY"
            self._add_row(table, "population-update", [state, "Run Update", "Homepage revisions + resume catch-up", "Watch" if busy else "Enter / U"], {"title": "Run Update", "state": state, "action": "update", "body": "Normal maintenance run: check DVDCompare homepage revisions, then resume the persisted post-76,200 cursor."})
            self._add_row(table, "population-catchup", [state, "Catch Up Since 76,200", "Re-scan every post-initial FID", "Watch" if busy else "Enter / C"], {"title": "Catch Up Since 76,200", "state": state, "action": "catchup", "body": "Starts at FID 76,201 and skips titles already stored. Use this when you want a full post-initial verification pass."})
            self._add_row(table, "population-posters", [state, "Poster Backfill", f"{int(self.insights.get('missing_art') or 0):,} titles need art", "Watch" if busy else "Enter / P"], {"title": "Poster Backfill", "state": "NEEDS ART" if self.insights.get("missing_art") else state, "action": "posters", "body": "Fetch artwork for archive titles that do not have a usable poster yet."})
            self._add_row(table, "population-db", [state, "Database Maintenance", "Integrity + FTS optimize + VACUUM", "Watch" if busy else "Enter / M"], {"title": "Database Maintenance", "state": state, "action": "maintenance", "body": "Checks SQLite integrity, optimizes full-text search, then VACUUMs the archive database."})

        elif self.section == "discoveries":
            title.update("CURRENT WORK AREA  //  Recent Discoveries")
            table = self._clear_table(["Status", "Title", "Year", "Format", "FID"])
            discoveries = list(self.status.get("recent_discoveries") or [])
            if not discoveries:
                discoveries = list(self.insights.get("newest") or [])[:20]
            if not discoveries:
                self._add_row(table, "empty", ["IDLE", "No recent discoveries", "—", "—", "—"], {"title": "Recent Discoveries", "state": "IDLE", "body": "Nothing new has been recorded yet."})
            else:
                for row in discoveries[:25]:
                    fid = int(row.get("fid") or 0)
                    self._add_row(table, f"discovery-{fid}", ["COMPLETE", str(row.get("title") or row.get("clean_title") or "Unknown"), str(row.get("year") or "—"), str(row.get("format") or row.get("format_category") or "—"), f"{fid:,}"], {"title": str(row.get("title") or row.get("clean_title") or "Unknown"), "state": "COMPLETE", "fid": fid, "body": f"Newly observed archive record at FID {fid:,}."})

        elif self.section == "failures":
            title.update("CURRENT WORK AREA  //  Failures")
            table = self._clear_table(["Status", "FID", "Phase", "Problem", "Next Step"])
            failures = list(self.status.get("failed_fids") or [])
            if not failures:
                self._add_row(table, "empty", ["COMPLETE", "—", "—", "No failures right now", "Nothing to do"], {"title": "Failures", "state": "COMPLETE", "body": "No saved failures are currently waiting for attention."})
            else:
                for idx, row in enumerate(reversed(failures[-25:])):
                    fid = int(row.get("fid") or 0)
                    problem = str(row.get("error") or "Unknown error")
                    self._add_row(table, f"failure-{fid}-{idx}", ["FAILED", f"{fid:,}", str(row.get("phase") or "—"), problem[:60], "Enter / E"], {"title": f"Failed FID {fid:,}", "state": "FAILED", "action": "retry", "body": problem})

        elif self.section == "maintenance":
            title.update("CURRENT WORK AREA  //  Maintenance")
            table = self._clear_table(["Status", "Area", "Value", "Next Step"])
            art = int(self.insights.get("missing_art") or 0)
            imdb = int(self.insights.get("missing_imdb") or 0)
            missing = int(self.insights.get("missing_records") or 0)
            self._add_row(table, "maint-art", ["NEEDS ART" if art else "COMPLETE", "Artwork", f"{art:,} missing posters", "P backfill" if art else "Healthy"], {"title": "Artwork", "state": "NEEDS ART" if art else "COMPLETE", "body": f"{art:,} non-missing titles currently lack usable poster artwork."})
            self._add_row(table, "maint-imdb", ["NEEDS MATCH" if imdb else "COMPLETE", "IMDb Links", f"{imdb:,} without IMDb ID", "Review metadata" if imdb else "Healthy"], {"title": "IMDb Links", "state": "NEEDS MATCH" if imdb else "COMPLETE", "body": f"{imdb:,} titles have no IMDb identifier. Unknown is not treated as failed."})
            self._add_row(table, "maint-missing", ["IDLE", "Known Missing FIDs", f"{missing:,} records", "Informational"], {"title": "Known Missing FIDs", "state": "IDLE", "body": "These are known unavailable DVDCompare IDs, not crawler failures."})
            self._add_row(table, "maint-db", ["READY", "Database", f"{float(self.status.get('db_size_mb') or (self.status.get('metrics') or {}).get('db_size_mb') or 0):.2f} MB", "Enter / M"], {"title": "Database Maintenance", "state": "READY", "action": "maintenance", "body": "Run integrity check, FTS optimization, and VACUUM."})

        max_row = max(0, table.row_count - 1)
        table.move_cursor(row=min(previous, max_row), animate=False)
        self._restored_cursor = True
        self.update_detail_for_cursor()

    def update_detail_for_cursor(self) -> None:
        table = self.query_one("#work_table", DataTable)
        if table.row_count <= 0:
            self.query_one("#detail", Static).update("[#8aa7c1]No details available.[/#8aa7c1]")
            return
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            detail = self.row_details.get(str(row_key.value)) or {}
        except Exception:
            detail = {}
        state = str(detail.get("state") or "IDLE")
        badge = BADGE.get(state, f"[white on #344a60] {state} [/white on #344a60]")
        title = str(detail.get("title") or "Selected item")
        body = str(detail.get("body") or "No additional detail.")
        action_hint = ""
        if detail.get("action"):
            action_hint = "\n[#8aa7c1]Enter runs the selected action.[/#8aa7c1]"
        self.query_one("#detail", Static).update(
            f"[bold #63c7ff]DETAIL[/bold #63c7ff]  {badge}\n[bold white]{title}[/bold white]\n{body}{action_hint}"
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if item_id.startswith("section-"):
            self.section = item_id.replace("section-", "", 1)
            self.render_section()
            self.save_place()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.update_detail_for_cursor()
        self.save_place()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        detail = self.row_details.get(key) or {}
        action = detail.get("action")
        if action:
            await self.run_named_action(str(action))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        search = self.query_one("#search_input", Input)
        search.styles.display = "none"
        if not query:
            return
        self.search_query = query
        try:
            repo = ArchiveRepository()
            try:
                self.search_results = await asyncio.to_thread(repo.search_fts, query, 40)
            finally:
                repo.close()
        except Exception as exc:
            self.notify(f"Search failed: {exc}", severity="error")
            return
        self.section = "movies"
        section_list = self.query_one("#sections", ListView)
        section_list.index = [key for key, _ in SECTIONS].index("movies")
        self.render_section()
        self.save_place()

    async def run_named_action(self, name: str) -> None:
        if name == "update":
            await self.start_worker("sync", {"since_initial": False}, "Normal archive update started")
        elif name == "catchup":
            await self.start_worker("sync", {"since_initial": True}, "Post-76,200 catch-up started")
        elif name == "posters":
            await self.start_worker("posters", {}, "Poster backfill started")
        elif name == "maintenance":
            await self.start_worker("vacuum", {}, "Database maintenance started")
        elif name == "retry":
            await self.action_retry_failures()

    async def start_worker(self, endpoint: str, payload: Dict[str, Any], message: str) -> None:
        if self.status.get("is_running"):
            self.notify("A DVD Rewind job is already running. Select Watch Live Run instead.", severity="warning")
            return
        try:
            result = await asyncio.to_thread(_post_action, endpoint, payload)
        except Exception as exc:
            self.notify(f"Could not start task: {exc}", severity="error")
            return
        if not result.get("ok"):
            self.notify(str(result.get("message") or "Task could not be started"), severity="warning")
            return
        self.section = "population"
        self.notify(message, severity="information")
        await self.refresh_data()
        self.save_place()

    def show_completion_state(self) -> None:
        last = self.status.get("last_sync") or {}
        self.section = "population"
        section_list = self.query_one("#sections", ListView)
        section_list.index = [key for key, _ in SECTIONS].index("population")
        self.render_section()
        self.query_one("#detail", Static).update(
            "[bold black on #55e39f] RUN COMPLETE [/bold black on #55e39f]\n"
            f"[bold white]Archive job finished successfully.[/bold white]\n"
            f"{int(last.get('new_titles_ingested') or 0)} new titles  |  "
            f"{int(last.get('revisions_updated') or 0)} revised  |  "
            f"{int(last.get('posters_fetched') or 0)} posters  |  "
            f"{int(last.get('errors') or 0)} errors  |  {_duration(last.get('elapsed_seconds'))}"
        )
        self.notify("DVD Rewind run complete", severity="information", timeout=3)
        self.save_place()
        if os.environ.get("DVDREWIND_TUI_WRAPPER") == "1" and not self._completion_timer_started:
            self._completion_timer_started = True
            self.set_timer(3.0, lambda: self.exit(return_code=20))

    async def action_refresh_data(self) -> None:
        await self.refresh_data()
        self.notify("Archive data refreshed", timeout=1.5)

    def action_find(self) -> None:
        search = self.query_one("#search_input", Input)
        search.styles.display = "block"
        search.value = self.search_query
        search.focus()

    def action_best_next(self) -> None:
        items = self._best_next_items()
        detail = "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(items)) if items else "Nothing urgent right now."
        self.query_one("#detail", Static).update(f"[bold #63c7ff]BEST NEXT[/bold #63c7ff]\n{detail}\n[#8aa7c1]Choose Population or Failures to act on it.[/#8aa7c1]")

    def action_show_keys(self) -> None:
        self.push_screen(KeysScreen())

    def action_reload_code(self) -> None:
        self.save_place()
        self.exit(return_code=82)

    def action_quit_app(self) -> None:
        self.save_place()
        self.exit(return_code=0)

    async def action_run_update(self) -> None:
        await self.run_named_action("update")

    async def action_run_catchup(self) -> None:
        await self.run_named_action("catchup")

    async def action_poster_backfill(self) -> None:
        await self.run_named_action("posters")

    async def action_maintenance(self) -> None:
        await self.run_named_action("maintenance")

    async def action_retry_failures(self) -> None:
        if self.status.get("is_running"):
            self.notify("Wait for the active archive job to finish before retrying failures.", severity="warning")
            return
        self.notify("Retrying saved failed FIDs…", timeout=2)
        code = await asyncio.to_thread(retry_failed_fids)
        await self.refresh_data()
        if code == 0:
            self.notify("Failure retry completed", severity="information")
        else:
            self.notify("Some FIDs still failed; see Failures", severity="warning")

    def action_open_web(self) -> None:
        self.save_place()
        self.exit(return_code=81)


def run_tui_command_center() -> int:
    app = DVDRewindTUI()
    app.run(mouse=False)
    return int(app.return_code or 0)
