# Data Model

The DVDRewind database schema is designed to balance normalized relational structures with raw source fidelity.

---

## Entity Relationship Diagram (Conceptual)

```
       ┌───────────────────────────────┐
       │             titles            │
       │───────────────────────────────│
       │ id (PK)                       │
       │ fid (UNIQUE)                  │
       │ clean_title, year, format     │
       │ imdb_id, source_hash          │
       └───────┬───────────────┬───────┘
               │ 1           1 │
               │               │
        1..N   ▼          1..1 ▼
  ┌──────────────┐     ┌─────────────────────┐
  │   releases   │     │   recommendations   │
  │──────────────│     │─────────────────────│
  │ id (PK)      │     │ id (PK)             │
  │ title_id(FK) │     │ title_id (FK)       │
  │ format,region│     │ overall_winner      │
  │ country,dist │     │ recommendation_text │
  └──────┬───────┘     └─────────────────────┘
         │ 1
         ├──────────────────┬─────────────────┐
    1..N ▼             1..N ▼            1..N ▼
┌──────────────┐   ┌───────────────┐   ┌───────────────┐
│ audio_tracks │   │subtitle_tracks│   │    extras     │
└──────────────┘   └───────────────┘   └───────────────┘

       ┌───────────────┐       ┌───────────────┐
       │     cuts      │       │  update_log   │
       │ (title_id FK) │       │ (title_id FK) │
       └───────────────┘       └───────────────┘
```

---

## Table Definitions

### 1. `titles`
Primary record for a DVDCompare comparison page.
- `id`: Internal integer primary key.
- `fid`: DVDCompare numeric film ID (`dvdcompare.net/comparisons/film.php?fid=<fid>`).
- `raw_title`: Unmodified string from the source `<h2>` tag.
- `clean_title`: Normalized primary movie title (e.g. `Blade Runner`).
- `aka_titles`: JSON array of alternate/international titles (e.g. `["Zombie Flesh Eaters", "Zombi 2"]`).
- `year`: Film release year (integer).
- `format_category`: Format of the comparison (`4K UHD`, `Blu-ray`, `DVD`, `HD DVD`, etc.).
- `imdb_id`: Extracted IMDb identifier (e.g. `tt0083658`).
- `source_url`: Full remote URL.
- `source_hash`: SHA-256 hash of the untouched raw HTML.
- `is_missing`: Boolean flag for non-existent IDs.
- `scraped_at`: Timestamp of successful retrieval.

### 2. `releases`
Each physical media edition listed on the page.
- `id`: Primary key.
- `title_id`: Foreign key to `titles.id` (cascading).
- `release_index`: 1-based order on the source page.
- `header_raw`: Full unparsed header string from the `<h3>` tag.
- `media_format`: Normalized format (`4K UHD`, `Blu-ray`, `DVD`).
- `region`: Region coding (`ALL`, `A`, `B`, `R0`, `R1`, `R2`, etc.).
- `country`: Release territory (`America`, `United Kingdom`, `Germany`, etc.).
- `distributor`: Publishing label/distributor (e.g. `Warner Home Video`, `Arrow Video`, `Criterion`).
- `release_year`: Year this physical edition was manufactured/sold.
- `edition_name`: Specific title variant (e.g. `The Final Cut`, `40th Anniversary Edition`).
- `aspect_ratio`: Video aspect ratio (e.g. `2.40:1`, `1.85:1`, `1.33:1`).
- `picture_format`: Resolution and transfer (e.g. `2160p24 HEVC`, `1080p24 AVC`, `Anamorphic`).
- `hdr_format`: High Dynamic Range metadata (e.g. `HDR10`, `Dolby Vision`).
- `tv_system`: Broadcast system (`NTSC`, `PAL`, `None`).
- `case_type`: Packaging (e.g. `Keep Case`, `Steelbook`, `Digi-Pack`).
- `upc_number`: Barcode where provided.
- `notes_raw`: Complete notes text.
- `extras_raw`, `subtitles_raw`, `soundtracks_raw`: Full unparsed text strings.
- `contributor`: Contributor attribution string.

### 3. `audio_tracks`
Normalized audio streams per release.
- `id`: Primary key.
- `release_id`: Foreign key to `releases.id` (cascading).
- `disc_or_version`: Disc association (e.g. `4K`, `Blu-ray (Final Cut)`, `Main Feature`).
- `language`: Primary language name.
- `codec`: Detected audio format (`Dolby Atmos`, `Dolby TrueHD 5.1`, `DTS-HD MA 5.1`, `Dolby Digital 2.0 Mono`).
- `channels`: Channel layout (`Atmos`, `5.1`, `Stereo`, `Mono`).
- `raw_text`: Exact unparsed track string.

### 4. `subtitle_tracks`
Normalized subtitle streams per release.
- `id`: Primary key.
- `release_id`: Foreign key to `releases.id` (cascading).
- `disc_or_version`: Disc label.
- `language`: Subtitle language name.
- `is_hoh`: Flag indicating Hearing Impaired / SDH subtitles.
- `is_forced`: Flag indicating forced subtitles.
- `raw_text`: Exact unparsed subtitle string.

### 5. `extras`
Individual bonus features per release.
- `id`: Primary key.
- `release_id`: Foreign key to `releases.id` (cascading).
- `extra_type`: Bonus feature classification (`commentary`, `featurette`, `trailer`, `deleted_scene`, `extra`).
- `title`: Feature description.
- `runtime`: Extracted running time if present.
- `source_order`: Display order from source.
- `raw_text`: Complete unparsed feature text.

### 6. `cuts`
Censorship, version differences, and running time notes.
- `id`: Primary key.
- `title_id`: Foreign key to `titles.id` (cascading).
- `release_label`: Edition name or country reference.
- `cut_status`: Status classification (`Uncut`, `Cut`, `Unknown`).
- `description`: Detailed explanation.
- `runtime_diff`: Running time difference or exact duration.
- `raw_text`: Original cut description.

### 7. `recommendations`
Rewind's overall verdict.
- `id`: Primary key.
- `title_id`: Foreign key to `titles.id` (cascading, unique per title).
- `overall_winner`: Short verdict summary (e.g. `Draw`, `R1`, `Blu-ray ALL`).
- `recommendation_text`: Detailed explanation and comparison verdict.
- `raw_text`: Source text.

### 8. `update_log`
Changelog history.
- `id`: Primary key.
- `title_id`: Foreign key to `titles.id` (cascading).
- `entry_date`: Date of modification.
- `entry_text`: Changelog description.

### 9. `crawl_manifest`
State machine for scraper orchestration.
- `fid`: Primary key.
- `status`: Lifecycle state (`queued`, `fetched`, `parsed`, `imported`, `unchanged`, `missing`, `failed`).
- `fetch_timestamp`: ISO 8601 timestamp.
- `content_sha256`: Hash of raw payload.
- `retry_count`: Retry tracking.
- `error_message`: Diagnostic failure reason.

### 10. `titles_fts` (FTS5 Virtual Table)
Full-text search virtual table indexed with `unicode61` tokenizer:
- Columns: `clean_title`, `aka_titles`, `year`, `format_category`, `distributors`, `countries`, `winner_summary`.
- Automatic synchronization on title deletion via SQLite trigger.
