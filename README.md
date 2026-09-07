# DVDRewind / Rewind Private Archive

A private, self-hosted archival, parsing, and modernization system for **Rewind / DVDCompare (dvdcompare.net)**.

> **Notice:** This project is strictly for personal/private reference and disc collection preservation. It is not intended to be a public clone or redistribution of DVDCompare's content.

---

## Features (Phase 1 Delivered)

- **Responsible Crawling:** Single-request serial fetcher with randomized jitter (2–3s), exponential backoff, and full SHA-256 integrity verification.
- **Untouched Raw Archive:** Every downloaded HTML source is stored byte-for-byte in `archive/raw/{fid}.html` with companion `.meta.json` metadata before parsing.
- **Resilient Multi-Format Parser:** Extracts titles, alternate/AKA titles, release years, format categories (4K UHD, Blu-ray, DVD, HD DVD), releases, audio/subtitle tracks, extras, cuts, and overall winner recommendations.
- **Normalized SQLite Database:** Clean schema with foreign keys, cascading integrity, versioned migrations, and full raw text retention.
- **Instant FTS5 Search:** Sub-millisecond full-text search across titles, AKAs, distributors, countries, and recommendation conclusions.
- **Sibling Format Resolution:** Automatically connects separate format listings for the same title (e.g. Blade Runner 4K <-> Blu-ray <-> DVD) via IMDb ID and title/year correlation.
- **Comprehensive Test Suite:** Fixture-based automated unit tests covering 12 representative comparison archetypes.

---

## Quick Start

### 1. Requirements
- Python 3.12+ (Standard library + `beautifulsoup4`, `requests`)

### 2. Initialize Database & Run Tests
```powershell
cd C:\Users\deadman36g\.gemini\antigravity\scratch\dvdrewind

# Run all unit tests
python -m unittest discover tests

# Import test fixtures into SQLite database
python -m src.cli import-fixtures
```

### 3. Local CLI Commands
```powershell
# Search the local archive
python -m src.cli search "Blade Runner"
python -m src.cli search "Halloween"

# Inspect detailed comparison
python -m src.cli show 43651

# Parse all test fixtures and inspect parser diagnostics
python -m src.cli parse-fixtures

# Fetch a single new comparison politely
python -m src.cli fetch 111
```

---

## Project Structure
- `DVDREWIND_RECONNAISSANCE.md`: Full technical reconnaissance report.
- `ARCHITECTURE.md`: High-level system architecture and data flow.
- `DATA_MODEL.md`: Entity-relationship model and table schema.
- `SCRAPER_NOTES.md`: Crawling policy, rate limits, and error handling.
- `DEPLOYMENT.md`: Self-hosted deployment guide (Docker & Caddy).
- `src/`: Python source code (scraper, parser, database repository, CLI).
- `archive/`: Local storage for raw HTML snapshots and SQLite database (`dvdrewind.db`).
- `tests/`: Unit test suite and raw test fixtures (`tests/fixtures/`).
