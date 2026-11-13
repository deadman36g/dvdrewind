import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.migrations import get_connection, init_db
from src.db.repository import ArchiveRepository
from src.web import sync_manager as sm

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
REAL_PAGE = (FIXTURES / "111_blade_runner_dvd.html").read_bytes()
NOT_FOUND_PAGE = b"<html><title>FILMID NOT FOUND</title></html>"


class FakeClient:
    """Pretends to be DVDCompare: 2 is a real title, 3 is down, the rest do not exist."""

    def __init__(self):
        self.requested = []

    def fetch_film(self, fid, save_to_raw=True):
        self.requested.append(fid)
        if fid == 3:
            raise RuntimeError("connection reset")
        body = REAL_PAGE if fid == 2 else NOT_FOUND_PAGE
        return 200, body, {"is_missing": body is NOT_FOUND_PAGE, "content_sha256": "x"}


class TestGapFill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.db_path = tmp / "test.db"
        init_db(self.db_path)
        self.raw_dir = tmp / "raw"
        self.raw_dir.mkdir()
        # FID 5 was downloaded during the original import but never saved to the DB.
        (self.raw_dir / "5.html").write_bytes(REAL_PAGE)
        self.state_file = tmp / "gap_fill_state.json"
        self.client = FakeClient()

        # FID 1 is already in the archive.
        repo = ArchiveRepository(get_connection(self.db_path))
        from src.parser.html_parser import DVDCompareParser
        from src.parser.warnings import WarningCollector
        repo.save_parsed_comparison(
            DVDCompareParser(WarningCollector()).parse(REAL_PAGE, fid=1), source_hash="x", raw_html_path=""
        )
        repo.close()

        self.patches = [
            patch.object(sm, "INITIAL_MAX_FID", 6),
            patch.object(sm, "RAW_DIR", self.raw_dir),
            patch.object(sm, "GAP_FILL_STATE_FILE", self.state_file),
            patch.object(sm, "FAILED_FIDS_FILE", tmp / "failed.json"),
            patch.object(sm, "ArchiveRepository", lambda: ArchiveRepository(get_connection(self.db_path))),
            patch.object(sm, "ScraperClient", lambda: self.client),
            patch.object(sm, "fetch_poster_from_tmdb", lambda *a, **k: None),
            patch.object(sm.time, "sleep", lambda s: None),
        ]
        for p in self.patches:
            p.start()
        self.manager = sm.ArchiveSyncManager()
        self.manager._init_state()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.manager._init_state()
        self.tmp.cleanup()

    def run_gap_fill(self):
        self.assertTrue(self.manager.start_gap_fill())
        self.manager.thread.join(timeout=30)
        self.assertFalse(self.manager.is_running)

    def fids_in_db(self):
        conn = get_connection(self.db_path)
        try:
            return sorted(r[0] for r in conn.execute("SELECT fid FROM titles WHERE is_missing = 0"))
        finally:
            conn.close()

    def test_recovers_titles_remembers_empty_ids_and_retries_failures(self):
        self.run_gap_fill()

        self.assertEqual(self.fids_in_db(), [1, 2, 5])
        # FID 1 already archived and FID 5 came from the saved raw page: no network for either.
        self.assertEqual(self.client.requested, [2, 3, 4, 6])
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["confirmed_empty"], [4, 6])
        stats = self.manager.stats
        self.assertEqual(stats["new_titles"], 2)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["phase_total"], 5)
        self.assertIn("1 IDs still unchecked", self.manager.last_result["message"])

        # A second run only retries the ID that failed.
        self.client.requested = []
        self.run_gap_fill()
        self.assertEqual(self.client.requested, [3])

    def test_status_endpoint_shape_includes_progress(self):
        self.run_gap_fill()
        status = self.manager.get_status()
        self.assertEqual(status["task_type"], "gapfill")
        self.assertIn("eta_seconds", status["stats"])
        self.assertEqual(status["stats"]["phase_current"], 5)


if __name__ == "__main__":
    unittest.main()
