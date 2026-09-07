import sys
from pathlib import Path
from bs4 import BeautifulSoup
import re

sys.stdout.reconfigure(encoding='utf-8')

fixtures_dir = Path('tests/fixtures')
all_labels = set()

for html_file in sorted(fixtures_dir.glob('*.html')):
    if 'missing' in html_file.name or 'fid10' in html_file.name:
        continue
    with open(html_file, 'rb') as f:
        text = f.read().decode('utf-8', errors='replace')
    soup = BeautifulSoup(text, 'html.parser')

    for label_div in soup.find_all('div', class_='label'):
        lbl = label_div.get_text(strip=True).rstrip(':')
        if lbl:
            all_labels.add(lbl)

print("Observed labels across all sample releases:")
for l in sorted(all_labels):
    print(f"  - '{l}'")
