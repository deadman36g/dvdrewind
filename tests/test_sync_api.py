import unittest
from pathlib import Path
import sys
from unittest.mock import patch

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
        self.assertIn("post_initial", data)
        self.assertIn("metrics", data)
        self.assertIn("growth", data)
        self.assertIn("recent_discoveries", data)
        self.assertIn("failed_fids", data)
        self.assertIn("phase", data["stats"])
        self.assertIn("phase_current", data["stats"])
        self.assertIn("phase_total", data["stats"])
        self.assertIn("current_title", data["stats"])
        self.assertIn("posters_fetched", data["stats"])
        self.assertIsInstance(data["log_lines"], list)
        self.assertIsInstance(data["recent_discoveries"], list)
        self.assertIsInstance(data["failed_fids"], list)
        self.assertIsInstance(data["db_titles"], int)

    @unittest_run_loop
    async def test_api_archive_sync_can_force_post_initial_catchup(self):
        with patch.object(ArchiveSyncManager, "start_sync", return_value=True) as start_sync:
            resp = await self.client.request(
                "POST",
                "/api/archive/sync",
                json={"since_initial": True, "limit": 25},
            )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("76,200", data["message"])
        start_sync.assert_called_once_with(limit=25, force_from_initial=True)

    @unittest_run_loop
    async def test_archive_control_center_exposes_post_initial_catchup(self):
        resp = await self.client.request("GET", "/")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn('id="btn-catchup-since-initial"', text)
        self.assertIn("Catch Up Since 76,200", text)

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
