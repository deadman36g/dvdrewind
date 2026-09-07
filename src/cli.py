import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import FIXTURES_DIR, RAW_DIR
from src.db.migrations import init_db
from src.db.repository import ArchiveRepository
from src.parser.html_parser import DVDCompareParser
from src.parser.warnings import WarningCollector
from src.scraper.client import ScraperClient

def cmd_init_db(args):
    print("Initializing DVDRewind SQLite archive database...")
    init_db()
    print("Database initialized successfully.")

def cmd_fetch(args):
    fid = args.fid
    print(f"Fetching comparison FID {fid} politely...")
    client = ScraperClient()
    status, content, meta = client.fetch_film(fid, save_to_raw=True)
    print(f"Status: {status}, Size: {len(content)} bytes, SHA-256: {meta['content_sha256']}")
    if meta.get("is_missing"):
        print("Note: Server indicated FILMID NOT FOUND.")
    else:
        print(f"Saved untouched source to {RAW_DIR / f'{fid}.html'}")

def cmd_parse(args):
    fid = args.fid
    raw_file = RAW_DIR / f"{fid}.html"
    if not raw_file.exists():
        print(f"Raw file {raw_file} does not exist. Fetch it first using: python -m src.cli fetch {fid}")
        sys.exit(1)

    collector = WarningCollector()
    parser = DVDCompareParser(collector)
    with open(raw_file, "rb") as f:
        content = f.read()
    data = parser.parse(content, fid=fid)
    print(json.dumps(data, indent=2))
    if collector.warnings:
        print(f"\nLogged {len(collector.warnings)} warnings during parsing.")

def cmd_parse_fixtures(args):
    collector = WarningCollector()
    parser = DVDCompareParser(collector)
    fixtures = sorted(FIXTURES_DIR.glob("*.html"))

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
    print(f"Parsed {len(fixtures)} fixtures with {len(collector.warnings)} warnings.")

def cmd_import_fixtures(args):
    init_db()
    repo = ArchiveRepository()
    collector = WarningCollector()
    parser = DVDCompareParser(collector)

    manifest_path = FIXTURES_DIR / "manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    print(f"Importing {len(manifest)} fixtures into database...")
    imported, missing = 0, 0
    for item in manifest:
        fixture_file = FIXTURES_DIR.parent.parent / item["fixture_path"]
        fid = item["fid"]
        sha256 = item["sha256"]
        with open(fixture_file, "rb") as fp:
            content = fp.read()
        parsed = parser.parse(content, fid=fid)
        repo.save_parsed_comparison(parsed, source_hash=sha256, raw_html_path=str(fixture_file))
        if parsed["is_missing"]:
            missing += 1
            print(f"  [-] FID {fid:5d}: MISSING")
        else:
            imported += 1
            rec = parsed["recommendation"]
            winner = rec["overall_winner"] if rec and rec["overall_winner"] else "N/A"
            print(f"  [+] FID {fid:5d}: {parsed['clean_title']} ({parsed['format_category']}, {parsed['year']}) -> {len(parsed['releases'])} releases | Winner: {winner}")

    repo.close()
    print(f"\nFinished import: {imported} imported, {missing} marked missing.")

def cmd_search(args):
    query = args.query
    repo = ArchiveRepository()
    results = repo.search_fts(query, limit=args.limit)

    print(f"\nSearch results for '{query}': ({len(results)} matches)")
    print("-" * 90)
    print(f"{'FID':<6} | {'TITLE':<32} | {'YEAR':<5} | {'FORMAT':<9} | {'RELEASES':<8} | {'WINNER':<20}")
    print("-" * 90)
    for r in results:
        winner = (r["overall_winner"] or "N/A")[:20]
        print(f"{r['fid']:<6} | {r['clean_title'][:32]:<32} | {str(r['year'] or ''):<5} | {r['format_category']:<9} | {r['release_count']:<8} | {winner:<20}")
    print("-" * 90)
    repo.close()

def cmd_show(args):
    fid = args.fid
    repo = ArchiveRepository()
    title = repo.get_title_detail(fid)
    if not title:
        print(f"Film ID {fid} not found in local database.")
        repo.close()
        return

    print("=" * 80)
    print(f"{title['clean_title']} ({title['year']}) - [{title['format_category']}] (FID {title['fid']})")
    if title['aka_titles']:
        print(f"AKA: {', '.join(title['aka_titles'])}")
    if title['imdb_id']:
        print(f"IMDb: https://www.imdb.com/title/{title['imdb_id']}/")
    print(f"Archived from: {title['source_url']}")
    print(f"Source SHA-256: {title['source_hash']}")
    print("=" * 80)

    # Siblings
    if title.get("format_siblings"):
        print("\nAvailable Other Formats:")
        for s in title["format_siblings"]:
            print(f"  * {s['format_category']}: FID {s['fid']} ({s['clean_title']})")

    # Winner
    rec = title.get("recommendation")
    if rec:
        print("\n--- OVERALL WINNER ---")
        print(f"Winner: {rec.get('overall_winner')}")
        if rec.get('recommendation_text'):
            print(f"Rationale: {rec.get('recommendation_text')}")

    # Cuts
    if title.get("cuts"):
        print("\n--- CUTS & RUNTIMES ---")
        for c in title["cuts"]:
            diff = f" [{c['runtime_diff']}]" if c.get('runtime_diff') else ""
            print(f"  * [{c.get('cut_status')}] {c.get('description')}{diff}")

    # Releases
    print(f"\n--- RELEASES ({len(title['releases'])} total) ---")
    for r in title["releases"]:
        print(f"\n[#{r['release_index']}] {r['header_raw']}")
        print(f"     Region: {r.get('region') or 'N/A'} | Country: {r.get('country') or 'N/A'} | Distributor: {r.get('distributor') or 'N/A'}")
        if r.get('aspect_ratio') or r.get('picture_format') or r.get('hdr_format'):
            hdr_str = f" | HDR: {r['hdr_format']}" if r.get('hdr_format') else ""
            print(f"     Video: {r.get('picture_format') or 'N/A'} ({r.get('aspect_ratio') or 'N/A'}){hdr_str}")
        if r.get('audio_tracks'):
            print(f"     Audio: {len(r['audio_tracks'])} tracks (e.g. {r['audio_tracks'][0]['raw_text']})")
        if r.get('subtitle_tracks'):
            print(f"     Subtitles: {len(r['subtitle_tracks'])} languages")
        if r.get('extras'):
            print(f"     Extras: {len(r['extras'])} items")
        if r.get('notes_raw'):
            print(f"     Notes: {r['notes_raw'][:100]}...")

    repo.close()

def main():
    parser = argparse.ArgumentParser(description="DVDRewind / Rewind Private Archive CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init-db
    p_init = subparsers.add_parser("init-db", help="Initialize or migrate the SQLite database")
    p_init.set_defaults(func=cmd_init_db)

    # fetch
    p_fetch = subparsers.add_parser("fetch", help="Fetch a single film ID politely")
    p_fetch.add_argument("fid", type=int, help="DVDCompare film ID (fid)")
    p_fetch.set_defaults(func=cmd_fetch)

    # parse
    p_parse = subparsers.add_parser("parse", help="Parse a locally archived HTML file")
    p_parse.add_argument("fid", type=int, help="DVDCompare film ID (fid)")
    p_parse.set_defaults(func=cmd_parse)

    # parse-fixtures
    p_fixtures = subparsers.add_parser("parse-fixtures", help="Parse all test corpus fixtures")
    p_fixtures.set_defaults(func=cmd_parse_fixtures)

    # import-fixtures
    p_import = subparsers.add_parser("import-fixtures", help="Import all fixtures into SQLite")
    p_import.set_defaults(func=cmd_import_fixtures)

    # search
    p_search = subparsers.add_parser("search", help="Instant full-text search across local archive")
    p_search.add_argument("query", type=str, help="Search terms")
    p_search.add_argument("--limit", type=int, default=20, help="Max results")
    p_search.set_defaults(func=cmd_search)

    # show
    p_show = subparsers.add_parser("show", help="Show full comparison details for a film ID")
    p_show.add_argument("fid", type=int, help="DVDCompare film ID")
    p_show.set_defaults(func=cmd_show)

    # serve
    p_serve = subparsers.add_parser("serve", help="Launch the local modern web interface")
    p_serve.add_argument("--host", type=str, default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8088, help="Port (default: 8088)")
    p_serve.set_defaults(func=lambda args: _run_web_server(args))

    args = parser.parse_args()
    args.func(args)

def _run_web_server(args):
    from aiohttp import web
    from src.web.app import create_app
    print(f"Starting DVDRewind web server at http://{args.host}:{args.port} ...")
    web.run_app(create_app(), host=args.host, port=args.port)

if __name__ == "__main__":
    main()
