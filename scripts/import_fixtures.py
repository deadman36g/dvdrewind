import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import FIXTURES_DIR
from src.db.migrations import init_db
from src.db.repository import ArchiveRepository
from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector

def main():
    print("Initializing SQLite database...")
    init_db()
    repo = ArchiveRepository()
    collector = WarningCollector()
    parser = DVDCompareParser(collector)

    manifest_path = FIXTURES_DIR / "manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    print(f"Importing {len(manifest)} fixtures into SQLite database...")
    imported_count = 0
    missing_count = 0

    for item in manifest:
        fixture_file = FIXTURES_DIR.parent.parent / item["fixture_path"]
        fid = item["fid"]
        sha256 = item["sha256"]

        with open(fixture_file, "rb") as fp:
            content_bytes = fp.read()

        parsed = parser.parse(content_bytes, fid=fid)
        title_id = repo.save_parsed_comparison(
            parsed,
            source_hash=sha256,
            raw_html_path=str(fixture_file)
        )

        if parsed["is_missing"]:
            missing_count += 1
            print(f"  [-] FID {fid:5d}: Marked as MISSING")
        else:
            imported_count += 1
            rec = parsed["recommendation"]
            winner = rec["overall_winner"] if rec and rec["overall_winner"] else "N/A"
            print(f"  [+] FID {fid:5d}: '{parsed['clean_title']}' ({parsed['format_category']}, {parsed['year']}) -> {len(parsed['releases'])} releases, Winner: {winner}")

    print(f"\n[+] Import complete! Imported: {imported_count}, Missing: {missing_count}")

    # Test FTS5 Searches
    test_queries = ["Blade Runner", "Halloween", "Zombie", "Adam and Evil"]
    print("\n" + "=" * 60)
    print("VERIFYING LOCAL SQLite FTS5 FULL-TEXT SEARCH")
    print("=" * 60)

    for q in test_queries:
        results = repo.search_fts(q)
        print(f"\nSearch Query: '{q}' -> Found {len(results)} matches:")
        for r in results:
            print(f"  - FID {r['fid']}: {r['clean_title']} ({r['year']}) [{r['format_category']}] - {r['release_count']} releases | Winner: {r['overall_winner']}")

    # Test movie page detail retrieval and sibling formats
    print("\n" + "=" * 60)
    print("TESTING FORMAT SIBLING GROUPING (Blade Runner 4K fid=43651)")
    print("=" * 60)
    detail = repo.get_title_detail(43651)
    if detail:
        print(f"Title: {detail['clean_title']} ({detail['year']}) [Current Format: {detail['format_category']}]")
        print(f"IMDb: {detail['imdb_id']}")
        print("Available Media Formats / Sibling Comparisons:")
        for s in detail["format_siblings"]:
            print(f"  * {s['format_category']}: FID {s['fid']} -> {s['clean_title']} ({s['year']})")

    repo.close()

if __name__ == "__main__":
    main()
