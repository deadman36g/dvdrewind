import html
import json
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from aiohttp import web
import aiohttp_jinja2
from bs4 import BeautifulSoup
import jinja2

from src.config import FIXTURES_DIR, PROJECT_ROOT, RAW_DIR
from src.db.migrations import init_db
from src.db.repository import ArchiveRepository
from src.web.shelves import get_curated_shelves
from src.web.security import (
    DEFAULT_MAX_HTML_BYTES,
    ResponseTooLarge,
    TooManyRedirects,
    URLSecurityError,
    archive_control_middleware,
    fetch_public_bytes,
    validate_url_structure,
)

STATIC_DIR = PROJECT_ROOT / "src" / "web" / "static"
TEMPLATES_DIR = PROJECT_ROOT / "src" / "web" / "templates"

EMBED_SERVICE_DOMAINS = {
    "imdb": ("imdb.com",),
    "wikipedia": ("wikipedia.org",),
    "letterboxd": ("letterboxd.com",),
    "bluray": ("blu-ray.com",),
    "dvdbeaver": ("dvdbeaver.com",),
    "moviecensorship": ("movie-censorship.com", "schnittberichte.com"),
    "dvdcompare": ("dvdcompare.net",),
}

EMBED_SERVICE_NAMES = {
    "imdb": "IMDb",
    "wikipedia": "Wikipedia",
    "letterboxd": "Letterboxd",
    "bluray": "Blu-ray.com",
    "dvdbeaver": "DVDBeaver",
    "moviecensorship": "Movie-Censorship",
    "dvdcompare": "DVDCompare",
}

EMBED_RESPONSE_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-popups",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

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
            try:
                cached_url = await cache_custom_poster(custom_url, cache_key)
            except ResponseTooLarge:
                return web.json_response({"error": "Poster response is too large"}, status=413)
            except URLSecurityError as exc:
                return web.json_response({"error": f"Poster URL blocked: {exc}"}, status=400)
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
    force_from_initial = False
    try:
        data = await request.json()
        limit = data.get("limit")
        force_from_initial = bool(data.get("since_initial"))
    except Exception:
        pass
    started = manager.start_sync(limit=limit, force_from_initial=force_from_initial)
    if force_from_initial:
        message = "Historical-tail verification started" if started else "A task is already running"
    else:
        message = "Update to Latest started" if started else "A task is already running"
    return web.json_response({"ok": started, "message": message})

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

async def handle_api_archive_imdb(request: web.Request) -> web.Response:
    manager = ArchiveSyncManager()
    limit = None
    fid = None
    try:
        data = await request.json()
        limit = data.get("limit")
        fid = data.get("fid")
        if fid is not None:
            fid = int(fid)
    except Exception:
        pass
    started = manager.start_imdb_match(limit=limit, fid=fid)
    message = "IMDb repair started" if started else "A task is already running"
    return web.json_response({"ok": started, "message": message})

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
    service_name = EMBED_SERVICE_NAMES.get(service, "External Service")
    safe_service_name = html.escape(service_name, quote=True)
    safe_target_url = html.escape(target_url, quote=True)
    fallback_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_service_name} Embedded View</title>
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
    <h2>{safe_service_name} Resource Ready</h2>
    <p>This resource restricts automated in-frame proxying. You can open the live page directly in a dedicated tab:</p>
    <a href="{safe_target_url}" target="_blank" rel="noopener noreferrer" class="btn-launch">
      <span>Open {safe_service_name} In Full Window &rarr;</span>
    </a>
  </div>
</body>
</html>"""
    return web.Response(
        text=fallback_html,
        content_type="text/html",
        charset="utf-8",
        headers=EMBED_RESPONSE_HEADERS,
    )

def _load_title_for_dossier(fid: str) -> dict[str, Any]:
    if not fid or not fid.isdigit():
        return {}
    repo = ArchiveRepository()
    try:
        return repo.get_title_detail(int(fid)) or {}
    except Exception:
        return {}
    finally:
        repo.close()


def _first_json_ld_item(soup: BeautifulSoup) -> dict[str, Any]:
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = node.string or node.get_text("", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@graph"):
                candidates.extend(item for item in candidate["@graph"] if isinstance(item, dict))
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            item_type = candidate.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if any(t in {"Movie", "TVSeries", "TVEpisode", "CreativeWork"} for t in types):
                return candidate
    return {}


def _person_names(value: Any, limit: int = 6) -> list[str]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    names = []
    for item in values:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = str(item) if item else ""
        if name and name not in names:
            names.append(str(name))
        if len(names) >= limit:
            break
    return names


def _format_iso_duration(value: Any) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", text, flags=re.I)
    if not match:
        return text
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    bits = []
    if hours:
        bits.append(f"{hours}h")
    if minutes:
        bits.append(f"{minutes}m")
    return " ".join(bits)


def _safe_poster_url(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("/"):
        return text
    try:
        parsed = urlsplit(text)
    except Exception:
        return ""
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return ""


def _extract_service_metadata(service: str, html_text: str, title: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if not html_text:
        return metadata

    soup = BeautifulSoup(html_text, "html.parser")
    json_ld = _first_json_ld_item(soup)

    def meta_content(*, prop: str | None = None, name: str | None = None) -> str:
        attrs = {"property": prop} if prop else {"name": name}
        node = soup.find("meta", attrs=attrs)
        return str(node.get("content") or "").strip() if node else ""

    if json_ld:
        metadata["name"] = json_ld.get("name")
        metadata["description"] = json_ld.get("description")
        metadata["content_rating"] = json_ld.get("contentRating")
        metadata["runtime"] = _format_iso_duration(json_ld.get("duration"))
        metadata["genres"] = json_ld.get("genre")
        metadata["director"] = ", ".join(_person_names(json_ld.get("director"), 3))
        metadata["stars"] = _person_names(json_ld.get("actor"), 6)
        date_published = str(json_ld.get("datePublished") or "")
        if len(date_published) >= 4 and date_published[:4].isdigit():
            metadata["year"] = date_published[:4]
        aggregate = json_ld.get("aggregateRating") or {}
        if isinstance(aggregate, dict):
            metadata["rating"] = aggregate.get("ratingValue")
            metadata["rating_count"] = aggregate.get("ratingCount") or aggregate.get("reviewCount")
        image = json_ld.get("image")
        if isinstance(image, dict):
            image = image.get("url")
        if isinstance(image, list):
            image = image[0] if image else ""
        metadata["image"] = image

    metadata["name"] = metadata.get("name") or meta_content(prop="og:title")
    metadata["description"] = (
        metadata.get("description")
        or meta_content(prop="og:description")
        or meta_content(name="description")
    )
    metadata["image"] = metadata.get("image") or meta_content(prop="og:image")

    if not metadata.get("name") and soup.title:
        metadata["name"] = soup.title.get_text(" ", strip=True)

    if not metadata.get("description"):
        for paragraph in soup.find_all("p"):
            text = " ".join(paragraph.get_text(" ", strip=True).split())
            if len(text) >= 80:
                metadata["description"] = text[:900]
                break

    if service == "wikipedia" and metadata.get("description"):
        metadata["description"] = str(metadata["description"])[:1200]

    return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}


def render_service_dossier(
    target_url: str,
    service: str,
    fid: str,
    html_text: str = "",
    fetch_note: str = "",
) -> web.Response:
    title = _load_title_for_dossier(fid)
    scraped = _extract_service_metadata(service, html_text, title)
    service_name = EMBED_SERVICE_NAMES.get(service, service.replace("_", " ").title() or "External Resource")

    clean_title = scraped.get("name") or title.get("clean_title") or "Movie resource"
    year = scraped.get("year") or title.get("year") or ""
    runtime = scraped.get("runtime") or title.get("runtime") or ""
    director = scraped.get("director") or title.get("director") or ""
    synopsis = scraped.get("description") or title.get("synopsis") or title.get("overview") or ""
    genres_value = scraped.get("genres") or title.get("genres") or []
    if isinstance(genres_value, str):
        genres = genres_value
    else:
        genres = " • ".join(str(item) for item in genres_value if item)
    stars = scraped.get("stars") or []
    rating = scraped.get("rating")
    rating_count = scraped.get("rating_count")
    content_rating = scraped.get("content_rating") or ""
    poster_url = _safe_poster_url(title.get("poster_url") or scraped.get("image"))

    safe = lambda value: html.escape(str(value or ""), quote=True)
    safe_service = safe(service_name)
    safe_title = safe(clean_title)
    safe_year = safe(year)
    safe_runtime = safe(runtime)
    safe_director = safe(director)
    safe_synopsis = safe(synopsis)
    safe_genres = safe(genres)
    safe_content_rating = safe(content_rating)
    safe_target_url = safe(target_url)
    safe_fetch_note = safe(fetch_note)
    safe_poster_url = safe(poster_url)
    safe_stars = safe(", ".join(str(item) for item in stars if item))
    safe_rating = safe(rating)
    safe_rating_count = safe(rating_count)

    meta_bits = []
    for value in (safe_content_rating, safe_runtime, safe_genres):
        if value:
            meta_bits.append(f"<span>{value}</span>")
    meta_html = '<span class="sep">•</span>'.join(meta_bits) if meta_bits else '<span>Metadata summary</span>'

    rating_html = ""
    if safe_rating:
        votes = f' <span class="rating-count">({safe_rating_count} votes)</span>' if safe_rating_count else ""
        rating_html = f'<div class="rating"><span>★</span><strong>{safe_rating}</strong><span>/10</span>{votes}</div>'

    credits = []
    if safe_director:
        credits.append(f"<p><strong>Director:</strong> {safe_director}</p>")
    if safe_stars:
        credits.append(f"<p><strong>Cast:</strong> {safe_stars}</p>")
    credits_html = "".join(credits)

    poster_html = f'<img src="{safe_poster_url}" class="resource-poster" alt="Poster">' if safe_poster_url else ""
    note_html = f'<div class="fetch-note">{safe_fetch_note}</div>' if safe_fetch_note else ""
    year_html = f' <span class="year">({safe_year})</span>' if safe_year else ""

    dossier_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_service}: {safe_title}</title>
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  body {{
    margin: 0;
    padding: 1.15rem;
    overflow-y: auto;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0b1120;
    color: #f8fafc;
  }}
  .resource-card {{ max-width: 980px; margin: 0 auto; }}
  .resource-bar {{
    display: flex; align-items: center; justify-content: space-between; gap: .75rem;
    padding-bottom: .75rem; border-bottom: 1px solid rgba(255,255,255,.11); margin-bottom: 1rem;
  }}
  .source-badge {{
    display: inline-flex; align-items: center; padding: .28rem .62rem; border-radius: 6px;
    background: #f59e0b; color: #111827; font-size: .82rem; font-weight: 900;
  }}
  .mode {{ color: #94a3b8; font-size: .72rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }}
  .resource-grid {{ display: flex; align-items: flex-start; gap: 1.15rem; }}
  .resource-poster {{ width: 116px; height: 174px; object-fit: cover; border-radius: 8px; flex: 0 0 auto; box-shadow: 0 6px 18px rgba(0,0,0,.45); }}
  .resource-info {{ min-width: 0; flex: 1; }}
  h1 {{ margin: 0 0 .45rem; font-size: 1.32rem; line-height: 1.2; }}
  .year {{ color: #94a3b8; font-weight: 500; }}
  .meta {{ display: flex; flex-wrap: wrap; gap: .45rem; color: #94a3b8; font-size: .79rem; margin-bottom: .55rem; }}
  .sep {{ color: #475569; }}
  .rating {{ display: flex; align-items: baseline; gap: .3rem; color: #fbbf24; margin: .25rem 0 .65rem; }}
  .rating strong {{ font-size: 1.1rem; }}
  .rating-count {{ color: #64748b; font-size: .72rem; margin-left: .25rem; }}
  .synopsis {{ margin: 0; color: #cbd5e1; font-size: .86rem; line-height: 1.52; }}
  .credits {{ margin-top: .7rem; color: #94a3b8; font-size: .8rem; }}
  .credits p {{ margin: .22rem 0; }}
  .credits strong {{ color: #e2e8f0; }}
  .fetch-note {{ margin-top: .8rem; padding: .55rem .7rem; border: 1px solid rgba(245,158,11,.24); border-radius: 7px; color: #cbd5e1; background: rgba(245,158,11,.06); font-size: .76rem; }}
  .resource-actions {{
    margin-top: 1rem; padding-top: .75rem; border-top: 1px solid rgba(255,255,255,.08);
    display: flex; justify-content: space-between; align-items: center; gap: .75rem;
  }}
  .source-line {{ color: #64748b; font-size: .72rem; }}
  .open-full {{
    display: inline-flex; align-items: center; padding: .48rem .85rem; border-radius: 6px;
    background: rgba(245,158,11,.16); border: 1px solid rgba(245,158,11,.45); color: #fbbf24;
    text-decoration: none; font-size: .79rem; font-weight: 800;
  }}
  .open-full:hover {{ background: rgba(245,158,11,.25); }}
  @media (max-width: 560px) {{ .resource-poster {{ width: 86px; height: 129px; }} .resource-grid {{ gap: .8rem; }} }}
</style>
</head>
<body>
  <div class="resource-card">
    <div class="resource-bar">
      <span class="source-badge">{safe_service}</span>
      <span class="mode">Fixed scraped info view</span>
    </div>
    <div class="resource-grid">
      {poster_html}
      <div class="resource-info">
        <h1>{safe_title}{year_html}</h1>
        <div class="meta">{meta_html}</div>
        {rating_html}
        <p class="synopsis">{safe_synopsis or 'No summary was available from this source. Use the full-site link below for the live page.'}</p>
        <div class="credits">{credits_html}</div>
      </div>
    </div>
    {note_html}
    <div class="resource-actions">
      <span class="source-line">Source: {safe_service}</span>
      <a href="{safe_target_url}" target="_blank" rel="noopener noreferrer" class="open-full">Open Full Site &rarr;</a>
    </div>
  </div>
</body>
</html>"""
    response_headers = dict(EMBED_RESPONSE_HEADERS)
    response_headers["Cache-Control"] = "public, max-age=300"
    return web.Response(text=dossier_html, content_type="text/html", charset="utf-8", headers=response_headers)


def render_imdb_dossier(target_url: str, fid: str, html_text: str = "", fetch_note: str = "") -> web.Response:
    return render_service_dossier(target_url, "imdb", fid, html_text=html_text, fetch_note=fetch_note)

async def handle_embed_proxy(request: web.Request) -> web.Response:
    import logging
    import re

    target_url = request.query.get("url", "").strip()
    service = request.query.get("service", "").strip().lower()
    fid = request.query.get("fid", "").strip()

    if not target_url:
        return web.Response(text="No target URL provided", status=400)

    allowed_domains = EMBED_SERVICE_DOMAINS.get(service)
    if not allowed_domains:
        return web.Response(text="Unsupported embedded service", status=400)

    # Structural validation happens before any rewrite or network access.
    try:
        parsed = validate_url_structure(target_url, allowed_domains)
    except URLSecurityError:
        return web.Response(text="Blocked target URL", status=400)

    # Convert English Wikipedia to its mobile hostname only after validating
    # the exact parsed host, never by substring replacement.
    if service == "wikipedia" and (parsed.hostname or "").rstrip(".").lower() == "en.wikipedia.org":
        mobile_netloc = "en.m.wikipedia.org"
        if parsed.port:
            mobile_netloc = f"{mobile_netloc}:{parsed.port}"
        target_url = parsed._replace(netloc=mobile_netloc).geturl()

    headers = {
        "User-Agent": "RewindMovieArchive/1.0 (Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15; mailto:admin@rewind.local)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    logger = logging.getLogger(__name__)

    try:
        fetched = await fetch_public_bytes(
            target_url,
            allowed_domains=allowed_domains,
            headers=headers,
            max_redirects=5,
            max_bytes=DEFAULT_MAX_HTML_BYTES,
            timeout_seconds=12,
        )
    except ResponseTooLarge:
        return web.Response(text="Embedded resource is too large", status=413)
    except (URLSecurityError, TooManyRedirects) as exc:
        logger.warning("[PROXY] Blocked %s URL: %s", service, exc)
        return web.Response(text="Blocked target URL", status=400)
    except Exception as exc:
        logger.warning("[PROXY] Fetch failed for %s: %s", service, exc)
        return render_service_dossier(
            target_url,
            service,
            fid,
            fetch_note="The live site blocked or failed the metadata fetch. DVDRewind is showing the local title data it already knows.",
        )

    status = fetched.status
    body_bytes = fetched.body
    final_url = fetched.final_url
    logger.info("[PROXY] Fetched %s -> status=%s, size=%s", final_url, status, len(body_bytes))

    # The endpoint is an information scraper, not a generic binary relay.
    if fetched.content_type and fetched.content_type not in ("text/html", "application/xhtml+xml"):
        return render_service_dossier(
            final_url,
            service,
            fid,
            fetch_note="The source returned non-HTML content, so DVDRewind is showing its local metadata instead.",
        )

    # IMDb commonly returns an AWS WAF challenge (often HTTP 202) instead of
    # title HTML. Keep the panel stable and fall back to local title metadata.
    is_waf_block = (
        b"awsWaf" in body_bytes
        or (service == "imdb" and len(body_bytes) < 5000)
        or (status in (403, 202) and service == "imdb")
    )
    if is_waf_block:
        return render_service_dossier(
            final_url,
            service,
            fid,
            fetch_note="The source returned an anti-bot challenge. DVDRewind kept the same info box and fell back to local metadata.",
        )

    if status >= 400 and not (status == 404 and b"<html" in body_bytes):
        return render_service_dossier(
            final_url,
            service,
            fid,
            fetch_note=f"The source returned HTTP {status}; local metadata is shown instead.",
        )

    try:
        encoding = fetched.charset or "utf-8"
        html_text = body_bytes.decode(encoding, errors="replace")
    except Exception:
        html_text = body_bytes.decode("utf-8", errors="replace")

    return render_service_dossier(final_url, service, fid, html_text=html_text)

def create_app() -> web.Application:
    # Ensure database is initialized.
    init_db()

    # Pre-warm the homepage shelf cache before accepting requests. On the full
    # archive this moves the expensive first shelf build into container startup
    # so the first person opening the site gets the same fast response as later
    # requests.
    warm_repo = ArchiveRepository()
    try:
        get_curated_shelves(warm_repo)
    finally:
        warm_repo.close()

    app = web.Application(middlewares=[archive_control_middleware])
    env = aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
    )
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
    app.router.add_post("/api/archive/imdb", handle_api_archive_imdb)
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
