from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from rich.text import Text
from textual import events
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
INITIAL_MAX_FID = 76200  # Historical baseline only; live frontier is persisted separately.

SECTIONS = [
    ("dashboard", "◆  Dashboard"),
    ("movies", "▦  Movies"),
    ("population", "↻  Population"),
    ("imdb", "◎  IMDb Repair"),
    ("artwork", "▧  Artwork Repair"),
    ("discoveries", "✦  Discoveries"),
    ("failures", "!  Failures"),
    ("maintenance", "⚙  Maintenance"),
]

BADGE = {
    "READY": "[bold #f1c477 on #2a2115] READY [/bold #f1c477 on #2a2115]",
    "LIVE": "[bold #71d49b on #13251d] LIVE [/bold #71d49b on #13251d]",
    "IDLE": "[#93a3b3 on #202a35] IDLE [/#93a3b3 on #202a35]",
    "COMPLETE": "[bold #71d49b on #13251d] COMPLETE [/bold #71d49b on #13251d]",
    "FAILED": "[bold #ef7f73 on #2a1717] FAILED [/bold #ef7f73 on #2a1717]",
    "NEEDS ART": "[bold #b89de8 on #211a2e] NEEDS ART [/bold #b89de8 on #211a2e]",
    "NEEDS MATCH": "[bold #f1c477 on #2a2115] NEEDS MATCH [/bold #f1c477 on #2a2115]",
    "NEEDS BOTH": "[bold #efaa73 on #2b1c17] NEEDS BOTH [/bold #efaa73 on #2b1c17]",
    "LOCAL MEDIA": "[#b89de8 on #211a2e] LOCAL MEDIA [/#b89de8 on #211a2e]",
    "EXTERNAL LINK": "[#67c7d9 on #13232a] EXTERNAL LINK [/#67c7d9 on #13232a]",
    "CURRENT": "[bold #67c7d9 on #13232a] CURRENT [/bold #67c7d9 on #13232a]",
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


def _meter(done: int, total: int, width: int = 22, fill_style: str = "#71d49b") -> str:
    total = max(0, int(total or 0))
    done = max(0, min(int(done or 0), total)) if total else 0
    pct = (done / total * 100.0) if total else 0.0
    filled = int(round(width * pct / 100.0))
    return f"[{fill_style}]{'━' * filled}[/{fill_style}][#2b3440]{'─' * (width - filled)}[/#2b3440]"


def _mini_bar(value: int, maximum: int, width: int = 12, style: str = "#67c7d9") -> str:
    maximum = max(1, int(maximum or 1))
    value = max(0, int(value or 0))
    filled = int(round(width * min(1.0, value / maximum)))
    return f"[{style}]{'▰' * filled}[/{style}][#2b3440]{'▱' * (width - filled)}[/#2b3440]"


class KeysScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "close", "Close", show=False),
        Binding("h", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    CSS = """
    KeysScreen { align: center middle; background: rgba(4, 6, 10, 0.88); }
    #keys_box {
        width: 78;
        height: 36;
        border: round #9a7338;
        background: #0f1620;
        padding: 1 3;
    }
    #keys_title { height: 2; color: #f1c477; text-style: bold; }
    #keys_body { height: 1fr; color: #c8d1da; }
    #keys_hint { height: 2; color: #66788a; text-align: center; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="keys_box"):
            yield Static("DVD REWIND  /  KEYBOARD", id="keys_title")
            yield Static(
                "[bold #d7b476]Browse[/]\n"
                "  Up/Down        Move through sections or rows\n"
                "  Left/Right     Move between navigation and work area\n"
                "  Enter          Open / run selected row action\n"
                "  Backspace      Go back to the previous area\n"
                "  F              Find movies\n"
                "  N              Show Best Next recommendation\n\n"
                "[bold #d7b476]Repair / population[/]\n"
                "  U              Update archive to latest\n"
                "  C              Deep-verify the historical tail\n"
                "  I              Open IMDb Repair\n"
                "  A              Open Artwork Repair\n"
                "  P              Run full poster backfill\n"
                "  M              Database maintenance\n"
                "  E              Retry saved failed FIDs\n\n"
                "[bold #d7b476]App controls[/]\n"
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


class DetailScreen(ModalScreen[None]):
    """Keyboard-first expanded inspector for rows that don't launch a task."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "close", "Close", show=False),
        Binding("enter", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    CSS = """
    DetailScreen { align: center middle; background: rgba(4, 6, 10, 0.88); }
    #detail_box {
        width: 88;
        height: 22;
        border: round #6c7b89;
        background: #0f1620;
        padding: 1 3;
    }
    #detail_modal_title { height: 3; color: #f1c477; text-style: bold; }
    #detail_modal_body { height: 1fr; color: #c8d1da; }
    #detail_modal_hint { height: 2; color: #66788a; text-align: center; }
    """

    def __init__(self, detail: Dict[str, Any]) -> None:
        super().__init__()
        self.detail = dict(detail or {})

    def compose(self) -> ComposeResult:
        state = str(self.detail.get("state") or "IDLE")
        badge = BADGE.get(state, f"[white on #344a60] {state} [/white on #344a60]")
        title = str(self.detail.get("title") or "Selected item")
        body = str(self.detail.get("body") or "No additional detail is available.")
        fid = self.detail.get("fid")
        meta = f"\n\n[#708397]FID[/#708397]  [#e8edf2]{int(fid):,}[/#e8edf2]" if fid else ""
        with Vertical(id="detail_box"):
            yield Static(f"{badge}   {title}", id="detail_modal_title", markup=True)
            yield Static(f"{body}{meta}", id="detail_modal_body", markup=True)
            yield Static("Enter / Backspace / Esc  Close", id="detail_modal_hint")

    def action_close(self) -> None:
        self.dismiss(None)


class DVDRewindTUI(App[None]):
    TITLE = "DVD Rewind"
    SUB_TITLE = "Archive Console"

    BINDINGS = [
        Binding("q", "quit_app", "Quit", show=False),
        Binding("backspace", "go_back", "Back", show=False),
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
        background: #090c12;
        color: #e8edf2;
    }
    #brand {
        height: 5;
        border-bottom: solid #9a7338;
        background: #0e1219;
        color: #f3f5f7;
        padding: 0 2;
    }
    #workspace {
        height: 1fr;
        padding: 1 1 0 1;
    }
    #left {
        width: 26;
        min-width: 24;
        border: round #263342;
        background: #0d131c;
        margin-right: 1;
    }
    #center {
        width: 1fr;
        min-width: 60;
        background: #090c12;
        margin-right: 1;
    }
    #right {
        width: 38;
        min-width: 34;
        background: #090c12;
    }
    .panel-title {
        height: 3;
        padding: 1 2 0 2;
        color: #d7b476;
        text-style: bold;
        background: #101722;
    }
    #sections {
        height: 1fr;
        padding: 1 1;
        background: #0d131c;
    }
    ListItem {
        height: 3;
        padding: 0 1;
        color: #9dacbc;
        background: #0d131c;
    }
    ListItem.--highlight {
        background: #211c15;
        color: #f5d79c;
        text-style: bold;
        border-left: thick #d5a85b;
    }
    #nav_hint {
        height: 7;
        margin: 0 1 1 1;
        padding: 1 1;
        border-top: solid #263342;
        color: #6f8295;
        background: #0d131c;
    }
    #work_title {
        height: 3;
        margin: 0 1;
        padding: 1 1 0 1;
        color: #f2f4f6;
        text-style: bold;
    }
    #health_graphs {
        height: 7;
        margin: 0 1 1 1;
    }
    .health-card {
        width: 1fr;
        height: 7;
        border: round #263342;
        background: #0f1620;
        padding: 0 1;
        margin-right: 1;
    }
    #health_complete { margin-right: 0; }
    #job_graph {
        height: 5;
        margin: 0 1 1 1;
        padding: 0 2;
        border: round #263342;
        background: #0f1620;
    }
    #search_input {
        height: 3;
        margin: 0 1 1 1;
        display: none;
        border: round #3c4a59;
        background: #101722;
        color: #f2f4f6;
    }
    #work_table {
        height: 1fr;
        margin: 0 1;
        border: round #263342;
        background: #0d131c;
    }
    DataTable > .datatable--header {
        background: #151e29;
        color: #d7b476;
        text-style: bold;
    }
    DataTable > .datatable--even-row { background: #0d131c; }
    DataTable > .datatable--odd-row { background: #101721; }
    DataTable > .datatable--cursor {
        background: #28384a;
        color: #ffffff;
        text-style: bold;
    }
    #detail {
        height: 8;
        min-height: 7;
        margin: 1 1 0 1;
        border: round #263342;
        background: #0f1620;
        padding: 0 2;
        color: #cdd6df;
    }
    .right-card {
        border: round #263342;
        background: #0f1620;
        padding: 1 2;
        margin-bottom: 1;
    }
    #best_next { height: 11; }
    #catalog_mix { height: 10; }
    #recent_activity { height: 1fr; }
    #warnings { height: 9; margin-bottom: 0; }
    #status_line {
        height: 2;
        border-top: solid #9a7338;
        background: #0e1219;
        padding: 0 2;
        color: #8293a6;
    }
    #footer_keys {
        height: 2;
        background: #090c12;
        color: #66788a;
        text-align: center;
        padding: 0 1;
    }

    /* Section identity: the same layout, with task-specific accent language. */
    .theme-imdb #work_title { color: #f1c477; }
    .theme-imdb #job_graph { border: round #7b5d26; background: #17130d; }
    .theme-imdb #work_table { border: round #5d4927; }
    .theme-imdb DataTable > .datatable--header { background: #2a2115; color: #f1c477; }
    .theme-imdb DataTable > .datatable--cursor { background: #4a3a1d; color: #fff3cf; }

    .theme-artwork #work_title { color: #c9afea; }
    .theme-artwork #job_graph { border: round #614d7c; background: #15111e; }
    .theme-artwork #work_table { border: round #4e3e65; }
    .theme-artwork DataTable > .datatable--header { background: #211a2e; color: #c9afea; }
    .theme-artwork DataTable > .datatable--cursor { background: #3e3150; color: #f3ebff; }

    .theme-population #work_title { color: #7bd0df; }
    .theme-population #job_graph { border: round #315b66; background: #0d171b; }
    .theme-population #work_table { border: round #31515c; }
    .theme-population DataTable > .datatable--header { background: #13232a; color: #7bd0df; }
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
        self._section_history: List[str] = []

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold #f1c477]DVD REWIND[/bold #f1c477]  [#73879a]PRIVATE ARCHIVE CONSOLE[/#73879a]\n"
            "[#56697c]Physical-media research • edition comparison • archive maintenance[/#56697c]",
            id="brand",
            markup=True,
        )
        with Horizontal(id="workspace"):
            with Vertical(id="left"):
                yield Static("BROWSE", classes="panel-title")
                yield ListView(
                    *[ListItem(Label(label), id=f"section-{key}") for key, label in SECTIONS],
                    id="sections",
                )
                yield Static(
                    "[#d7b476]NAVIGATION[/#d7b476]\n"
                    "[#8293a6]← / →[/#8293a6] move between panes\n"
                    "[#8293a6]↑ / ↓[/#8293a6] move selection   [#8293a6]↵[/#8293a6] open / run\n"
                    "[#8293a6]⌫[/#8293a6] back   [#8293a6]H[/#8293a6] all shortcuts",
                    id="nav_hint",
                    markup=True,
                )
            with Vertical(id="center"):
                yield Static("DASHBOARD", id="work_title", markup=True)
                with Horizontal(id="health_graphs"):
                    yield Static("IMDb MATCHES\nLoading…", id="health_imdb", classes="health-card", markup=True)
                    yield Static("ARTWORK\nLoading…", id="health_art", classes="health-card", markup=True)
                    yield Static("FULLY CLEAN\nLoading…", id="health_complete", classes="health-card", markup=True)
                yield Static("ACTIVE JOB\nNo active archive job.", id="job_graph", markup=True)
                yield Input(placeholder="Search titles, editions, distributors…", value=self.search_query, id="search_input")
                yield DataTable(id="work_table", cursor_type="row", zebra_stripes=True)
                yield Static("Select a row to see details.", id="detail", markup=True)
            with Vertical(id="right"):
                yield Static("BEST NEXT\nLoading…", id="best_next", classes="right-card", markup=True)
                yield Static("CATALOG MIX\nLoading…", id="catalog_mix", classes="right-card", markup=True)
                yield Static("RECENT ACTIVITY\nLoading…", id="recent_activity", classes="right-card", markup=True)
                yield Static("ATTENTION\nLoading…", id="warnings", classes="right-card", markup=True)
        yield Static("Connecting to archive…", id="status_line", markup=True)
        yield Static("←/→  Move     ↵  Open / Run     ⌫  Back     F  Find     H  Help     F5  Reload     Q  Quit", id="footer_keys", markup=True)

    async def on_mount(self) -> None:
        table = self.query_one("#work_table", DataTable)
        table.cursor_type = "row"
        section_list = self.query_one("#sections", ListView)
        section_list.index = [key for key, _ in SECTIONS].index(self.section)
        self.set_interval(1.5, self.refresh_data)
        self.set_interval(2.0, self.check_code_update)
        await self.refresh_data()
        self.render_section()
        section_list.focus()

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

    def _frontier_info(self) -> Dict[str, Any]:
        """Return the live persisted scan frontier, never the historical baseline as 'current'."""
        frontier = dict(self.status.get("frontier") or {})
        post = self.status.get("post_initial") or {}
        last = self.status.get("last_sync") or {}
        metrics = self.status.get("metrics") or {}
        running = bool(self.status.get("is_running")) and str(self.status.get("task_type") or "") == "sync"
        stats = self.status.get("stats") or {}

        try:
            persisted_next = int(frontier.get("next_fid") or post.get("next_fid") or INITIAL_MAX_FID + 1)
        except (TypeError, ValueError):
            persisted_next = INITIAL_MAX_FID + 1
        try:
            active_next = int(stats.get("next_fid") or 0)
        except (TypeError, ValueError):
            active_next = 0
        next_fid = active_next if running and active_next else persisted_next

        try:
            verified = int(frontier.get("verified_through_fid") or max(INITIAL_MAX_FID, persisted_next - 1))
        except (TypeError, ValueError):
            verified = max(INITIAL_MAX_FID, persisted_next - 1)
        try:
            highest_title = max(
                int(frontier.get("highest_title_fid") or 0),
                int(metrics.get("max_fid") or 0),
            )
        except (TypeError, ValueError):
            highest_title = 0
        try:
            highest_seen = max(
                int(frontier.get("highest_seen_fid") or 0),
                int(post.get("highest_seen_fid") or 0),
                int(last.get("highest_seen_fid") or 0),
            )
        except (TypeError, ValueError):
            highest_seen = 0
        return {
            "historical_baseline_fid": INITIAL_MAX_FID,
            "verified_through_fid": verified,
            "next_fid": next_fid,
            "highest_title_fid": highest_title,
            "highest_seen_fid": highest_seen,
            "checked_at": frontier.get("checked_at") or post.get("updated_at") or last.get("last_sync"),
        }

    def _apply_section_theme(self) -> None:
        center = self.query_one("#center", Vertical)
        for class_name in ("theme-imdb", "theme-artwork", "theme-population"):
            center.remove_class(class_name)
        if self.section == "imdb":
            center.add_class("theme-imdb")
        elif self.section == "artwork":
            center.add_class("theme-artwork")
        elif self.section == "population":
            center.add_class("theme-population")

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
        total = int(self.insights.get("total_titles") or (self.status.get("metrics") or {}).get("titles") or 0)
        marker = "[black on #71d49b] ● LIVE [/black on #71d49b]" if running else "[white on #263342] ● IDLE [/white on #263342]"
        update = "  [black on #f1c477] UPDATE READY · F5 [/black on #f1c477]" if self._update_ready else ""
        activity = str(self.status.get("status_message") or "Working") if running else "Archive worker standing by"
        self.query_one("#brand", Static).update(
            "[bold #f1c477]DVD REWIND[/bold #f1c477]  [#73879a]PRIVATE ARCHIVE CONSOLE[/#73879a]"
            f"  {marker}{update}\n"
            f"[#56697c]PHYSICAL MEDIA RESEARCH[/#56697c]  [#36495d]•[/#36495d]  "
            f"[#8293a6]{total:,} titles[/#8293a6]  [#36495d]•[/#36495d]  "
            f"[#8293a6]{phase}[/#8293a6]  [#36495d]•[/#36495d]  [#c8d1da]{activity}[/#c8d1da]"
        )

    def update_status_line(self) -> None:
        stats = self.status.get("stats") or {}
        metrics = self.status.get("metrics") or {}
        running = bool(self.status.get("is_running"))
        current = int(stats.get("current_fid") or 0)
        frontier = self._frontier_info()
        next_fid = int(frontier.get("next_fid") or INITIAL_MAX_FID + 1)
        verified = int(frontier.get("verified_through_fid") or INITIAL_MAX_FID)
        prefix = "[bold #71d49b]● RUNNING[/bold #71d49b]" if running else "[#708397]● IDLE[/#708397]"
        total = int(self.insights.get("total_titles") or metrics.get("titles") or self.status.get("db_titles") or 0)
        with_imdb = int(self.insights.get("with_imdb") or 0)
        with_art = int(self.insights.get("with_art") or 0)
        imdb_pct = (with_imdb / total * 100.0) if total else 0.0
        art_pct = (with_art / total * 100.0) if total else 0.0
        fid_text = f"FID {current:,} → {next_fid:,}" if running and current else f"Verified {verified:,} · next {next_fid:,}"
        text = (
            f"{prefix}  [#36495d]•[/#36495d]  [#9dacbc]{fid_text}[/#9dacbc]  [#36495d]•[/#36495d]  "
            f"[#9dacbc]{total:,} titles[/#9dacbc]  [#36495d]•[/#36495d]  "
            f"[#67c7d9]IMDb {imdb_pct:.1f}%[/#67c7d9]  [#36495d]•[/#36495d]  "
            f"[#b89de8]Art {art_pct:.1f}%[/#b89de8]  [#36495d]•[/#36495d]  "
            f"[#9dacbc]{int(stats.get('errors') or 0)} errors[/#9dacbc]"
        )
        if self._update_ready:
            text += "  [#36495d]•[/#36495d]  [bold #f1c477]F5 UPDATE READY[/bold #f1c477]"
        self.query_one("#status_line", Static).update(text)

    def update_health_graphs(self) -> None:
        total = int(self.insights.get("total_titles") or (self.status.get("metrics") or {}).get("titles") or self.status.get("db_titles") or 0)
        with_imdb = int(self.insights.get("with_imdb") or max(0, total - int(self.insights.get("missing_imdb") or 0)))
        with_art = int(self.insights.get("with_art") or max(0, total - int(self.insights.get("missing_art") or 0)))
        ready = int(self.insights.get("ready_titles") or 0)
        missing_imdb = max(0, total - with_imdb)
        missing_art = max(0, total - with_art)
        missing_both = int(self.insights.get("missing_both") or 0)

        def progress_card(title: str, done: int, color: str, done_label: str, left_label: str) -> str:
            left = max(0, total - done)
            pct = (done / total * 100.0) if total else 0.0
            return (
                f"[#708397]{title}[/#708397]\n"
                f"[bold {color}]{pct:5.1f}%[/bold {color}]\n"
                f"{_meter(done, total, 15, color)}\n"
                f"[#c8d1da]{done:,} {done_label}[/#c8d1da]\n"
                f"[{color}]{left:,} {left_label}[/{color}]"
            )

        def count_card(title: str, value: Any, color: str, line1: str, line2: str = "") -> str:
            display = f"{value:,}" if isinstance(value, int) else str(value or "—")
            return (
                f"[#708397]{title}[/#708397]\n"
                f"[bold {color}]{display}[/bold {color}]\n"
                f"[{color}]━━━━━━━━━━━━━━━[/{color}]\n"
                f"[#c8d1da]{line1}[/#c8d1da]\n"
                f"[#8293a6]{line2}[/#8293a6]"
            )

        if self.section == "imdb":
            self.query_one("#health_imdb", Static).update(
                progress_card("IMDb COVERAGE", with_imdb, "#f1c477", "matched", "unmatched")
            )
            self.query_one("#health_art", Static).update(
                count_card("UNMATCHED QUEUE", missing_imdb, "#f1c477", "titles waiting", "Enter first row to run")
            )
            self.query_one("#health_complete", Static).update(
                count_card("ALSO NEED ART", missing_both, "#efaa73", "need both repairs", "IMDb match comes first")
            )
            return

        if self.section == "artwork":
            matched_art_missing = max(0, missing_art - missing_both)
            self.query_one("#health_imdb", Static).update(
                progress_card("ART COVERAGE", with_art, "#b89de8", "ready", "missing")
            )
            self.query_one("#health_art", Static).update(
                count_card("MISSING ART", missing_art, "#b89de8", "titles waiting", "Enter first row to run")
            )
            self.query_one("#health_complete", Static).update(
                count_card("READY TO FETCH", matched_art_missing, "#67c7d9", "already IMDb-matched", f"{missing_both:,} also need IMDb")
            )
            return

        if self.section == "population":
            frontier = self._frontier_info()
            verified = int(frontier.get("verified_through_fid") or INITIAL_MAX_FID)
            next_fid = int(frontier.get("next_fid") or INITIAL_MAX_FID + 1)
            highest = int(frontier.get("highest_title_fid") or 0)
            checked = _fmt_ts(frontier.get("checked_at"))
            self.query_one("#health_imdb", Static).update(
                count_card("VERIFIED THROUGH", verified, "#67c7d9", "frontier FID", "not the old baseline")
            )
            self.query_one("#health_art", Static).update(
                count_card("NEXT SCAN", next_fid, "#67c7d9", "resume from here", f"highest title {highest:,}" if highest else "highest title unknown")
            )
            self.query_one("#health_complete", Static).update(
                count_card("LAST CHECK", checked, "#71d49b", "frontier freshness", "Update to Latest advances this")
            )
            return

        self.query_one("#health_imdb", Static).update(progress_card("IMDb MATCHES", with_imdb, "#f1c477", "matched", "left"))
        self.query_one("#health_art", Static).update(progress_card("ARTWORK", with_art, "#b89de8", "ready", "left"))
        self.query_one("#health_complete", Static).update(progress_card("FULLY CLEAN", ready, "#71d49b", "clean", "need work"))

    def update_job_graph(self) -> None:
        stats = self.status.get("stats") or {}
        if not self.status.get("is_running"):
            last = self.status.get("last_sync") or {}
            task = str(self.status.get("task_type") or "")
            phase = str(stats.get("phase") or "")
            if self.section == "imdb":
                missing = int(self.insights.get("missing_imdb") or 0)
                if task == "imdb" and phase == "complete":
                    detail = (
                        f"Last pass: {int(stats.get('matches_found') or 0):,} matched  ·  "
                        f"{int(stats.get('unmatched') or 0):,} unresolved  ·  {int(stats.get('errors') or 0):,} errors"
                    )
                else:
                    detail = f"{missing:,} titles are waiting for a confident IMDb match. Enter the first row to run the pass."
                self.query_one("#job_graph", Static).update(
                    "[#8e733d]IMDb MATCHING[/#8e733d]   [bold #f1c477 on #2a2115] ● READY [/bold #f1c477 on #2a2115]\n"
                    f"[#d9c8a2]{detail}[/#d9c8a2]"
                )
                return
            if self.section == "artwork":
                missing = int(self.insights.get("missing_art") or 0)
                if task == "posters" and phase == "complete":
                    detail = f"Last pass: {int(stats.get('posters_fetched') or 0):,} posters filled  ·  {int(stats.get('errors') or 0):,} errors"
                else:
                    detail = f"{missing:,} titles are waiting for artwork. Enter the first row to start or continue the backfill."
                self.query_one("#job_graph", Static).update(
                    "[#796795]ARTWORK REPAIR[/#796795]   [bold #c9afea on #211a2e] ● READY [/bold #c9afea on #211a2e]\n"
                    f"[#cfc1df]{detail}[/#cfc1df]"
                )
                return
            if self.section == "population":
                frontier = self._frontier_info()
                self.query_one("#job_graph", Static).update(
                    "[#527d86]ARCHIVE SYNC[/#527d86]   [bold #7bd0df on #13232a] ● READY [/bold #7bd0df on #13232a]\n"
                    f"[#a9c7cd]Verified through FID {int(frontier.get('verified_through_fid') or INITIAL_MAX_FID):,}  ·  "
                    f"next scan {int(frontier.get('next_fid') or INITIAL_MAX_FID + 1):,}  ·  last checked {_fmt_ts(frontier.get('checked_at'))}[/#a9c7cd]"
                )
                return

            last_label = "Last archive run"
            last_text = "No completed run recorded" if not last else (
                f"{str(last.get('status') or 'complete').upper()}  ·  {_duration(last.get('elapsed_seconds'))}  ·  "
                f"{int(last.get('new_titles_ingested') or 0)} new  ·  {int(last.get('errors') or 0)} errors"
            )
            self.query_one("#job_graph", Static).update(
                "[#708397]ACTIVE JOB[/#708397]   [white on #263342] ● IDLE [/white on #263342]\n"
                f"[bold #d7b476]{last_label}[/bold #d7b476]  [#8293a6]{last_text}[/#8293a6]"
            )
            return

        current = int(stats.get("phase_current") or 0)
        total = int(stats.get("phase_total") or 0)
        remaining = max(0, total - current)
        task = str(self.status.get("task_type") or "job").upper()
        phase = str(stats.get("phase") or "working").replace("_", " ").upper()
        pct = (current / total * 100.0) if total else 0.0
        task_color = {"IMDB": "#f1c477", "POSTERS": "#b89de8", "SYNC": "#67c7d9"}.get(task, "#71d49b")
        task_bg = {"IMDB": "#2a2115", "POSTERS": "#211a2e", "SYNC": "#13232a"}.get(task, "#13251d")
        task_label = {"IMDB": "IMDb MATCHING", "POSTERS": "ARTWORK REPAIR", "SYNC": "ARCHIVE SYNC"}.get(task, task)
        if total:
            graph = _meter(current, total, 32, task_color)
            progress = f"{pct:5.1f}%  ·  {current:,}/{total:,}  ·  {remaining:,} left"
        else:
            graph = "[#2b3440]────────────────────────────────[/#2b3440]"
            progress = "Calculating work…"
        extras = []
        if task == "IMDB":
            extras.extend([f"{int(stats.get('matches_found') or 0):,} matched", f"{int(stats.get('unmatched') or 0):,} unresolved"])
        elif task == "POSTERS":
            extras.append(f"{int(stats.get('posters_fetched') or 0):,} posters found")
        elif task == "SYNC":
            extras.extend([f"{int(stats.get('new_titles') or 0):,} new", f"{int(stats.get('revisions_updated') or 0):,} revised"])
        extra_text = "  [#36495d]•[/#36495d]  " + "  ·  ".join(extras) if extras else ""
        self.query_one("#job_graph", Static).update(
            f"[#708397]ACTIVE JOB[/#708397]   [bold {task_color} on {task_bg}] ● {task_label} [/bold {task_color} on {task_bg}]   [#8293a6]{phase}[/#8293a6]\n"
            f"{graph}  [bold #e8edf2]{progress}[/bold #e8edf2]{extra_text}"
        )

    def update_catalog_mix(self) -> None:
        mix = dict(self.insights.get("format_mix") or {})
        lines = ["[#708397]CATALOG MIX[/#708397]", ""]
        if not mix:
            lines.append("[#8293a6]Catalog breakdown unavailable.[/#8293a6]")
        else:
            maximum = max(mix.values()) if mix else 1
            styles = {"4K UHD": "#f1c477", "Blu-ray": "#67c7d9", "DVD": "#71d49b", "Other": "#b89de8"}
            for name in ("4K UHD", "Blu-ray", "DVD", "Other"):
                if name not in mix:
                    continue
                value = int(mix[name])
                lines.append(
                    f"[#b6c1cc]{name:<8}[/#b6c1cc] {_mini_bar(value, maximum, 9, styles.get(name, '#67c7d9'))} "
                    f"[bold #e8edf2]{value:>6,}[/bold #e8edf2]"
                )
        self.query_one("#catalog_mix", Static).update("\n".join(lines))

    def _best_next_items(self) -> List[str]:
        running = bool(self.status.get("is_running"))
        stats = self.status.get("stats") or {}
        last = self.status.get("last_sync") or {}
        frontier = self._frontier_info()
        verified = int(frontier.get("verified_through_fid") or INITIAL_MAX_FID)
        next_fid = int(frontier.get("next_fid") or INITIAL_MAX_FID + 1)
        missing_art = int(self.insights.get("missing_art") or 0)
        missing_imdb = int(self.insights.get("missing_imdb") or 0)
        missing_both = int(self.insights.get("missing_both") or 0)
        items: List[str] = []
        if running:
            items.append("[bold #71d49b]● LIVE[/bold #71d49b]  Keep the current archive job visible until it completes.")
            if int(stats.get("errors") or 0):
                items.append(f"[bold #ef7f73]● ERRORS[/bold #ef7f73]  Review {int(stats.get('errors') or 0)} fetch errors after the run.")
        else:
            failures = int(last.get("errors") or stats.get("errors") or 0)
            if failures:
                items.append(f"[bold #ef7f73]● RETRY[/bold #ef7f73]  {failures} failed FIDs are waiting from the last run.")
            if missing_imdb:
                note = f" · {missing_both:,} also need art" if missing_both else ""
                items.append(f"[bold #f1c477]● IMDb[/bold #f1c477]  Repair {missing_imdb:,} unmatched titles{note}.")
            if missing_art:
                items.append(f"[bold #b89de8]● ARTWORK[/bold #b89de8]  Backfill posters for {missing_art:,} titles.")
            items.append(f"[#67c7d9]● UPDATE[/#67c7d9]  Verified through FID {verified:,}; update to latest resumes at {next_fid:,}.")
        return items[:3]

    def update_intelligence(self) -> None:
        best = self._best_next_items()
        self.query_one("#best_next", Static).update(
            "[#708397]BEST NEXT[/#708397]\n\n" + "\n\n".join(best or ["[#8293a6]Nothing urgent right now.[/#8293a6]"])
        )

        logs = list(self.status.get("log_lines") or [])[-5:]
        activity = ["[#708397]RECENT ACTIVITY[/#708397]", ""]
        if logs:
            for item in reversed(logs):
                ts = str(item.get("ts") or "--:--")
                msg = str(item.get("msg") or "")
                prefix = "[bold #ef7f73]![/bold #ef7f73]" if "error" in msg.lower() else "[#71d49b]•[/#71d49b]"
                activity.append(f"{prefix} [#66788a]{ts}[/#66788a] [#b6c1cc]{msg[:54]}[/#b6c1cc]")
        else:
            activity.append("[#8293a6]Quiet right now. No worker activity.[/#8293a6]")
        self.query_one("#recent_activity", Static).update("\n".join(activity))

        warnings = ["[#708397]ATTENTION[/#708397]", ""]
        missing_imdb = int(self.insights.get("missing_imdb") or 0)
        missing_art = int(self.insights.get("missing_art") or 0)
        frontier = self._frontier_info()
        verified = int(frontier.get("verified_through_fid") or INITIAL_MAX_FID)
        next_fid = int(frontier.get("next_fid") or INITIAL_MAX_FID + 1)
        current_errors = int((self.status.get("stats") or {}).get("errors") or 0)
        if current_errors:
            warnings.append(f"[bold #ef7f73]● {current_errors:,} current errors[/bold #ef7f73]")
        if missing_imdb:
            warnings.append(f"[#f1c477]●[/#f1c477] [#c8d1da]{missing_imdb:,} titles need IMDb matches[/#c8d1da]")
        if missing_art:
            warnings.append(f"[#b89de8]●[/#b89de8] [#c8d1da]{missing_art:,} titles need artwork[/#c8d1da]")
        warnings.append(f"[#67c7d9]◆[/#67c7d9] [#c8d1da]Frontier {verified:,} → next {next_fid:,}[/#c8d1da]")
        if not current_errors and not missing_art and not missing_imdb:
            warnings.append("[#71d49b]● Library has no repair warnings.[/#71d49b]")
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
                "READY": "bold #f1c477 on #2a2115",
                "LIVE": "bold #71d49b on #13251d",
                "IDLE": "#93a3b3 on #202a35",
                "COMPLETE": "bold #71d49b on #13251d",
                "FAILED": "bold #ef7f73 on #2a1717",
                "NEEDS ART": "bold #b89de8 on #211a2e",
                "NEEDS MATCH": "bold #f1c477 on #2a2115",
                "NEEDS BOTH": "bold #efaa73 on #2b1c17",
                "CURRENT": "bold #67c7d9 on #13232a",
            }
            if state in state_styles:
                values = [Text(f" {state} ", style=state_styles[state]), *values[1:]]
        table.add_row(*values, key=key)
        self.row_details[key] = detail

    def render_section(self, preserve_cursor: bool = False) -> None:
        if not self.is_mounted:
            return
        self._apply_section_theme()
        self.update_health_graphs()
        self.update_job_graph()
        table = self.query_one("#work_table", DataTable)
        if preserve_cursor:
            previous = table.cursor_row
        elif not self._restored_cursor:
            previous = self._saved_cursor_row
        else:
            previous = 0
        title = self.query_one("#work_title", Static)

        if self.section == "dashboard":
            title.update("[#708397]OVERVIEW[/#708397]   [bold #e8edf2]Dashboard[/bold #e8edf2]")
            table = self._clear_table(["State", "Work Item", "Value", "Next Step"])
            running = bool(self.status.get("is_running"))
            stats = self.status.get("stats") or {}
            last = self.status.get("last_sync") or {}
            frontier = self._frontier_info()
            verified = int(frontier.get("verified_through_fid") or INITIAL_MAX_FID)
            next_fid = int(frontier.get("next_fid") or INITIAL_MAX_FID + 1)
            checked = _fmt_ts(frontier.get("checked_at"))
            self._add_row(table, "worker", ["LIVE" if running else "IDLE", "Archive Worker", str(self.status.get("status_message") or "Ready"), "Watch" if running else "Update to Latest"], {"title": "Archive Worker", "state": "LIVE" if running else "IDLE", "action": "open_population", "body": "The NAS worker handles archive sync, IMDb matching, artwork repair, and maintenance. Enter opens Population & Sync; leaving the TUI never stops an active job."})
            self._add_row(table, "cursor", ["CURRENT", "Archive Frontier", f"Verified through FID {verified:,}", f"Next {next_fid:,}"], {"title": "Live Archive Frontier", "state": "CURRENT", "action": "open_population", "body": f"The original 76,200 value was only the historical baseline. The persisted frontier is now verified through FID {verified:,}; the next normal update resumes at {next_fid:,}. Last frontier check: {checked}."})
            self._add_row(table, "last", ["COMPLETE" if last else "IDLE", "Last Update", _fmt_ts(last.get("last_sync")) if last else "No completed run", f"{int(last.get('new_titles_ingested') or 0)} new / {int(last.get('errors') or 0)} errors"], {"title": "Last Archive Update", "state": "COMPLETE" if last else "IDLE", "action": "open_population", "body": f"Checked {int(last.get('homepage_checked') or 0):,} homepage-linked comparisons. Duration {_duration(last.get('elapsed_seconds'))}. {int(last.get('new_titles_ingested') or 0)} new titles, {int(last.get('revisions_updated') or 0)} revisions, {int(last.get('posters_fetched') or 0)} posters, {int(last.get('errors') or 0)} errors."})
            self._add_row(table, "best", ["READY", "Best Next", self._best_next_items()[0] if self._best_next_items() else "Nothing urgent", "Enter / N"], {"title": "Best Next", "state": "READY", "action": "best_next", "body": "Enter or press N to jump to the highest-priority area and see what to do next."})

        elif self.section == "movies":
            suffix = f"   [#56697c]/[/#56697c]   [#d7b476]{self.search_query}[/#d7b476]" if self.search_query else ""
            title.update(f"[#708397]LIBRARY[/#708397]   [bold #e8edf2]Movies[/bold #e8edf2]{suffix}")
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
                        next_step = "IMDb Repair first"
                        action = "open_imdb"
                    elif not has_imdb:
                        state = "NEEDS MATCH"
                        next_step = "Open IMDb Repair"
                        action = "open_imdb"
                    elif not has_art:
                        state = "NEEDS ART"
                        next_step = "Open Artwork Repair"
                        action = "open_artwork"
                    else:
                        state = "COMPLETE"
                        next_step = "Inspect"
                        action = None
                    self._add_row(table, f"movie-{fid}", [state, title_text, year, source, next_step], {"title": title_text, "state": state, "fid": fid, "action": action, "body": f"FID {fid:,}  |  {source}  |  IMDb: {row.get('imdb_id') or 'not matched'}  |  Artwork: {'available' if has_art else 'not downloaded'}."})

        elif self.section == "population":
            frontier = self._frontier_info()
            verified = int(frontier.get("verified_through_fid") or INITIAL_MAX_FID)
            next_fid = int(frontier.get("next_fid") or INITIAL_MAX_FID + 1)
            highest = int(frontier.get("highest_title_fid") or 0)
            checked = _fmt_ts(frontier.get("checked_at"))
            title.update(f"[#527d86]ARCHIVE[/#527d86]   [bold #7bd0df]Population & Sync[/bold #7bd0df]   [#36495d]•[/#36495d]   [#7bd0df]next {next_fid:,}[/#7bd0df]")
            table = self._clear_table(["Status", "Task", "Scope", "Next Step"])
            busy = bool(self.status.get("is_running"))
            state = "LIVE" if busy else "READY"
            self._add_row(table, "population-update", [state, "UPDATE TO LATEST", f"Homepage + resume from FID {next_fid:,}", "Watch" if busy else "Enter / U"], {"title": "Update to Latest", "state": state, "action": "update", "body": f"This is the normal keep-current action. It checks DVDCompare's homepage for new/revised comparisons, then resumes the persisted frontier from FID {next_fid:,} and probes beyond the newest known title. Last frontier check: {checked}."})
            self._add_row(table, "population-frontier", ["CURRENT", "Verified Frontier", f"Through FID {verified:,}", f"Next {next_fid:,}"], {"title": "Verified Archive Frontier", "state": "CURRENT", "body": f"The archive has already checked through FID {verified:,}. The next normal update starts at {next_fid:,}. Highest actual title FID seen: {highest:,}. The old 76,200 number is kept only as historical provenance, not as the current frontier."})
            self._add_row(table, "population-catchup", [state, "Deep Verify Historical Tail", f"Original baseline → FID {verified:,}", "Watch" if busy else "Enter / C"], {"title": "Deep Historical-Tail Verification", "state": state, "action": "catchup", "body": "Recovery/verification pass: re-checks the entire tail beginning immediately after the original catalog baseline and skips records already stored. Use normal Update to Latest for day-to-day freshness."})
            self._add_row(table, "population-last", ["COMPLETE" if frontier.get("checked_at") else "IDLE", "Last Freshness Check", checked, f"Highest title {highest:,}" if highest else "No high-water title"], {"title": "Freshness Check", "state": "COMPLETE" if frontier.get("checked_at") else "IDLE", "action": "update" if not busy else None, "body": f"Last persisted frontier check: {checked}. Enter runs Update to Latest again. Verified through FID {verified:,}; next scan {next_fid:,}."})

        elif self.section == "imdb":
            missing = int(self.insights.get("missing_imdb") or 0)
            total = int(self.insights.get("total_titles") or 0)
            matched = int(self.insights.get("with_imdb") or max(0, total - missing))
            title.update(f"[#8e733d]IMDb REPAIR[/#8e733d]   [bold #f1c477]Matching & Metadata[/bold #f1c477]   [#5e4a27]•[/#5e4a27]   [bold #f1c477]{missing:,} left[/bold #f1c477]")
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
            title.update(f"[#796795]ARTWORK REPAIR[/#796795]   [bold #c9afea]Posters & Covers[/bold #c9afea]   [#4e3e65]•[/#4e3e65]   [bold #c9afea]{missing:,} left[/bold #c9afea]")
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
            title.update("[#708397]LIBRARY[/#708397]   [bold #e8edf2]Recent Discoveries[/bold #e8edf2]")
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
            title.update("[#708397]ATTENTION[/#708397]   [bold #e8edf2]Failures & Retries[/bold #e8edf2]")
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
            title.update("[#708397]SYSTEM[/#708397]   [bold #e8edf2]Maintenance[/bold #e8edf2]")
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
            self.query_one("#detail", Static).update("[#8293a6]No details available.[/#8293a6]")
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
        accent = {"imdb": "#f1c477", "artwork": "#c9afea", "population": "#7bd0df"}.get(self.section, "#d7b476")
        fid = detail.get("fid")
        meta = f"  [#56697c]•[/#56697c]  [#8293a6]FID {int(fid):,}[/#8293a6]" if fid else ""
        if detail.get("action"):
            action_hint = "\n[#8293a6]↵ Enter[/#8293a6] [#c8d1da]open / run[/#c8d1da]   [#8293a6]⌫ Backspace[/#8293a6] [#c8d1da]go back[/#c8d1da]"
        else:
            action_hint = "\n[#8293a6]↵ Enter[/#8293a6] [#c8d1da]more details[/#c8d1da]   [#8293a6]⌫ Backspace[/#8293a6] [#c8d1da]go back[/#c8d1da]"
        self.query_one("#detail", Static).update(
            f"[bold {accent}]SELECTED[/bold {accent}]  {badge}{meta}\n"
            f"[bold #f2f4f6]{title}[/bold #f2f4f6]\n"
            f"[#c8d1da]{body}[/#c8d1da]{action_hint}"
        )

    def on_key(self, event: events.Key) -> None:
        """Make the main layout behave like a two-pane keyboard application."""
        focused = self.focused
        if event.key == "left" and isinstance(focused, DataTable):
            self.query_one("#sections", ListView).focus()
            event.prevent_default()
            event.stop()
            return
        if event.key == "right" and isinstance(focused, ListView):
            section_list = self.query_one("#sections", ListView)
            index = max(0, int(section_list.index or 0))
            if index < len(SECTIONS):
                self._switch_section(SECTIONS[index][0])
                self.query_one("#work_table", DataTable).focus()
            event.prevent_default()
            event.stop()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if item_id.startswith("section-"):
            self._switch_section(item_id.replace("section-", "", 1))
            self.query_one("#work_table", DataTable).focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.update_detail_for_cursor()
        self.save_place()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        detail = self.row_details.get(key) or {}
        action = detail.get("action")
        if action:
            await self.run_named_action(str(action))
            return
        # Enter should never feel dead. Non-action rows open a larger inspector.
        self.push_screen(DetailScreen(detail))

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
            await self.start_worker("sync", {"since_initial": False}, "Update to Latest started")
        elif name == "catchup":
            await self.start_worker("sync", {"since_initial": True}, "Deep historical-tail verification started")
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
        elif name == "open_population":
            self._switch_section("population")
        elif name == "best_next":
            self.action_best_next()
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

    def _switch_section(self, section: str, remember: bool = True) -> None:
        if section not in {key for key, _ in SECTIONS}:
            return
        if section != self.section and remember:
            if not self._section_history or self._section_history[-1] != self.section:
                self._section_history.append(self.section)
                self._section_history = self._section_history[-20:]
        self.section = section
        section_list = self.query_one("#sections", ListView)
        section_list.index = [key for key, _ in SECTIONS].index(section)
        self.render_section()
        self.save_place()

    def action_go_back(self) -> None:
        search = self.query_one("#search_input", Input)
        if search.styles.display != "none":
            search.styles.display = "none"
            self.query_one("#work_table", DataTable).focus()
            return
        if self._section_history:
            previous = self._section_history.pop()
            self._switch_section(previous, remember=False)
            self.query_one("#work_table", DataTable).focus()
            return
        if self.section != "dashboard":
            self._switch_section("dashboard", remember=False)
            self.query_one("#work_table", DataTable).focus()
            return
        # At the root, Backspace simply returns focus to the navigation rail.
        self.query_one("#sections", ListView).focus()

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
