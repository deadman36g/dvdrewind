import sys
from pathlib import Path
from bs4 import BeautifulSoup
import re

sys.stdout.reconfigure(encoding='utf-8')

fixtures_dir = Path('tests/fixtures')
for html_file in sorted(fixtures_dir.glob('*.html')):
    print(f"\n==========================================")
    print(f"FILE: {html_file.name}")
    with open(html_file, 'rb') as f:
        raw_bytes = f.read()

    # Detect encoding
    text = raw_bytes.decode('utf-8', errors='replace')
    soup = BeautifulSoup(text, 'html.parser')

    # Check missing
    if "FILMID NOT FOUND" in text or "Unable to find film details" in text:
        print("  -> STATUS: Missing film ID")
        continue

    # Title
    h2 = soup.find('h2')
    h2_text = h2.get_text(strip=True) if h2 else "NO H2"
    print(f"  H2: {h2_text}")

    # IMDb
    imdb_link = soup.find('a', href=re.compile(r'imdb\.com/title/(tt\d+)'))
    imdb_id = imdb_link['href'] if imdb_link else "None"
    print(f"  IMDb: {imdb_id}")

    # UL.dvd
    uls = soup.find_all('ul', class_='dvd')
    print(f"  Total ul.dvd: {len(uls)}")

    releases = []
    conclusion_ul = None

    for ul_idx, ul in enumerate(uls):
        h3 = ul.find('h3')
        h3_text = h3.get_text(strip=True) if h3 else ""
        if "OVERALL" in h3_text or "CUTS" in h3_text:
            conclusion_ul = ul
        elif h3 and any(fmt in h3_text for fmt in ['R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'Blu-ray', '4K', 'ALL', 'Region', 'America', 'United Kingdom', 'Germany', 'France', 'Japan', 'Australia', 'Canada', 'Italy', 'Holland', 'Spain', 'Scandinavia', 'Hong Kong']):
            releases.append((ul_idx, h3_text))

    print(f"  Identified Releases ({len(releases)}):")
    for r_idx, r_title in releases[:4]:
        print(f"    [{r_idx}] {r_title}")
    if len(releases) > 4:
        print(f"    ... and {len(releases) - 4} more")

    if conclusion_ul:
        overall_h3 = conclusion_ul.find(lambda tag: tag.name == 'h3' and 'OVERALL' in tag.text)
        overall_txt = overall_h3.parent.get_text(strip=True) if overall_h3 else "None"
        print(f"  Conclusion: {overall_txt[:100]}")
    else:
        print(f"  Conclusion: NOT FOUND")
