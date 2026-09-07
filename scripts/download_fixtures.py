import json
import shutil
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import FIXTURES_DIR, RAW_DIR
from src.scraper.client import ScraperClient

TARGET_FIXTURES = [
    {"fid": 111, "label": "blade_runner_dvd", "category": "DVD with many releases"},
    {"fid": 12240, "label": "blade_runner_bluray", "category": "Blu-ray with extensive extras"},
    {"fid": 43651, "label": "blade_runner_4k", "category": "4K UHD with 14 international editions"},
    {"fid": 45272, "label": "adam_and_evil_dvd", "category": "DVD with few releases"},
    {"fid": 6726, "label": "halloween_dvd", "category": "DVD with detailed cuts"},
    {"fid": 57756, "label": "halloween_bluray", "category": "Blu-ray with complex overall recommendation"},
    {"fid": 62354, "label": "halloween_4k", "category": "Modern 4K UHD horror release"},
    {"fid": 58934, "label": "halloween2_4k", "category": "4K UHD with multiple domestic editions"},
    {"fid": 10, "label": "early_comparison_fid10", "category": "Very early low-ID comparison"},
    {"fid": 76145, "label": "recent_comparison_fid76145", "category": "Recently added comparison"},
    {"fid": 80000, "label": "missing_fid80000", "category": "Non-existent film ID negative test"},
]

def main():
    client = ScraperClient()
    print(f"Downloading {len(TARGET_FIXTURES)} representative fixtures...")
    manifest = []

    for item in TARGET_FIXTURES:
        fid = item["fid"]
        label = item["label"]
        raw_file = RAW_DIR / f"{fid}.html"
        raw_meta = RAW_DIR / f"{fid}.meta.json"

        # Check if already present in raw/
        if raw_file.exists() and raw_meta.exists():
            print(f"[*] FID {fid} ({label}) already exists in archive/raw. Using cached copy.")
            with open(raw_meta, "r", encoding="utf-8") as f:
                meta = json.load(f)
            status_code = meta.get("status_code", 200)
        else:
            print(f"[-] Fetching FID {fid} ({label}) from remote...")
            status_code, content, meta = client.fetch_film(fid, save_to_raw=True)
            print(f"    Fetched {len(content)} bytes, status={status_code}, sha256={meta['content_sha256'][:12]}...")

        # Copy to tests/fixtures
        fixture_html = FIXTURES_DIR / f"{fid}_{label}.html"
        fixture_meta = FIXTURES_DIR / f"{fid}_{label}.meta.json"
        shutil.copy2(raw_file, fixture_html)
        shutil.copy2(raw_meta, fixture_meta)

        manifest.append({
            "fid": fid,
            "label": label,
            "category": item["category"],
            "fixture_path": str(fixture_html.relative_to(FIXTURES_DIR.parent.parent)),
            "sha256": meta["content_sha256"],
            "is_missing": meta.get("is_missing", False),
            "size_bytes": meta.get("content_length", raw_file.stat().st_size),
        })

    manifest_path = FIXTURES_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n[+] Successfully saved {len(manifest)} fixtures and manifest to {manifest_path}")

if __name__ == "__main__":
    main()
