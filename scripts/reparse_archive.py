import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import RAW_DIR
from src.db.migrations import get_connection, init_db
from src.db.repository import ArchiveRepository
from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector

def main():
    conn = get_connection()
    # Check if column exists
    cols = [col[1] for col in conn.execute("PRAGMA table_info(titles)").fetchall()]
    if "dvdbeaver_url" not in cols:
        print("Adding dvdbeaver_url column to titles table...")
        conn.execute("ALTER TABLE titles ADD COLUMN dvdbeaver_url TEXT;")
        conn.commit()
    conn.close()

    repo = ArchiveRepository()
    collector = WarningCollector()
    parser = DVDCompareParser(collector)

    raw_files = sorted(RAW_DIR.glob("*.html"))
    print(f"Reparsing and updating {len(raw_files)} locally archived HTML files...")

    beaver_count = 0
    for f in raw_files:
        try:
            fid = int(f.stem)
        except ValueError:
            continue

        with open(f, "rb") as fp:
            content = fp.read()

        source_hash = hashlib.sha256(content).hexdigest()
        parsed = parser.parse(content, fid=fid)
        repo.save_parsed_comparison(parsed, source_hash=source_hash, raw_html_path=str(f))

        if parsed.get("dvdbeaver_url"):
            beaver_count += 1
            print(f"  [+] FID {fid:5d}: {parsed['clean_title']} ({parsed['format_category']}) -> DVDBeaver: {parsed['dvdbeaver_url']}")

    repo.close()
    print(f"\n[+] Reparse complete! Updated {len(raw_files)} files. Found {beaver_count} titles with DVDBeaver review links.")

if __name__ == "__main__":
    main()
