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
        # Stub the TMDB/Wikipedia lookups: a still-pending first visit is not
        # cached on purpose, and network speed must not decide this test.
        from unittest.mock import patch
        with patch("src.web.app._enrichment_for", return_value=({}, {})):
            first = await self.client.request("GET", "/film/43651")
            self.assertEqual(first.status, 200)
            await first.text()
            second = await self.client.request("GET", "/film/43651")
        self.assertEqual(second.status, 200)
        self.assertEqual(second.headers.get("X-DVDRewind-Page-Cache"), "HIT")

    @unittest_run_loop
    async def test_pending_film_page_fills_itself_in(self):
        from unittest.mock import patch
        with patch("src.web.app._enrichment_for", return_value=None):
            resp = await self.client.request("GET", "/film/45272")
            text = await resp.text()
        self.assertEqual(resp.headers.get("X-DVDRewind-Page-Cache"), "PENDING")
        self.assertIn("fillIn", text)
        self.assertIn('data-enrich="overview"', text)

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
        # The saved DVDCompare page must never run its scripts on this origin.
        self.assertEqual(resp.headers["Content-Security-Policy"], "sandbox allow-same-origin")

    @unittest_run_loop
    async def test_unchanged_html_page_revalidates_with_304(self):
        first = await self.client.request("GET", "/archive/43651")
        etag = first.headers["ETag"]
        await first.text()
        again = await self.client.request("GET", "/archive/43651", headers={"If-None-Match": etag})
        self.assertEqual(again.status, 304)
        self.assertEqual(again.headers["ETag"], etag)

    @unittest_run_loop
    async def test_short_word_search_matches_title_starts(self):
        resp = await self.client.request("GET", "/api/search?q=bla")
        data = await resp.json()
        self.assertIn("Blade Runner", [d["clean_title"] for d in data])
        again = await self.client.request("GET", "/api/search?q=bla")
        self.assertEqual(await again.json(), data)

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

class TestCompactHtml(unittest.TestCase):
    def test_repeated_svgs_become_one_sprite_and_scripts_are_untouched(self):
        from src.web.app import compact_html
        icon = '<svg width="20" fill="red"><path d="' + "M1 1L2 2" * 60 + '"/></svg>'
        script = "<script>\n    el.innerHTML = '" + icon + "';\n</script>"
        page = "<html><body>\n    <p>A</p>\n    " + icon + "\n\n    " + icon + "\n" + script + "</body></html>"
        out = compact_html(page)
        self.assertEqual(out.count('<g id="rw-svg-'), 1)
        self.assertEqual(out.count('<use href="#rw-svg-'), 2)
        self.assertIn('<svg width="20" fill="red"><use', out)
        self.assertIn(script, out)
        self.assertNotIn("\n    <p>", out)

    def test_image_filters_map_local_files_only(self):
        from src.web.app import backdrop_webp, poster_thumb
        self.assertEqual(poster_thumb("/static/posters/tt1.jpg"), "/thumbs/tt1.jpg.webp")
        self.assertEqual(poster_thumb("/static/posters/tt1.jpg", 440), "/thumbs/w440/tt1.jpg.webp")
        self.assertEqual(backdrop_webp("/static/backdrops/tt1.jpg"), "/thumbs/backdrops/tt1.jpg.webp")
        self.assertEqual(poster_thumb("https://image.tmdb.org/x.jpg", 440), "https://image.tmdb.org/x.jpg")
        self.assertIsNone(backdrop_webp(None))

    def test_secrets_are_redacted(self):
        from src.config import redact_secrets
        msg = redact_secrets("Max retries exceeded with url: /3/find/tt1?api_key=abc123&external_source=imdb_id")
        self.assertNotIn("abc123", msg)
        self.assertIn("api_key=***&external_source", msg)


if __name__ == "__main__":
    unittest.main()
