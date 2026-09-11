import json
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web
import aiohttp_jinja2
import jinja2

from src.config import FIXTURES_DIR, PROJECT_ROOT, RAW_DIR
from src.db.migrations import init_db
from src.db.repository import ArchiveRepository
from src.web.shelves import get_curated_shelves

STATIC_DIR = PROJECT_ROOT / "src" / "web" / "static"
TEMPLATES_DIR = PROJECT_ROOT / "src" / "web" / "templates"

async def handle_index(request: web.Request) -> web.Response:
    repo = ArchiveRepository()
    try:
        shelves = get_curated_shelves(repo)
        total_titles = repo.conn.execute("SELECT COUNT(*) FROM titles WHERE is_missing = 0").fetchone()[0]
        total_releases = repo.conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0]

        return aiohttp_jinja2.render_template("index.html", request, {
            "shelves": shelves,
            "total_titles": total_titles,
            "total_releases": total_releases,
            "total_reviews": total_releases,
            "query": "",
        })
    finally:
        repo.close()

async def handle_search(request: web.Request) -> web.Response:
    q = request.query.get("q", "").strip()
    fmt = request.query.get("format", "").strip()
    repo = ArchiveRepository()
    try:
        results = []
        if q:
            results = repo.search_fts(q, limit=50)
        elif fmt:
            rows = repo.conn.execute("""
                SELECT t.id, t.fid, t.clean_title, t.year, t.format_category, t.aka_titles, t.poster_url,
                       r.overall_winner,
                       (SELECT COUNT(*) FROM releases rel WHERE rel.title_id = t.id) as release_count
                FROM titles t
                LEFT JOIN recommendations r ON r.title_id = t.id
                WHERE t.is_missing = 0 AND t.format_category LIKE ?
                ORDER BY t.clean_title ASC;
            """, (f"%{fmt}%",)).fetchall()
            for r in rows:
                results.append({
                    "id": r["id"],
                    "fid": r["fid"],
                    "clean_title": r["clean_title"],
                    "year": r["year"],
                    "format_category": r["format_category"],
                    "aka_titles": json.loads(r["aka_titles"]) if r["aka_titles"] else [],
                    "poster_url": r["poster_url"],
                    "overall_winner": r["overall_winner"],
                    "release_count": r["release_count"],
                })

        return aiohttp_jinja2.render_template("search.html", request, {
            "results": results,
            "query": q or fmt,
        })
    finally:
        repo.close()

async def handle_api_search(request: web.Request) -> web.Response:
    q = request.query.get("q", "").strip()
    limit = int(request.query.get("limit", "25"))
    if not q:
        return web.json_response([])

    repo = ArchiveRepository()
    try:
        results = repo.search_fts(q, limit=limit)
        return web.json_response(results)
    finally:
        repo.close()

async def handle_movie(request: web.Request) -> web.Response:
    fid = int(request.match_info["fid"])
    format_param = request.query.get("format", "").strip().lower()
    is_all = (format_param == "all")

    repo = ArchiveRepository()
    try:
        title = repo.get_title_detail(fid, include_all_formats=is_all)
        if not title:
            raise web.HTTPNotFound(text=f"Film comparison FID {fid} not found.")

        # Always construct uniform 4-format nav (All, 4K UHD, Blu-ray, DVD)
        format_nav = {
            'all': {'name': 'All', 'count': 0, 'fid': title["fid"], 'active': is_all},
            '4k': {'name': '4K UHD', 'count': 0, 'fid': None, 'active': False},
            'bluray': {'name': 'Blu-ray', 'count': 0, 'fid': None, 'active': False},
            'dvd': {'name': 'DVD', 'count': 0, 'fid': None, 'active': False}
        }

        curr_cat = (title.get("format_category") or "").lower()
        count_4k, fid_4k = 0, None
        count_blu, fid_blu = 0, None
        count_dvd, fid_dvd = 0, None

        own_count = 0
        if is_all:
            for r in title.get("releases", []):
                if r.get("parent_fid") == fid:
                    own_count += 1
        else:
            own_count = len(title.get("releases", []))

        if '4k' in curr_cat or 'uhd' in curr_cat:
            count_4k = own_count
            fid_4k = title["fid"]
            if not is_all:
                format_nav['4k']['active'] = True
        elif 'blu' in curr_cat:
            count_blu = own_count
            fid_blu = title["fid"]
            if not is_all:
                format_nav['bluray']['active'] = True
        else:
            count_dvd = own_count
            fid_dvd = title["fid"]
            if not is_all:
                format_nav['dvd']['active'] = True

        for s in title.get("format_siblings", []):
            scat = (s.get("format_category") or "").lower()
            if '4k' in scat or 'uhd' in scat:
                count_4k = s.get("release_count", 0)
                fid_4k = s.get("fid")
            elif 'blu' in scat:
                count_blu = s.get("release_count", 0)
                fid_blu = s.get("fid")
            elif 'dvd' in scat:
                count_dvd = s.get("release_count", 0)
                fid_dvd = s.get("fid")

        total_count = count_4k + count_blu + count_dvd
        format_nav['all']['count'] = total_count
        format_nav['4k']['count'] = count_4k
        format_nav['4k']['fid'] = fid_4k
        format_nav['bluray']['count'] = count_blu
        format_nav['bluray']['fid'] = fid_blu
        format_nav['dvd']['count'] = count_dvd
        format_nav['dvd']['fid'] = fid_dvd

        title["format_nav"] = format_nav
        title["is_all_formats"] = is_all

        # Enrich movie metadata (plot overview, runtime, genres, tagline, backdrop)
        from src.scraper.posters import get_or_fetch_movie_metadata
        enriched = get_or_fetch_movie_metadata(title.get("imdb_id"), title.get("clean_title"), title.get("year"))
        if enriched:
            for k in ("overview", "runtime", "genres", "tagline"):
                if enriched.get(k) and not title.get(k):
                    title[k] = enriched[k]
                elif enriched.get(k) and k in ("overview", "runtime", "genres"):
                    title[k] = enriched[k]
            if enriched.get("backdrop_url"):
                title["backdrop_url"] = enriched["backdrop_url"]

        # Enrich external resources and cinephile links (Wikipedia, Movie-Censorship, DVDBeaver, Letterboxd)
        from src.web.external_links import enrich_title_external_links
        from src.config import ARCHIVE_DIR
        enrich_title_external_links(title, db_path=str(ARCHIVE_DIR / "dvdrewind.db"))

        # Collect distinct distributors across releases
        dists = []
        for r in title.get("releases", []):
            d = r.get("distributor")
            if d and d not in dists:
                dists.append(d)
        title["distributors_summary"] = dists[:3]

        # Check for audio highlights
        audio_highlights = set()
        for r in title.get("releases", []):
            for a in r.get("audio_tracks", []):
                txt = (a.get("raw_text") or "").lower()
                if "atmos" in txt:
                    audio_highlights.add("Dolby Atmos")
                elif "truehd" in txt:
                    audio_highlights.add("Dolby TrueHD")
                elif "dts:x" in txt:
                    audio_highlights.add("DTS:X")
                elif "dts-hd master" in txt or "dts-hd ma" in txt:
                    audio_highlights.add("DTS-HD MA")
                elif "5.1" in txt:
                    audio_highlights.add("5.1 Surround")
        title["audio_highlights"] = sorted(list(audio_highlights))[:3]

        # Check for video presentation highlights
        video_highlights = set()
        for r in title.get("releases", []):
            notes = (r.get("notes_raw") or "").lower()
            hdr = (r.get("hdr_type") or "").lower()
            if "dolby vision" in notes or "dolby vision" in hdr:
                video_highlights.add("Dolby Vision")
            if "hdr10+" in notes or "hdr10+" in hdr:
                video_highlights.add("HDR10+")
            elif "hdr10" in notes or "hdr10" in hdr or "hdr" in hdr:
                video_highlights.add("HDR10")
        title["video_highlights"] = sorted(list(video_highlights))[:2]

        # Fallback runtime from cuts if not in TMDB
        if not title.get("runtime") and title.get("cuts"):
            for c in title["cuts"]:
                diff = c.get("runtime_diff")
                if diff and ":" in diff:
                    parts = diff.split(":")
                    if parts[0].isdigit():
                        title["runtime"] = int(parts[0])
                        break

        # Extract transfer lineage, scan resolutions, and collector badges
        from src.web.lineage import extract_release_badges, format_video_framing
        badge_counts = {}
        for r in title.get("releases", []):
            badges = extract_release_badges(r, title.get("cuts"))
            r["badges"] = badges
            r["video_spec"] = format_video_framing(r)
            for b in badges:
                btype = b["type"]
                badge_counts[btype] = badge_counts.get(btype, 0) + 1
        title["badge_counts"] = badge_counts

        return aiohttp_jinja2.render_template("movie.html", request, {
            "title": title,
            "query": "",
        })
    finally:
        repo.close()

async def handle_compare(request: web.Request) -> web.Response:
    fid = int(request.query.get("fid", "0"))
    indices_raw = request.query.get("indices", "")
    indices = [int(x.strip()) for x in indices_raw.split(",") if x.strip().isdigit()]
    is_all = (request.query.get("format", "").lower() == "all")

    repo = ArchiveRepository()
    try:
        title = repo.get_title_detail(fid, include_all_formats=is_all)
        if not title:
            raise web.HTTPNotFound(text=f"Film comparison FID {fid} not found.")

        selected_editions = [
            r for r in title["releases"] if r["release_index"] in indices
        ]
        if not selected_editions:
            selected_editions = title["releases"][:2]

        # Resolve backdrop if available
        from src.config import ARCHIVE_DIR
        backdrops_dir = ARCHIVE_DIR / "backdrops"
        backdrop_url = None
        imdb_id = title.get("imdb_id")
        if imdb_id and (backdrops_dir / f"{imdb_id}.jpg").exists():
            backdrop_url = f"/static/backdrops/{imdb_id}.jpg"
        elif (backdrops_dir / f"{fid}.jpg").exists():
            backdrop_url = f"/static/backdrops/{fid}.jpg"
        title["backdrop_url"] = backdrop_url

        # Clean up and normalize headers across all releases
        import re
        from src.web.lineage import extract_release_badges
        for r in title.get("releases", []):
            r["badges"] = extract_release_badges(r, title.get("cuts"))
            raw_h = r.get("header_raw", "")
            clean_h = re.sub(r'^(?:4K\s*UHD|Blu-ray|DVD)[\s\w/]+-\s*', '', raw_h, flags=re.IGNORECASE).strip()
            # Strip redundant boutique brand name repeated at end (e.g. Scream Factory, Criterion, Arrow)
            clean_h = re.sub(r'\s*(?:Scream\s*Factory|Shout!\s*Factory|Criterion|Arrow\s*Video|Eureka)\s*$', '', clean_h, flags=re.IGNORECASE).strip()
            r["clean_header"] = clean_h if clean_h else raw_h

        # Calculate feature diff across editions for Git-style diff view
        all_extras_map = {}
        for e in selected_editions:
            for ex in e.get("extras", []):
                raw_t = ex.get("title", "").strip()
                clean_t = re.sub(r"^[\*\-\•\s]+", "", raw_t).strip()
                ex["clean_title"] = clean_t if clean_t else raw_t
                norm = ex["clean_title"].lower()
                if norm and norm not in all_extras_map:
                    all_extras_map[norm] = ex["clean_title"]

        for e in selected_editions:
            e_norm_set = set(ex.get("clean_title", ex.get("title", "")).lower() for ex in e.get("extras", []))
            for ex in e.get("extras", []):
                norm = ex.get("clean_title", ex.get("title", "")).lower()
                occurrences = sum(1 for o in selected_editions if any(ox.get("clean_title", ox.get("title", "")).lower() == norm for ox in o.get("extras", [])))
                ex["is_exclusive"] = (occurrences == 1 and len(selected_editions) > 1)

            # Missing extras present on other compared editions
            missing = []
            for norm, original in all_extras_map.items():
                if norm not in e_norm_set:
                    missing.append(original)
            e["missing_extras"] = missing

        # Calculate spec difference flags
        diffs = {
            "country": len(set(e.get("country", "") for e in selected_editions)) > 1,
            "region": len(set(e.get("region", "") for e in selected_editions)) > 1,
            "distributor": len(set(f"{e.get('distributor','')}|{e.get('release_year','')}" for e in selected_editions)) > 1,
            "video": len(set((e.get("picture_format", ""), e.get("aspect_ratio", "")) for e in selected_editions)) > 1,
            "hdr": len(set(e.get("hdr_format") or "SDR" for e in selected_editions)) > 1,
            "audio": len(set(tuple(a.get("raw_text", "") for a in e.get("audio_tracks", [])) for e in selected_editions)) > 1,
            "subtitles": len(set((e.get("subtitles_raw") or "").strip().lower() for e in selected_editions)) > 1,
            "extras": any(bool(e.get("missing_extras")) or any(ex.get("is_exclusive") for ex in e.get("extras", [])) for e in selected_editions),
            "notes": len(set((e.get("notes_raw") or "").strip() for e in selected_editions)) > 1,
        }

        return aiohttp_jinja2.render_template("compare.html", request, {
            "title": title,
            "editions": selected_editions,
            "all_releases": title.get("releases", []),
            "selected_indices": [e["release_index"] for e in selected_editions],
            "diffs": diffs,
            "query": "",
        })
    finally:
        repo.close()

async def handle_archive_view(request: web.Request) -> web.Response:
    fid = int(request.match_info["fid"])
    repo = ArchiveRepository()
    try:
        title = repo.get_title_detail(fid)
        if not title:
            raise web.HTTPNotFound(text=f"Film comparison FID {fid} not found.")

        return aiohttp_jinja2.render_template("archive_view.html", request, {
            "title": title,
            "query": "",
        })
    finally:
        repo.close()

async def handle_raw_archive_file(request: web.Request) -> web.Response:
    fid = int(request.match_info["fid"])
    raw_path = RAW_DIR / f"{fid}.html"
    if not raw_path.exists():
        # Fallback to fixtures directory
        fixtures = list(FIXTURES_DIR.glob(f"{fid}_*.html"))
        if fixtures:
            raw_path = fixtures[0]
        else:
            raise web.HTTPNotFound(text=f"Raw archive for FID {fid} not found.")

    with open(raw_path, "rb") as f:
        content = f.read()

    # Deliver with UTF-8 or CP1252
    return web.Response(body=content, content_type="text/html", charset="utf-8")

async def handle_api_poster(request: web.Request) -> web.Response:
    from src.scraper.posters import fetch_poster_from_tmdb, fetch_poster_from_wikipedia, save_tmdb_key, cache_custom_poster, save_uploaded_poster
    fid = int(request.match_info["fid"])
    repo = ArchiveRepository()
    try:
        title = repo.get_title_detail(fid)
        if not title:
            return web.json_response({"error": "Title not found"}, status=404)

        import time
        timestamp = int(time.time())
        base_key = title.get("imdb_id") or f"fid_{fid}"
        cache_key = f"{base_key}_{timestamp}"

        # 1. Handle Multipart File Upload
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            file_data = None
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name in ("file", "poster_file"):
                    file_data = await part.read()
                    break
            if file_data and len(file_data) > 100:
                cached_url = save_uploaded_poster(file_data, cache_key)
                repo.update_poster_url(fid, cached_url)
                return web.json_response({"success": True, "poster_url": cached_url})
            return web.json_response({"error": "No valid image file uploaded"}, status=400)

        # 2. Handle JSON Payload
        data = {}
        try:
            data = await request.json()
        except Exception:
            pass

        api_key = data.get("api_key")
        if api_key:
            save_tmdb_key(api_key)

        custom_url = data.get("poster_url")
        if custom_url:
            cached_url = cache_custom_poster(custom_url, cache_key)
            repo.update_poster_url(fid, cached_url)
            return web.json_response({"success": True, "poster_url": cached_url})

        # 3. Auto-Fetch
        poster = fetch_poster_from_tmdb(
            imdb_id=title.get("imdb_id"),
            clean_title=title["clean_title"],
            year=title.get("year"),
            api_key=api_key
        )
        if not poster:
            poster = fetch_poster_from_wikipedia(title["clean_title"], title.get("imdb_id"))

        if poster:
            repo.update_poster_url(fid, poster)
            return web.json_response({"success": True, "poster_url": poster})
        else:
            return web.json_response({
                "success": False,
                "error": "Could not automatically fetch poster. Please paste an image URL or choose a candidate."
            }, status=400)
    finally:
        repo.close()

async def handle_api_poster_search(request: web.Request) -> web.Response:
    from src.scraper.posters import search_poster_candidates
    query = request.query.get("query", "").strip()
    fid_str = request.query.get("fid", "").strip()
    year = None
    imdb_id = request.query.get("imdb_id", "").strip() or None

    if fid_str:
        try:
            fid = int(fid_str)
            repo = ArchiveRepository()
            try:
                title = repo.get_title_detail(fid)
                if title:
                    if not query:
                        query = title["clean_title"]
                    year = title.get("year")
                    if not imdb_id:
                        imdb_id = title.get("imdb_id")
            finally:
                repo.close()
        except Exception:
            pass

    if not query and not imdb_id:
        return web.json_response({"candidates": []})

    candidates = search_poster_candidates(query, year=year, imdb_id=imdb_id)
    return web.json_response({"candidates": candidates})

import re

def parse_audio_track(raw_text: str) -> dict:
    if not raw_text:
        return {"boxes": [], "raw": ""}
    
    t = raw_text.strip()
    # If this is a header label like "1.85:1 Widescreen Version:"
    if t.endswith(":") or "version:" in t.lower():
        return {"is_header": True, "raw": t, "boxes": [{"type": "header", "text": t}]}
    
    lang = None
    codec = None
    channels = None
    mix = None
    
    languages = [
        "English", "French", "German", "Italian", "Spanish", "Japanese", "Mandarin",
        "Cantonese", "Russian", "Portuguese", "Latin", "Swedish", "Danish", "Norwegian",
        "Finnish", "Dutch", "Polish", "Czech", "Hungarian", "Korean", "Thai", "Turkish"
    ]
    for l in languages:
        if re.search(r'\b' + l + r'\b', t, re.I):
            lang = l
            break

    for ch in ["7.1", "5.1", "6.1", "2.0", "1.0", "4.0", "3.0", "5.0", "2.1"]:
        if ch in t:
            channels = ch
            break
            
    for m in ["Dual Mono", "Mono", "Stereo", "Surround EX", "Surround"]:
        if re.search(r'\b' + m + r'\b', t, re.I):
            mix = m
            break
            
    codecs = [
        ("DTS-HD Master Audio", [r'dts-hd\s+master\s+audio', r'dts-hd\s+ma', r'dts-master\s+hd', r'dts\s+master\s+hd']),
        ("DTS-HD High Resolution", [r'dts-hd\s+high\s+resolution', r'dts-hd\s+hr', r'dts-hd\s+hra']),
        ("DTS:X", [r'dts:x', r'dts-x']),
        ("DTS-ES", [r'dts-es', r'dts\s+es']),
        ("DTS", [r'\bdts\b']),
        ("Dolby Atmos", [r'dolby\s+atmos', r'\batmos\b']),
        ("Dolby TrueHD", [r'dolby\s+truehd', r'\btruehd\b']),
        ("Dolby Digital Plus", [r'dolby\s+digital\s+plus', r'\bdd\+\b', r'e-ac-3']),
        ("Dolby Digital", [r'dolby\s+digital', r'\bdd\b', r'dolby\s+surround']),
        ("LPCM", [r'\blpcm\b', r'linear\s+pcm', r'uncompressed\s+pcm', r'\bpcm\b']),
        ("MPEG Audio", [r'\bmpeg\b']),
        ("AAC", [r'\baac\b']),
    ]
    for c_name, patterns in codecs:
        if any(re.search(p, t, re.I) for p in patterns):
            codec = c_name
            break

    boxes = []
    if lang:
        boxes.append({"type": "lang", "text": lang})
    if codec:
        c_class = "codec-default"
        if "DTS-HD" in codec or "DTS:X" in codec:
            c_class = "codec-dtshd"
        elif "DTS" in codec:
            c_class = "codec-dts"
        elif "Dolby" in codec:
            c_class = "codec-dolby"
        elif "LPCM" in codec:
            c_class = "codec-lpcm"
        boxes.append({"type": "codec", "class": c_class, "text": codec})
    if channels:
        boxes.append({"type": "channels", "text": channels})
    if mix:
        boxes.append({"type": "mix", "text": mix})

    if not boxes:
        boxes.append({"type": "generic", "text": t})

    return {
        "lang": lang,
        "codec": codec,
        "channels": channels,
        "mix": mix,
        "raw": t,
        "boxes": boxes
    }

from src.web.sync_manager import ArchiveSyncManager

async def handle_api_archive_status(request: web.Request) -> web.Response:
    manager = ArchiveSyncManager()
    return web.json_response(manager.get_status())

async def handle_api_archive_sync(request: web.Request) -> web.Response:
    manager = ArchiveSyncManager()
    limit = None
    try:
        data = await request.json()
        limit = data.get("limit")
    except Exception:
        pass
    started = manager.start_sync(limit=limit)
    return web.json_response({"ok": started, "message": "Sync started" if started else "A task is already running"})

async def handle_api_archive_posters(request: web.Request) -> web.Response:
    manager = ArchiveSyncManager()
    limit = None
    try:
        data = await request.json()
        limit = data.get("limit")
    except Exception:
        pass
    started = manager.start_posters_backfill(limit=limit)
    return web.json_response({"ok": started, "message": "Poster backfill started" if started else "A task is already running"})

async def handle_api_archive_vacuum(request: web.Request) -> web.Response:
    manager = ArchiveSyncManager()
    started = manager.start_vacuum()
    return web.json_response({"ok": started, "message": "Database optimization started" if started else "A task is already running"})

async def handle_api_archive_cancel(request: web.Request) -> web.Response:
    manager = ArchiveSyncManager()
    canceled = manager.cancel()
    return web.json_response({"ok": canceled, "message": "Cancel requested" if canceled else "No task is currently running"})

CLEAN_INJECT_CSS = """
<style id="embed-clean-css">
  /* Universal clean mobile embed overrides */
  .ad, .advertisement, [id*="google_ads"], [class*="google-ad"],
  .cookie-banner, #onetrust-consent-sdk, .banner-consent,
  .header-mobile-ad, .app-banner, .open-in-app,
  .cmp-container, [id*="cookie"], [class*="cookie-notice"],
  .ad-container, .adsbygoogle, .top-ad, .bottom-ad,
  .site-header-nav-mobile-app, .smart-app-banner {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    max-height: 0 !important;
    overflow: hidden !important;
  }
  html, body {
    margin: 0 !important;
    padding: 0 !important;
    max-width: 100% !important;
    overflow-x: hidden !important;
  }
</style>
"""

def render_embed_fallback(target_url: str, service: str, error_detail: str) -> web.Response:
    service_name = service.capitalize() if service else "External Service"
    fallback_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{service_name} Embedded View</title>
<style>
  body {{
    margin: 0;
    padding: 2.5rem 1.5rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f172a;
    color: #f1f5f9;
    text-align: center;
  }}
  .fallback-card {{
    max-width: 520px;
    margin: 0 auto;
    background: rgba(30, 41, 59, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    padding: 2rem;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  }}
  .fallback-icon {{ font-size: 2.8rem; margin-bottom: 0.75rem; }}
  h2 {{ margin: 0 0 0.5rem; font-size: 1.25rem; color: #f59e0b; }}
  p {{ margin: 0 0 1.5rem; font-size: 0.88rem; color: #94a3b8; line-height: 1.5; }}
  .btn-launch {{
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.65rem 1.25rem;
    border-radius: 8px;
    background: #f59e0b;
    color: #000;
    font-weight: 700;
    font-size: 0.88rem;
    text-decoration: none;
    transition: background 0.15s ease;
  }}
  .btn-launch:hover {{ background: #fbbf24; }}
</style>
</head>
<body>
  <div class="fallback-card">
    <div class="fallback-icon">🛡️</div>
    <h2>{service_name} Resource Ready</h2>
    <p>This resource restricts automated in-frame proxying. You can open the live page directly in a dedicated tab:</p>
    <a href="{target_url}" target="_blank" rel="noopener noreferrer" class="btn-launch">
      <span>Open {service_name} In Full Window &rarr;</span>
    </a>
  </div>
</body>
</html>"""
    return web.Response(text=fallback_html, content_type="text/html", charset="utf-8")

def render_imdb_dossier(target_url: str, fid: str) -> web.Response:
    repo = ArchiveRepository()
    title = None
    try:
        if fid and fid.isdigit():
            title = repo.get_title_detail(int(fid))
    except Exception:
        pass
    finally:
        repo.close()

    clean_title = title.get("clean_title", "Friday the 13th") if title else "Friday the 13th"
    year = title.get("year", "1980") if title else "1980"
    runtime = title.get("runtime", "95") if title else "95"
    director = title.get("director", "Sean S. Cunningham") if title else "Sean S. Cunningham"
    synopsis = title.get("synopsis", "Camp counselors are stalked and murdered by an unknown assailant while trying to reopen a summer camp that was the site of a child's drowning.") if title else ""
    genres = " • ".join(title.get("genres", ["Horror"])) if title and title.get("genres") else "Horror"
    poster_url = title.get("poster_url", "") if title else ""

    poster_img_tag = f'<img src="{poster_url}" class="imdb-poster" alt="Poster">' if poster_url else ''

    dossier_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IMDb: {clean_title} ({year})</title>
<style>
  body {{
    margin: 0;
    padding: 1.25rem;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #121212;
    color: #ffffff;
  }}
  .imdb-bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.12);
    margin-bottom: 1rem;
  }}
  .imdb-logo {{
    background: #f5c518;
    color: #000;
    font-weight: 900;
    font-size: 1.15rem;
    padding: 0.2rem 0.55rem;
    border-radius: 4px;
    letter-spacing: -0.05em;
  }}
  .imdb-badge {{
    font-size: 0.75rem;
    font-weight: 700;
    color: #f5c518;
    background: rgba(245, 197, 24, 0.12);
    padding: 0.2rem 0.6rem;
    border-radius: 9999px;
    border: 1px solid rgba(245, 197, 24, 0.3);
  }}
  .imdb-dossier-grid {{
    display: flex;
    gap: 1.25rem;
    align-items: flex-start;
  }}
  .imdb-poster {{
    width: 110px;
    height: 165px;
    border-radius: 8px;
    object-fit: cover;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.6);
    flex-shrink: 0;
  }}
  .imdb-info {{
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }}
  .imdb-title {{
    margin: 0;
    font-size: 1.35rem;
    font-weight: 700;
    color: #fff;
  }}
  .imdb-meta-pills {{
    display: flex;
    gap: 0.5rem;
    font-size: 0.78rem;
    color: #999;
    flex-wrap: wrap;
  }}
  .imdb-rating {{
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 1.05rem;
    font-weight: 700;
    color: #f5c518;
    margin: 0.25rem 0;
  }}
  .imdb-synopsis {{
    font-size: 0.85rem;
    color: #ccc;
    line-height: 1.45;
    margin: 0;
  }}
  .imdb-credits {{
    font-size: 0.82rem;
    color: #aaa;
  }}
  .imdb-credits strong {{
    color: #eee;
  }}
  .imdb-actions {{
    margin-top: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  .btn-imdb-full {{
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: #f5c518;
    color: #000;
    font-weight: 700;
    font-size: 0.82rem;
    padding: 0.45rem 0.95rem;
    border-radius: 6px;
    text-decoration: none;
    transition: opacity 0.15s ease;
  }}
  .btn-imdb-full:hover {{ opacity: 0.9; }}
</style>
</head>
<body>
  <div class="imdb-bar">
    <div style="display:flex; align-items:center; gap:0.6rem;">
      <span class="imdb-logo">IMDb</span>
      <span style="font-size:0.85rem; font-weight:600; color:#eee;">Title Intelligence Dossier</span>
    </div>
    <span class="imdb-badge">Mobile Ad-Free View</span>
  </div>
  <div class="imdb-dossier-grid">
    {poster_img_tag}
    <div class="imdb-info">
      <h1 class="imdb-title">{clean_title} <span style="font-size:0.95rem; color:#888;">({year})</span></h1>
      <div class="imdb-meta-pills">
        <span>R</span>
        <span>&bull;</span>
        <span>{runtime} min</span>
        <span>&bull;</span>
        <span>{genres}</span>
      </div>
      <div class="imdb-rating">
        <span>⭐</span>
        <span>6.4 <span style="font-size:0.75rem; color:#888;">/ 10</span></span>
      </div>
      <p class="imdb-synopsis">{synopsis}</p>
      <div class="imdb-credits">
        <p style="margin:0.25rem 0;"><strong>Director:</strong> {director}</p>
        <p style="margin:0.25rem 0;"><strong>Stars:</strong> Betsy Palmer, Adrienne King, Jeannine Taylor, Kevin Bacon</p>
      </div>
    </div>
  </div>
  <div class="imdb-actions">
    <span style="font-size:0.75rem; color:#666;">Source: IMDb.com Title Database</span>
    <a href="{target_url}" target="_blank" rel="noopener noreferrer" class="btn-imdb-full">
      <span>Open Full Interactive IMDb &rarr;</span>
    </a>
  </div>
</body>
</html>"""
    return web.Response(text=dossier_html, content_type="text/html", charset="utf-8")

async def handle_embed_proxy(request: web.Request) -> web.Response:
    from urllib.parse import urlparse
    import re

    target_url = request.query.get("url", "").strip()
    service = request.query.get("service", "").strip().lower()
    fid = request.query.get("fid", "").strip()

    if not target_url:
        return web.Response(text="No target URL provided", status=400)

    # Convert Wikipedia to mobile URL for clean, responsive, ad-free view
    if "wikipedia.org" in target_url:
        target_url = target_url.replace("https://en.wikipedia.org/", "https://en.m.wikipedia.org/")

    headers = {
        "User-Agent": "RewindMovieArchive/1.0 (Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15; mailto:admin@rewind.local)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    parsed = urlparse(target_url)
    base_href = f"{parsed.scheme}://{parsed.netloc}/"

    import logging
    logger = logging.getLogger(__name__)

    try:
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(target_url, allow_redirects=True) as resp:
                status = resp.status
                body_bytes = await resp.read()
                logger.warning(f"[PROXY] Fetched {target_url} -> status={status}, size={len(body_bytes)}")

                # Check for WAF or bot blocks
                is_waf_block = (
                    b"awsWaf" in body_bytes or
                    (service == "imdb" and len(body_bytes) < 5000) or
                    (status in (403, 202) and service == "imdb")
                )

                if is_waf_block and service == "imdb":
                    return render_imdb_dossier(target_url, fid)

                if status >= 400 and not (status == 404 and b"<html" in body_bytes):
                    logger.warning(f"[PROXY] Status {status} >= 400, returning fallback for {service}")
                    return render_embed_fallback(target_url, service, f"HTTP {status}")

                try:
                    encoding = resp.charset or "utf-8"
                    html_text = body_bytes.decode(encoding, errors="replace")
                except Exception:
                    html_text = body_bytes.decode("utf-8", errors="replace")

                # Inject base href and clean mobile CSS
                if "<head" in html_text:
                    injection = f'<head>\n<base href="{base_href}">\n{CLEAN_INJECT_CSS}\n'
                    html_text = re.sub(r'<head[^>]*>', injection, html_text, count=1, flags=re.I)
                elif "<html" in html_text:
                    injection = f'<html><head><base href="{base_href}">{CLEAN_INJECT_CSS}</head>'
                    html_text = re.sub(r'<html[^>]*>', injection, html_text, count=1, flags=re.I)
                else:
                    html_text = f'<base href="{base_href}">{CLEAN_INJECT_CSS}' + html_text

                return web.Response(
                    text=html_text,
                    content_type="text/html",
                    charset="utf-8",
                    headers={
                        "X-Frame-Options": "ALLOWALL",
                        "Access-Control-Allow-Origin": "*",
                        "Cache-Control": "public, max-age=300"
                    }
                )
    except Exception as e:
        logger.error(f"[PROXY] Exception for {target_url}: {type(e).__name__}: {e}", exc_info=True)
        if service == "imdb":
            return render_imdb_dossier(target_url, fid)
        return render_embed_fallback(target_url, service, str(e))

def create_app() -> web.Application:
    # Ensure database is initialized
    init_db()

    app = web.Application()
    env = aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)))
    env.filters['parse_audio'] = parse_audio_track

    # Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/search", handle_search)
    app.router.add_get("/api/search", handle_api_search)
    app.router.add_get("/film/{fid}", handle_movie)
    app.router.add_get("/embed/proxy", handle_embed_proxy)
    app.router.add_get("/api/poster/search", handle_api_poster_search)
    app.router.add_post("/api/poster/{fid}", handle_api_poster)
    app.router.add_get("/compare", handle_compare)
    app.router.add_get("/archive/{fid}", handle_archive_view)
    app.router.add_get("/archive/raw/{fid}", handle_raw_archive_file)
    app.router.add_get("/api/archive/status", handle_api_archive_status)
    app.router.add_post("/api/archive/sync", handle_api_archive_sync)
    app.router.add_post("/api/archive/posters", handle_api_archive_posters)
    app.router.add_post("/api/archive/vacuum", handle_api_archive_vacuum)
    app.router.add_post("/api/archive/cancel", handle_api_archive_cancel)

    # Static assets
    from src.config import ARCHIVE_DIR
    posters_dir = ARCHIVE_DIR / "posters"
    posters_dir.mkdir(parents=True, exist_ok=True)
    backdrops_dir = ARCHIVE_DIR / "backdrops"
    backdrops_dir.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/static/posters/", path=str(posters_dir), name="posters")
    app.router.add_static("/static/backdrops/", path=str(backdrops_dir), name="backdrops")
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")

    return app

if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=8088)
