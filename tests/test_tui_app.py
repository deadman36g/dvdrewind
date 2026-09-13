import asyncio
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import DataTable, Input, Static

from src.tui_app import DVDRewindTUI, KeysScreen


SAMPLE_STATUS = {
    "is_running": False,
    "task_type": "sync",
    "status_message": "Ready",
    "stats": {"errors": 0, "next_fid": 76305, "phase": "idle"},
    "metrics": {"titles": 26709, "releases": 70080, "db_size_mb": 315.8},
    "post_initial": {"next_fid": 76305},
    "last_sync": {
        "status": "success",
        "elapsed_seconds": 100,
        "new_titles_ingested": 11,
        "revisions_updated": 2,
        "errors": 4,
        "posters_fetched": 9,
        "last_sync": "2026-09-13T21:00:00",
    },
    "log_lines": [],
}

INSIGHTS = {
    "missing_art": 10,
    "missing_imdb": 20,
    "missing_records": 30,
    "newest": [],
}


class TestDVDRewindTextualTUI(unittest.IsolatedAsyncioTestCase):
    async def test_main_layout_is_three_column_and_quiet_footer(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(140, 50)) as pilot:
                await pilot.pause()
                self.assertIsNotNone(app.query_one("#left"))
                self.assertIsNotNone(app.query_one("#center"))
                self.assertIsNotNone(app.query_one("#right"))
                self.assertEqual(app.query_one("#work_table", DataTable).row_count, 4)
                footer = str(app.query_one("#footer_keys", Static).content)
                self.assertIn("N Best Next", footer)
                self.assertIn("F Find", footer)
                self.assertIn("H Keys", footer)
                self.assertIn("F5 Reload", footer)
                self.assertIn("Q Quit", footer)
                self.assertNotIn("R Refresh", footer)

    async def test_help_and_find_are_progressively_disclosed(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(140, 50)) as pilot:
                await pilot.pause()
                search = app.query_one("#search_input", Input)
                self.assertEqual(search.styles.display, "none")
                await pilot.press("h")
                await pilot.pause()
                self.assertIsInstance(app.screen, KeysScreen)
                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("f")
                await pilot.pause()
                self.assertEqual(search.styles.display, "block")

    async def test_running_job_drives_semantic_state_and_best_next(self):
        live = dict(SAMPLE_STATUS)
        live["is_running"] = True
        live["status_message"] = "Catching up FIDs 76,201 through 76,304..."
        live["stats"] = {
            "errors": 3,
            "next_fid": 76288,
            "current_fid": 76287,
            "phase": "catchup",
            "phase_current": 87,
            "phase_total": 104,
        }
        with patch("src.tui_app._fetch_status", return_value=live), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(140, 50)) as pilot:
                await pilot.pause()
                brand = str(app.query_one("#brand", Static).content)
                best = str(app.query_one("#best_next", Static).content)
                self.assertIn("LIVE", brand)
                self.assertIn("CATCHUP", brand.replace(" ", ""))
                self.assertIn("BEST NEXT", best)
                self.assertIn("current population job", best)

    async def test_f5_returns_reload_code_for_wrapper_restart(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(140, 50)) as pilot:
                await pilot.pause()
                await pilot.press("f5")
                await pilot.pause()
            self.assertEqual(app.return_code, 82)


if __name__ == "__main__":
    unittest.main()
