import json
import sqlite3
from typing import Any, Dict, List, Optional
from src.db.migrations import get_connection

class ArchiveRepository:
    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        self.conn = db_conn or get_connection()

    def close(self):
        if self.conn:
            self.conn.close()

    def save_parsed_comparison(
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
                    imdb_id, dvdbeaver_url, source_url, added_by, added_date, updated_by, updated_date,
                    source_hash, is_missing, raw_html_path, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(fid) DO UPDATE SET
                    raw_title = excluded.raw_title,
                    clean_title = excluded.clean_title,
                    aka_titles = excluded.aka_titles,
                    year = excluded.year,
                    format_category = excluded.format_category,
                    imdb_id = excluded.imdb_id,
                    dvdbeaver_url = excluded.dvdbeaver_url,
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
                        picture_format, hdr_format, tv_system, case_type, upc_number,
                        notes_raw, easter_eggs, commentaries_raw, extras_raw, subtitles_raw,
                        soundtracks_raw, contributor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    rel.get("notes_raw"),
                    rel.get("easter_eggs"),
                    rel.get("commentaries_raw"),
                    rel.get("extras_raw"),
                    rel.get("subtitles_raw"),
                    rel.get("soundtracks_raw"),
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
                    """, (release_id, e.get("extra_type"), e["title"], e.get("runtime"), e.get("source_order"), e["raw_text"]))

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

        # If user typed simple keywords, append * for prefix matching
        tokens = [t.replace('"', '') for t in clean_q.split() if t.strip()]
        fts_query = " ".join(f'"{tok}"*' for tok in tokens)

        sql = """
            SELECT 
                t.id, t.fid, t.clean_title, t.year, t.format_category, t.aka_titles,
                r.overall_winner,
                (SELECT COUNT(*) FROM releases rel WHERE rel.title_id = t.id) as release_count
            FROM titles_fts fts
            JOIN titles t ON t.id = fts.title_id
            LEFT JOIN recommendations r ON r.title_id = t.id
            WHERE titles_fts MATCH ?
            ORDER BY bm25(titles_fts)
            LIMIT ?;
        """
        rows = self.conn.execute(sql, (fts_query, limit)).fetchall()
        results = []
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
        return results

    def get_title_detail(self, fid: int) -> Optional[Dict[str, Any]]:
        """
        Returns full structured title details including all releases, tracks, extras,
        cuts, recommendations, and update logs.
        """
        title_row = self.conn.execute("SELECT * FROM titles WHERE fid = ?", (fid,)).fetchone()
        if not title_row:
            return None

        title_id = title_row["id"]
        title_data = dict(title_row)
        title_data["aka_titles"] = json.loads(title_data["aka_titles"]) if title_data["aka_titles"] else []

        # Recommendation
        rec_row = self.conn.execute("SELECT * FROM recommendations WHERE title_id = ?", (title_id,)).fetchone()
        title_data["recommendation"] = dict(rec_row) if rec_row else None

        # Cuts
        cuts_rows = self.conn.execute("SELECT * FROM cuts WHERE title_id = ?", (title_id,)).fetchall()
        title_data["cuts"] = [dict(r) for r in cuts_rows]

        # Update log
        log_rows = self.conn.execute("SELECT * FROM update_log WHERE title_id = ? ORDER BY id ASC", (title_id,)).fetchall()
        title_data["update_log"] = [dict(r) for r in log_rows]

        # Releases
        rel_rows = self.conn.execute("SELECT * FROM releases WHERE title_id = ? ORDER BY release_index ASC", (title_id,)).fetchall()
        releases = []
        for r in rel_rows:
            rel_id = r["id"]
            rel_dict = dict(r)

            # Audio
            a_rows = self.conn.execute("SELECT * FROM audio_tracks WHERE release_id = ?", (rel_id,)).fetchall()
            rel_dict["audio_tracks"] = [dict(a) for a in a_rows]

            # Subtitles
            s_rows = self.conn.execute("SELECT * FROM subtitle_tracks WHERE release_id = ?", (rel_id,)).fetchall()
            rel_dict["subtitle_tracks"] = [dict(s) for s in s_rows]

            # Extras
            e_rows = self.conn.execute("SELECT * FROM extras WHERE release_id = ? ORDER BY source_order ASC", (rel_id,)).fetchall()
            rel_dict["extras"] = [dict(e) for e in e_rows]

            releases.append(rel_dict)

        title_data["releases"] = releases

        # Find sibling formats (grouping by clean_title and year or IMDb ID)
        sibling_sql = """
            SELECT t.fid, t.clean_title, t.format_category, t.year,
                   (SELECT COUNT(*) FROM releases r WHERE r.title_id = t.id) as release_count
            FROM titles t
            WHERE t.id != ? AND t.is_missing = 0 AND (
                (t.imdb_id IS NOT NULL AND t.imdb_id = ?)
                OR (t.clean_title = ? AND t.year = ?)
            )
            ORDER BY t.format_category ASC;
        """
        siblings = self.conn.execute(sibling_sql, (
            title_id,
            title_data.get("imdb_id") or "",
            title_data["clean_title"],
            title_data.get("year")
        )).fetchall()
        title_data["format_siblings"] = [dict(s) for s in siblings]

        return title_data
