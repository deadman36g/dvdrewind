-- DVDRewind Normalized SQLite Schema with FTS5

PRAGMA foreign_keys = ON;

-- 1. Titles
CREATE TABLE IF NOT EXISTS titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fid INTEGER UNIQUE NOT NULL,
    raw_title TEXT NOT NULL,
    clean_title TEXT NOT NULL,
    aka_titles TEXT, -- JSON array of strings
    year INTEGER,
    format_category TEXT NOT NULL, -- e.g. '4K UHD', 'Blu-ray', 'DVD', 'HD DVD'
    imdb_id TEXT, -- e.g. 'tt0083658'
    source_url TEXT NOT NULL,
    added_by TEXT,
    added_date TEXT,
    updated_by TEXT,
    updated_date TEXT,
    scraped_at TEXT,
    source_hash TEXT NOT NULL,
    is_missing BOOLEAN NOT NULL DEFAULT 0,
    raw_html_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_titles_fid ON titles(fid);
CREATE INDEX IF NOT EXISTS idx_titles_clean_title ON titles(clean_title);
CREATE INDEX IF NOT EXISTS idx_titles_year ON titles(year);
CREATE INDEX IF NOT EXISTS idx_titles_format ON titles(format_category);
CREATE INDEX IF NOT EXISTS idx_titles_imdb ON titles(imdb_id);

-- 2. Releases
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    release_index INTEGER NOT NULL,
    header_raw TEXT NOT NULL,
    media_format TEXT, -- e.g. 'Blu-ray', 'DVD', '4K UHD'
    region TEXT,       -- e.g. 'ALL', 'A', 'B', 'R0', 'R1', 'R2'
    country TEXT,      -- e.g. 'America', 'United Kingdom', 'Germany'
    distributor TEXT,  -- e.g. 'Warner Home Video', 'Criterion'
    release_year INTEGER,
    edition_name TEXT, -- e.g. 'The Final Cut', "Collector's Edition"
    aspect_ratio TEXT,
    picture_format TEXT,
    hdr_format TEXT,
    tv_system TEXT,
    case_type TEXT,
    upc_number TEXT,
    notes_raw TEXT,
    easter_eggs TEXT,
    commentaries_raw TEXT,
    extras_raw TEXT,
    subtitles_raw TEXT,
    soundtracks_raw TEXT,
    contributor TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_releases_title_id ON releases(title_id);
CREATE INDEX IF NOT EXISTS idx_releases_country ON releases(country);
CREATE INDEX IF NOT EXISTS idx_releases_distributor ON releases(distributor);
CREATE INDEX IF NOT EXISTS idx_releases_region ON releases(region);

-- 3. Audio Tracks
CREATE TABLE IF NOT EXISTS audio_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    disc_or_version TEXT,
    language TEXT,
    codec TEXT,
    channels TEXT,
    raw_text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audio_release ON audio_tracks(release_id);

-- 4. Subtitle Tracks
CREATE TABLE IF NOT EXISTS subtitle_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    disc_or_version TEXT,
    language TEXT,
    is_hoh BOOLEAN DEFAULT 0,
    is_forced BOOLEAN DEFAULT 0,
    raw_text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subtitles_release ON subtitle_tracks(release_id);

-- 5. Extras
CREATE TABLE IF NOT EXISTS extras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    extra_type TEXT, -- e.g. 'commentary', 'featurette', 'trailer', 'extra'
    title TEXT NOT NULL,
    runtime TEXT,
    source_order INTEGER,
    raw_text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_extras_release ON extras(release_id);

-- 6. Cuts
CREATE TABLE IF NOT EXISTS cuts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    release_id INTEGER REFERENCES releases(id) ON DELETE SET NULL,
    release_label TEXT,
    cut_status TEXT, -- 'Uncut', 'Cut', 'Censored', 'Unknown'
    description TEXT,
    runtime_diff TEXT,
    raw_text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cuts_title ON cuts(title_id);

-- 7. Recommendations
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
    overall_winner TEXT,
    recommendation_text TEXT,
    raw_text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recs_title ON recommendations(title_id);

-- 8. Update Log
CREATE TABLE IF NOT EXISTS update_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
    entry_date TEXT,
    entry_text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_update_log_title ON update_log(title_id);

-- 9. Crawl Manifest
CREATE TABLE IF NOT EXISTS crawl_manifest (
    fid INTEGER PRIMARY KEY,
    status TEXT NOT NULL, -- 'queued', 'fetched', 'parsed', 'imported', 'unchanged', 'missing', 'failed'
    fetch_timestamp TEXT,
    http_status INTEGER,
    content_sha256 TEXT,
    retry_count INTEGER DEFAULT 0,
    error_message TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 10. FTS5 Virtual Search Table
CREATE VIRTUAL TABLE IF NOT EXISTS titles_fts USING fts5(
    title_id UNINDEXED,
    fid UNINDEXED,
    clean_title,
    aka_titles,
    year,
    format_category,
    distributors,
    countries,
    winner_summary,
    notes_summary,
    tokenize='unicode61 remove_diacritics 2'
);

-- Sync trigger to remove FTS entries on title deletion
CREATE TRIGGER IF NOT EXISTS titles_fts_delete_trg AFTER DELETE ON titles BEGIN
    DELETE FROM titles_fts WHERE title_id = old.id;
END;
