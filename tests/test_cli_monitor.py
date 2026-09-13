import io
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console

from src.cli_monitor import build_archive_status_panel, build_command_center_panel


class TestEnhancedCLIMonitor(unittest.TestCase):
    def render(self, data, view="main"):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=140)
        console.print(build_archive_status_panel(data, view=view))
        return buf.getvalue()

    def sample_status(self):
        return {
            "is_running": True,
            "task_type": "sync",
            "status_message": "Catching up FIDs 76201 through 76800...",
            "current_action": "Scanning FID 76250",
            "elapsed_seconds": 100.0,
            "stats": {
                "phase": "catchup",
                "phase_current": 50,
                "phase_total": 600,
                "new_titles": 7,
                "revisions_updated": 3,
                "posters_fetched": 4,
                "errors": 1,
                "current_fid": 76250,
                "next_fid": 76251,
                "current_title": "The Thing",
                "current_format": "4K UHD",
                "current_year": 1982,
                "poster_found": True,
            },
            "recent_discoveries": [
                {"ts": "12:00:00", "fid": 76249, "title": "The Thing", "year": 1982, "format": "4K UHD", "releases": 5}
            ],
            "failed_fids": [{"fid": 76230, "phase": "catchup", "error": "timeout"}],
            "metrics": {"titles": 26560, "releases": 90000, "posters": 25000, "raw_html": 76000, "db_size_mb": 314.5},
            "growth": {"titles": 7, "releases": 24, "posters": 4, "raw_html": 50, "db_size_mb": 0.2},
            "last_sync": {"new_titles_ingested": 2, "revisions_updated": 1, "errors": 0, "elapsed_seconds": 80},
        }

    def test_main_dashboard_contains_blueprint_console_features(self):
        text = self.render(self.sample_status())
        self.assertIn("DVD REWIND", text)
        self.assertIn("ARCHIVE OPERATIONS", text)
        self.assertIn("76,201+ CATCH-UP", text)
        self.assertIn("SYSTEM STATUS", text)
        self.assertIn("THIS RUN", text)
        self.assertIn("NOW PROCESSING", text)
        self.assertIn("The Thing", text)
        self.assertIn("RECENT ACTIVITY", text)
        self.assertIn("ERRORS", text)
        self.assertIn("ETA", text)

    def test_error_drawer_lists_failed_fids(self):
        text = self.render(self.sample_status(), view="errors")
        self.assertIn("76230", text)
        self.assertIn("timeout", text)

    def test_new_only_view_lists_discoveries(self):
        text = self.render(self.sample_status(), view="new")
        self.assertIn("RECENT DISCOVERIES", text)
        self.assertIn("76,249", text)
        self.assertIn("The Thing", text)

    def test_command_center_exposes_unified_project_actions(self):
        data = self.sample_status()
        data["is_running"] = False
        data["post_initial"] = {"next_fid": 76251}
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=140)
        console.print(build_command_center_panel(data))
        text = buf.getvalue()
        self.assertIn("ARCHIVE CONTROL CONSOLE", text)
        self.assertIn("OPERATIONS", text)
        self.assertIn("SYSTEM STATUS", text)
        self.assertIn("Run Update", text)
        self.assertIn("Catch Up Since 76,200", text)
        self.assertIn("Watch Live Run", text)
        self.assertIn("Search Archive", text)
        self.assertIn("Errors / Retry", text)
        self.assertIn("Last Run Report", text)
        self.assertIn("Open Web Interface", text)
        self.assertIn("Posters", text)
        self.assertIn("Database maintenance", text)
        self.assertIn("76,251", text)

    def test_explicit_report_view_is_unambiguous_after_completion(self):
        data = self.sample_status()
        data["is_running"] = False
        data["last_sync"].update({
            "status": "success",
            "post_initial_scanned_fids": 600,
            "post_initial_next_fid": 76801,
            "posters_fetched": 4,
        })
        text = self.render(data, view="report")
        self.assertIn("RUN COMPLETE", text)
        self.assertIn("LAST RUN", text)
        self.assertIn("Catch-up FIDs scanned", text)
        self.assertIn("Next FID", text)
        idle = self.render(data)
        self.assertIn("● IDLE", idle)
        self.assertNotIn("RUN COMPLETE", idle)


if __name__ == "__main__":
    unittest.main()
