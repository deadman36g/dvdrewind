import json
import unittest
from pathlib import Path
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector
from src.config import FIXTURES_DIR

class TestDVDCompareParser(unittest.TestCase):
    def setUp(self):
        self.collector = WarningCollector()
        self.parser = DVDCompareParser(self.collector)

    def test_missing_film_fixtures(self):
        """Verify non-existent IDs are gracefully identified without raising errors."""
        for missing_fid in [10, 80000]:
            file_match = list(FIXTURES_DIR.glob(f"{missing_fid}_*.html"))
            self.assertTrue(len(file_match) > 0, f"Fixture for missing FID {missing_fid} not found")
            with open(file_match[0], "rb") as f:
                content = f.read()
            data = self.parser.parse(content, fid=missing_fid)
            self.assertTrue(data["is_missing"])
            self.assertEqual(data["raw_title"], "FILMID NOT FOUND")
            self.assertEqual(len(data["releases"]), 0)

    def test_blade_runner_4k_fid43651(self):
        """Verify complex 4K UHD release with 14 editions, HDR, and Draw conclusion."""
        fixture_file = FIXTURES_DIR / "43651_blade_runner_4k.html"
        with open(fixture_file, "rb") as f:
            content = f.read()
        data = self.parser.parse(content, fid=43651)

        self.assertFalse(data["is_missing"])
        self.assertEqual(data["clean_title"], "Blade Runner")
        self.assertEqual(data["year"], 1982)
        self.assertEqual(data["format_category"], "4K UHD")
        self.assertEqual(data["imdb_id"], "tt0083658")
        self.assertEqual(len(data["releases"]), 14)

        # Check Overall
        rec = data["recommendation"]
        self.assertIsNotNone(rec)
        self.assertEqual(rec["overall_winner"], "Draw")
        self.assertIn("US/French/UK Special Edition", rec["recommendation_text"])

        # Check First Release
        rel1 = data["releases"][0]
        self.assertEqual(rel1["media_format"], "Blu-ray")
        self.assertEqual(rel1["region"], "Blu-ray ALL")
        self.assertEqual(rel1["country"], "America")
        self.assertEqual(rel1["distributor"], "Warner Home Video")
        self.assertEqual(rel1["release_year"], 2017)
        self.assertEqual(rel1["edition_name"], "Special Edition")
        self.assertEqual(rel1["hdr_format"], "HDR10")
        self.assertTrue(len(rel1["audio_tracks"]) > 0)
        self.assertTrue(len(rel1["subtitle_tracks"]) > 0)

    def test_zombie_fid1(self):
        """Verify historic fid=1 DVD comparison with AKA titles."""
        fixture_file = FIXTURES_DIR / "1_zombie_dvd.html"
        with open(fixture_file, "rb") as f:
            content = f.read()
        data = self.parser.parse(content, fid=1)

        self.assertFalse(data["is_missing"])
        self.assertEqual(data["clean_title"], "Zombie")
        self.assertEqual(data["year"], 1979)
        self.assertEqual(data["format_category"], "DVD")
        self.assertEqual(data["imdb_id"], "tt0080057")
        self.assertIn("Zombie Flesh Eaters", data["aka_titles"])
        self.assertIn("Zombi 2", data["aka_titles"])
        self.assertEqual(len(data["releases"]), 14)

    def test_adam_and_evil_fid45272(self):
        """Verify few releases title with single winner and cuts."""
        fixture_file = FIXTURES_DIR / "45272_adam_and_evil_dvd.html"
        with open(fixture_file, "rb") as f:
            content = f.read()
        data = self.parser.parse(content, fid=45272)

        self.assertFalse(data["is_missing"])
        self.assertEqual(data["clean_title"], "Adam and Evil")
        self.assertEqual(data["year"], 2004)
        self.assertEqual(data["format_category"], "DVD")
        self.assertEqual(len(data["releases"]), 5)

        # Cuts
        self.assertTrue(len(data["cuts"]) > 0)
        cut1 = data["cuts"][0]
        self.assertEqual(cut1["cut_status"], "Uncut")

        # Winner
        self.assertEqual(data["recommendation"]["overall_winner"], "R1")

    def test_halloween_dvd_fid6726(self):
        """Verify cuts parsing and R0 winner."""
        fixture_file = FIXTURES_DIR / "6726_halloween_dvd.html"
        with open(fixture_file, "rb") as f:
            content = f.read()
        data = self.parser.parse(content, fid=6726)

        self.assertFalse(data["is_missing"])
        self.assertEqual(data["clean_title"], "Bloody Murder 2: Closing Camp")
        self.assertEqual(data["year"], 2003)
        self.assertIn("Halloween Camp", data["aka_titles"])
        self.assertEqual(len(data["releases"]), 3)
        self.assertEqual(data["recommendation"]["overall_winner"], "R0")

if __name__ == "__main__":
    unittest.main()
