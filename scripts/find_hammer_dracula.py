import re
import sys
import time
from pathlib import Path
from bs4 import BeautifulSoup
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import DEFAULT_USER_AGENT, SEARCH_URL

SEARCH_TERMS = [
    "Horror of Dracula",
    "Brides of Dracula",
    "Prince of Darkness",
    "Risen from the Grave",
    "Taste the Blood of Dracula",
    "Scars of Dracula",
    "Dracula A.D. 1972",
    "Satanic Rites of Dracula",
    "7 Golden Vampires",
    "Seven Golden Vampires",
]

def search_term(term):
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    resp = requests.post(SEARCH_URL, data={"param": term, "searchtype": "title"}, headers=headers, timeout=20)
    soup = BeautifulSoup(resp.text, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "film.php?fid=" in href:
            fid_match = re.search(r'fid=(\d+)', href)
            if fid_match:
                fid = int(fid_match.group(1))
                text = a.get_text(" ", strip=True)
                found.append((fid, text))
    return found

def main():
    all_fids = {}
    print("Searching DVDCompare for Hammer Horror Dracula comparisons...")
    for term in SEARCH_TERMS:
        print(f"Searching for '{term}'...")
        try:
            results = search_term(term)
            for fid, title in results:
                all_fids[fid] = title
                print(f"  -> Found FID {fid}: {title}")
        except Exception as e:
            print(f"  Error searching '{term}': {e}")
        time.sleep(2.5) # Polite delay

    print("\nTotal unique Dracula comparisons found:", len(all_fids))
    for fid, title in sorted(all_fids.items()):
        print(f"  {fid:6d} : {title}")

if __name__ == "__main__":
    main()
