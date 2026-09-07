# DVDCompare / Rewind Reconnaissance Report

**Date of Reconnaissance:** September 2026  
**Target Host:** `dvdcompare.net` / `www.dvdcompare.net`  
**Purpose:** Technical survey and protocol evaluation for a self-hosted, private personal archive and modernization system.

---

## 1. Network & Access Policy

### Robots.txt Findings
- Location: `https://www.dvdcompare.net/robots.txt` (HTTP 200 OK, Apache server).
- Rules: Specifically disallows known AI scrapers and aggressive SEO bots (`ChatGPT-User`, `GPTBot`, `ClaudeBot`, `CCBot`, `PerplexityBot`, `SemrushBot`, `Scrapy`, `Go-http-client`, etc.).
- General User-Agents: Default browser clients are not blocked by robots.txt.
- Sitemaps: `https://www.dvdcompare.net/sitemap.xml` returns `404 Not Found`.

### HTTP Request & Bot Defenses
- **Bot Filtering:** Requests with default automated / headless user-agents (e.g. `Go-http-client`, `Python-urllib`) receive an immediate `HTTP 403 Forbidden`.
- **Browser User-Agents:** Standard browser headers (Chrome on Windows/macOS) receive `HTTP 200 OK`.
- **Connection Behavior:** Server runs Apache on Linux. Keep-alive is supported. Simultaneous requests or high concurrency will trigger rate limiting or server instability.
- **Recommended Polite Policy:**
  - Strict concurrency = 1 (single worker).
  - Inter-request delay: 2.0s to 3.0s minimum with randomized jitter (0.5s–1.5s).
  - Exponential backoff on 5xx or timeouts (initial 2s, doubling up to 32s, max 3 retries).

---

## 2. URL Conventions & Site Scale

### Comparison URL Structure
- Standard URL: `/comparisons/film.php?fid=<numeric-id>`
- Example: `https://www.dvdcompare.net/comparisons/film.php?fid=43651`
- Selection parameter: `sel=on` (used in legacy UI to check/hide comparisons). Not needed for scraping.

### ID Behavior & Non-Contiguous Keys
- The film ID (`fid`) is a positive integer.
- **Missing / Deleted IDs Behavior:**
  - Non-existent IDs **do not** return `HTTP 404 Not Found`.
  - The server returns `HTTP 200 OK` with `<title>Rewind @ www.dvdcompare.net - FILMID NOT FOUND</title>` and the text `"Unable to find film details"`.
  - Negative detection must check for this title/string in the response payload.
- **Site Upper Boundary:**
  - The latest comparisons in late 2025 / 2026 reach film IDs around `76,150` – `76,200`.
  - Film IDs start at `fid=1` (*Zombie* / *Zombi 2*).
  - Total potential keyspace is approximately ~76,200 IDs (of which some IDs have been retired or skipped).

### Storage Footprint Estimates
- Average HTML page size:
  - Small comparison (1–3 releases): 12 KB – 25 KB
  - Medium comparison (4–8 releases): 30 KB – 60 KB
  - Large multi-edition comparison (14–32 releases, e.g. *Blade Runner*): 100 KB – 165 KB
  - Weighted average per page: ~45 KB uncompressed (~10 KB gzip-compressed).
- **Estimated Storage for Entire Site (~76,000 pages):**
  - Raw HTML (uncompressed): ~3.4 GB
  - Raw HTML (gzipped / zstd): ~750 MB – 850 MB
  - SQLite Database (Normalized tables + FTS5 search index): ~250 MB – 400 MB
  - Total storage requirement: **under 5 GB** (easily fits on any standard SSD or home server).

---

## 3. Media & Artwork Hosting

- **No Local Artwork:** DVDCompare does **not** host movie posters or cover scans locally.
- **Third-Party Amazon Embeds:** Edition images are rendered via remote Amazon affiliate ad iframes and dynamic image widgets (`ws-eu.amazon-adsystem.com/widgets/q?...` with affiliate tags such as `rewinddvdco05-21`).
- **Policy:** The archival project strips all third-party affiliate embeds and ad trackers. Comparison metadata is 100% self-sufficient without artwork.

---

## 4. Character Encoding & Legacy Quirks

- **Declared Encoding:** Varies across pages. Older comparisons declare `<?xml version="1.0" encoding="iso-8859-1" ?>` or `Content-Type: text/html; charset=iso-8859-1`, while newer pages use `UTF-8`.
- **Database Artifacts:** The remote database contains legacy Windows-1252 / ISO-8859-1 characters (e.g. smart quotes, em-dashes, and accented European letters such as `Åsmund_Utvik`). When served with UTF-8 headers, replacement characters (`\ufffd`) or latin1 mojibake can appear if decoding is naive.
- **Parser Solution:** The parser decodes bytes first as UTF-8, falling back to CP1252 with lossless fallback, and always persists the raw byte stream untouched alongside SHA-256 signatures.

---

## 5. What is Reliably Parseable

1. **Title, Year, and Format:**
   - The primary heading (`<h2>`) consistently encodes `Title [AKA Alternate Titles] [(Format)] (Year)`.
   - Format markers `(Blu-ray 4K)`, `(Blu-ray)`, `(HD DVD)` are consistently placed. If omitted, the comparison is standard `DVD`.
2. **IMDb Link:**
   - Present on almost all feature comparisons as `<a href="http://www.imdb.com/title/tt.../">`.
   - Critical for automatically grouping separate format comparisons (4K, Blu-ray, DVD) of the same film.
3. **Releases List:**
   - Every release is rendered as an `<ul class="dvd">`.
   - The release header (`<h3>`) follows `[Region/Format] [Country] - [Distributor] [[Year Release]] [Edition Name]`.
   - Key metadata items are structured in pairs: `<div class="label">Field:</div>` and `<div class="description">Value</div>`.
   - Recognized fields: `Aspect Ratio`, `Picture Format`, `HDR`, `TV System`, `Soundtrack(s)`, `Subtitles`, `Extras`, `Case type`, `Notes`, `Easter eggs`, `Commentaries`, `UPC Number`.
4. **Overall Recommendation:**
   - Found under `<h3><a name="overall">OVERALL: [Winner]</a></h3>`.
   - Explicit winner string (e.g. `Draw`, `R1`, `Blu-ray ALL`, `R0`) followed by clear rationale.
5. **Cuts:**
   - Found under `<h3>CUTS:</h3>`.
   - Explicitly notes cut status (`No cuts`, `Cut`, `Censored`) with runtime differences in parentheses.
6. **Update Log & Contributors:**
   - Found under `<h3>UPDATE LOG:</h3>` and `Comparison added by...` / `Comparison last updated by...`.
   - Contributor attribution is explicitly preserved.

---

## 6. What is Inconsistent & Requires Special Handling

1. **Multi-disc Audio & Subtitles:**
   - Multi-disc releases often lump all soundtracks and subtitles together, prefixing discs within the text (e.g. `4K: English Dolby Atmos ... | Blu-ray: French Dolby Digital 5.1`). The normalizer uses regex disc-prefix splitting to normalize tracks while preserving the complete raw string.
2. **Missing Film IDs returning HTTP 200:**
   - Must be intercepted at the scraper/parser boundary by inspecting body content rather than checking HTTP status codes alone.
3. **Edition Variations:**
   - Some release headers omit distributor or use non-standard country abbreviations (e.g. `R2 Europe` or `R0 World`). Normalizer falls back gracefully without dropping text.

---

## 7. Recommended Crawl Strategy for Phase 3

1. **Phase 1 & 2 Corpus:** Use the 12 representative fixtures for parser testing and UI development.
2. **Incremental Batch Crawl:**
   - Process IDs in batches (e.g., 500 IDs per job).
   - Single thread, 2.5s delay + 1.0s jitter.
   - At ~3 seconds per request, 1,000 requests take ~50 minutes.
   - Entire site (~76k IDs) will take ~60–65 hours of low-intensity background execution.
   - Check `crawl_manifest` SQLite table before fetching to resume safely after interruptions.
   - For update checks, scrape the homepage `/` or search recently updated comparisons rather than polling 76k IDs.
