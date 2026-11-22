import sqlite3
from pathlib import Path
from src.config import DB_PATH, PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "src" / "db" / "schema.sql"

def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    # Map the file instead of copying pages into a per-connection cache: every
    # request opens its own connection, and mmap pages stay warm in the OS cache.
    conn.execute("PRAGMA mmap_size = 1073741824;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(schema_sql)
            # Record schema version
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
            )
            curr = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            if curr is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (1);")

            # Check for existing table columns and migrate if necessary
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(titles);").fetchall()]
            if "director" not in cols:
                conn.execute("ALTER TABLE titles ADD COLUMN director TEXT;")
            if "synopsis" not in cols:
                conn.execute("ALTER TABLE titles ADD COLUMN synopsis TEXT;")

            release_cols = [r["name"] for r in conn.execute("PRAGMA table_info(releases);").fetchall()]
            if "amazon_asin" not in release_cols:
                conn.execute("ALTER TABLE releases ADD COLUMN amazon_asin TEXT;")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_releases_amazon_asin ON releases(amazon_asin);")

            # Planner statistics; without them SQLite picks index lookups that
            # are slower than a plain scan for the status counts.
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_stat1'").fetchone():
                conn.execute("PRAGMA analysis_limit = 1000;")
                conn.execute("ANALYZE;")

            curr = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
            if curr < 2:
                conn.execute("INSERT INTO schema_version (version) VALUES (2);")
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print(f"Database initialized successfully at {DB_PATH}")
