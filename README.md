# DVDRewind / Rewind Private Archive

[![CI](https://github.com/deadman36g/dvdrewind/actions/workflows/ci.yml/badge.svg)](https://github.com/deadman36g/dvdrewind/actions/workflows/ci.yml)

A private, self-hosted archival, parsing, and modernization system for **Rewind / DVDCompare (dvdcompare.net)** with an interactive cinematic Web UI and built-in Archive Control Center.

> **Notice:** This project is strictly for personal/private reference and disc collection preservation. It is not intended to be a public clone or redistribution of DVDCompare's content.

---

## Quick Start (Docker & NAS Deployment)

The fastest and easiest way to deploy DVDRewind 24/7 on your NAS (Synology, Unraid, TrueNAS, QNAP) or home server:

```bash
# Clone repository
git clone https://github.com/deadman36g/dvdrewind.git
cd dvdrewind

# Start container with persistent storage
docker compose up -d
```

Open your browser at **`http://<your-nas-ip>:8088`**.

---

## Local Quick Start (Without Docker)

### 1. Requirements
- Python 3.12+

```bash
# Install dependencies
pip install -r requirements.txt

# Run automated test suite
python -m unittest discover tests

# Launch web server
python -m src.cli serve --host 127.0.0.1 --port 8088
```

---

## Archive Maintenance & Ingestion Engine

You can manage and update your archive directly in the **Web UI** by clicking the **`Archive Sync`** button in the header, or via the command line:

```bash
# Interactive full catalog sweep with live curses dashboard
python populate_all.py

# Incremental update: checks DVDCompare homepage revisions & probes new releases
python populate_all.py --sync

# Headless daily sync with auto-vacuum (ideal for crontab on a NAS/server)
python populate_all.py --cron --sync

# Backfill missing posters from TMDB / Wikipedia without re-scraping HTML
python populate_all.py --posters-only

# Check SQLite integrity, optimize FTS5 full-text search index, and VACUUM
python populate_all.py --vacuum
```

---

## Features

- **Cinematic Web Interface:** Netflix-style horizontal shelves, instant search dropdown, authentic BDA pointy hexagon and DVD globe region logos, and side-by-side edition diff tool.
- **Web UI Archive Control Center:** 1-click sync with DVDCompare, live activity terminal streaming, database optimization, and poster backfill directly from any browser.
- **TMDB Official Poster Selector:** Integrated modal loading top 10 official theatrical and release posters from TMDB with 1-click instant apply.
- **Responsible Crawling:** Serial fetcher with randomized jitter (2–3s), exponential backoff, and full SHA-256 integrity verification.
- **Normalized SQLite Database:** Clean schema with foreign keys, cascading integrity, versioned migrations, and full raw text retention.
- **Instant FTS5 Search:** Sub-millisecond full-text search across titles, AKAs, distributors, countries, and recommendation conclusions.
- **Comprehensive Test Suite:** 23 automated unit tests covering parser archetypes, database integrity, and Web APIs.

---

## Project Structure
- `Dockerfile`: Multi-stage production container.
- `docker-compose.yml`: 1-command NAS deployment with persistent `/archive` volume.
- `.github/workflows/ci.yml`: Automated GitHub Actions testing workflow.
- `src/`: Python source code (web server, scraper, parser, database repository, CLI).
- `archive/`: Local storage for SQLite database (`dvdrewind.db`) and posters.
- `tests/`: Unit test suite and raw test fixtures (`tests/fixtures/`).

