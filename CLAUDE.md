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
   - **Service Buttons (Letterboxd, Wikipedia, IMDb, etc.):** Buttons open in-page embedded views (iframe proxy via `/embed/proxy` or inline dossier), **not** external popup tabs.
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
1. **Hero Title Bar Clean-up:**
   - Removed format switcher pills from `.movie-hero` title bar.
   - Relocated format filter pills to the physical editions toolbar (`.section-toolbar .title-format-pills`) ensuring test compatibility.
2. **Mastering Reference Guide Connection:**
   - Relocated `#mastering-hierarchy-shelf` inside `<section class="editions-section">` right below `.table-scroll-box`.
   - Styled with `.mastering-shelf-attached` to eliminate the visual gap, making it feel like an integrated extension.
3. **Embedded Service Viewer & Proxy Route (`/embed/proxy`):**
   - Added `/embed/proxy` endpoint in `src/web/app.py` fetching mobile ad-free views of Letterboxd, Wikipedia, and external sources with `<base href>` injection and clean CSS.
   - Fixed missing `import aiohttp` in `app.py`.
   - Created rich in-app dossier fallback for IMDb (which blocks automated traffic via WAF).
   - In `src/web/static/app.js`, updated `initHeroInlineShelf()` to handle both internal tabs (verdict/cuts) and external embedded service iframes with reload and close controls.
4. **Unit Tests:**
   - 24/24 unit tests passing cleanly.

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
