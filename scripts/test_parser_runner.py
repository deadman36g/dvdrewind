import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector

def main():
    collector = WarningCollector()
    parser = DVDCompareParser(collector)
    fixtures = sorted(Path("tests/fixtures").glob("*.html"))

    print(f"{'FIXTURE FILE':<38} | {'STATUS':<7} | {'REL':<3} | {'TITLE':<25} | {'FORMAT':<8} | {'YEAR':<4} | {'WINNER':<18}")
    print("-" * 115)

    for f in fixtures:
        with open(f, "rb") as fp:
            raw = fp.read()
        fid = int(f.name.split("_")[0])
        data = parser.parse(raw, fid=fid)
        status = "MISSING" if data["is_missing"] else "OK"
        rec = data["recommendation"]
        winner = (rec["overall_winner"] if rec and rec["overall_winner"] else "N/A")[:18]
        title = data["clean_title"][:25]
        year = str(data["year"]) if data["year"] else "N/A"
        print(f"{f.name:<38} | {status:<7} | {len(data['releases']):<3} | {title:<25} | {data['format_category']:<8} | {year:<4} | {winner:<18}")

    print("-" * 115)
    print(f"Total Warnings Logged: {len(collector.warnings)}")
    for w in collector.warnings:
        print(f"  [Warning FID {w.fid}] {w.field}: {w.message} ({w.snippet})")

if __name__ == "__main__":
    main()
