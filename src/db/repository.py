import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional
from src.db.migrations import get_connection

# Bumped after every archive write made through this process. Web page caches
# key on it, so they survive unrelated writes (like a resolved Wikipedia link)
# and are dropped once titles, releases, posters or IMDb IDs change.
# A sync saves a title every few seconds for hours, so imported titles publish
# a new generation at most once a minute; edits a person makes (poster, IMDb
# ID) and finished tasks publish at once.
GENERATION_PUBLISH_SECONDS = 60
_DATA_GENERATION = 0
_PUBLISHED_GENERATION = 0
_PUBLISHED_AT = 0.0


def data_generation() -> int:
    global _PUBLISHED_GENERATION, _PUBLISHED_AT
    if (
        _PUBLISHED_GENERATION != _DATA_GENERATION
        and time.monotonic() - _PUBLISHED_AT >= GENERATION_PUBLISH_SECONDS
    ):
        _PUBLISHED_GENERATION, _PUBLISHED_AT = _DATA_GENERATION, time.monotonic()
    return _PUBLISHED_GENERATION


def bump_data_generation(immediate: bool = False) -> None:
    global _DATA_GENERATION, _PUBLISHED_GENERATION, _PUBLISHED_AT
    _DATA_GENERATION += 1
    if immediate:
        _PUBLISHED_GENERATION, _PUBLISHED_AT = _DATA_GENERATION, time.monotonic()

def format_display_title(clean_title: str) -> str:
    if not clean_title:
        return ""
    m = re.match(r"^(.*?)(?:,\s*|\s+\()(The|A|An)\)?$", clean_title.strip(), re.IGNORECASE)
    if m:
        return f"{m.group(2)} {m.group(1)}".strip()
    return clean_title.strip()

def deduplicate_text(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    half = len(t) // 2
    for offset in range(-25, 26):
        mid = half + offset
        if 0 < mid < len(t):
            left = t[:mid].strip()
            right = t[mid:].strip()
            if left == right:
                return left
    return t

class ArchiveRepository:
    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.conn = db_conn or get_connection()

    def close(self):
        if self.conn:
            self.conn.close()

    def save_parsed_comparison(self, *args, **kwargs) -> int:
        try:
            return self._save_parsed_comparison(*args, **kwargs)
        finally:
            bump_data_generation()

    def _save_parsed_comparison(
        self,
        data: Dict[str, Any],
        source_hash: str,
        raw_html_path: Optional[str] = None,
        source_url: Optional[str] = None
    ) -> int:
        """
        Idempotently inserts or updates a parsed comparison into SQLite,
        including all child releases, tracks, extras, cuts, recommendations,
        and synchronizes the FTS5 search index.
        """
        fid = data["fid"]
        is_missing = 1 if data.get("is_missing") else 0
        url = source_url or f"https://www.dvdcompare.net/comparisons/film.php?fid={fid}"

        with self.conn:
            # 1. Upsert into titles
            self.conn.execute("""
                INSERT INTO titles (
                    fid, raw_title, clean_title, aka_titles, year, format_category,
                    imdb_id, dvdbeaver_url, director, synopsis, source_url, added_by, added_date, updated_by, updated_date,
                    source_hash, is_missing, raw_html_path, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(fid) DO UPDATE SET
                    raw_title = excluded.raw_title,
                    clean_title = excluded.clean_title,
                    aka_titles = excluded.aka_titles,
                    year = excluded.year,
                    format_category = excluded.format_category,
                    imdb_id = excluded.imdb_id,
                    dvdbeaver_url = excluded.dvdbeaver_url,
                    director = excluded.director,
                    synopsis = excluded.synopsis,
                    source_url = excluded.source_url,
                    added_by = excluded.added_by,
                    added_date = excluded.added_date,
                    updated_by = excluded.updated_by,
                    updated_date = excluded.updated_date,
                    source_hash = excluded.source_hash,
                    is_missing = excluded.is_missing,
                    raw_html_path = excluded.raw_html_path,
                    scraped_at = CURRENT_TIMESTAMP;
            """, (
                fid,
                data["raw_title"],
                data["clean_title"],
                json.dumps(data.get("aka_titles", [])),
                data.get("year"),
                data.get("format_category", "Unknown"),
                data.get("imdb_id"),
                data.get("dvdbeaver_url"),
                data.get("director"),
                data.get("synopsis"),
                url,
                data.get("added_by"),
                data.get("added_date"),
                data.get("updated_by"),
                data.get("updated_date"),
                source_hash,
                is_missing,
                raw_html_path,
            ))

            title_row = self.conn.execute("SELECT id FROM titles WHERE fid = ?", (fid,)).fetchone()
            title_id = title_row[0]

            # 2. Clear old children (cascading deletes will handle nested tracks/extras)
            self.conn.execute("DELETE FROM releases WHERE title_id = ?", (title_id,))
            self.conn.execute("DELETE FROM cuts WHERE title_id = ?", (title_id,))
            self.conn.execute("DELETE FROM recommendations WHERE title_id = ?", (title_id,))
            self.conn.execute("DELETE FROM update_log WHERE title_id = ?", (title_id,))
            self.conn.execute("DELETE FROM titles_fts WHERE title_id = ?", (title_id,))

            if is_missing:
                # Update manifest and return early for missing IDs
                self.conn.execute("""
                    INSERT INTO crawl_manifest (fid, status, content_sha256)
                    VALUES (?, 'missing', ?)
                    ON CONFLICT(fid) DO UPDATE SET status='missing', content_sha256=excluded.content_sha256;
                """, (fid, source_hash))
                return title_id

            # 3. Insert Releases
            distributors_set = set()
            countries_set = set()

            for rel in data.get("releases", []):
                dist = rel.get("distributor")
                if dist:
                    distributors_set.add(dist)
                ctry = rel.get("country")
                if ctry:
                    countries_set.add(ctry)

                cur = self.conn.execute("""
                    INSERT INTO releases (
                        title_id, release_index, header_raw, media_format, region,
                        country, distributor, release_year, edition_name, aspect_ratio,
                        picture_format, hdr_format, tv_system, case_type, upc_number, amazon_asin,
                        notes_raw, easter_eggs, commentaries_raw, extras_raw, subtitles_raw,
                        soundtracks_raw, contributor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    title_id,
                    rel["release_index"],
                    rel["header_raw"],
                    rel.get("media_format"),
                    rel.get("region"),
                    rel.get("country"),
                    rel.get("distributor"),
                    rel.get("release_year"),
                    rel.get("edition_name"),
                    rel.get("aspect_ratio"),
                    rel.get("picture_format"),
                    rel.get("hdr_format"),
                    rel.get("tv_system"),
                    rel.get("case_type"),
                    rel.get("upc_number"),
                    rel.get("amazon_asin"),
                    rel.get("notes_raw"),
                    rel.get("easter_eggs"),
                    rel.get("commentaries_raw"),
                    # extras_raw/soundtracks_raw are parsed into the extras and
                    # audio_tracks tables and never read back (~130 MB);
                    # archive/raw keeps the source HTML if they're ever needed.
                    None,
                    rel.get("subtitles_raw"),
                    None,
                    rel.get("contributor"),
                ))
                release_id = cur.lastrowid

                # Insert audio tracks
                for a in rel.get("audio_tracks", []):
                    self.conn.execute("""
                        INSERT INTO audio_tracks (release_id, disc_or_version, language, codec, channels, raw_text)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (release_id, a.get("disc_or_version"), a.get("language"), a.get("codec"), a.get("channels"), a["raw_text"]))

                # Insert subtitle tracks
                for s in rel.get("subtitle_tracks", []):
                    self.conn.execute("""
                        INSERT INTO subtitle_tracks (release_id, disc_or_version, language, is_hoh, is_forced, raw_text)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (release_id, s.get("disc_or_version"), s.get("language"), 1 if s.get("is_hoh") else 0, 1 if s.get("is_forced") else 0, s["raw_text"]))

                # Insert extras
                for e in rel.get("extras", []):
                    self.conn.execute("""
                        INSERT INTO extras (release_id, extra_type, title, runtime, source_order, raw_text)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (release_id, e.get("extra_type"), e["title"], e.get("runtime"), e.get("source_order"),
                          # Parsed extras repeat the title as raw_text; store it once.
                          "" if e["raw_text"] == e["title"] else e["raw_text"]))

            # 4. Insert Cuts
            for c in data.get("cuts", []):
                self.conn.execute("""
                    INSERT INTO cuts (title_id, release_label, cut_status, description, runtime_diff, raw_text)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (title_id, c.get("release_label"), c.get("cut_status"), c.get("description"), c.get("runtime_diff"), c["raw_text"]))

            # 5. Insert Recommendation
            rec = data.get("recommendation")
            winner_str = ""
            rec_text = ""
            if rec:
                winner_str = rec.get("overall_winner") or ""
                rec_text = rec.get("recommendation_text") or ""
                self.conn.execute("""
                    INSERT INTO recommendations (title_id, overall_winner, recommendation_text, raw_text)
                    VALUES (?, ?, ?, ?)
                """, (title_id, winner_str, rec_text, rec.get("raw_text", "")))

            # 6. Insert Update Log
            for u in data.get("update_log", []):
                self.conn.execute("""
                    INSERT INTO update_log (title_id, entry_date, entry_text)
                    VALUES (?, ?, ?)
                """, (title_id, u.get("entry_date"), u["entry_text"]))

            # 7. Populate FTS5 Index
            akas_str = " ".join(data.get("aka_titles", []))
            distributors_str = " ".join(distributors_set)
            countries_str = " ".join(countries_set)

            self.conn.execute("""
                INSERT INTO titles_fts (
                    title_id, fid, clean_title, aka_titles, year, format_category,
                    distributors, countries, winner_summary, notes_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                title_id,
                fid,
                data["clean_title"],
                akas_str,
                str(data.get("year") or ""),
                data.get("format_category", ""),
                distributors_str,
                countries_str,
                f"{winner_str} {rec_text}".strip(),
                "",
            ))

            # 8. Update Crawl Manifest
            self.conn.execute("""
                INSERT INTO crawl_manifest (fid, status, content_sha256)
                VALUES (?, 'imported', ?)
                ON CONFLICT(fid) DO UPDATE SET status='imported', content_sha256=excluded.content_sha256;
            """, (fid, source_hash))

            return title_id

    def search_fts(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Executes an instant FTS5 match query across titles, AKAs, distributors, etc.
        """
        clean_q = query.strip()
        if not clean_q:
            return []

        # Prefix-match the word still being typed and any longer word. Short
        # finished words ("the", "of", "ii") match exactly: "the"* expands to
        # every title word starting with "the" and dominated query time.
        tokens = [t.replace('"', '') for t in clean_q.split() if t.strip()]
        if not tokens:
            return []
        if len(tokens) == 1 and len(tokens[0]) <= 3:
            # A lone short word ("the", "a") matches most of the archive and
            # ranking it all took 1-10 s. While typing, titles that start with
            # it are both faster and what the person is after.
            fts_query = f'clean_title : ^ "{tokens[0]}" *'
        else:
            fts_query = " ".join(
                f'"{tok}"*' if i == len(tokens) - 1 or len(tok) > 3 else f'"{tok}"'
                for i, tok in enumerate(tokens)
            )

        # Rank and limit inside FTS first, then join only the kept rows. Joining
        # before the LIMIT looked up every match ("the" matches 61k titles).
        sql = """
            WITH hits AS (
                SELECT title_id, rank FROM titles_fts
                WHERE titles_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            )
            SELECT
                t.id, t.fid, t.clean_title, t.year, t.format_category, t.aka_titles, t.poster_url,
                r.overall_winner,
                (SELECT COUNT(*) FROM releases rel WHERE rel.title_id = t.id) as release_count
            FROM hits
            JOIN titles t ON t.id = hits.title_id
            LEFT JOIN recommendations r ON r.title_id = t.id
            ORDER BY hits.rank;
        """
        rows = self.conn.execute(sql, (fts_query, limit)).fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "fid": r["fid"],
                "clean_title": format_display_title(r["clean_title"]),
                "raw_clean_title": r["clean_title"],
                "year": r["year"],
                "format_category": r["format_category"],
                "aka_titles": json.loads(r["aka_titles"]) if r["aka_titles"] else [],
                "poster_url": r["poster_url"],
                "overall_winner": r["overall_winner"],
                "release_count": r["release_count"],
            })
        return results

    _RELEASE_COLUMNS: Optional[str] = None
    # Never displayed; skip reading them for every film page.
    _UNREAD_RELEASE_COLUMNS = {"extras_raw", "soundtracks_raw"}

    def _release_columns(self) -> str:
        if ArchiveRepository._RELEASE_COLUMNS is None:
            names = [row[1] for row in self.conn.execute("PRAGMA table_info(releases)")]
            ArchiveRepository._RELEASE_COLUMNS = ", ".join(n for n in names if n not in self._UNREAD_RELEASE_COLUMNS)
        return ArchiveRepository._RELEASE_COLUMNS

    def _load_releases_with_children(self, title_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
        """Load releases and all child rows in batches instead of N+1 queries."""
        if not title_ids:
            return {}

        placeholders = ",".join("?" for _ in title_ids)
        rel_rows = self.conn.execute(
            f"SELECT {self._release_columns()} FROM releases WHERE title_id IN ({placeholders}) ORDER BY title_id, release_index ASC",
            tuple(title_ids),
        ).fetchall()
        if not rel_rows:
            return {title_id: [] for title_id in title_ids}

        release_ids = [int(row["id"]) for row in rel_rows]
        release_placeholders = ",".join("?" for _ in release_ids)
        release_params = tuple(release_ids)

        children = {
            "audio_tracks": {},
            "subtitle_tracks": {},
            "extras": {},
        }
        for table, key, order_by in (
            ("audio_tracks", "audio_tracks", "release_id, id"),
            ("subtitle_tracks", "subtitle_tracks", "release_id, id"),
            ("extras", "extras", "release_id, source_order, id"),
        ):
            rows = self.conn.execute(
                f"SELECT * FROM {table} WHERE release_id IN ({release_placeholders}) ORDER BY {order_by}",
                release_params,
            ).fetchall()
            grouped: Dict[int, List[Dict[str, Any]]] = {}
            for row in rows:
                item = dict(row)
                if table == "extras" and not item["raw_text"]:
                    item["raw_text"] = item["title"]
                grouped.setdefault(int(row["release_id"]), []).append(item)
            children[key] = grouped

        grouped_releases: Dict[int, List[Dict[str, Any]]] = {title_id: [] for title_id in title_ids}
        for row in rel_rows:
            release = dict(row)
            release_id = int(release["id"])
            release["audio_tracks"] = children["audio_tracks"].get(release_id, [])
            release["subtitle_tracks"] = children["subtitle_tracks"].get(release_id, [])
            release["extras"] = children["extras"].get(release_id, [])
            grouped_releases.setdefault(int(release["title_id"]), []).append(release)
        return grouped_releases

    def get_title_detail(self, fid: int, include_all_formats: bool = False) -> Optional[Dict[str, Any]]:
        """
        Returns full structured title details including all releases, tracks, extras,
        cuts, recommendations, and update logs. When include_all_formats=True,
        merges all releases and cuts from sibling format titles (4K, Blu-ray, DVD).
        """
        title_row = self.conn.execute("SELECT * FROM titles WHERE fid = ?", (fid,)).fetchone()
        if not title_row:
            return None

        title_id = title_row["id"]
        title_data = dict(title_row)
        title_data["aka_titles"] = json.loads(title_data["aka_titles"]) if title_data["aka_titles"] else []
        title_data["display_title"] = format_display_title(title_data["clean_title"])

        # Find sibling formats using separate indexed lookups. A single OR across
        # IMDb and title/year caused SQLite to scan most of the titles table on
        # NAS storage, turning every film page into a 1–2 second request.
        sibling_select = """
            SELECT t.id, t.fid, t.clean_title, t.format_category, t.year,
                   (SELECT COUNT(*) FROM releases r WHERE r.title_id = t.id) as release_count
            FROM titles t
        """
        sibling_map: Dict[int, Dict[str, Any]] = {}
        imdb_id = title_data.get("imdb_id") or ""
        if imdb_id:
            rows = self.conn.execute(
                sibling_select + " WHERE t.id != ? AND t.is_missing = 0 AND t.imdb_id = ?",
                (title_id, imdb_id),
            ).fetchall()
            sibling_map.update({int(row["id"]): dict(row) for row in rows})

        if title_data.get("year") is not None:
            rows = self.conn.execute(
                sibling_select + " WHERE t.id != ? AND t.is_missing = 0 AND t.clean_title = ? AND t.year = ?",
                (title_id, title_data["clean_title"], title_data["year"]),
            ).fetchall()
            sibling_map.update({int(row["id"]): dict(row) for row in rows})

        siblings = sorted(sibling_map.values(), key=lambda row: row.get("format_category") or "")
        title_data["format_siblings"] = siblings

        # Recommendation
        rec_row = self.conn.execute("SELECT * FROM recommendations WHERE title_id = ?", (title_id,)).fetchone()
        if rec_row:
            rec_dict = dict(rec_row)
            if rec_dict.get("recommendation_text"):
                rec_dict["recommendation_text"] = deduplicate_text(rec_dict["recommendation_text"])
            title_data["recommendation"] = rec_dict
        else:
            title_data["recommendation"] = None

        # Update log
        log_rows = self.conn.execute("SELECT * FROM update_log WHERE title_id = ? ORDER BY id ASC", (title_id,)).fetchall()
        title_data["update_log"] = [dict(r) for r in log_rows]

        def _fmt_order(cat_str: str) -> int:
            c = (cat_str or "").lower()
            if "4k" in c or "uhd" in c:
                return 1
            elif "blu" in c:
                return 2
            elif "dvd" in c:
                return 3
            return 4

        if include_all_formats:
            title_data["is_all_formats"] = True
            all_format_titles = [
                {"id": title_id, "fid": fid, "format_category": title_data.get("format_category", "")}
            ] + [dict(s) for s in siblings]
            all_format_titles.sort(key=lambda x: _fmt_order(x.get("format_category", "")))

            merged_releases = []
            global_index = 1
            cuts_all = []
            cuts_seen = set()
            recs_all = []

            releases_by_title = self._load_releases_with_children([int(item["id"]) for item in all_format_titles])

            for t_item in all_format_titles:
                t_id = int(t_item["id"])
                t_fid = t_item["fid"]
                t_fmt = t_item.get("format_category", "")

                for rel_dict in releases_by_title.get(t_id, []):
                    rel_dict["parent_fid"] = t_fid
                    rel_dict["parent_format"] = t_fmt
                    rel_dict["original_release_index"] = rel_dict["release_index"]
                    rel_dict["release_index"] = global_index
                    global_index += 1
                    merged_releases.append(rel_dict)

                # Cuts
                c_rows = self.conn.execute("SELECT * FROM cuts WHERE title_id = ?", (t_id,)).fetchall()
                for c in c_rows:
                    c_dict = dict(c)
                    key = (c_dict.get("cut_status", "").lower(), c_dict.get("runtime_diff", ""), (c_dict.get("description", "") or "").strip().lower())
                    if key not in cuts_seen:
                        cuts_seen.add(key)
                        cuts_all.append(c_dict)

                # Recommendations
                rc_row = self.conn.execute("SELECT * FROM recommendations WHERE title_id = ?", (t_id,)).fetchone()
                if rc_row:
                    rc_dict = dict(rc_row)
                    if rc_dict.get("overall_winner"):
                        rc_dict["format_category"] = t_fmt
                        recs_all.append(rc_dict)

            title_data["releases"] = merged_releases
            title_data["cuts"] = cuts_all
            title_data["all_recommendations"] = recs_all
        else:
            title_data["is_all_formats"] = False
            # Cuts
            cuts_rows = self.conn.execute("SELECT * FROM cuts WHERE title_id = ?", (title_id,)).fetchall()
            title_data["cuts"] = [dict(r) for r in cuts_rows]

            # Releases + audio/subtitle/extra children in four total queries.
            releases = self._load_releases_with_children([int(title_id)]).get(int(title_id), [])
            for rel_dict in releases:
                rel_dict["parent_fid"] = fid
                rel_dict["parent_format"] = title_data.get("format_category", "")
            title_data["releases"] = releases

        return title_data

    def get_collector_family_context(self, title_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        """Load the physical-format family used by collector-facing intelligence.

        The normal movie view stays scoped to its current format. This helper reuses
        those already-loaded releases and batch-loads only sibling formats, while
        cuts and recommendations are fetched in one query each with parent-format
        provenance attached.
        """
        current_id = int(title_data["id"])
        current_fid = int(title_data["fid"])
        current_format = title_data.get("format_category", "")
        family_titles = [
            {"id": current_id, "fid": current_fid, "format_category": current_format}
        ] + [dict(sibling) for sibling in title_data.get("format_siblings", [])]

        # Protect against an IMDb/title sibling lookup ever returning the same row
        # through more than one path.
        unique_titles: Dict[int, Dict[str, Any]] = {}
        for item in family_titles:
            unique_titles[int(item["id"])] = item
        family_titles = list(unique_titles.values())
        title_by_id = {int(item["id"]): item for item in family_titles}

        releases: List[Dict[str, Any]] = []
        if title_data.get("is_all_formats"):
            releases = [dict(release) for release in title_data.get("releases", [])]
        else:
            for release in title_data.get("releases", []):
                release_copy = dict(release)
                release_copy.setdefault("parent_fid", current_fid)
                release_copy.setdefault("parent_format", current_format)
                release_copy.setdefault("original_release_index", release_copy.get("release_index"))
                releases.append(release_copy)

            sibling_ids = [title_id for title_id in title_by_id if title_id != current_id]
            sibling_releases = self._load_releases_with_children(sibling_ids)
            for title_id in sibling_ids:
                parent = title_by_id[title_id]
                for release in sibling_releases.get(title_id, []):
                    release_copy = dict(release)
                    release_copy["parent_fid"] = parent["fid"]
                    release_copy["parent_format"] = parent.get("format_category", "")
                    release_copy["original_release_index"] = release_copy.get("release_index")
                    releases.append(release_copy)

        title_ids = list(title_by_id)
        placeholders = ",".join("?" for _ in title_ids)
        params = tuple(title_ids)

        cuts: List[Dict[str, Any]] = []
        recommendations: List[Dict[str, Any]] = []
        if title_ids:
            cut_rows = self.conn.execute(
                f"""
                SELECT c.*, t.fid AS parent_fid, t.format_category AS parent_format
                FROM cuts c
                JOIN titles t ON t.id = c.title_id
                WHERE c.title_id IN ({placeholders})
                ORDER BY c.title_id, c.id
                """,
                params,
            ).fetchall()
            cuts = [dict(row) for row in cut_rows]

            recommendation_rows = self.conn.execute(
                f"""
                SELECT r.*, t.fid AS parent_fid, t.format_category AS parent_format
                FROM recommendations r
                JOIN titles t ON t.id = r.title_id
                WHERE r.title_id IN ({placeholders})
                ORDER BY r.title_id
                """,
                params,
            ).fetchall()
            recommendations = [dict(row) for row in recommendation_rows]

        return {
            "releases": releases,
            "cuts": cuts,
            "recommendations": recommendations,
        }

    def update_poster_url(self, fid: int, poster_url: str) -> None:
        """Updates the cached poster URL for a title and all sibling formats with the same IMDb ID."""
        with self.conn:
            row = self.conn.execute("SELECT id, imdb_id, clean_title, year FROM titles WHERE fid = ?", (fid,)).fetchone()
            if not row:
                return
            _title_id, imdb_id, clean_title, year = row["id"], row["imdb_id"], row["clean_title"], row["year"]

            if imdb_id:
                self.conn.execute("UPDATE titles SET poster_url = ? WHERE imdb_id = ?", (poster_url, imdb_id))
            elif year is not None:
                self.conn.execute("UPDATE titles SET poster_url = ? WHERE clean_title = ? AND year = ?", (poster_url, clean_title, year))
            else:
                self.conn.execute("UPDATE titles SET poster_url = ? WHERE fid = ?", (poster_url, fid))
        bump_data_generation(immediate=True)

    def update_imdb_id(self, fid: int, imdb_id: str) -> int:
        """Attach a verified IMDb ID and propagate it to same-title/year format siblings."""
        imdb_id = (imdb_id or "").strip()
        if not imdb_id.startswith("tt"):
            raise ValueError("IMDb ID must start with 'tt'")

        with self.conn:
            row = self.conn.execute(
                "SELECT clean_title, year FROM titles WHERE fid = ? AND is_missing = 0",
                (fid,),
            ).fetchone()
            if not row:
                return 0

            clean_title, year = row[0], row[1]
            if year is None:
                cur = self.conn.execute(
                    "UPDATE titles SET imdb_id = ? WHERE fid = ? AND (imdb_id IS NULL OR TRIM(imdb_id) = '')",
                    (imdb_id, fid),
                )
            else:
                cur = self.conn.execute(
                    "UPDATE titles SET imdb_id = ? "
                    "WHERE is_missing = 0 AND (fid = ? OR (clean_title = ? AND year = ?)) "
                    "AND (imdb_id IS NULL OR TRIM(imdb_id) = '')",
                    (imdb_id, fid, clean_title, year),
                )
        bump_data_generation(immediate=True)
        return int(cur.rowcount or 0)
