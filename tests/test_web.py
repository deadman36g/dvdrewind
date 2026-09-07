import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from src.web.app import create_app

class TestWebServer(AioHTTPTestCase):
    async def get_application(self):
        return create_app()

    @unittest_run_loop
    async def test_home_page(self):
        resp = await self.client.request("GET", "/")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("DVDRewind", text)
        self.assertIn("Which physical release should I own?", text)

    @unittest_run_loop
    async def test_search_page(self):
        resp = await self.client.request("GET", "/search?q=Blade+Runner")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn("Blade Runner", text)
        self.assertIn("4K UHD", text)

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
        self.assertIn("Available Formats:", text)
        self.assertIn("imdb.com", text)

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

if __name__ == "__main__":
    unittest.main()
