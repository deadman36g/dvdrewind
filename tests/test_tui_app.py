import asyncio
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import DataTable, Input, ListView, Static

from src.tui_app import DVDRewindTUI, DetailScreen, KeysScreen, STATE_FILE


SAMPLE_STATUS = {
    "is_running": False,
    "task_type": "sync",
    "status_message": "Ready",
    "stats": {"errors": 0, "next_fid": 76201, "phase": "idle"},
    "metrics": {"titles": 26709, "releases": 70080, "max_fid": 76204, "db_size_mb": 315.8},
    "post_initial": {"next_fid": 76305, "highest_seen_fid": 76204, "updated_at": "2026-09-13T21:26:08"},
    "frontier": {
        "historical_baseline_fid": 76200,
        "verified_through_fid": 76304,
        "next_fid": 76305,
        "highest_title_fid": 76204,
        "checked_at": "2026-09-13T21:26:08",
    },
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
    "total_titles": 26709,
    "with_art": 26699,
    "with_imdb": 26689,
    "ready_titles": 26685,
    "missing_art": 10,
    "missing_imdb": 20,
    "missing_both": 4,
    "missing_records": 30,
    "format_mix": {"4K UHD": 5000, "Blu-ray": 12000, "DVD": 9500, "Other": 209},
    "missing_imdb_rows": [
        {"fid": 76001, "clean_title": "Needs Match", "year": 1985, "format_category": "Blu-ray", "poster_url": "/static/posters/example.jpg", "imdb_id": None},
    ],
    "missing_art_rows": [
        {"fid": 76002, "clean_title": "Needs Art", "year": 1990, "format_category": "4K UHD", "poster_url": None, "imdb_id": "tt1234567"},
    ],
    "newest": [],
}


class TestDVDRewindTextualTUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            STATE_FILE.unlink()
        except FileNotFoundError:
            pass

    async def asyncTearDown(self):
        try:
            STATE_FILE.unlink()
        except FileNotFoundError:
            pass

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
                health = "\n".join(
                    str(app.query_one(selector, Static).content)
                    for selector in ("#health_imdb", "#health_art", "#health_complete")
                )
                mix = str(app.query_one("#catalog_mix", Static).content)
                self.assertIn("IMDb MATCHES", health)
                self.assertIn("ARTWORK", health)
                self.assertIn("FULLY CLEAN", health)
                self.assertIn("20 left", health)
                self.assertIn("CATALOG MIX", mix)
                self.assertIn("Blu-ray", mix)
                footer = str(app.query_one("#footer_keys", Static).content)
                self.assertIn("Move", footer)
                self.assertIn("Open / Run", footer)
                self.assertIn("Back", footer)
                self.assertIn("Find", footer)
                self.assertIn("Help", footer)
                self.assertIn("Reload", footer)
                self.assertIn("Quit", footer)
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
                self.assertIn("current archive job", best)

    async def test_repair_sections_expose_real_queues_and_actions(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.pause()
                await pilot.press("i")
                await pilot.pause()
                self.assertEqual(app.section, "imdb")
                title = str(app.query_one("#work_title", Static).content)
                self.assertIn("IMDb REPAIR", title)
                self.assertIn("Matching & Metadata", title)
                self.assertGreaterEqual(app.query_one("#work_table", DataTable).row_count, 2)
                imdb_graph = str(app.query_one("#job_graph", Static).content)
                self.assertIn("IMDb MATCHING", imdb_graph)
                await pilot.press("a")
                await pilot.pause()
                self.assertEqual(app.section, "artwork")
                title = str(app.query_one("#work_title", Static).content)
                self.assertIn("ARTWORK REPAIR", title)
                self.assertIn("Posters & Covers", title)
                self.assertGreaterEqual(app.query_one("#work_table", DataTable).row_count, 2)
                art_graph = str(app.query_one("#job_graph", Static).content)
                self.assertIn("ARTWORK REPAIR", art_graph)

    async def test_active_job_graph_shows_progress_and_remaining(self):
        live = dict(SAMPLE_STATUS)
        live["is_running"] = True
        live["task_type"] = "imdb"
        live["stats"] = {
            "errors": 0,
            "phase": "imdb",
            "phase_current": 25,
            "phase_total": 100,
            "current_fid": 76000,
            "next_fid": 76305,
            "matches_found": 20,
            "unmatched": 5,
        }
        with patch("src.tui_app._fetch_status", return_value=live), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.pause()
                graph = str(app.query_one("#job_graph", Static).content)
                self.assertIn("ACTIVE JOB", graph)
                self.assertIn("25/100", graph)
                self.assertIn("75 left", graph)
                self.assertIn("20 matched", graph)

    async def test_population_uses_live_frontier_not_idle_baseline(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.pause()
                app._switch_section("population")
                await pilot.pause()
                title = str(app.query_one("#work_title", Static).content)
                status = str(app.query_one("#status_line", Static).content)
                cards = "\n".join(
                    str(app.query_one(selector, Static).content)
                    for selector in ("#health_imdb", "#health_art", "#health_complete")
                )
                self.assertIn("76,305", title)
                self.assertIn("Verified 76,304", status)
                self.assertIn("76,304", cards)
                self.assertIn("76,305", cards)
                self.assertNotIn("Verified 76,200", status)

    async def test_sidebar_keeps_active_section_visually_marked(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.pause()
                dashboard_item = app.query_one("#section-dashboard")
                self.assertTrue(dashboard_item.has_class("active-section"))
                await pilot.press("i")
                await pilot.pause()
                self.assertTrue(app.query_one("#section-imdb").has_class("active-section"))
                self.assertFalse(dashboard_item.has_class("active-section"))
                browse_title = str(app.query_one("#browse_title", Static).content)
                self.assertIn("IMDb Repair", browse_title)
                app.query_one("#work_table", DataTable).focus()
                await pilot.pause()
                self.assertTrue(app.query_one("#section-imdb").has_class("active-section"))

    async def test_last_repair_result_stays_visible_in_detail_panel(self):
        status = dict(SAMPLE_STATUS)
        status["task_type"] = "imdb"
        status["stats"] = {"errors": 0, "phase": "complete", "matches_found": 1, "unmatched": 0}
        status["last_result"] = {
            "task": "imdb",
            "status": "matched",
            "fid": 76001,
            "title": "Needs Match",
            "imdb_id": "tt1234567",
            "message": "Matched Needs Match to tt1234567",
        }
        with patch("src.tui_app._fetch_status", return_value=status), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.pause()
                app._switch_section("imdb")
                await pilot.pause()
                detail = str(app.query_one("#detail", Static).content)
                self.assertIn("RESULT", detail)
                self.assertIn("tt1234567", detail)

    async def test_backspace_and_arrow_navigation_are_keyboard_friendly(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.pause()
                self.assertIsInstance(app.focused, ListView)
                await pilot.press("right")
                await pilot.pause()
                self.assertIsInstance(app.focused, DataTable)
                await pilot.press("left")
                await pilot.pause()
                self.assertIsInstance(app.focused, ListView)
                await pilot.press("i")
                await pilot.pause()
                self.assertEqual(app.section, "imdb")
                await pilot.press("backspace")
                await pilot.pause()
                self.assertEqual(app.section, "dashboard")

    async def test_enter_on_informational_row_opens_detail_screen(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.pause()
                app._switch_section("maintenance")
                table = app.query_one("#work_table", DataTable)
                table.move_cursor(row=2, animate=False)  # Known Missing FIDs: informational only.
                table.focus()
                await pilot.press("enter")
                await pilot.pause()
                self.assertIsInstance(app.screen, DetailScreen)
                await pilot.press("backspace")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, DetailScreen)

    async def test_backspace_edits_search_text_instead_of_leaving_search(self):
        with patch("src.tui_app._fetch_status", return_value=SAMPLE_STATUS), patch(
            "src.tui_app._library_insights", return_value=INSIGHTS
        ):
            app = DVDRewindTUI()
            async with app.run_test(size=(150, 54)) as pilot:
                await pilot.pause()
                await pilot.press("f")
                await pilot.press("a", "b", "c")
                search = app.query_one("#search_input", Input)
                self.assertTrue(str(search.value).endswith("abc"))
                await pilot.press("backspace")
                await pilot.pause()
                self.assertTrue(str(search.value).endswith("ab"))
                self.assertEqual(search.styles.display, "block")

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
