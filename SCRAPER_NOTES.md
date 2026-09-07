# Scraper Notes & Crawling Policy

## Golden Rules
1. **Never Hammer the Source Site:** Concurrency is strictly limited to 1 worker. Deliberate delays with jitter are enforced between requests.
2. **Never Discard Raw Source:** Every successfully retrieved HTTP payload must be stored untouched in `archive/raw/{fid}.html` with a SHA-256 hash before parsing.
3. **Never Guess or Fabricate Identifiers:** Use only remote film IDs (`fid`) and verified IMDb IDs.
4. **Never Bypass Bot Defenses or Security Controls:** Respect rate limits, honor exponential backoff, and do not employ automated proxy rotators or CAPTCHA solvers.

---

## Remote Server Characteristics

### Target: `https://www.dvdcompare.net`
- **Server:** Apache on Linux.
- **Bot Filtering:** The server blocks automated headless agents with `HTTP 403 Forbidden` if default script headers are used. A standard modern desktop browser User-Agent is required.
- **Handling of Missing IDs:**
  - Non-existent film IDs **do not** return `HTTP 404`.
  - The server returns `HTTP 200 OK` with `<title>Rewind @ www.dvdcompare.net - FILMID NOT FOUND</title>`.
  - The scraper client inspects the first 4KB of content for `"FILMID NOT FOUND"` and flags the record as `is_missing = True`.

---

## Rate Limits & Politeness Settings
Configured in `src/config.py`:
- `REQUEST_DELAY_SECONDS`: `2.0` (minimum wait between requests).
- `REQUEST_JITTER_SECONDS`: `1.0` (random delay from 0.0s to 1.0s added to each request).
- `MAX_RETRIES`: `3` (maximum retries per URL).
- `BACKOFF_FACTOR`: `2.0` (exponential backoff: 2s -> 4s -> 8s -> 16s).
- `TIMEOUT_SECONDS`: `15.0`.

---

## State Machine (`crawl_manifest`)

Every film ID transitions through explicit states:
- `queued`: Planned for retrieval.
- `fetched`: Raw HTML successfully saved to disk.
- `parsed`: Data extracted into Python structures.
- `imported`: Written to SQLite normalized tables and FTS5 index.
- `missing`: Verified non-existent ID on remote server.
- `failed`: Terminal failure after retry limit exhausted (with recorded error).
- `unchanged`: Remote SHA-256 matches existing local archive during incremental sync.

---

## Resuming & Incremental Sync (Phase 3 Blueprint)
- **Batch Processing:** Scrape in manageable chunks (e.g. 500–1,000 IDs per run).
- **Restart Safety:** The crawler queries `crawl_manifest` for existing IDs before making network calls, skipping any ID already marked `imported` or `missing`.
- **Homepage Polling:** For incremental updates, query `/` for recent additions (`fid`s) instead of scanning all 76,000 IDs repeatedly.
