import json
from pathlib import Path
from typing import Optional

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
    limit = int(request.query.get("limit", "10"))
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
    repo = ArchiveRepository()
    try:
        title = repo.get_title_detail(fid)
        if not title:
            raise web.HTTPNotFound(text=f"Film comparison FID {fid} not found.")

        # Always construct uniform 3-format nav (4K UHD, Blu-ray, DVD)
        format_nav = {
            '4k': {'name': '4K UHD', 'count': 0, 'fid': None, 'active': False},
            'bluray': {'name': 'Blu-ray', 'count': 0, 'fid': None, 'active': False},
            'dvd': {'name': 'DVD', 'count': 0, 'fid': None, 'active': False}
        }
        
        curr_cat = (title.get("format_category") or "").lower()
        if '4k' in curr_cat or 'uhd' in curr_cat:
            format_nav['4k']['active'] = True
            format_nav['4k']['count'] = len(title.get("releases", []))
            format_nav['4k']['fid'] = title["fid"]
        elif 'blu' in curr_cat:
            format_nav['bluray']['active'] = True
            format_nav['bluray']['count'] = len(title.get("releases", []))
            format_nav['bluray']['fid'] = title["fid"]
        else:
            format_nav['dvd']['active'] = True
            format_nav['dvd']['count'] = len(title.get("releases", []))
            format_nav['dvd']['fid'] = title["fid"]
            
        for s in title.get("format_siblings", []):
            scat = (s.get("format_category") or "").lower()
            if '4k' in scat or 'uhd' in scat:
                format_nav['4k']['count'] = s.get("release_count", 0)
                format_nav['4k']['fid'] = s.get("fid")
            elif 'blu' in scat:
                format_nav['bluray']['count'] = s.get("release_count", 0)
                format_nav['bluray']['fid'] = s.get("fid")
            elif 'dvd' in scat:
                format_nav['dvd']['count'] = s.get("release_count", 0)
                format_nav['dvd']['fid'] = s.get("fid")
                
        title["format_nav"] = format_nav

        # Resolve fanart / backdrop image
        from src.config import ARCHIVE_DIR
        backdrops_dir = ARCHIVE_DIR / "backdrops"
        backdrop_url = None
        imdb_id = title.get("imdb_id")
        if imdb_id and (backdrops_dir / f"{imdb_id}.jpg").exists():
            backdrop_url = f"/static/backdrops/{imdb_id}.jpg"
        elif (backdrops_dir / f"{fid}.jpg").exists():
            backdrop_url = f"/static/backdrops/{fid}.jpg"
        title["backdrop_url"] = backdrop_url

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

    repo = ArchiveRepository()
    try:
        title = repo.get_title_detail(fid)
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
        for r in title.get("releases", []):
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

        cache_key = title.get("imdb_id") or f"custom_{fid}"

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
