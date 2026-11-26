import os
import re
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Storage Paths
ARCHIVE_DIR = PROJECT_ROOT / "archive"
RAW_DIR = ARCHIVE_DIR / "raw"
MANIFESTS_DIR = ARCHIVE_DIR / "manifests"
# The container mounts the database from a plain disk (DVDREWIND_DB_PATH), not
# through the mergerfs pool that holds posters and raw HTML: FUSE made random
# SQLite reads several times slower.
DB_PATH = Path(os.environ.get("DVDREWIND_DB_PATH") or ARCHIVE_DIR / "dvdrewind.db")
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"

# Ensure directories exist
RAW_DIR.mkdir(parents=True, exist_ok=True)
MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

# Remote Target Configuration
BASE_URL = "https://www.dvdcompare.net"
FILM_URL_TEMPLATE = f"{BASE_URL}/comparisons/film.php?fid={{fid}}"
SEARCH_URL = f"{BASE_URL}/comparisons/search.php"

# Polite Crawling Configuration
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 (DVDRewind-PrivateArchive/1.0)"
)
REQUEST_DELAY_SECONDS = 2.0
REQUEST_JITTER_SECONDS = 1.0
MAX_RETRIES = 3
BACKOFF_FACTOR = 2.0
TIMEOUT_SECONDS = 15.0

# Parser Version
PARSER_VERSION = "1.0.0"


_SECRET_PARAM_RE = re.compile(r"((?:api_?key|apikey|token)=)[^&\s'\"]+", re.IGNORECASE)


def redact_secrets(text) -> str:
    """Hide API keys in messages: request errors repeat the full URL."""
    return _SECRET_PARAM_RE.sub(r"\1***", str(text))
