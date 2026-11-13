from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote_plus, urlsplit

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

from src.config import ARCHIVE_DIR, DEFAULT_USER_AGENT, RAW_DIR
from src.parser.html_parser import DVDCompareParser

EDITION_COVERS_DIR = ARCHIVE_DIR / "edition_covers"
EDITION_COVERS_DIR.mkdir(parents=True, exist_ok=True)
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_DIMENSIONS = (900, 1200)
NEAR_DUPLICATE_DHASH_DISTANCE = 2
GALLERY_DUPLICATE_DHASH_DISTANCE = 15
ALLOWED_COVER_IMAGE_HOSTS = {
    "m.media-amazon.com",
    "images-na.ssl-images-amazon.com",
    "images.amazon.com",
    "images.static-bluray.com",
}

BLURAY_SEARCH_TEMPLATE = (
    "https://www.blu-ray.com/search/?quicksearch=1&quicksearch_country=all"
    "&quicksearch_keyword={query}&section=bluraymovies"
)
BLURAY_COUNTRIES = {
    "argentina", "australia", "austria", "belgium", "brazil", "bulgaria",
    "canada", "chile", "china", "colombia", "cyprus", "czech republic",
    "denmark", "estonia", "finland", "france", "germany", "greece",
    "hong kong", "hungary", "iceland", "india", "indonesia", "ireland",
    "israel", "italy", "japan", "latvia", "lithuania", "malaysia",
    "mexico", "netherlands", "new zealand", "norway", "philippines",
    "poland", "portugal", "romania", "russia", "singapore", "south africa",
    "south korea", "spain", "sweden", "switzerland", "taiwan", "thailand",
    "turkey", "united kingdom",
}

AMAZON_DOMAINS = {
    "america": "amazon.com",
    "united states": "amazon.com",
    "canada": "amazon.ca",
    "united kingdom": "amazon.co.uk",
    "uk": "amazon.co.uk",
    "france": "amazon.fr",
    "germany": "amazon.de",
    "italy": "amazon.it",
    "spain": "amazon.es",
    "japan": "amazon.co.jp",
    "australia": "amazon.com.au",
}


def _norm(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def release_signature(release: Dict[str, Any]) -> str:
    asin = str(release.get("amazon_asin") or "").strip().upper()
    if re.fullmatch(r"[A-Z0-9]{10}", asin):
        return f"asin:{asin}"
    parts = [
        release.get("parent_format") or release.get("media_format"),
        release.get("region"),
        release.get("country"),
        release.get("distributor"),
        release.get("release_year"),
        release.get("edition_name"),
        release.get("header_raw"),
    ]
    return "release:" + "|".join(_norm(part) for part in parts)


def _amazon_domain(country: str) -> str:
    return AMAZON_DOMAINS.get(_norm(country), "amazon.com")


def _amazon_product_url(asin: str, country: str) -> str:
    return f"https://www.{_amazon_domain(country)}/dp/{asin}"


def _extract_amazon_cover_url(html_text: str) -> Optional[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    image = soup.select_one("#landingImage") or soup.select_one("#imgBlkFront")
    if image:
        hires = str(image.get("data-old-hires") or "").strip()
        if hires:
            return hires
        dynamic = str(image.get("data-a-dynamic-image") or "").strip()
        if dynamic:
            try:
                payload = json.loads(dynamic)
                if isinstance(payload, dict) and payload:
                    return max(payload.items(), key=lambda item: (item[1][0] * item[1][1]) if isinstance(item[1], list) and len(item[1]) >= 2 else 0)[0]
            except Exception:
                pass
        src = str(image.get("src") or "").strip()
        if src:
            return src
    match = re.search(r'"hiRes"\s*:\s*"(https:[^"]+)"', html_text)
    return match.group(1).replace("\\u0026", "&") if match else None


def _dhash(image: Image.Image) -> str:
    gray = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(gray.tobytes())
    value = 0
    for row in range(8):
        offset = row * 9
        for col in range(8):
            value = (value << 1) | int(pixels[offset + col] > pixels[offset + col + 1])
    return f"{value:016x}"


def dhash_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except Exception:
        return 64


def normalize_cover_image(image_bytes: bytes) -> Dict[str, Any]:
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("invalid cover image size")
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        if image.width < 120 or image.height < 160:
            raise ValueError("cover image is too small")
        image.thumbnail(MAX_DIMENSIONS, Image.Resampling.LANCZOS)
        fingerprint = _dhash(image)
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=88, method=6)
        normalized = output.getvalue()
        sha256 = hashlib.sha256(normalized).hexdigest()
        return {
            "bytes": normalized,
            "sha256": sha256,
            "dhash": fingerprint,
            "width": image.width,
            "height": image.height,
            "mime_type": "image/webp",
        }


def _download_image(url: str) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("invalid cover URL")
    hostname = (parsed.hostname or "").lower()
    if hostname not in ALLOWED_COVER_IMAGE_HOSTS:
        raise ValueError(f"untrusted cover image host: {hostname}")
    response = requests.get(
        url,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
        timeout=20,
        stream=True,
    )
    response.raise_for_status()
    content_type = str(response.headers.get("content-type") or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise ValueError("cover URL did not return an image")
    body = bytearray()
    for chunk in response.iter_content(65536):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > MAX_IMAGE_BYTES:
            raise ValueError("cover image exceeded size limit")
    return bytes(body)


def fetch_amazon_cover(asin: str, country: str) -> Dict[str, Any]:
    product_url = _amazon_product_url(asin, country)
    response = requests.get(
        product_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36 DVDRewind/2.0",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=20,
    )
    response.raise_for_status()
    image_url = _extract_amazon_cover_url(response.text)
    if not image_url:
        raise ValueError("Amazon product page did not expose a cover image")
    return {
        "source_name": "DVDCompare + Amazon",
        "source_url": product_url,
        "source_ref": asin,
        "image_url": image_url,
        "image_bytes": _download_image(image_url),
        "match_score": 0.99,
    }


def _format_key(value: Any) -> str:
    text = _norm(value)
    if "4k" in text or "uhd" in text:
        return "4k"
    if "blu" in text:
        return "bluray"
    if "dvd" in text:
        return "dvd"
    return text


def _country_key(value: Any) -> str:
    text = _norm(value)
    aliases = {
        "united states": "america",
        "usa": "america",
        "us": "america",
        "uk": "united kingdom",
    }
    return aliases.get(text, text)


def _edition_tokens(value: Any) -> set[str]:
    text = _norm(value)
    text = text.replace("digi book", "digibook").replace("5 disc", "5disc")
    stop = {
        "the", "edition", "release", "blu", "ray", "4k", "uhd", "ultra", "hd",
        "warner", "bros", "home", "video", "disc", "discs",
    }
    return {token for token in text.split() if len(token) > 1 and token not in stop}


def _bluray_release_metadata(url: str, html_text: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    title_meta = soup.find("meta", property="og:title")
    image_meta = soup.find("meta", property="og:image")
    desc_meta = soup.find("meta", attrs={"name": "description"})
    page_title = str(title_meta.get("content") if title_meta else "").strip()
    image_url = str(image_meta.get("content") if image_meta else "").strip()
    description = str(desc_meta.get("content") if desc_meta else "").strip()
    if not page_title or not image_url:
        raise ValueError("Blu-ray.com release page did not expose title/cover metadata")

    groups = re.findall(r"\(([^()]*)\)", page_title)
    country = "America"
    if groups and _norm(groups[-1]) in BLURAY_COUNTRIES:
        country = groups[-1]
        groups = groups[:-1]

    edition_parts: list[str] = []
    for group in groups:
        group = group.strip()
        if group and group not in edition_parts:
            edition_parts.append(group)
    desc_edition = re.search(r"Release Date [^(]+\(([^()]*)\)", description, re.IGNORECASE)
    if desc_edition:
        value = desc_edition.group(1).strip()
        if value and value not in edition_parts:
            edition_parts.append(value)

    release_year = None
    year_match = re.search(r"Release Date [A-Za-z]+\s+\d{1,2},\s+(\d{4})", description, re.IGNORECASE)
    if year_match:
        release_year = int(year_match.group(1))

    product_match = re.search(r"/(\d+)/?$", urlsplit(url).path)
    return {
        "source_name": "Blu-ray.com",
        "source_url": url,
        "source_ref": product_match.group(1) if product_match else url,
        "image_url": image_url,
        "format_category": "4K UHD" if "-4K-Blu-ray/" in url else "Blu-ray",
        "country": country,
        "release_year": release_year,
        "edition_text": " ".join(edition_parts).strip(),
        "page_title": page_title,
    }


def search_bluray_release_candidates(
    clean_title: str,
    movie_year: Optional[int],
    max_results: int = 16,
) -> tuple[list[Dict[str, Any]], int, int]:
    search_url = BLURAY_SEARCH_TEMPLATE.format(query=quote_plus(clean_title))
    response = requests.get(
        search_url,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        timeout=20,
    )
    response.raise_for_status()
    request_count = 1
    errors = 0
    soup = BeautifulSoup(response.text, "html.parser")
    expected = _norm(clean_title)
    seen_urls: set[str] = set()
    detail_urls: list[str] = []

    for link in soup.find_all("a", href=True, title=True):
        href = str(link.get("href") or "").strip()
        label = str(link.get("title") or "").strip()
        if "blu-ray.com/movies/" not in href or href in seen_urls:
            continue
        year_match = re.search(r"\((\d{4})\)\s*$", label)
        if movie_year is not None and (not year_match or int(year_match.group(1)) != int(movie_year)):
            continue
        core = re.sub(r"\s*\(\d{4}\)\s*$", "", label).strip()
        core = re.sub(r"\s+4K$", "", core, flags=re.IGNORECASE).strip()
        if _norm(core) != expected:
            continue
        seen_urls.add(href)
        detail_urls.append(href)
        if len(detail_urls) >= max_results:
            break

    candidates: list[Dict[str, Any]] = []
    for detail_url in detail_urls:
        try:
            detail = requests.get(
                detail_url,
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                timeout=20,
            )
            request_count += 1
            detail.raise_for_status()
            candidates.append(_bluray_release_metadata(detail_url, detail.text))
        except Exception:
            errors += 1
        time.sleep(0.12)
    return candidates, request_count, errors


def match_bluray_candidate(
    releases: Iterable[Dict[str, Any]],
    candidate: Dict[str, Any],
) -> tuple[Optional[Dict[str, Any]], float]:
    pool = []
    for release in releases:
        if _format_key(release.get("parent_format") or release.get("media_format")) != _format_key(candidate.get("format_category")):
            continue
        if _country_key(release.get("country")) != _country_key(candidate.get("country")):
            continue
        release_year = release.get("release_year")
        candidate_year = candidate.get("release_year")
        if release_year is not None and candidate_year is not None and int(release_year) != int(candidate_year):
            continue
        pool.append(release)

    if not pool:
        return None, 0.0
    if len(pool) == 1:
        return pool[0], 0.94

    candidate_tokens = _edition_tokens(candidate.get("edition_text"))
    if not candidate_tokens:
        return None, 0.0

    scored: list[tuple[float, Dict[str, Any]]] = []
    for release in pool:
        release_tokens = _edition_tokens(
            release.get("edition_name") or release.get("header_raw") or ""
        )
        if not release_tokens:
            score = 0.0
        else:
            score = len(candidate_tokens & release_tokens) / len(candidate_tokens | release_tokens)
        scored.append((score, release))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_release = scored[0]
    next_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < 0.45 or (best_score - next_score) < 0.15:
        return None, 0.0
    return best_release, round(0.92 + min(best_score, 1.0) * 0.05, 3)


def _family_title_ids(repo, title_row) -> list[int]:
    title_id = int(title_row["id"])
    imdb_id = str(title_row["imdb_id"] or "").strip()
    ids = {title_id}
    if imdb_id:
        rows = repo.conn.execute("SELECT id FROM titles WHERE is_missing = 0 AND imdb_id = ?", (imdb_id,)).fetchall()
        ids.update(int(row[0]) for row in rows)
    elif title_row["year"] is not None:
        rows = repo.conn.execute(
            "SELECT id FROM titles WHERE is_missing = 0 AND clean_title = ? AND year = ?",
            (title_row["clean_title"], title_row["year"]),
        ).fetchall()
        ids.update(int(row[0]) for row in rows)
    return sorted(ids)


def _find_near_duplicate(repo, family_title_ids: Iterable[int], dhash: str, width: int, height: int) -> Optional[Dict[str, Any]]:
    ids = list(family_title_ids)
    if not ids:
        return None
    placeholders = ",".join("?" for _ in ids)
    rows = repo.conn.execute(
        f"SELECT asset_sha256, dhash, local_path, width, height FROM edition_art WHERE title_id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    ratio = width / max(height, 1)
    for row in rows:
        if not row["dhash"]:
            continue
        other_ratio = int(row["width"] or 0) / max(int(row["height"] or 1), 1)
        if abs(ratio - other_ratio) > 0.08:
            continue
        if dhash_distance(dhash, row["dhash"]) <= NEAR_DUPLICATE_DHASH_DISTANCE:
            return dict(row)
    return None


def _write_asset(normalized: Dict[str, Any]) -> str:
    sha256 = normalized["sha256"]
    subdir = EDITION_COVERS_DIR / sha256[:2]
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{sha256}.webp"
    if not path.exists():
        path.write_bytes(normalized["bytes"])
    return f"/static/edition-covers/{sha256[:2]}/{sha256}.webp"


def _hydrate_asins_from_raw(repo, title_row) -> int:
    fid = int(title_row["fid"])
    raw_path = Path(str(title_row["raw_html_path"] or ""))
    if not raw_path.is_file():
        raw_path = RAW_DIR / f"{fid}.html"
    if not raw_path.is_file():
        return 0
    parsed = DVDCompareParser().parse(raw_path.read_bytes(), fid=fid)
    updated = 0
    with repo.conn:
        for release in parsed.get("releases", []):
            asin = str(release.get("amazon_asin") or "").strip().upper()
            if not asin:
                continue
            cur = repo.conn.execute(
                "UPDATE releases SET amazon_asin = ? WHERE title_id = ? AND release_index = ? AND (amazon_asin IS NULL OR amazon_asin = '')",
                (asin, int(title_row["id"]), int(release["release_index"])),
            )
            updated += int(cur.rowcount or 0)
    return updated


def _cover_exists(repo, title_id: int, release: Dict[str, Any]) -> bool:
    return repo.conn.execute(
        "SELECT 1 FROM edition_art WHERE title_id = ? AND release_signature = ? LIMIT 1",
        (int(title_id), release_signature(release)),
    ).fetchone() is not None


def _store_cover_source(
    repo,
    title_row,
    release: Dict[str, Any],
    source: Dict[str, Any],
    family_ids: Iterable[int],
) -> str:
    image_bytes = source.get("image_bytes")
    if not image_bytes:
        image_bytes = _download_image(str(source["image_url"]))
    normalized = normalize_cover_image(image_bytes)
    near = _find_near_duplicate(
        repo,
        family_ids,
        normalized["dhash"],
        normalized["width"],
        normalized["height"],
    )
    if near:
        asset_sha = near["asset_sha256"]
        local_path = near["local_path"]
        width = int(near["width"] or normalized["width"])
        height = int(near["height"] or normalized["height"])
        outcome = "deduped"
    else:
        asset_sha = normalized["sha256"]
        local_path = _write_asset(normalized)
        width = normalized["width"]
        height = normalized["height"]
        outcome = "fetched"

    with repo.conn:
        repo.conn.execute(
            """
            INSERT OR IGNORE INTO edition_art (
                title_id, release_signature, release_index, format_category,
                asset_sha256, dhash, local_path, width, height,
                source_name, source_url, source_ref, match_score, is_primary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                int(title_row["id"]), release_signature(release), int(release["release_index"]),
                str(title_row["format_category"] or ""), asset_sha,
                normalized["dhash"], local_path, width, height,
                source["source_name"], source["source_url"], source["source_ref"], source["match_score"],
            ),
        )
    return outcome


def harvest_title(
    repo,
    fid: int,
    max_releases: Optional[int] = None,
    include_bluray: bool = False,
) -> Dict[str, int]:
    title_row = repo.conn.execute("SELECT * FROM titles WHERE fid = ? AND is_missing = 0", (int(fid),)).fetchone()
    empty = {
        "checked": 0,
        "fetched": 0,
        "deduped": 0,
        "skipped": 0,
        "errors": 0,
        "asins_hydrated": 0,
        "network_requests": 0,
        "bluray_candidates": 0,
        "bluray_matched": 0,
    }
    if not title_row:
        return empty

    stats = dict(empty)
    stats["asins_hydrated"] = _hydrate_asins_from_raw(repo, title_row)
    family_ids = _family_title_ids(repo, title_row)
    release_rows = repo.conn.execute(
        "SELECT * FROM releases WHERE title_id = ? ORDER BY release_index",
        (int(title_row["id"]),),
    ).fetchall()
    releases = [dict(row) for row in release_rows]
    for release in releases:
        release["parent_format"] = title_row["format_category"]
    if max_releases and max_releases > 0:
        releases = releases[:max_releases]
    stats["checked"] = len(releases)

    # Tier 1: exact Amazon ASIN evidence embedded in the archived DVDCompare row.
    for release in releases:
        asin = str(release.get("amazon_asin") or "").strip().upper()
        if not asin:
            continue
        if _cover_exists(repo, int(title_row["id"]), release):
            stats["skipped"] += 1
            continue
        try:
            # Product page + product image.
            stats["network_requests"] += 2
            source = fetch_amazon_cover(asin, str(release.get("country") or ""))
            outcome = _store_cover_source(repo, title_row, release, source, family_ids)
            stats[outcome] += 1
        except Exception:
            stats["errors"] += 1

    # Tier 2: targeted Blu-ray.com matching. Keep this opt-in for targeted title
    # harvests; the full 26k-title background pass stays conservative/polite.
    if include_bluray and _format_key(title_row["format_category"]) in {"bluray", "4k"}:
        try:
            candidates, request_count, candidate_errors = search_bluray_release_candidates(
                str(title_row["clean_title"] or ""),
                int(title_row["year"]) if title_row["year"] is not None else None,
            )
            stats["network_requests"] += request_count
            stats["errors"] += candidate_errors
            stats["bluray_candidates"] = len(candidates)
            for candidate in candidates:
                remaining = [
                    release for release in releases
                    if not _cover_exists(repo, int(title_row["id"]), release)
                ]
                matched, score = match_bluray_candidate(remaining, candidate)
                if matched is None:
                    continue
                try:
                    stats["network_requests"] += 1
                    source = {
                        **candidate,
                        "match_score": score,
                        "image_bytes": _download_image(str(candidate["image_url"])),
                    }
                    outcome = _store_cover_source(repo, title_row, matched, source, family_ids)
                    stats[outcome] += 1
                    stats["bluray_matched"] += 1
                except Exception:
                    stats["errors"] += 1
        except Exception:
            stats["errors"] += 1

    return stats


def _gallery_visual_duplicate(candidate: Dict[str, Any], existing: Dict[str, Any]) -> bool:
    """Collapse repeated cover designs in the gallery without merging release records."""
    candidate_sha = str(candidate.get("asset_sha256") or "")
    existing_sha = str(existing.get("asset_sha256") or "")
    if candidate_sha and existing_sha and candidate_sha == existing_sha:
        return True

    left_hash = str(candidate.get("dhash") or "")
    right_hash = str(existing.get("dhash") or "")
    if not left_hash or not right_hash:
        return False

    left_width = int(candidate.get("width") or 0)
    left_height = int(candidate.get("height") or 0)
    right_width = int(existing.get("width") or 0)
    right_height = int(existing.get("height") or 0)
    if not all((left_width, left_height, right_width, right_height)):
        return False
    if abs((left_width / left_height) - (right_width / right_height)) > 0.08:
        return False

    distance = dhash_distance(left_hash, right_hash)
    if distance > GALLERY_DUPLICATE_DHASH_DISTANCE:
        return False

    left_tokens = _edition_tokens(candidate.get("edition_name"))
    right_tokens = _edition_tokens(existing.get("edition_name"))
    if not left_tokens or not right_tokens:
        # Without edition wording, only collapse extremely-close visual matches.
        return distance <= 4

    shared = len(left_tokens & right_tokens)
    containment = shared / max(1, min(len(left_tokens), len(right_tokens)))
    return containment >= 0.75


def attach_art_to_family(repo, title: Dict[str, Any], family_context: Dict[str, Any]) -> list[Dict[str, Any]]:
    releases = family_context.get("releases", [])
    title_ids = sorted({int(release.get("title_id") or 0) for release in releases if release.get("title_id")})
    if not title_ids:
        return []
    placeholders = ",".join("?" for _ in title_ids)
    rows = repo.conn.execute(
        f"SELECT * FROM edition_art WHERE title_id IN ({placeholders}) ORDER BY is_primary DESC, match_score DESC, id ASC",
        tuple(title_ids),
    ).fetchall()
    grouped: Dict[tuple[int, str], list[Dict[str, Any]]] = {}
    for row in rows:
        art = dict(row)
        grouped.setdefault((int(art["title_id"]), art["release_signature"]), []).append(art)

    release_lookup: Dict[tuple[int, str], Dict[str, Any]] = {}
    for release in releases:
        key = (int(release.get("title_id") or 0), release_signature(release))
        release["cover_art"] = grouped.get(key, [])
        release_lookup[key] = release

    gallery: list[Dict[str, Any]] = []
    for key, art_items in grouped.items():
        release = release_lookup.get(key)
        if not release:
            continue
        for art in art_items:
            candidate = {
                **art,
                "format": release.get("parent_format") or release.get("media_format") or art.get("format_category") or "Other",
                "release_index": release.get("original_release_index") or release.get("release_index"),
                "header_raw": release.get("header_raw") or "Edition",
                "country": release.get("country") or "",
                "distributor": release.get("distributor") or "",
                "release_year": release.get("release_year"),
                "edition_name": release.get("edition_name") or "",
            }
            if any(_gallery_visual_duplicate(candidate, existing) for existing in gallery):
                continue
            gallery.append(candidate)
    gallery.sort(key=lambda item: (int(item.get("release_year") or 9999), str(item.get("format") or ""), int(item.get("release_index") or 9999)))
    return gallery
