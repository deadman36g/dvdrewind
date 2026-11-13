import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from src.web.app import create_app

class TestWebServer(AioHTTPTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from src.cli import cmd_import_fixtures
        class DummyArgs:
            pass
        try:
            cmd_import_fixtures(DummyArgs())
        except Exception:
            pass

    async def get_application(self):
        return create_app()

    @unittest_run_loop
    async def test_home_page(self):
        resp = await self.client.request("GET", "/")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("DVDRewind", text)
        self.assertIn("4K Ultra HD", text)

    @unittest_run_loop
    async def test_search_page(self):
        resp = await self.client.request("GET", "/search?q=Blade+Runner")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("Blade Runner", text)
        self.assertIn("4K UHD", text)

    @unittest_run_loop
    async def test_home_page_only_renders_initial_shelf_mix(self):
        resp = await self.client.request("GET", "/")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertLessEqual(text.count('class="shelf-section"'), 6)
        self.assertIn("/api/shelves?category=", text)

    @unittest_run_loop
    async def test_shelf_fragment_loads_on_demand(self):
        resp = await self.client.request("GET", "/api/shelves?category=director")
        self.assertEqual(resp.status, 200)
        self.assertIn("X-Shelf-Count", resp.headers)
        text = await resp.text()
        self.assertNotIn("<!DOCTYPE html>", text)

    @unittest_run_loop
    async def test_format_search_is_paginated(self):
        resp = await self.client.request("GET", "/search?format=DVD&page=1")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertLessEqual(text.count('class="comparison-card"'), 60)

    @unittest_run_loop
    async def test_static_assets_receive_cache_headers(self):
        resp = await self.client.request("GET", "/static/app.js?v=3.0")
        self.assertEqual(resp.status, 200)
        self.assertIn("max-age=", resp.headers.get("Cache-Control", ""))

    @unittest_run_loop
    async def test_api_search(self):
        resp = await self.client.request("GET", "/api/search?q=Blade")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(len(data) > 0)
        titles = [d["clean_title"] for d in data]
        self.assertIn("Blade Runner", titles)

    @unittest_run_loop
    async def test_movie_page(self):
        resp = await self.client.request("GET", "/film/43651")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("Blade Runner", text)
        self.assertIn("OVERALL VERDICT", text)
        self.assertIn("Draw", text)
        self.assertIn("Cuts &amp; Censorship Differences", text)
        self.assertIn("format-tabs-list", text)
        self.assertIn("imdb.com", text)

    @unittest_run_loop
    async def test_movie_page_hot_cache_hits_on_repeat(self):
        first = await self.client.request("GET", "/film/43651")
        self.assertEqual(first.status, 200)
        await first.text()
        second = await self.client.request("GET", "/film/43651")
        self.assertEqual(second.status, 200)
        self.assertEqual(second.headers.get("X-DVDRewind-Page-Cache"), "HIT")

    @unittest_run_loop
    async def test_movie_page_lazy_loads_release_specs(self):
        resp = await self.client.request("GET", "/film/43651")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("accordion-lazy-panel", text)
        self.assertIn("/api/film/43651/release/1", text)
        self.assertNotIn('<div class="accordion-grid">', text)

        detail = await self.client.request("GET", "/api/film/43651/release/1")
        self.assertEqual(detail.status, 200)
        detail_text = await detail.text()
        self.assertIn("Sound &amp; Subtitles", detail_text)
        self.assertIn("Special Features", detail_text)
        self.assertIn("Technical Notes &amp; Provenance", detail_text)

    @unittest_run_loop
    async def test_compare_page(self):
        resp = await self.client.request("GET", "/compare?fid=43651&indices=1,2")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("Edition Comparison: Blade Runner", text)
        self.assertIn("Comparing 2 selected releases", text)

    @unittest_run_loop
    async def test_archive_view(self):
        resp = await self.client.request("GET", "/archive/43651")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("LOCAL ARCHIVE SNAPSHOT", text)
        self.assertIn("/archive/raw/43651", text)

    @unittest_run_loop
    async def test_archive_raw_file(self):
        resp = await self.client.request("GET", "/archive/raw/43651")
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers["Content-Type"])

    @unittest_run_loop
    async def test_movie_not_found(self):
        resp = await self.client.request("GET", "/film/999999")
        self.assertEqual(resp.status, 404)

    @unittest_run_loop
    async def test_movie_page_has_fix_poster_button(self):
        resp = await self.client.request("GET", "/film/43651")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("btn-fix-poster", text)
        self.assertIn("poster-modal", text)

    @unittest_run_loop
    async def test_api_poster_search(self):
        resp = await self.client.request("GET", "/api/poster/search?query=Alien+1979")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("candidates", data)

    @unittest_run_loop
    async def test_movie_page_all_formats(self):
        # 1. Normal single format view
        resp = await self.client.request("GET", "/film/111")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("format-pill-all", text)
        self.assertIn("format-pill-dvd", text)
        self.assertIn("Physical Editions (32)", text)

        # 2. Unified All Formats view
        resp_all = await self.client.request("GET", "/film/111?format=all")
        self.assertEqual(resp_all.status, 200)
        text_all = await resp_all.text()
        self.assertIn("format-pill active format-pill-all", text_all)
        self.assertIn("Physical Editions (70)", text_all)
        self.assertIn("table-format-chip", text_all)

if __name__ == "__main__":
    unittest.main()
