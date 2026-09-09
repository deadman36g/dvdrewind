import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from src.web.app import create_app
from src.web.sync_manager import ArchiveSyncManager


class TestSyncAPI(AioHTTPTestCase):
    async def get_application(self):
        return create_app()

    @unittest_run_loop
    async def test_api_archive_status(self):
        resp = await self.client.request("GET", "/api/archive/status")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("is_running", data)
        self.assertIn("task_type", data)
        self.assertIn("db_titles", data)
        self.assertIn("db_size_mb", data)
        self.assertIn("log_lines", data)
        self.assertIsInstance(data["log_lines"], list)
        self.assertIsInstance(data["db_titles"], int)

    @unittest_run_loop
    async def test_api_archive_cancel(self):
        resp = await self.client.request("POST", "/api/archive/cancel")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("ok", data)
        self.assertIn("message", data)

    @unittest_run_loop
    async def test_api_archive_vacuum_trigger(self):
        # Trigger vacuum (or verify it can be invoked)
        resp = await self.client.request("POST", "/api/archive/vacuum")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("ok", data)
        # Cancel task to clean up immediately
        manager = ArchiveSyncManager()
        manager.cancel()


if __name__ == "__main__":
    unittest.main()
