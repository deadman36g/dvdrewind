import hashlib
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

from src.config import (
    BACKOFF_FACTOR,
    DEFAULT_USER_AGENT,
    FILM_URL_TEMPLATE,
    MAX_RETRIES,
    PARSER_VERSION,
    RAW_DIR,
    REQUEST_DELAY_SECONDS,
    REQUEST_JITTER_SECONDS,
    TIMEOUT_SECONDS,
)


class ScraperClient:
    def __init__(self, user_agent: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        })
        self.last_request_time = 0.0

    def _wait_polite(self):
        elapsed = time.time() - self.last_request_time
        target_delay = REQUEST_DELAY_SECONDS + random.uniform(0, REQUEST_JITTER_SECONDS)
        if elapsed < target_delay:
            time.sleep(target_delay - elapsed)
        self.last_request_time = time.time()

    def fetch_film(self, fid: int, save_to_raw: bool = True) -> Tuple[int, bytes, Dict]:
        url = FILM_URL_TEMPLATE.format(fid=fid)
        return self.fetch_url(url, fid=fid, save_to_raw=save_to_raw)

    def fetch_url(
        self, url: str, fid: Optional[int] = None, save_to_raw: bool = True
    ) -> Tuple[int, bytes, Dict]:
        retries = 0
        backoff = BACKOFF_FACTOR

        while retries <= MAX_RETRIES:
            self._wait_polite()
            fetch_time = datetime.now(timezone.utc).isoformat()
            try:
                resp = self.session.get(url, timeout=TIMEOUT_SECONDS)
                status_code = resp.status_code
                content_bytes = resp.content

                sha256 = hashlib.sha256(content_bytes).hexdigest()
                headers = dict(resp.headers)

                # Check for "FILMID NOT FOUND"
                # dvdcompare returns 200 OK with this title or text when an ID doesn't exist
                text_sample = content_bytes[:4000].decode("latin1", errors="replace")
                is_missing = "FILMID NOT FOUND" in text_sample or "Unable to find film details" in text_sample

                metadata = {
                    "url": url,
                    "fid": fid,
                    "status_code": status_code,
                    "fetch_timestamp": fetch_time,
                    "content_length": len(content_bytes),
                    "content_sha256": sha256,
                    "headers": headers,
                    "is_missing": is_missing,
                    "parser_version": PARSER_VERSION,
                }

                if save_to_raw and fid is not None:
                    raw_html_path = RAW_DIR / f"{fid}.html"
                    raw_meta_path = RAW_DIR / f"{fid}.meta.json"
                    with open(raw_html_path, "wb") as f:
                        f.write(content_bytes)
                    with open(raw_meta_path, "w", encoding="utf-8") as f:
                        json.dump(metadata, f, indent=2)

                return status_code, content_bytes, metadata

            except (requests.RequestException, Exception) as e:
                retries += 1
                if retries > MAX_RETRIES:
                    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} retries: {e}")
                time.sleep(backoff)
                backoff *= 2
