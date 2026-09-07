import json
from pathlib import Path
from typing import Optional

from aiohttp import web
import aiohttp_jinja2
import jinja2

from src.config import FIXTURES_DIR, PROJECT_ROOT, RAW_DIR
from src.db.migrations import init_db
from src.db.repository import ArchiveRepository

STATIC_DIR = PROJECT_ROOT / "src" / "web" / "static"
TEMPLATES_DIR = PROJECT_ROOT / "src" / "web" / "templates"

async def handle_index(request: web.Request) -> web.Response:
    repo = ArchiveRepository()
    try:
        rows = repo.conn.execute("""
            SELECT t.id, t.fid, t.clean_title, t.year, t.format_category, t.aka_titles,
                   r.overall_winner,
                   (SELECT COUNT(*) FROM releases rel WHERE rel.title_id = t.id) as release_count
            FROM titles t
            LEFT JOIN recommendations r ON r.title_id = t.id
            WHERE t.is_missing = 0
            ORDER BY t.id DESC;
        """).fetchall()

        comparisons = []
        for r in rows:
            comparisons.append({
                "id": r["id"],
                "fid": r["fid"],
                "clean_title": r["clean_title"],
                "year": r["year"],
                "format_category": r["format_category"],
                "aka_titles": json.loads(r["aka_titles"]) if r["aka_titles"] else [],
                "overall_winner": r["overall_winner"],
                "release_count": r["release_count"],
            })

        return aiohttp_jinja2.render_template("index.html", request, {
            "comparisons": comparisons,
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
                SELECT t.id, t.fid, t.clean_title, t.year, t.format_category, t.aka_titles,
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

        return aiohttp_jinja2.render_template("compare.html", request, {
            "title": title,
            "editions": selected_editions,
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

def create_app() -> web.Application:
    # Ensure database is initialized
    init_db()

    app = web.Application()
    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)))

    # Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/search", handle_search)
    app.router.add_get("/api/search", handle_api_search)
    app.router.add_get("/film/{fid}", handle_movie)
    app.router.add_get("/compare", handle_compare)
    app.router.add_get("/archive/{fid}", handle_archive_view)
    app.router.add_get("/archive/raw/{fid}", handle_raw_archive_file)

    # Static assets
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")

    return app

if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=8088)
