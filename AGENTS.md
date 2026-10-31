# 🤖 AGENTS.md — Mandatory AI Agent Instructions & Handoff Guide

> **ATTENTION ALL AI AGENTS (Claude, Codex, Antigravity, Gemini, GPT-4, etc.):**  
> **YOU MUST READ THIS FILE COMPLETELY BEFORE PROPOSING OR EXECUTING ANY CODE CHANGES.**  
> This project is actively developed across different AI tools and sessions. Respect the conventions, test suites, and ongoing tasks documented here.

---

## 1. Project Overview

- **Project Name:** DVDRewind (Rewind / DVDCompare Private Archive)
- **Local Path:** `C:\Users\deadman36g\.gemini\antigravity\scratch\dvdrewind`
- **GitHub Repository:** https://github.com/deadman36g/dvdrewind
- **Tech Stack:**
  - **Backend:** Python 3.12+, `aiohttp`, `aiohttp-jinja2`, `jinja2`, SQLite (with FTS5)
  - **Frontend:** Vanilla JavaScript (`app.js`, `archive_control.js`), CSS (`style.css`), Jinja2 templates
  - **Testing:** Python standard library `unittest`
  - **Scraper / Archiver:** `populate_all.py`, `src/scraper/`, `src/parser/`

---

## 2. Agent Workflow Rules & Conventions

1. **Test First & Test Always:**
   - Always run the test suite before and after making changes:
     ```bash
     python -m unittest discover tests
     ```
   - All 24 tests must pass. Do not break test assertions (e.g. `test_movie_page`, `format-tabs-list`).
2. **Do Not Hallucinate Dependencies:**
   - Do not install heavy JS frameworks (no React, no Vue, no Tailwind npm builds). The app uses clean, fast vanilla JS and CSS.
   - Playwright is not installed; screenshots and headless checks use Edge headless via subprocess if needed.
3. **Respect Established Design & UX Directives:**
   - **Hero Section:** Keep only clean movie information at the top (Title, Year, Runtime, Director, Studio, Tagline/Synopsis). Do **not** place format pills (like "Blu-ray" or "4K UHD") or specific audio tracks (like "DTS-HD MA") in the universal movie hero. Format selectors belong on the editions table toolbar.
   - **Physical Editions Table:** The Collector Mastering References Guide must sit directly connected beneath the physical editions table (using `.mastering-shelf-attached` with zero margin/top-border gap) looking like a natural extension, while remaining collapsible.
   - **Service Buttons:** External-resource buttons (IMDb, Wikipedia, Letterboxd, Blu-ray.com, DVDBeaver, Movie-Censorship, DVDCompare) open one consistent fixed-size in-page info panel. `/embed/proxy` fetches only allowlisted public HTML, extracts relevant metadata into a compact dossier, and always includes an explicit “Open Full Site” link. Do not open these buttons directly into external tabs as their primary action, and do not render raw third-party pages inside the app.
   - **Tone & Wording:** Use human-friendly, conversational archivist language (not sterile or robotic database jargon).
4. **Handoff Protocol:**
   - When finishing a session or switching models, update this `AGENTS.md` file with what was completed, any bugs found/fixed, and the exact next steps for the incoming agent.

---

## 3. How to Run & Verify

### Running the Web Server
```bash
# Direct run
python -m src.web.app
# OR via CLI
python -m src.cli serve --host 127.0.0.1 --port 8088
```
- Access at: `http://127.0.0.1:8088`
- Primary test fixture URL: `http://127.0.0.1:8088/movie/62304` (Friday the 13th)

### Running Automated Tests
```bash
python -m unittest discover -s tests -v
```

---

## 4. Current State & Recent Changes (Handoff Log)

### Completed Work:
1. **Project Branding Icon:**
   - Added the modernized square rewind-button icon under `assets/branding/dvdrewind-icon.webp`.
   - Added the runtime copy at `src/web/static/dvdrewind_icon.webp` and wired it as the site favicon while preserving the existing wide header wordmark.
2. **Hero Title Bar Clean-up:**
   - Removed format switcher pills from `.movie-hero` title bar.
   - Relocated format filter pills to the physical editions toolbar (`.section-toolbar .title-format-pills`) ensuring test compatibility.
2. **Mastering Reference Guide Connection:**
   - Relocated `#mastering-hierarchy-shelf` inside `<section class="editions-section">` right below `.table-scroll-box`.
   - Styled with `.mastering-shelf-attached` to eliminate the visual gap, making it feel like an integrated extension.
3. **Fixed External Info Viewer & Proxy Route (`/embed/proxy`):**
   - `/embed/proxy` validates the requested service/URL, fetches bounded public HTML, and converts the response into a fixed-size scraped-information dossier rather than relaying the raw third-party page.
   - IMDb JSON-LD is used when available for rating, vote count, runtime, genres, director, cast, description, and release metadata; anti-bot/WAF responses fall back cleanly to DVDRewind's local title metadata without resizing the panel.
   - Wikipedia, Letterboxd, Blu-ray.com, DVDBeaver, Movie-Censorship, and DVDCompare use the same fixed-panel formula with generic structured/meta-description extraction plus an “Open Full Site” action.
   - In `src/web/static/app.js`, updated `initHeroInlineShelf()` to handle both internal tabs (verdict/cuts) and external embedded service iframes with reload and close controls.
4. **Unit Tests:**
   - 24/24 original unit tests passing cleanly.

### Security Hardening — 2026-09-10
- **SSRF / open-proxy fix:** `/embed/proxy` now accepts only `http`/`https` URLs on the explicit per-service domain allowlist (IMDb, Wikipedia, Letterboxd, Blu-ray.com, DVDBeaver, Movie-Censorship/Schnittberichte, and DVDCompare). Hostnames use exact/subdomain-boundary matching, standard web ports only, DNS results must be globally routable, and localhost/loopback/private/link-local/reserved/unspecified/multicast plus local-network hostname suffixes are blocked.
- **Redirects and size limits:** Proxy redirects are followed manually with every hop revalidated, capped at 5 hops and 5 MiB. Custom poster URL downloads reuse the same public-network validation, revalidate redirects, and are capped at 15 MiB.
- **HTML safety:** Generated fallback/IMDb dossier values are HTML-escaped, Jinja templates now use autoescaping, and proxied service iframes no longer receive `allow-same-origin` alongside script execution.
- **Archive controls:** `POST /api/archive/sync`, `/api/archive/posters`, `/api/archive/vacuum`, and `/api/archive/cancel` remain seamless on true localhost requests. Non-local requests receive `403` unless `DVDREWIND_ADMIN_TOKEN` is configured and supplied as `X-DVDRewind-Admin-Token` (a reverse proxy can inject this header for trusted admin access).
- **Exposure guidance:** Keep DVDRewind private. If using a reverse proxy, preserve external Host/client-address headers or explicitly block the archive-control routes unless the admin-token header is injected; a proxy that rewrites remote requests to look exactly like localhost can defeat localhost-only detection. The embed proxy is intentionally still permitted to fetch only the listed public services.
- **Verification:** 38/38 tests pass after adding security regressions for blocked IPv4/IPv6/local schemes, hostname tricks, private redirects, response-size limits, HTML escaping, poster SSRF, and archive-control authorization.

### Remote Archive Controls Fix — 2026-10-31
- **Bug:** On the hosted site (`dvdrewind.d36g.xyz`) "Sync with DVDCompare" only showed "Could not start sync task." The security middleware was returning `403` (non-localhost, no admin token), but the browser never sent `X-DVDRewind-Admin-Token` and only read `data.message`, hiding the middleware's `data.error`.
- **Fix:** `archive_control.js` now routes every archive action through `archiveControlPost()`, which sends a token saved in `localStorage` (`dvdrewind.adminToken`), prompts once for `DVDREWIND_ADMIN_TOKEN` on a `403`, retries, forgets a rejected token, and surfaces `message`/`error` from the server. The 403 text now tells the user how to unlock controls.
- **Server requirement:** `DVDREWIND_ADMIN_TOKEN` must be set in the server's environment for remote use; without it remote archive actions stay locked by design.
- **Verification:** 66/66 tests pass; headless Chromium check posing as a remote client confirmed wrong token → clear error, correct token → `200`, token reused without re-prompting.

### Fill Archive Gaps Task — 2026-10-31
- **Finding:** The original `populate_all.py` sweep never finished (progress file stopped near FID 66,233 with ~715 errors). The live DB had ~49,400 IDs below `INITIAL_MAX_FID` (76,200) that were never requested. Sync / Deep Verify only scan above 76,200, so they never filled these holes.
- **Added:** "🧩 Fill Archive Gaps" button → `POST /api/archive/gap-fill` (admin-protected) → `ArchiveSyncManager.start_gap_fill()` / `_run_gap_fill()`. It checks every ID 1..76,200 with no `titles` row, reuses valid raw HTML already on disk, remembers DVDCompare "not found" IDs in `archive/gap_fill_state.json`, logs failures for retry on the next run, pauses 2 minutes after 5 consecutive failures, and reports progress/ETA (`stats.phase_current/phase_total/eta_seconds/confirmed_empty`).
- **Tests:** `tests/test_gap_fill.py` uses a fake DVDCompare client and a temp DB.
- **Deployment note:** The NAS copy at `/srv/mergerfs/storage_pool/nas/stacks/dvdrewind` is newer than GitHub (it has the Harvest Edition Covers feature). Deploy there with targeted patches, not a full overwrite.

### Known Watch Items / Next Steps for Incoming Agent:
1. **Visual Polish:**
   - Verify layout rendering on `http://127.0.0.1:8088/movie/62304`.
   - Ensure the collapsible animation for the attached mastering guide is smooth and doesn't overlap borders.
2. **Audio / Video Guide Polish:**
   - The user previously discussed audio/video rating details. Ensure audio specs and video mastering references are intuitive and accessible without cluttering the main view.
3. **Service Embed Testing:**
   - Ensure iframe proxy works smoothly across multiple titles and handles network timeouts gracefully.

---

## 5. Directory Map

- `src/web/app.py`: Web application routes, proxy handler (`/embed/proxy`), template filters.
- `src/web/templates/movie.html`: Movie page template (hero, shelf, editions table, mastering guide, archival shelf).
- `src/web/templates/base.html`: Common layout, navigation bar, archive status indicators.
- `src/web/static/app.js`: Client-side controllers (shelf toggle, embed viewer, sortable table).
- `src/web/static/style.css`: Application styling.
- `src/db/repository.py`: SQLite query methods.
- `tests/`: Automated test suite (`test_web.py`, `test_parser.py`, `test_db.py`, `test_sync_api.py`).
