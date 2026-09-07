import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.repository import ArchiveRepository
from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector
from src.scraper.client import ScraperClient
from src.config import RAW_DIR

HAMMER_DRACULA_FIDS = [
    # 1. Dracula / Horror of Dracula (1958)
    {"fid": 5378, "title": "Dracula / Horror of Dracula (DVD)"},
    {"fid": 22655, "title": "Dracula / Horror of Dracula (Blu-ray)"},

    # 2. The Brides of Dracula (1960)
    {"fid": 7987, "title": "The Brides of Dracula (DVD)"},
    {"fid": 26740, "title": "The Brides of Dracula (Blu-ray)"},

    # 3. Dracula: Prince of Darkness (1966)
    {"fid": 1436, "title": "Dracula: Prince of Darkness (DVD)"},
    {"fid": 20167, "title": "Dracula: Prince of Darkness (Blu-ray)"},

    # 4. Dracula Has Risen from the Grave (1968)
    {"fid": 5218, "title": "Dracula Has Risen from the Grave (DVD)"},
    {"fid": 34000, "title": "Dracula Has Risen from the Grave (Blu-ray)"},

    # 5. Taste the Blood of Dracula (1969)
    {"fid": 5183, "title": "Taste the Blood of Dracula (DVD)"},
    {"fid": 34002, "title": "Taste the Blood of Dracula (Blu-ray)"},

    # 6. Scars of Dracula (1970)
    {"fid": 2896, "title": "Scars of Dracula (DVD)"},
    {"fid": 44702, "title": "Scars of Dracula (Blu-ray)"},
    {"fid": 73645, "title": "Scars of Dracula (4K UHD)"},

    # 7. Dracula A.D. 1972 (1972)
    {"fid": 8234, "title": "Dracula A.D. 1972 (DVD)"},
    {"fid": 45298, "title": "Dracula A.D. 1972 (Blu-ray)"},

    # 8. The Satanic Rites of Dracula (1973)
    {"fid": 4943, "title": "The Satanic Rites of Dracula (DVD)"},
    {"fid": 44810, "title": "The Satanic Rites of Dracula (Blu-ray)"},

    # 9. The Legend of the 7 Golden Vampires (1974)
    {"fid": 4952, "title": "The Legend of the 7 Golden Vampires (DVD)"},
    {"fid": 50073, "title": "The Legend of the 7 Golden Vampires (Blu-ray)"},
]

def main():
    print(f"Starting archival of all {len(HAMMER_DRACULA_FIDS)} Hammer Dracula comparisons...")
    client = ScraperClient()
    repo = ArchiveRepository()
    collector = WarningCollector()
    parser = DVDCompareParser(collector)

    for i, item in enumerate(HAMMER_DRACULA_FIDS, start=1):
        fid = item["fid"]
        desc = item["title"]
        raw_html = RAW_DIR / f"{fid}.html"

        if raw_html.exists():
            print(f"[{i}/{len(HAMMER_DRACULA_FIDS)}] FID {fid} ({desc}) already cached in archive/raw.")
            with open(raw_html, "rb") as f:
                content = f.read()
            import hashlib
            source_hash = hashlib.sha256(content).hexdigest()
        else:
            print(f"[{i}/{len(HAMMER_DRACULA_FIDS)}] Fetching FID {fid} ({desc}) politely...")
            status, content, meta = client.fetch_film(fid, save_to_raw=True)
            source_hash = meta["content_sha256"]
            print(f"   -> Fetched {len(content)} bytes (status {status})")

        parsed = parser.parse(content, fid=fid)
        repo.save_parsed_comparison(parsed, source_hash=source_hash, raw_html_path=str(raw_html))
        winner = parsed["recommendation"]["overall_winner"] if parsed.get("recommendation") else "N/A"
        print(f"   -> Parsed & Imported: '{parsed['clean_title']}' ({parsed['format_category']}, {parsed['year']}) | {len(parsed['releases'])} releases | Winner: {winner}")

    repo.close()
    print("\n[+] All Hammer Dracula films successfully archived and imported into SQLite FTS5 database!")

if __name__ == "__main__":
    main()
