import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.db.repository import ArchiveRepository
from src.scraper.posters import find_imdb_match


class TestRepairHelpers(unittest.TestCase):
    def test_update_imdb_id_propagates_to_same_title_year_siblings(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE titles (fid INTEGER PRIMARY KEY, clean_title TEXT, year INTEGER, imdb_id TEXT, is_missing INTEGER DEFAULT 0)"
        )
        conn.executemany(
            "INSERT INTO titles (fid, clean_title, year, imdb_id, is_missing) VALUES (?, ?, ?, ?, 0)",
            [
                (10, "The Thing", 1982, None),
                (11, "The Thing", 1982, ""),
                (12, "The Thing", 2011, None),
            ],
        )
        repo = ArchiveRepository(conn)
        affected = repo.update_imdb_id(10, "tt0084787")
        self.assertEqual(affected, 2)
        rows = conn.execute("SELECT fid, imdb_id FROM titles ORDER BY fid").fetchall()
        self.assertEqual(rows, [(10, "tt0084787"), (11, "tt0084787"), (12, None)])
        repo.close()

    def test_find_imdb_match_requires_exact_title_and_year(self):
        search_response = Mock()
        search_response.status_code = 200
        search_response.json.return_value = {
            "results": [
                {
                    "id": 1091,
                    "title": "The Thing",
                    "original_title": "The Thing",
                    "release_date": "1982-06-25",
                    "poster_path": "/thing.jpg",
                }
            ]
        }
        details_response = Mock()
        details_response.status_code = 200
        details_response.json.return_value = {
            "id": 1091,
            "title": "The Thing",
            "imdb_id": "tt0084787",
            "poster_path": "/thing.jpg",
        }
        with patch("src.scraper.posters.requests.get", side_effect=[search_response, details_response]):
            match = find_imdb_match("Thing (The)", 1982, api_key="test")
        self.assertIsNotNone(match)
        self.assertEqual(match["imdb_id"], "tt0084787")
        self.assertEqual(match["confidence"], "exact-title-year")

    def test_find_imdb_match_leaves_ambiguous_year_unmatched(self):
        search_response = Mock()
        search_response.status_code = 200
        search_response.json.return_value = {
            "results": [
                {
                    "id": 1091,
                    "title": "The Thing",
                    "original_title": "The Thing",
                    "release_date": "2011-10-14",
                }
            ]
        }
        with patch("src.scraper.posters.requests.get", return_value=search_response) as get:
            match = find_imdb_match("The Thing", 1982, api_key="test")
        self.assertIsNone(match)
        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
