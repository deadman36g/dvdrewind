import json
import os
from pathlib import Path
from typing import Optional
import requests

from src.config import ARCHIVE_DIR, DEFAULT_USER_AGENT

POSTERS_DIR = ARCHIVE_DIR / "posters"
POSTERS_DIR.mkdir(parents=True, exist_ok=True)
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
                return data.get("tmdb_api_key")
        except Exception:
            pass
    return None

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
    """Normalizes titles like 'Brides of Dracula (The)' to 'The Brides of Dracula'"""
    t = title.strip()
    m = re.match(r"^(.*?)(?:,\s*|\s+\()(The|A|An)\)?$", t, re.IGNORECASE)
    if m:
        return f"{m.group(2)} {m.group(1)}".strip()
    return t

def fetch_poster_from_wikipedia(clean_title: str, imdb_id: Optional[str] = None) -> Optional[str]:
    """
    Fetches official theatrical movie poster from Wikipedia REST API as a zero-config fallback.
    Requires no API key and returns high quality images for classic films.
    """
    headers = {"User-Agent": "DVDRewind-Archive/1.0 (Personal Archive Research Tool)"}
    
    candidates = [
        clean_title,
        normalize_title_for_search(clean_title),
        f"{normalize_title_for_search(clean_title)} (film)",
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

def cache_custom_poster(url: str, cache_key: str) -> str:
    """
    Downloads an arbitrary poster image URL and caches it locally in archive/posters/
    """
    if url.startswith("/static/posters/"):
        return url
    if not url.startswith("http://") and not url.startswith("https://"):
        return url
    
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    local_file = POSTERS_DIR / f"{cache_key}.jpg"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            with open(local_file, "wb") as f:
                f.write(resp.content)
            return f"/static/posters/{cache_key}.jpg"
    except Exception as e:
        print(f"Error caching custom poster: {e}")
    return url

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
        try:
            search_url = "https://api.themoviedb.org/3/search/movie"
            params = {"api_key": key, "query": clean_title}
            if year:
                params["year"] = str(year)
            resp = requests.get(search_url, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results and results[0].get("poster_path"):
                    poster_path = results[0]["poster_path"]
        except Exception as e:
            print(f"Error searching TMDB by title: {e}")

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

