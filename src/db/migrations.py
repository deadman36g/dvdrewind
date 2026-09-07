import sqlite3
from pathlib import Path
from src.config import DB_PATH, PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "src" / "db" / "schema.sql"

def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
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
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()
    print(f"Database initialized successfully at {DB_PATH}")
