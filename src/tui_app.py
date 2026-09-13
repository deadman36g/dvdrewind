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
from textual.css.query import NoMatches
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
    ("imdb", "IMDb Repair"),
    ("artwork", "Artwork Repair"),
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
    "NEEDS BOTH": "[white on #9c6545] NEEDS BOTH [/white on #9c6545]",
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


def _post_poster(fid: int) -> Dict[str, Any]:
    response = requests.post(f"http://127.0.0.1:8088/api/poster/{int(fid)}", json={}, timeout=35)
    try:
        data = response.json()
    except Exception:
        data = {"success": False, "error": response.text[:300]}
    if response.status_code >= 400 and "error" not in data:
        data["error"] = f"HTTP {response.status_code}"
    return data


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
        art_missing_sql = "(poster_url IS NULL OR TRIM(poster_url)='' OR poster_url='/static/images/missing_poster.svg')"
        imdb_missing_sql = "(imdb_id IS NULL OR TRIM(imdb_id)='')"
        total_titles = one("SELECT COUNT(*) FROM titles WHERE is_missing=0")
        missing_art = one(f"SELECT COUNT(*) FROM titles WHERE is_missing=0 AND {art_missing_sql}")
        missing_imdb = one(f"SELECT COUNT(*) FROM titles WHERE is_missing=0 AND {imdb_missing_sql}")
        missing_both = one(
            f"SELECT COUNT(*) FROM titles WHERE is_missing=0 AND {imdb_missing_sql} AND {art_missing_sql}"
        )
        missing_records = one("SELECT COUNT(*) FROM titles WHERE is_missing=1")
        with_art = max(0, total_titles - missing_art)
        with_imdb = max(0, total_titles - missing_imdb)
        ready_titles = one(
            f"SELECT COUNT(*) FROM titles WHERE is_missing=0 AND NOT {imdb_missing_sql} AND NOT {art_missing_sql}"
        )
        newest = conn.execute(
            "SELECT fid, clean_title, year, format_category, poster_url, imdb_id, scraped_at "
            "FROM titles WHERE is_missing=0 ORDER BY scraped_at DESC, fid DESC LIMIT 30"
        ).fetchall()
        missing_imdb_rows = conn.execute(
            "SELECT fid, clean_title, year, format_category, poster_url, imdb_id, scraped_at "
            f"FROM titles WHERE is_missing=0 AND {imdb_missing_sql} "
            "ORDER BY fid DESC LIMIT 120"
        ).fetchall()
        missing_art_rows = conn.execute(
            "SELECT fid, clean_title, year, format_category, poster_url, imdb_id, scraped_at "
            f"FROM titles WHERE is_missing=0 AND {art_missing_sql} "
            "ORDER BY fid DESC LIMIT 120"
        ).fetchall()
        mix_rows = conn.execute(
            "SELECT CASE "
            "WHEN LOWER(format_category) LIKE '%4k%' OR LOWER(format_category) LIKE '%uhd%' THEN '4K UHD' "
            "WHEN LOWER(format_category) LIKE '%blu%' THEN 'Blu-ray' "
            "WHEN LOWER(format_category) LIKE '%dvd%' THEN 'DVD' "
            "ELSE 'Other' END AS bucket, COUNT(*) AS count "
            "FROM titles WHERE is_missing=0 GROUP BY bucket ORDER BY count DESC"
        ).fetchall()
        return {
            "total_titles": total_titles,
            "with_art": with_art,
            "with_imdb": with_imdb,
            "ready_titles": ready_titles,
            "missing_art": missing_art,
            "missing_imdb": missing_imdb,
            "missing_both": missing_both,
            "missing_records": missing_records,
            "missing_imdb_rows": [dict(row) for row in missing_imdb_rows],
            "missing_art_rows": [dict(row) for row in missing_art_rows],
            "format_mix": {str(row["bucket"]): int(row["count"] or 0) for row in mix_rows},
            "newest": [dict(row) for row in newest],
        }
    finally:
        repo.close()


def _meter(done: int, total: int, width: int = 22, fill_style: str = "#55e39f") -> str:
    total = max(0, int(total or 0))
    done = max(0, min(int(done or 0), total)) if total else 0
    pct = (done / total * 100.0) if total else 0.0
    filled = int(round(width * pct / 100.0))
    return f"[{fill_style}]{'#' * filled}[/{fill_style}][#344a60]{'-' * (width - filled)}[/#344a60] {pct:5.1f}%"


def _mini_bar(value: int, maximum: int, width: int = 12, style: str = "#63c7ff") -> str:
    maximum = max(1, int(maximum or 1))
    value = max(0, int(value or 0))
    filled = int(round(width * min(1.0, value / maximum)))
    return f"[{style}]{'#' * filled}[/{style}][#344a60]{'-' * (width - filled)}[/#344a60]"


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
        height: 36;
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
                "[bold #63c7ff]Repair / population[/]\n"
                "  U              Run normal update\n"
                "  C              Catch up since 76,200\n"
                "  I              Open IMDb Repair\n"
                "  A              Open Artwork Repair\n"
                "  P              Run full poster backfill\n"
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
        Binding("i", "imdb_repair", "IMDb repair", show=False),
        Binding("a", "artwork_repair", "Artwork repair", show=False),
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
    #health_graphs {
        height: 7;
        padding: 0 2;
        border-bottom: solid #2f78b7;
        background: #081d34;
    }
    #job_graph {
        height: 4;
        padding: 0 2;
        border-bottom: solid #2f78b7;
        background: #071a2f;
    }
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
    #best_next { height: 11; padding: 1 2; border-bottom: solid #2f78b7; }
    #catalog_mix { height: 9; padding: 1 2; border-bottom: solid #2f78b7; }
    #recent_activity { height: 1fr; padding: 1 2; border-bottom: solid #2f78b7; }
    #warnings { height: 8; padding: 1 2; }
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
                yield Static("LIBRARY HEALTH\nLoading…", id="health_graphs", markup=True)
                yield Static("ACTIVE JOB\nNo active archive job.", id="job_graph", markup=True)
                yield Input(placeholder="Find a title…", value=self.search_query, id="search_input")
                yield DataTable(id="work_table", cursor_type="row", zebra_stripes=True)
                yield Static("Select an item for details.", id="detail", markup=True)
            with Vertical(id="right"):
                yield Static("BEST NEXT\nLoading…", id="best_next", markup=True)
                yield Static("CATALOG MIX\nLoading…", id="catalog_mix", markup=True)
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
        if not self.is_mounted:
            return
        try:
            self.update_brand()
            self.update_health_graphs()
            self.update_job_graph()
            self.update_intelligence()
            self.update_catalog_mix()
            self.update_status_line()
            self.render_section(preserve_cursor=True)

            if was_running is True and not self._was_running:
                self.show_completion_state()
        except NoMatches:
            # A timer can finish while Textual is tearing down the screen in
            # tests or during F5 reload. There is nothing left to refresh.
            return

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
        total = int(self.insights.get("total_titles") or metrics.get("titles") or self.status.get("db_titles") or 0)
        with_imdb = int(self.insights.get("with_imdb") or 0)
        with_art = int(self.insights.get("with_art") or 0)
        imdb_pct = (with_imdb / total * 100.0) if total else 0.0
        art_pct = (with_art / total * 100.0) if total else 0.0
        text = (
            f"{prefix}  |  FID {current:,} -> {next_fid:,}  |  "
            f"{total:,} titles  |  IMDb {imdb_pct:.1f}%  |  Art {art_pct:.1f}%  |  "
            f"{int(stats.get('errors') or 0)} errors"
        )
        if self._update_ready:
            text += "  |  [black on #e0b85c] UPDATE READY - F5 [/black on #e0b85c]"
        self.query_one("#status_line", Static).update(text)

    def update_health_graphs(self) -> None:
        total = int(self.insights.get("total_titles") or (self.status.get("metrics") or {}).get("titles") or self.status.get("db_titles") or 0)
        with_imdb = int(self.insights.get("with_imdb") or max(0, total - int(self.insights.get("missing_imdb") or 0)))
        with_art = int(self.insights.get("with_art") or max(0, total - int(self.insights.get("missing_art") or 0)))
        ready = int(self.insights.get("ready_titles") or 0)
        missing_imdb = max(0, total - with_imdb)
        missing_art = max(0, total - with_art)
        not_ready = max(0, total - ready)
        text = (
            f"[bold #63c7ff]LIBRARY HEALTH[/bold #63c7ff]   [#8aa7c1]{total:,} catalog titles[/#8aa7c1]\n"
            f"IMDb     {_meter(with_imdb, total, 20, '#63c7ff')}  [white]{with_imdb:,} matched[/white]  [#e0b85c]{missing_imdb:,} left[/#e0b85c]\n"
            f"Artwork  {_meter(with_art, total, 20, '#c7a7ff')}  [white]{with_art:,} ready[/white]  [#e0b85c]{missing_art:,} left[/#e0b85c]\n"
            f"Complete {_meter(ready, total, 20, '#55e39f')}  [white]{ready:,} clean[/white]  [#e0b85c]{not_ready:,} need work[/#e0b85c]"
        )
        self.query_one("#health_graphs", Static).update(text)

    def update_job_graph(self) -> None:
        stats = self.status.get("stats") or {}
        if not self.status.get("is_running"):
            last = self.status.get("last_sync") or {}
            task = str(self.status.get("task_type") or "")
            phase = str(stats.get("phase") or "")
            if task == "imdb" and phase == "complete":
                last_text = (
                    f"Last IMDb repair: {int(stats.get('matches_found') or 0):,} matched  ·  "
                    f"{int(stats.get('unmatched') or 0):,} unresolved  ·  {int(stats.get('errors') or 0):,} errors"
                )
            elif task == "posters" and phase == "complete":
                last_text = (
                    f"Last artwork repair: {int(stats.get('posters_fetched') or 0):,} posters filled  ·  "
                    f"{int(stats.get('errors') or 0):,} errors"
                )
            else:
                last_text = "No completed run recorded" if not last else (
                    f"Last run: {str(last.get('status') or 'complete').upper()}  ·  {_duration(last.get('elapsed_seconds'))}  ·  "
                    f"{int(last.get('new_titles_ingested') or 0)} new / {int(last.get('errors') or 0)} errors"
                )
            self.query_one("#job_graph", Static).update(
                f"[bold #63c7ff]ACTIVE JOB[/bold #63c7ff]  [white on #344a60] IDLE [/white on #344a60]\n[#8aa7c1]{last_text}[/#8aa7c1]"
            )
            return

        current = int(stats.get("phase_current") or 0)
        total = int(stats.get("phase_total") or 0)
        remaining = max(0, total - current)
        task = str(self.status.get("task_type") or "job").upper()
        phase = str(stats.get("phase") or "working").replace("_", " ").upper()
        if total:
            graph = _meter(current, total, 30, "#55e39f")
            progress = f"{current:,}/{total:,}  ·  {remaining:,} left"
        else:
            graph = "[#344a60]------------------------------[/#344a60]"
            progress = "Calculating work…"
        extras = []
        if task == "IMDB":
            extras.append(f"{int(stats.get('matches_found') or 0):,} matched")
            extras.append(f"{int(stats.get('unmatched') or 0):,} unresolved")
        elif task == "POSTERS":
            extras.append(f"{int(stats.get('posters_fetched') or 0):,} posters found")
        elif task == "SYNC":
            extras.append(f"{int(stats.get('new_titles') or 0):,} new")
            extras.append(f"{int(stats.get('revisions_updated') or 0):,} revised")
        extra_text = "  ·  " + " / ".join(extras) if extras else ""
        self.query_one("#job_graph", Static).update(
            f"[bold #63c7ff]ACTIVE JOB[/bold #63c7ff]  [black on #55e39f] {task} [/black on #55e39f]  [#8aa7c1]{phase}[/#8aa7c1]\n"
            f"{graph}  [white]{progress}[/white]{extra_text}"
        )

    def update_catalog_mix(self) -> None:
        mix = dict(self.insights.get("format_mix") or {})
        lines = ["[bold #63c7ff]CATALOG MIX[/bold #63c7ff]", ""]
        if not mix:
            lines.append("[#8aa7c1]Catalog breakdown unavailable.[/#8aa7c1]")
        else:
            maximum = max(mix.values()) if mix else 1
            styles = {"4K UHD": "#e0b85c", "Blu-ray": "#63c7ff", "DVD": "#55e39f", "Other": "#c7a7ff"}
            for name in ("4K UHD", "Blu-ray", "DVD", "Other"):
                if name not in mix:
                    continue
                value = int(mix[name])
                lines.append(f"{name:<8} {_mini_bar(value, maximum, 10, styles.get(name, '#63c7ff'))} [white]{value:>6,}[/white]")
        self.query_one("#catalog_mix", Static).update("\n".join(lines))

    def _best_next_items(self) -> List[str]:
        running = bool(self.status.get("is_running"))
        stats = self.status.get("stats") or {}
        last = self.status.get("last_sync") or {}
        missing_art = int(self.insights.get("missing_art") or 0)
        missing_imdb = int(self.insights.get("missing_imdb") or 0)
        missing_both = int(self.insights.get("missing_both") or 0)
        items: List[str] = []
        if running:
            items.append("[black on #55e39f] LIVE [/black on #55e39f] Keep the current population job visible until it completes.")
            if int(stats.get("errors") or 0):
                items.append(f"[white on #b94a57] FAILED [/white on #b94a57] Review {int(stats.get('errors') or 0)} current fetch errors after the run.")
        else:
            failures = int(last.get("errors") or stats.get("errors") or 0)
            if failures:
                items.append(f"[white on #b94a57] FAILED [/white on #b94a57] Retry {failures} failed FIDs from the last run.")
            if missing_imdb:
                note = f" ({missing_both:,} also need art)" if missing_both else ""
                items.append(f"[black on #e0b85c] NEEDS MATCH [/black on #e0b85c] Repair IMDb IDs for {missing_imdb:,} titles{note}.")
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
                "NEEDS BOTH": "bold white on #9c6545",
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
                    has_art = bool(row.get("poster_url") and row.get("poster_url") != "/static/images/missing_poster.svg")
                    has_imdb = bool(str(row.get("imdb_id") or "").strip())
                    if not has_art and not has_imdb:
                        state = "NEEDS BOTH"
                        next_step = "Repair match + art"
                    elif not has_imdb:
                        state = "NEEDS MATCH"
                        next_step = "IMDb Repair"
                    elif not has_art:
                        state = "NEEDS ART"
                        next_step = "Artwork Repair"
                    else:
                        state = "COMPLETE"
                        next_step = "Inspect"
                    self._add_row(table, f"movie-{fid}", [state, title_text, year, source, next_step], {"title": title_text, "state": state, "fid": fid, "body": f"FID {fid:,}  |  {source}  |  IMDb: {row.get('imdb_id') or 'not matched'}  |  Artwork: {'available' if has_art else 'not downloaded'}."})

        elif self.section == "population":
            title.update("CURRENT WORK AREA  //  Population")
            table = self._clear_table(["Status", "Task", "Scope", "Next Step"])
            busy = bool(self.status.get("is_running"))
            state = "LIVE" if busy else "READY"
            self._add_row(table, "population-update", [state, "Run Update", "Homepage revisions + resume catch-up", "Watch" if busy else "Enter / U"], {"title": "Run Update", "state": state, "action": "update", "body": "Normal maintenance run: check DVDCompare homepage revisions, then resume the persisted post-76,200 cursor."})
            self._add_row(table, "population-catchup", [state, "Catch Up Since 76,200", "Re-scan every post-initial FID", "Watch" if busy else "Enter / C"], {"title": "Catch Up Since 76,200", "state": state, "action": "catchup", "body": "Starts at FID 76,201 and skips titles already stored. Use this when you want a full post-initial verification pass."})
            self._add_row(table, "population-posters", [state, "Poster Backfill", f"{int(self.insights.get('missing_art') or 0):,} titles need art", "Watch" if busy else "Enter / P"], {"title": "Poster Backfill", "state": "NEEDS ART" if self.insights.get("missing_art") else state, "action": "posters", "body": "Fetch artwork for archive titles that do not have a usable poster yet."})
            self._add_row(table, "population-db", [state, "Database Maintenance", "Integrity + FTS optimize + VACUUM", "Watch" if busy else "Enter / M"], {"title": "Database Maintenance", "state": state, "action": "maintenance", "body": "Checks SQLite integrity, optimizes full-text search, then VACUUMs the archive database."})

        elif self.section == "imdb":
            missing = int(self.insights.get("missing_imdb") or 0)
            total = int(self.insights.get("total_titles") or 0)
            matched = int(self.insights.get("with_imdb") or max(0, total - missing))
            title.update(f"CURRENT WORK AREA  //  IMDb Repair  //  {missing:,} LEFT")
            table = self._clear_table(["Status", "Title", "Year", "Format", "Next Step"])
            busy = bool(self.status.get("is_running"))
            task_type = str(self.status.get("task_type") or "")
            bulk_state = "LIVE" if busy and task_type == "imdb" else ("COMPLETE" if missing == 0 else "READY")
            self._add_row(
                table,
                "imdb-run-all",
                [bulk_state, "AUTO-MATCH MISSING IMDb IDs", f"{matched:,}/{total:,}" if total else "—", f"{missing:,} remaining", "Watch" if busy else "Enter to run"],
                {
                    "title": "Automatic IMDb Repair",
                    "state": bulk_state,
                    "action": "imdb" if not busy and missing else None,
                    "body": "Uses TMDB title/year matching and only saves exact normalized title matches. Ambiguous records stay unresolved for manual review instead of being guessed.",
                },
            )
            rows = list(self.insights.get("missing_imdb_rows") or [])
            if not rows and missing:
                self._add_row(table, "imdb-loading", ["NEEDS MATCH", "Repair queue unavailable", "—", "—", "Press R"], {"title": "IMDb Repair", "state": "NEEDS MATCH", "body": "Refresh the archive data to reload the repair queue."})
            elif not rows and not missing:
                self._add_row(table, "imdb-clean", ["COMPLETE", "All titles matched", "—", "—", "Nothing to do"], {"title": "IMDb Repair", "state": "COMPLETE", "body": "Every non-missing archive title currently has an IMDb identifier."})
            else:
                for row in rows[:80]:
                    fid = int(row.get("fid") or 0)
                    title_text = str(row.get("clean_title") or "Unknown")
                    has_art = bool(row.get("poster_url") and row.get("poster_url") != "/static/images/missing_poster.svg")
                    state = "NEEDS MATCH" if has_art else "NEEDS BOTH"
                    self._add_row(
                        table,
                        f"imdb-{fid}",
                        [state, title_text, str(row.get("year") or "—"), str(row.get("format_category") or "—"), "Enter: match one"],
                        {"title": title_text, "state": state, "fid": fid, "action": f"imdb_one:{fid}", "body": f"FID {fid:,}. IMDb has not been matched yet. Artwork is {'already present' if has_art else 'also missing'}. Enter runs the conservative matcher for this title only."},
                    )

        elif self.section == "artwork":
            missing = int(self.insights.get("missing_art") or 0)
            total = int(self.insights.get("total_titles") or 0)
            with_art = int(self.insights.get("with_art") or max(0, total - missing))
            title.update(f"CURRENT WORK AREA  //  Artwork Repair  //  {missing:,} LEFT")
            table = self._clear_table(["Status", "Title", "Year", "IMDb", "Next Step"])
            busy = bool(self.status.get("is_running"))
            task_type = str(self.status.get("task_type") or "")
            bulk_state = "LIVE" if busy and task_type == "posters" else ("COMPLETE" if missing == 0 else "READY")
            self._add_row(
                table,
                "art-run-all",
                [bulk_state, "BACKFILL MISSING ARTWORK", f"{with_art:,}/{total:,}" if total else "—", f"{missing:,} remaining", "Watch" if busy else "Enter to run"],
                {"title": "Artwork Backfill", "state": bulk_state, "action": "posters" if not busy and missing else None, "body": "Runs the archive artwork worker across every title without a usable poster. IMDb-matched titles get the most precise lookup; title/year and Wikipedia are fallback paths."},
            )
            rows = list(self.insights.get("missing_art_rows") or [])
            if not rows and not missing:
                self._add_row(table, "art-clean", ["COMPLETE", "All titles have artwork", "—", "—", "Nothing to do"], {"title": "Artwork Repair", "state": "COMPLETE", "body": "Every non-missing archive title currently has usable artwork."})
            else:
                for row in rows[:80]:
                    fid = int(row.get("fid") or 0)
                    title_text = str(row.get("clean_title") or "Unknown")
                    imdb_id = str(row.get("imdb_id") or "")
                    state = "NEEDS ART" if imdb_id else "NEEDS BOTH"
                    self._add_row(
                        table,
                        f"art-{fid}",
                        [state, title_text, str(row.get("year") or "—"), imdb_id or "not matched", "Enter: fetch one"],
                        {"title": title_text, "state": state, "fid": fid, "action": f"poster_one:{fid}", "body": f"FID {fid:,}. Artwork is missing. IMDb: {imdb_id or 'not matched yet'}. Enter attempts an automatic poster fetch for this title only."},
                    )

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
            self._add_row(table, "maint-art", ["NEEDS ART" if art else "COMPLETE", "Artwork", f"{art:,} missing posters", "Open Artwork Repair" if art else "Healthy"], {"title": "Artwork", "state": "NEEDS ART" if art else "COMPLETE", "action": "open_artwork" if art else None, "body": f"{art:,} non-missing titles currently lack usable poster artwork. Enter opens the dedicated repair queue."})
            self._add_row(table, "maint-imdb", ["NEEDS MATCH" if imdb else "COMPLETE", "IMDb Links", f"{imdb:,} without IMDb ID", "Open IMDb Repair" if imdb else "Healthy"], {"title": "IMDb Links", "state": "NEEDS MATCH" if imdb else "COMPLETE", "action": "open_imdb" if imdb else None, "body": f"{imdb:,} titles have no IMDb identifier. Unknown is not treated as failed. Enter opens the dedicated matching queue."})
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
        elif name == "imdb":
            await self.start_worker("imdb", {}, "IMDb repair started")
        elif name.startswith("imdb_one:"):
            fid = int(name.split(":", 1)[1])
            await self.start_worker("imdb", {"fid": fid}, f"IMDb repair started for FID {fid:,}")
        elif name == "posters":
            await self.start_worker("posters", {}, "Poster backfill started")
        elif name.startswith("poster_one:"):
            fid = int(name.split(":", 1)[1])
            await self.fetch_one_poster(fid)
        elif name == "maintenance":
            await self.start_worker("vacuum", {}, "Database maintenance started")
        elif name == "open_imdb":
            self._switch_section("imdb")
        elif name == "open_artwork":
            self._switch_section("artwork")
        elif name == "retry":
            await self.action_retry_failures()

    async def fetch_one_poster(self, fid: int) -> None:
        if self.status.get("is_running"):
            self.notify("Wait for the active archive job to finish before repairing one poster.", severity="warning")
            return
        self.notify(f"Fetching artwork for FID {fid:,}…", timeout=2)
        try:
            result = await asyncio.to_thread(_post_poster, fid)
        except Exception as exc:
            self.notify(f"Artwork repair failed: {exc}", severity="error")
            return
        self._last_insights_refresh = 0.0
        await self.refresh_data()
        if result.get("success"):
            self.notify(f"Artwork repaired for FID {fid:,}", severity="information")
        else:
            self.notify(str(result.get("error") or "No artwork match found"), severity="warning")

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
        target_section = {"imdb": "imdb", "posters": "artwork"}.get(endpoint, "population")
        self.section = target_section
        section_list = self.query_one("#sections", ListView)
        section_list.index = [key for key, _ in SECTIONS].index(target_section)
        self.notify(message, severity="information")
        self._last_insights_refresh = 0.0
        await self.refresh_data()
        self.save_place()

    def show_completion_state(self) -> None:
        last = self.status.get("last_sync") or {}
        stats = self.status.get("stats") or {}
        task_type = str(self.status.get("task_type") or "sync")
        target_section = {"imdb": "imdb", "posters": "artwork"}.get(task_type, "population")
        self.section = target_section
        section_list = self.query_one("#sections", ListView)
        section_list.index = [key for key, _ in SECTIONS].index(target_section)
        self._last_insights_refresh = 0.0
        self.render_section()
        if task_type == "imdb":
            summary = (
                f"{int(stats.get('matches_found') or 0):,} IMDb matches  |  "
                f"{int(stats.get('unmatched') or 0):,} unresolved  |  "
                f"{int(stats.get('posters_fetched') or 0):,} artwork filled  |  "
                f"{int(stats.get('errors') or 0):,} errors"
            )
        elif task_type == "posters":
            summary = f"{int(stats.get('posters_fetched') or 0):,} posters fetched  |  {int(stats.get('errors') or 0):,} errors"
        else:
            summary = (
                f"{int(last.get('new_titles_ingested') or 0)} new titles  |  "
                f"{int(last.get('revisions_updated') or 0)} revised  |  "
                f"{int(last.get('posters_fetched') or 0)} posters  |  "
                f"{int(last.get('errors') or 0)} errors  |  {_duration(last.get('elapsed_seconds'))}"
            )
        self.query_one("#detail", Static).update(
            "[bold black on #55e39f] RUN COMPLETE [/bold black on #55e39f]\n"
            f"[bold white]{str(self.status.get('status_message') or 'Archive job finished.')}[/bold white]\n"
            f"{summary}"
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
        stats = self.status.get("stats") or {}
        if self.status.get("is_running"):
            target = {"imdb": "imdb", "posters": "artwork"}.get(str(self.status.get("task_type") or ""), "population")
        elif int((self.status.get("last_sync") or {}).get("errors") or stats.get("errors") or 0):
            target = "failures"
        elif int(self.insights.get("missing_imdb") or 0):
            target = "imdb"
        elif int(self.insights.get("missing_art") or 0):
            target = "artwork"
        else:
            target = "population"
        self._switch_section(target)
        detail = "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(items)) if items else "Nothing urgent right now."
        self.query_one("#detail", Static).update(
            f"[bold #63c7ff]BEST NEXT[/bold #63c7ff]\n{detail}\n[#8aa7c1]You are now in the section that can act on the top recommendation.[/#8aa7c1]"
        )

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

    def _switch_section(self, section: str) -> None:
        if section not in {key for key, _ in SECTIONS}:
            return
        self.section = section
        section_list = self.query_one("#sections", ListView)
        section_list.index = [key for key, _ in SECTIONS].index(section)
        self.render_section()
        self.save_place()

    def action_imdb_repair(self) -> None:
        self._switch_section("imdb")

    def action_artwork_repair(self) -> None:
        self._switch_section("artwork")

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
