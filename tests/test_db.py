import json
import sqlite3
import unittest
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.repository import ArchiveRepository
from src.parser.html_parser import DVDCompareParser
from src.config import FIXTURES_DIR, PROJECT_ROOT

class TestDatabaseAndFTS(unittest.TestCase):
    def setUp(self):
        # Create an in-memory SQLite database with foreign keys and WAL
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.row_factory = sqlite3.Row

        # Execute schema
        schema_file = PROJECT_ROOT / "src" / "db" / "schema.sql"
        with open(schema_file, "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())

        self.repo = ArchiveRepository(self.conn)
        self.parser = DVDCompareParser()

        # Seed sample fixtures (Blade Runner 4K fid 43651, Blade Runner DVD fid 111, Adam and Evil fid 45272)
        for fid in [43651, 111, 45272]:
            fixture = list(FIXTURES_DIR.glob(f"{fid}_*.html"))[0]
            with open(fixture, "rb") as f:
                content = f.read()
            parsed = self.parser.parse(content, fid=fid)
            self.repo.save_parsed_comparison(parsed, source_hash="dummy_hash", raw_html_path=str(fixture))

    def tearDown(self):
        self.repo.close()

    def test_fts_search_exact_and_prefix(self):
        """Test FTS5 search by title keywords."""
        results = self.repo.search_fts("Blade Runner")
        self.assertEqual(len(results), 2)
        fids = [r["fid"] for r in results]
        self.assertIn(43651, fids)
        self.assertIn(111, fids)

    def test_fts_search_aka(self):
        """Test FTS5 search matching AKA titles."""
        results = self.repo.search_fts("Halloween Camp")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["fid"], 45272)

    def test_fts_distributor_search(self):
        """Test FTS5 search matching distributor name."""
        results = self.repo.search_fts("Velocity Home Entertainment")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["fid"], 45272)

    def test_sibling_format_resolution(self):
        """Test that Blade Runner 4K resolves Blade Runner DVD as a sibling format."""
        detail = self.repo.get_title_detail(43651)
        self.assertIsNotNone(detail)
        siblings = detail["format_siblings"]
        self.assertTrue(len(siblings) >= 1)
        self.assertEqual(siblings[0]["fid"], 111)
        self.assertEqual(siblings[0]["format_category"], "DVD")

    def test_cascading_deletion(self):
        """Verify that deleting a title cleanly cascades to child releases and tracks."""
        row = self.conn.execute("SELECT id FROM titles WHERE fid = 45272").fetchone()
        title_id = row["id"]

        self.conn.execute("DELETE FROM titles WHERE id = ?", (title_id,))

        releases_count = self.conn.execute("SELECT COUNT(*) FROM releases WHERE title_id = ?", (title_id,)).fetchone()[0]
        self.assertEqual(releases_count, 0)

        # Verify FTS index entry cleaned up
        fts_count = self.conn.execute("SELECT COUNT(*) FROM titles_fts WHERE title_id = ?", (title_id,)).fetchone()[0]
        self.assertEqual(fts_count, 0)

if __name__ == "__main__":
    unittest.main()
