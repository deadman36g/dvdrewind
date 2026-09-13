import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
import requests

from src.config import ARCHIVE_DIR, DEFAULT_USER_AGENT
from src.web.security import (
    DEFAULT_MAX_IMAGE_BYTES,
    URLSecurityError,
    fetch_public_bytes,
)

POSTERS_DIR = ARCHIVE_DIR / "posters"
POSTERS_DIR.mkdir(parents=True, exist_ok=True)
BACKDROPS_DIR = ARCHIVE_DIR / "backdrops"
BACKDROPS_DIR.mkdir(parents=True, exist_ok=True)
METADATA_DIR = ARCHIVE_DIR / "metadata"
METADATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = ARCHIVE_DIR / "config.json"

def get_saved_tmdb_key() -> Optional[str]:
    # 1. Environment variable
    env_key = os.environ.get("TMDB_API_KEY")
    if env_key:
        return env_key.strip()

    # 2. Config file in archive/
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("tmdb_api_key"):
                    return data.get("tmdb_api_key")
        except Exception:
            pass
    # 3. Default key for seamless offline/desktop metadata enrichment
    return "15d2ea6d0dc1d476efbca3eba2b9bbfb"

def save_tmdb_key(api_key: str):
    data = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["tmdb_api_key"] = api_key.strip()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

import re

def normalize_title_for_search(title: str) -> str:
    """Normalizes titles like 'Brides of Dracula (The)' to 'The Brides of Dracula'."""
    t = title.strip()
    t = re.sub(r"\s*\(\d{4}(?:-\d{4})?\)\s*$", "", t).strip()
    m = re.match(r"^(.*?)(?:,\s*|\s+\()(The|A|An)\)?$", t, re.IGNORECASE)
    if m:
        t = f"{m.group(2)} {m.group(1)}".strip()
    return t


def _match_title_key(title: str) -> str:
    normalized = normalize_title_for_search(title or "")
    return re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()


def find_imdb_match(clean_title: str, year: Optional[int] = None, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return a conservative TMDB-backed IMDb match for a title.

    Automatic repair only accepts an exact normalized title match. When a source
    year exists, TMDB must also report the same release year. This intentionally
    leaves ambiguous records for manual review instead of silently attaching the
    wrong IMDb title.
    """
    key = api_key or get_saved_tmdb_key()
    if not key or not clean_title:
        return None

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    query = normalize_title_for_search(clean_title)
    wanted_key = _match_title_key(query)
    wanted_year = int(year) if isinstance(year, int) or (str(year).isdigit() if year is not None else False) else None

    try:
        params = {"api_key": key, "query": query}
        if wanted_year:
            params["year"] = str(wanted_year)
        response = requests.get(
            "https://api.themoviedb.org/3/search/movie",
            params=params,
            headers=headers,
            timeout=8,
        )
        if response.status_code != 200:
            return None
        results = response.json().get("results", [])
    except Exception:
        return None

    candidates = []
    for result in results[:12]:
        result_titles = [result.get("title") or "", result.get("original_title") or ""]
        if not any(_match_title_key(value) == wanted_key for value in result_titles if value):
            continue
        release_date = str(result.get("release_date") or "")
        result_year = int(release_date[:4]) if len(release_date) >= 4 and release_date[:4].isdigit() else None
        if wanted_year and result_year != wanted_year:
            continue
        candidates.append((result, result_year))

    if not candidates:
        return None

    result, result_year = candidates[0]
    movie_id = result.get("id")
    if not movie_id:
        return None

    try:
        details_response = requests.get(
            f"https://api.themoviedb.org/3/movie/{movie_id}",
            params={"api_key": key},
            headers=headers,
            timeout=8,
        )
        if details_response.status_code != 200:
            return None
        details = details_response.json()
    except Exception:
        return None

    imdb_id = str(details.get("imdb_id") or "").strip()
    if not re.fullmatch(r"tt\d+", imdb_id):
        return None

    return {
        "imdb_id": imdb_id,
        "tmdb_id": movie_id,
        "title": details.get("title") or result.get("title") or clean_title,
        "year": result_year,
        "poster_path": details.get("poster_path") or result.get("poster_path"),
        "confidence": "exact-title-year" if wanted_year else "exact-title",
    }


def fetch_poster_from_wikipedia(clean_title: str, imdb_id: Optional[str] = None) -> Optional[str]:
    """
    Fetches official theatrical movie poster from Wikipedia REST API as a zero-config fallback.
    Requires no API key and returns high quality images for classic films.
    """
    headers = {"User-Agent": "DVDRewind-Archive/1.0 (Personal Archive Research Tool)"}
    
    norm = normalize_title_for_search(clean_title)
    candidates = [
        norm,
        f"{norm} (film)",
        clean_title,
        f"{clean_title} (film)"
    ]
    
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        wiki_title = candidate.replace(" ", "_")
        api_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_title}"
        try:
            resp = requests.get(api_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                img_url = data.get("originalimage", {}).get("source") or data.get("thumbnail", {}).get("source")
                if img_url:
                    cache_key = imdb_id or f"wiki_{abs(hash(clean_title))}"
                    local_file = POSTERS_DIR / f"{cache_key}.jpg"
                    img_resp = requests.get(img_url, headers=headers, timeout=15)
                    if img_resp.status_code == 200:
                        with open(local_file, "wb") as f:
                            f.write(img_resp.content)
                        return f"/static/posters/{cache_key}.jpg"
        except Exception as e:
            print(f"Wikipedia poster fallback error: {e}")
    return None

async def cache_custom_poster(url: str, cache_key: str) -> str:
    """Download a user-selected poster without allowing SSRF or huge bodies."""
    if url.startswith("/static/posters/"):
        if ".." in url or "\\" in url:
            raise URLSecurityError("Invalid local poster path")
        return url

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    result = await fetch_public_bytes(
        url,
        headers=headers,
        max_redirects=5,
        max_bytes=DEFAULT_MAX_IMAGE_BYTES,
        timeout_seconds=15,
    )
    if result.status != 200:
        raise URLSecurityError(f"Poster host returned HTTP {result.status}")
    if result.content_type and not result.content_type.lower().startswith("image/"):
        raise URLSecurityError("Poster URL did not return an image")

    local_file = POSTERS_DIR / f"{cache_key}.jpg"
    with open(local_file, "wb") as f:
        f.write(result.body)
    return f"/static/posters/{cache_key}.jpg"

def fetch_poster_from_tmdb(imdb_id: Optional[str], clean_title: str, year: Optional[int] = None, api_key: Optional[str] = None) -> Optional[str]:
    """
    Fetches official movie poster path from TMDB using IMDb ID or title search.
    If TMDB key is missing, seamlessly falls back to Wikipedia theatrical poster.
    Returns the web URL of the locally cached poster.
    """
    key = api_key or get_saved_tmdb_key()
    
    # If no TMDB key, try Wikipedia zero-config fallback
    if not key:
        wiki_poster = fetch_poster_from_wikipedia(clean_title, imdb_id)
        if wiki_poster:
            return wiki_poster
        return None

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    poster_path = None

    # 1. Try Find by IMDb ID
    if imdb_id and imdb_id.startswith("tt"):
        try:
            find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={key}&external_source=imdb_id"
            resp = requests.get(find_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                movie_results = data.get("movie_results", [])
                if movie_results and movie_results[0].get("poster_path"):
                    poster_path = movie_results[0]["poster_path"]
        except Exception as e:
            print(f"Error finding by IMDb ID: {e}")

    # 2. Fallback to Search by Title + Year
    if not poster_path and clean_title:
        norm_title = normalize_title_for_search(clean_title)
        search_candidates = [norm_title]
        stripped = re.sub(r"\s+(?:Box\s+Set|Trilogy|Collection|Anthology)\s*$", "", norm_title, flags=re.IGNORECASE).strip()
        if stripped and stripped not in search_candidates:
            search_candidates.append(stripped)
        if clean_title not in search_candidates:
            search_candidates.append(clean_title)

        for candidate in search_candidates:
            if poster_path:
                break
            try:
                search_url = "https://api.themoviedb.org/3/search/movie"
                params = {"api_key": key, "query": candidate}
                if year and isinstance(year, int):
                    params["year"] = str(year)
                resp = requests.get(search_url, params=params, headers=headers, timeout=10)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if results and results[0].get("poster_path"):
                        poster_path = results[0]["poster_path"]
                        break
                # Try without year restriction if year was passed
                if not poster_path and "year" in params:
                    params.pop("year")
                    resp = requests.get(search_url, params=params, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        results = resp.json().get("results", [])
                        if results and results[0].get("poster_path"):
                            poster_path = results[0]["poster_path"]
                            break
            except Exception as e:
                pass

    if not poster_path:
        # Fallback to Wikipedia
        return fetch_poster_from_wikipedia(clean_title, imdb_id)

    # Download and cache poster locally
    tmdb_image_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
    cache_key = imdb_id or f"title_{abs(hash(clean_title))}"
    local_file = POSTERS_DIR / f"{cache_key}.jpg"

    try:
        img_resp = requests.get(tmdb_image_url, headers=headers, timeout=15)
        if img_resp.status_code == 200:
            with open(local_file, "wb") as f:
                f.write(img_resp.content)
            return f"/static/posters/{cache_key}.jpg"
    except Exception as e:
        print(f"Error caching TMDB poster: {e}")

    return tmdb_image_url

import urllib.parse

def save_uploaded_poster(image_bytes: bytes, cache_key: str) -> str:
    """Saves raw uploaded image bytes locally in archive/posters/"""
    local_file = POSTERS_DIR / f"{cache_key}.jpg"
    with open(local_file, "wb") as f:
        f.write(image_bytes)
    return f"/static/posters/{cache_key}.jpg"

def search_poster_candidates(query: str, year: Optional[int] = None, imdb_id: Optional[str] = None) -> list:
    """
    Searches TMDB and loads the top 10 movie poster images for the film.
    Also supplements with Wikipedia poster options.
    Returns a list of dicts with title, source, url, and thumbnail.
    """
    candidates = []
    seen_urls = set()
    headers = {"User-Agent": "DVDRewind-Archive/1.0 (Personal Archive Research Tool)"}

    # 1. Fetch Top 10 Posters from TMDB
    key = get_saved_tmdb_key()
    if key:
        movie_id = None
        # Try finding by IMDb ID first (100% precision)
        if imdb_id and imdb_id.startswith("tt"):
            try:
                find_url = f"https://api.themoviedb.org/3/find/{imdb_id}"
                resp = requests.get(find_url, params={"api_key": key, "external_source": "imdb_id"}, headers=headers, timeout=5)
                if resp.status_code == 200:
                    mr = resp.json().get("movie_results", [])
                    if mr:
                        movie_id = mr[0].get("id")
            except Exception as e:
                print(f"TMDB find by IMDb error: {e}")

        # If not found by IMDb, search by title + year
        if not movie_id and query:
            try:
                clean_q = normalize_title_for_search(query)
                params = {"api_key": key, "query": clean_q}
                if year:
                    params["year"] = str(year)
                s_resp = requests.get("https://api.themoviedb.org/3/search/movie", params=params, headers=headers, timeout=5)
                if s_resp.status_code == 200:
                    res = s_resp.json().get("results", [])
                    if res:
                        movie_id = res[0].get("id")
            except Exception as e:
                print(f"TMDB title search error: {e}")

        # Fetch image gallery for this movie (first 10 posters)
        if movie_id:
            try:
                imgs_url = f"https://api.themoviedb.org/3/movie/{movie_id}/images"
                img_resp = requests.get(imgs_url, params={"api_key": key}, headers=headers, timeout=6)
                if img_resp.status_code == 200:
                    posters = img_resp.json().get("posters", [])
                    # Separate English/universal vs other languages
                    en_posters = [p for p in posters if p.get("iso_639_1") in ("en", None)]
                    other_posters = [p for p in posters if p.get("iso_639_1") not in ("en", None)]
                    top_posters = (en_posters + other_posters)[:10]

                    for idx, p in enumerate(top_posters, 1):
                        fpath = p.get("file_path")
                        if fpath:
                            full_url = f"https://image.tmdb.org/t/p/w500{fpath}"
                            thumb_url = f"https://image.tmdb.org/t/p/w185{fpath}"
                            if full_url not in seen_urls:
                                seen_urls.add(full_url)
                                lang = f" [{p.get('iso_639_1').upper()}]" if p.get('iso_639_1') else ""
                                res_label = f"{p.get('width', '')}x{p.get('height', '')}"
                                candidates.append({
                                    "title": f"TMDB #{idx}{lang} ({res_label})",
                                    "source": "TMDB",
                                    "url": full_url,
                                    "thumb": thumb_url
                                })
            except Exception as e:
                print(f"TMDB images fetch error: {e}")

    # 2. Supplement with Wikipedia OpenSearch if needed
    if len(candidates) < 10:
        try:
            clean_q = normalize_title_for_search(query)
            search_terms = [clean_q]
            if year:
                search_terms.append(f"{clean_q} ({year} film)")
            else:
                search_terms.append(f"{clean_q} film")

            article_titles = []
            for term in search_terms[:2]:
                opensearch_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(term)}&limit=4&namespace=0&format=json"
                r = requests.get(opensearch_url, headers=headers, timeout=4)
                if r.status_code == 200:
                    data = r.json()
                    if len(data) > 1:
                        for t in data[1]:
                            if t not in article_titles:
                                article_titles.append(t)

            for atitle in article_titles[:4]:
                slug = urllib.parse.quote(atitle.replace(" ", "_"))
                summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
                try:
                    sr = requests.get(summary_url, headers=headers, timeout=3)
                    if sr.status_code == 200:
                        sdata = sr.json()
                        orig = sdata.get("originalimage", {}).get("source")
                        thumb = sdata.get("thumbnail", {}).get("source")
                        img_url = orig or thumb
                        if img_url and img_url not in seen_urls:
                            seen_urls.add(img_url)
                            candidates.append({
                                "title": atitle,
                                "source": "Wikipedia",
                                "url": img_url,
                                "thumb": thumb or img_url
                            })
                except Exception:
                    pass
        except Exception as e:
            print(f"Wikipedia candidate search error: {e}")

    return candidates


def get_or_fetch_movie_metadata(imdb_id: Optional[str], clean_title: str, year: Optional[int] = None) -> dict:
    """
    Retrieves enriched movie metadata (overview, runtime, genres, tagline, backdrop_url)
    from local cache or fetches on-demand from TMDB.
    """
    cache_key = imdb_id or f"meta_{abs(hash(clean_title))}"
    cache_file = METADATA_DIR / f"{cache_key}.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Verify backdrop file exists if referenced
                if data.get("backdrop_url"):
                    bd_fname = Path(data["backdrop_url"]).name
                    if not (BACKDROPS_DIR / bd_fname).exists():
                        data["backdrop_url"] = None
                return data
        except Exception:
            pass

    key = get_saved_tmdb_key()
    if not key:
        return {}

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    movie_id = None

    # 1. Look up by IMDb ID
    if imdb_id and imdb_id.startswith("tt"):
        try:
            find_url = f"https://api.themoviedb.org/3/find/{imdb_id}?api_key={key}&external_source=imdb_id"
            resp = requests.get(find_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                mr = resp.json().get("movie_results", [])
                if mr:
                    movie_id = mr[0].get("id")
        except Exception as e:
            print(f"Metadata TMDB find error: {e}")

    # 2. Look up by title + year
    if not movie_id and clean_title:
        try:
            norm_q = normalize_title_for_search(clean_title)
            params = {"api_key": key, "query": norm_q}
            if year:
                params["year"] = str(year)
            s_resp = requests.get("https://api.themoviedb.org/3/search/movie", params=params, headers=headers, timeout=5)
            if s_resp.status_code == 200:
                res = s_resp.json().get("results", [])
                if res:
                    movie_id = res[0].get("id")
        except Exception as e:
            print(f"Metadata TMDB search error: {e}")

    if not movie_id:
        return {}

    # 3. Fetch comprehensive details
    try:
        det_url = f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={key}"
        det_resp = requests.get(det_url, headers=headers, timeout=6)
        if det_resp.status_code != 200:
            return {}
        details = det_resp.json()

        bd_path = details.get("backdrop_path")
        bd_url = None
        if bd_path:
            bd_file = BACKDROPS_DIR / f"{cache_key}.jpg"
            if not bd_file.exists():
                try:
                    img_resp = requests.get(f"https://image.tmdb.org/t/p/w1280{bd_path}", headers=headers, timeout=10)
                    if img_resp.status_code == 200:
                        with open(bd_file, "wb") as f:
                            f.write(img_resp.content)
                        bd_url = f"/static/backdrops/{cache_key}.jpg"
                except Exception as e:
                    print(f"Backdrop download error: {e}")
            else:
                bd_url = f"/static/backdrops/{cache_key}.jpg"

        meta = {
            "overview": details.get("overview") or "",
            "runtime": details.get("runtime"),
            "genres": [g["name"] for g in details.get("genres", []) if "name" in g],
            "tagline": details.get("tagline") or "",
            "backdrop_url": bd_url,
            "vote_average": details.get("vote_average")
        }

        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            pass

        return meta
    except Exception as e:
        print(f"TMDB details fetch error: {e}")
        return {}


