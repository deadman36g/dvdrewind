"""
DVDRewind External Resources and Cinephile Review Link Resolver
"""

import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional
import urllib.parse
import urllib.request
import sqlite3

from src.config import ARCHIVE_DIR

WIKI_CACHE_DIR = ARCHIVE_DIR / "metadata" / "wikipedia"
WIKI_CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "DVDRewind-Archive/1.0 (Personal Film Research Tool; https://github.com/dvdrewind)"
}


def normalize_title_for_wiki(title: str) -> str:
    t = title.strip()
    t = re.sub(r"\s*\(\d{4}(?:-\d{4})?\)\s*$", "", t).strip()
    m = re.match(r"^(.*?)(?:,\s*|\s+\()(The|A|An)\)?$", t, re.IGNORECASE)
    if m:
        t = f"{m.group(2)} {m.group(1)}".strip()
    return t


def resolve_wikipedia_url(clean_title: str, year: Optional[int] = None, imdb_id: Optional[str] = None) -> Optional[str]:
    """
    Resolves the exact Wikipedia film article URL via Wikidata or Wikipedia OpenSearch API.
    Caches results on-disk in archive/metadata/wikipedia/.
    """
    cache_key = imdb_id if (imdb_id and imdb_id.startswith("tt")) else f"wiki_{abs(hash(clean_title))}"
    cache_file = WIKI_CACHE_DIR / f"{cache_key}.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("wikipedia_url")
        except Exception:
            pass

    resolved_url = None

    # 1. Wikidata IMDb (P345) lookup (highest precision)
    if imdb_id and imdb_id.startswith("tt"):
        try:
            q_url = f"https://www.wikidata.org/w/api.php?action=query&list=search&srsearch=haswbstatement:P345={imdb_id}&format=json"
            req = urllib.request.Request(q_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                results = data.get("query", {}).get("search", [])
                if results:
                    qid = results[0]["title"]
                    entity_url = f"https://www.wikidata.org/w/api.php?action=wbgetentities&ids={qid}&props=sitelinks/urls&format=json"
                    req2 = urllib.request.Request(entity_url, headers=HEADERS)
                    with urllib.request.urlopen(req2, timeout=3.5) as resp2:
                        d2 = json.loads(resp2.read().decode('utf-8'))
                        enwiki = d2.get("entities", {}).get(qid, {}).get("sitelinks", {}).get("enwiki", {})
                        if enwiki.get("url"):
                            resolved_url = enwiki["url"]
        except Exception:
            pass

    # 2. Wikipedia OpenSearch fallback
    if not resolved_url and clean_title:
        try:
            norm = normalize_title_for_wiki(clean_title)
            queries = [
                f"{norm} {year} film" if year else f"{norm} film",
                f"{norm} (film)",
                norm
            ]
            for q_str in queries:
                encoded_q = urllib.parse.quote(q_str)
                search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={encoded_q}&limit=3&namespace=0&format=json"
                req = urllib.request.Request(search_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=3.5) as resp:
                    res = json.loads(resp.read().decode('utf-8'))
                    titles = res[1] if len(res) > 1 else []
                    urls = res[3] if len(res) > 3 else []
                    for t, u in zip(titles, urls):
                        if "film" in t.lower() or (year and str(year) in t) or norm.lower() in t.lower():
                            resolved_url = u
                            break
                    if resolved_url:
                        break
                    elif urls:
                        resolved_url = urls[0]
                        break
        except Exception:
            pass

    # Save to local cache
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"wikipedia_url": resolved_url, "clean_title": clean_title, "imdb_id": imdb_id}, f)
    except Exception:
        pass

    return resolved_url


WIKI_SUMMARY_DIR = ARCHIVE_DIR / "metadata" / "wikipedia_summary"
WIKI_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

MC_SUMMARY_DIR = ARCHIVE_DIR / "metadata" / "movie_censorship"
MC_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)


def resolve_movie_censorship_url(fid: int, siblings: Optional[list] = None) -> Optional[str]:
    """
    Finds direct Movie-Censorship comparison report URL from raw HTML archive files.
    Checks target FID first, then sibling format FIDs.
    Handles both quoted and unquoted href attributes in legacy HTML.
    """
    raw_dir = ARCHIVE_DIR / "raw"
    fids_to_check = [fid]
    if siblings:
        for s in siblings:
            s_fid = s.get("fid")
            if s_fid and s_fid not in fids_to_check:
                fids_to_check.append(s_fid)

    for check_fid in fids_to_check:
        p = raw_dir / f"{check_fid}.html"
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                m = re.findall(r'href=[\'"]?(https?://(?:www\.)?(?:movie-censorship\.com|schnittberichte\.com)/[^\'">\s]*)', content, re.I)
                if m:
                    en_urls = [u for u in m if "movie-censorship.com" in u]
                    return en_urls[0] if en_urls else m[0]
            except Exception:
                pass
    return None


def get_wikipedia_summary(clean_title: str, year: Optional[int] = None, imdb_id: Optional[str] = None, wikipedia_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Fetches lead extract, description, and thumbnail from Wikipedia's REST API.
    Caches locally in archive/metadata/wikipedia_summary/.
    """
    if not wikipedia_url and not clean_title:
        return None

    cache_key = imdb_id if (imdb_id and imdb_id.startswith("tt")) else f"wiki_sum_{abs(hash(clean_title))}"
    cache_file = WIKI_SUMMARY_DIR / f"{cache_key}.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    resolved_slug = None
    if wikipedia_url:
        m = re.search(r'/wiki/([^#?]+)', wikipedia_url)
        if m:
            resolved_slug = m.group(1)

    if not resolved_slug and clean_title:
        wiki_url = resolve_wikipedia_url(clean_title, year, imdb_id)
        if wiki_url:
            m = re.search(r'/wiki/([^#?]+)', wiki_url)
            if m:
                resolved_slug = m.group(1)

    if not resolved_slug:
        return None

    try:
        api_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{resolved_slug}"
        req = urllib.request.Request(api_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            summary = {
                "title": data.get("title") or clean_title,
                "description": data.get("description"),
                "extract": data.get("extract"),
                "thumbnail_url": data.get("thumbnail", {}).get("source"),
                "page_url": data.get("content_urls", {}).get("desktop", {}).get("page") or wikipedia_url,
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(summary, f)
            return summary
    except Exception:
        return None


def get_movie_censorship_summary(movie_censorship_url: str, clean_title: str = "") -> Optional[Dict[str, Any]]:
    """
    Fetches comparison versions, runtime differences, and intro description from Movie-Censorship report.
    Caches locally in archive/metadata/movie_censorship/.
    """
    if not movie_censorship_url:
        return None

    # Extract ID from URL
    id_m = re.search(r'ID=(\d+)', movie_censorship_url, re.I)
    cache_id = id_m.group(1) if id_m else str(abs(hash(movie_censorship_url)))
    cache_file = MC_SUMMARY_DIR / f"mc_{cache_id}.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    try:
        req = urllib.request.Request(movie_censorship_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            
            title_m = re.search(r'<title>(.*?)</title>', html, re.I)
            raw_title = title_m.group(1).strip() if title_m else f"{clean_title} Comparison Report"
            report_title = re.sub(r'\s*-\s*Movie-Censorship\.com\s*$', '', raw_title, flags=re.I).strip()

            # Find versions compared from title e.g. "Friday the 13th (Comparison: R-Rated - Unrated)"
            comp_m = re.search(r'Comparison:\s*([^)]+)', report_title, re.I)
            versions = comp_m.group(1).strip() if comp_m else "Censorship / Cut Differences"

            # Clean HTML to extract lead text
            clean_html = re.sub(r'<script.*?</script>', '', html, flags=re.DOTALL | re.I)
            clean_html = re.sub(r'<style.*?</style>', '', clean_html, flags=re.DOTALL | re.I)

            # Search for the comparison intro paragraphs
            lead_text = ""
            for p in re.findall(r'<p[^>]*>(.*?)</p>', clean_html, re.I | re.DOTALL):
                txt = re.sub(r'<[^>]+>', ' ', p).strip()
                if len(txt) > 80 and any(k in txt.lower() for k in ["unrated", "r-rated", "cut", "version", "censored", "theatrical"]):
                    lead_text = txt
                    break
            
            if not lead_text:
                # Fallback to td text
                for td in re.findall(r'<td[^>]*>(.*?)</td>', clean_html, re.I | re.DOTALL):
                    txt = re.sub(r'<[^>]+>', ' ', td).strip()
                    if len(txt) > 120 and any(k in txt.lower() for k in ["unrated", "r-rated", "theatrical version", "violence was cut"]):
                        lead_text = txt[:600]
                        break

            summary = {
                "report_title": report_title,
                "versions": versions,
                "lead_text": lead_text,
                "report_url": movie_censorship_url,
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(summary, f)
            return summary
    except Exception:
        return None


def enrich_title_external_links(title: Dict[str, Any], db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Ensures title has verified, direct external links populated and scraped metadata attached:
    - wikipedia_url & wikipedia_summary
    - movie_censorship_url & movie_censorship_summary
    - dvdbeaver_url (propagates from siblings if missing)
    - letterboxd_url
    """
    fid = title.get("fid")
    siblings = title.get("format_siblings", [])
    clean_title = title.get("clean_title") or ""
    year = title.get("year")
    imdb_id = title.get("imdb_id")

    needs_db_update = False
    mc_url = title.get("movie_censorship_url")
    wiki_url = title.get("wikipedia_url")
    beaver_url = title.get("dvdbeaver_url")

    # 1. Movie-Censorship
    if not mc_url and fid:
        mc_url = resolve_movie_censorship_url(fid, siblings)
        if mc_url:
            title["movie_censorship_url"] = mc_url
            needs_db_update = True

    # 2. Wikipedia
    if not wiki_url and clean_title:
        wiki_url = resolve_wikipedia_url(clean_title, year, imdb_id)
        if wiki_url:
            title["wikipedia_url"] = wiki_url
            needs_db_update = True

    # 3. DVDBeaver (check siblings if empty)
    if not beaver_url and siblings and db_path:
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                SELECT dvdbeaver_url FROM titles
                WHERE (
                    (imdb_id IS NOT NULL AND imdb_id != '' AND imdb_id = ?)
                    OR (clean_title = ? AND year = ?)
                ) AND dvdbeaver_url IS NOT NULL AND dvdbeaver_url != ''
                LIMIT 1
            """, (imdb_id or "", clean_title, year))
            row = c.fetchone()
            if row and row[0]:
                title["dvdbeaver_url"] = row[0]
                beaver_url = row[0]
                needs_db_update = True
            conn.close()
        except Exception:
            pass

    # 4. Save to DB if newly resolved
    if needs_db_update and fid and db_path:
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            if mc_url:
                c.execute("UPDATE titles SET movie_censorship_url = ? WHERE fid = ?", (mc_url, fid))
            if wiki_url:
                c.execute("UPDATE titles SET wikipedia_url = ? WHERE fid = ?", (wiki_url, fid))
            if beaver_url:
                c.execute("UPDATE titles SET dvdbeaver_url = ? WHERE fid = ?", (beaver_url, fid))
            conn.commit()
            conn.close()
        except Exception:
            pass

    # 5. Letterboxd direct URL
    if imdb_id and imdb_id.startswith("tt"):
        title["letterboxd_url"] = f"https://letterboxd.com/imdb/{imdb_id}/"
    elif clean_title:
        title["letterboxd_url"] = f"https://letterboxd.com/search/{urllib.parse.quote(clean_title)}/"

    # 6. Scrape and attach Wikipedia lead summary
    if wiki_url or clean_title:
        title["wikipedia_summary"] = get_wikipedia_summary(clean_title, year, imdb_id, wiki_url)

    # 7. Scrape and attach Movie-Censorship summary
    if mc_url:
        title["movie_censorship_summary"] = get_movie_censorship_summary(mc_url, clean_title)

    return title

