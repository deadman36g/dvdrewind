# System Architecture

The DVDRewind system is architected as an offline-first, private personal reference application. It decouples acquisition, parsing, structured storage, and presentation.

```
                    ┌─────────────────────────┐
                    │     dvdcompare.net      │
                    └───────────┬─────────────┘
                                │ Polite HTTP Fetch (Jitter, Backoff, SHA-256)
                                ▼
                    ┌─────────────────────────┐
                    │  Raw HTML Archive       │
                    │  archive/raw/{fid}.html │
                    └───────────┬─────────────┘
                                │ Lossless Parser Execution
                                ▼
                    ┌─────────────────────────┐
                    │  DVDCompareParser       │
                    │  (DOM Extraction)       │
                    └───────────┬─────────────┘
                                │ Normalized Transaction
                                ▼
                    ┌─────────────────────────┐
                    │  SQLite DB (FTS5)       │
                    │  archive/dvdrewind.db   │
                    └───────────┬─────────────┘
                                │ Sub-millisecond Queries
                                ▼
                    ┌─────────────────────────┐
                    │  Local CLI / Web UI     │
                    │  (Search & Movie View)  │
                    └─────────────────────────┘
```

---

## 1. Raw Source Preservation Layer
- **Rule:** The normalized database is never the only copy of the data.
- Every fetch saves:
  1. `archive/raw/{fid}.html`: Untouched raw byte payload.
  2. `archive/raw/{fid}.meta.json`: Fetch timestamp, HTTP headers, status code, parser version, and SHA-256 digest.
- The parser can be rerun at any time entirely offline against `archive/raw/` to test new normalization rules or schema updates without contacting DVDCompare.

## 2. Parsing & Normalization Layer
- Built using Python's `BeautifulSoup4` with custom DOM tree traversal.
- Extracts all structured entities (`titles`, `releases`, `audio_tracks`, `subtitle_tracks`, `extras`, `cuts`, `recommendations`, `update_log`).
- Retains original raw strings in companion columns (`raw_title`, `header_raw`, `notes_raw`, `extras_raw`, etc.) to prevent silent data loss.
- Gathers warnings through `WarningCollector` for unparsed fields or markup deviations.

## 3. Storage Layer
- **Engine:** SQLite 3 with `PRAGMA foreign_keys = ON` and Write-Ahead Logging (`WAL` mode).
- **Search:** SQLite FTS5 virtual table with `unicode61` tokenizer and diacritic stripping for fast instant prefix and keyword searches.
- **Portability:** The entire database resides in a single file (`archive/dvdrewind.db`), making backups straightforward.

## 4. Presentation Layer
- **Phase 1:** High-performance terminal CLI (`src/cli.py`) supporting search, detailed release inspection, fixture parsing, and batch ingestion.
- **Phase 2:** Lightweight web client providing mobile-first responsive cards, desktop comparison tables, dark mode, and side-by-side edition diffing.
