"""
DVDRewind Shelf Engine
Groups archived film comparisons into Netflix-style horizontal shelves:
- Director collections (Spielberg, Scorsese, Kubrick, Lynch, Scott, Cameron, Carpenter, etc.)
- Franchise collections (Alien, Star Wars, Indiana Jones, Evil Dead, Mad Max, etc.)
- Boutique label spotlights (Criterion, Arrow Video, Scream Factory, Kino Lorber, etc.)
- Special Curations (Best 4K transfers, Infamous controversies)
- Genre shelves & Decades

Enforces:
1. Every shelf MUST have at least 8 unique film titles (shelves with < 8 titles are hidden).
2. Deduplicates multiple formats of the same film down to 1 dominant card (starting from DVD).
"""

import json
import re
import time
from collections import defaultdict
from typing import Any, Dict, List

MIN_SHELF_TITLES = 8
MAX_SHELF_CARDS = 12
MAX_DYNAMIC_DIRECTOR_SHELVES = 24
SHELF_CACHE_TTL_SECONDS = 60

_SHELF_CACHE_BUILT_AT = 0.0
_SHELF_CACHE = None

# 1. Curated Directors
TARGET_DIRECTORS = [
    ("Steven Spielberg", "🎬 Steven Spielberg Collection", "director", "spielberg"),
    ("Martin Scorsese", "🎬 Martin Scorsese Filmography", "director", "scorsese"),
    ("Stanley Kubrick", "🎬 Stanley Kubrick Auteur Vault", "director", "kubrick"),
    ("David Lynch", "🎬 David Lynch Dreamscapes", "director", "lynch"),
    ("Ridley Scott", "🎬 Ridley Scott Director's Cuts", "director", "scott"),
    ("James Cameron", "🎬 James Cameron Blockbusters", "director", "cameron"),
    ("John Carpenter", "🩸 John Carpenter Masters of Horror", "director", "carpenter"),
    ("Quentin Tarantino", "🎬 Quentin Tarantino Cinema Vault", "director", "tarantino"),
    ("David Cronenberg", "🩸 David Cronenberg Body Horror", "director", "cronenberg"),
    ("Wes Craven", "🩸 Wes Craven Nightmares", "director", "craven"),
    ("Alfred Hitchcock", "🎬 Alfred Hitchcock Master of Suspense", "director", "hitchcock"),
    ("Brian De Palma", "🎬 Brian De Palma Thrillers", "director", "depalma"),
    ("Sam Raimi", "🎬 Sam Raimi Cult Vault", "director", "raimi"),
    ("Christopher Nolan", "🎬 Christopher Nolan Visions", "director", "nolan"),
    ("George Miller", "🎬 George Miller Wastelands", "director", "miller"),
    ("Francis Ford Coppola", "🎬 Francis Ford Coppola Epics", "director", "coppola"),
    ("David Fincher", "🎬 David Fincher Dark Visions", "director", "fincher"),
]

# 2. Curated Franchises
TARGET_FRANCHISES = [
    ("Alien", ["alien", "aliens", "prometheus"], "👽 The Alien Saga", "franchise", "alien"),
    ("The Terminator", ["terminator"], "🤖 The Terminator Legacy", "franchise", "terminator"),
    ("The Evil Dead", ["evil dead", "army of darkness"], "🧟 The Evil Dead Trilogy", "franchise", "evildead"),
    ("Mad Max", ["mad max", "fury road", "road warrior", "thunderdome"], "🚗 Mad Max Wasteland Saga", "franchise", "madmax"),
    ("The Godfather", ["godfather"], "👔 The Godfather Trilogy", "franchise", "godfather"),
    ("Back to the Future", ["back to the future"], "⚡ Back to the Future Trilogy", "franchise", "bttf"),
    ("Jurassic Park", ["jurassic"], "🦖 Jurassic Park & World", "franchise", "jurassic"),
    ("Die Hard", ["die hard"], "💥 Die Hard Franchise", "franchise", "diehard"),
    ("Halloween", ["halloween"], "🎃 Halloween Franchise", "franchise", "halloween"),
    ("A Nightmare on Elm Street", ["nightmare on elm"], "🔪 A Nightmare on Elm Street", "franchise", "elmstreet"),
    ("Friday the 13th", ["friday the 13th"], "🏕 Friday the 13th Legacy", "franchise", "friday13"),
    ("Star Wars", ["star wars", "empire strikes", "return of the jedi"], "⚔️ Star Wars Saga", "franchise", "starwars"),
    ("Indiana Jones", ["indiana jones", "raiders of the lost ark", "temple of doom", "last crusade"], "🤠 Indiana Jones Adventures", "franchise", "indy"),
    ("Lord of the Rings", ["lord of the rings", "fellowship of the ring", "two towers", "return of the king", "hobbit"], "💍 The Lord of the Rings & Hobbit", "franchise", "lotr"),
    ("Planet of the Apes", ["planet of the apes"], "🦍 Planet of the Apes Universe", "franchise", "apes"),
    ("Hellraiser", ["hellraiser"], "⛓️ Hellraiser Lament Configuration", "franchise", "hellraiser"),
]

# 3. Boutique Labels
TARGET_LABELS = [
    ("Criterion", ["criterion"], "💿 The Criterion Collection", "label", "criterion"),
    ("Arrow Video", ["arrow", "arrow films", "arrow video"], "🏹 Arrow Video & Arrow Academy", "label", "arrow"),
    ("Scream Factory", ["scream factory", "shout! factory", "shout factory"], "😱 Scream Factory & Shout! Select", "label", "shout"),
    ("Vinegar Syndrome", ["vinegar syndrome"], "🍷 Vinegar Syndrome Archives", "label", "vinsyn"),
    ("Kino Lorber", ["kino lorber", "kino video"], "🎞️ Kino Lorber Studio Classics", "label", "kino"),
    ("Indicator", ["indicator", "powerhouse"], "📕 Indicator / Powerhouse Films", "label", "indicator"),
    ("Blue Underground", ["blue underground"], "🌃 Blue Underground Cult Vault", "label", "blueunderground"),
    ("Eureka", ["eureka", "masters of cinema"], "🏛️ Eureka! Masters of Cinema", "label", "eureka"),
    ("Synapse Films", ["synapse"], "🧠 Synapse Films Restorations", "label", "synapse"),
    ("88 Films", ["88 films"], "⚔️ 88 Films Cult Collection", "label", "88films"),
    ("Full Moon", ["full moon"], "🌙 Full Moon Features Vault", "label", "fullmoon"),
    ("StudioCanal", ["studiocanal", "studio canal"], "🏰 StudioCanal Classics & 4K", "label", "studiocanal"),
]

# 4. Curated Showcases
BEST_4K_TITLES = [
    "matrix", "blade runner", "2001", "shining", "jaws", "apocalypse now",
    "alien", "suspiria", "oppenheimer", "interstellar", "mad max", "taxi driver",
    "godfather", "scarface", "thing", "halloween", "texas chain saw", "gladiator"
]

CONTROVERSIAL_TITLES = [
    "predator", "terminator 2", "aliens", "terminator", "halloween",
    "suspiria", "total recall", "french connection"
]

# 5. Genre keyword heuristics
GENRE_KEYWORDS = {
    "Horror": ["horror", "dead", "nightmare", "halloween", "dracula", "vampire", "blood", "evil", "fly", "poltergeist", "carrie", "exorcist", "suspiria", "thing", "texas chain saw"],
    "Sci-Fi": ["matrix", "alien", "blade runner", "interstellar", "space odyssey", "terminator", "jurassic", "total recall", "mad max", "predator", "clockwork orange", "future"],
    "Action & Thriller": ["die hard", "heat", "gladiator", "robocop", "fury road", "french connection", "point break", "speed", "fugitive"],
    "Crime & Film Noir": ["godfather", "pulp fiction", "goodfellas", "casino", "scarface", "se7en", "taxi driver", "fargo", "silence of the lambs", "no country for old men", "fight club"],
}


def collapse_film_editions(titles: List[Dict[str, Any]], prefer_format: str = "DVD") -> List[Dict[str, Any]]:
    groups = {}
    for t in titles:
        raw_name = re.sub(r'\s*\((The|A|An)\)\s*$', '', t["clean_title"], flags=re.IGNORECASE).strip().lower()
        key = (raw_name, t.get("year"))
        if key not in groups:
            groups[key] = []
        groups[key].append(t)

    if prefer_format == "4K UHD":
        fmt_priority = ["4k uhd", "blu-ray", "dvd", "hd dvd"]
    else:
        fmt_priority = ["dvd", "blu-ray", "4k uhd", "hd dvd"]

    collapsed = []
    for (title_key, year), ed_list in groups.items():
        def get_priority(item):
            cat = item["format_category"].lower()
            for i, p in enumerate(fmt_priority):
                if p in cat:
                    return i
            return 99

        sorted_eds = sorted(ed_list, key=get_priority)
        dominant = sorted_eds[0]

        formats = []
        total_releases = 0
        best_poster = None
        for ed in ed_list:
            formats.append({
                "category": ed["format_category"],
                "fid": ed["fid"],
            })
            total_releases += ed.get("release_count", 0)
            if not best_poster and ed.get("poster_url"):
                best_poster = ed["poster_url"]

        def fmt_display_sort(f):
            cat = f["category"].lower()
            if "dvd" in cat and "hd" not in cat: return 1
            if "blu-ray" in cat: return 2
            if "4k" in cat: return 3
            return 4

        formats = sorted(formats, key=fmt_display_sort)

        entry = dict(dominant)
        entry["release_count"] = total_releases
        entry["poster_url"] = best_poster or dominant.get("poster_url")
        entry["formats"] = formats
        entry["format_count"] = len(formats)
        collapsed.append(entry)

    return collapsed


def get_curated_shelves(repo) -> List[Dict[str, Any]]:
    global _SHELF_CACHE_BUILT_AT, _SHELF_CACHE

    now = time.monotonic()
    if _SHELF_CACHE is not None and now - _SHELF_CACHE_BUILT_AT < SHELF_CACHE_TTL_SECONDS:
        return _SHELF_CACHE

    # Avoid correlated subqueries over releases for every title. Reading the
    # compact tables once is substantially faster on large NAS-hosted archives.
    title_rows = repo.conn.execute("""
        SELECT id, fid, clean_title, year, format_category, director, aka_titles, poster_url
        FROM titles
        WHERE is_missing = 0
        ORDER BY year ASC, clean_title ASC
    """).fetchall()
    recommendation_rows = repo.conn.execute(
        "SELECT title_id, overall_winner FROM recommendations"
    ).fetchall()
    release_rows = repo.conn.execute(
        "SELECT title_id, distributor FROM releases"
    ).fetchall()

    winners = {row["title_id"]: row["overall_winner"] for row in recommendation_rows}
    release_counts = defaultdict(int)
    distributors = defaultdict(list)
    for row in release_rows:
        title_id = row["title_id"]
        release_counts[title_id] += 1
        distributor = row["distributor"]
        if distributor:
            distributors[title_id].append(distributor.lower())

    titles_list = []
    for r in title_rows:
        title_id = r["id"]
        titles_list.append({
            "id": title_id,
            "fid": r["fid"],
            "clean_title": r["clean_title"],
            "year": r["year"],
            "format_category": r["format_category"],
            "director": r["director"] or "",
            "aka_titles": json.loads(r["aka_titles"]) if r["aka_titles"] else [],
            "poster_url": r["poster_url"],
            "overall_winner": winners.get(title_id),
            "release_count": release_counts[title_id],
            "distributors": " || ".join(distributors[title_id]),
        })

    shelves = []
    used_shelf_ids = set()

    def add_shelf(shelf_id: str, title: str, category: str, items: List[Dict[str, Any]], prefer_format: str = "DVD"):
        if not items or shelf_id in used_shelf_ids:
            return
        unique_films = collapse_film_editions(items, prefer_format=prefer_format)
        # HIDE SHELVES WITH LESS THAN 8 TITLES
        if len(unique_films) < MIN_SHELF_TITLES:
            return
        shelves.append({
            "id": shelf_id,
            "title": title,
            "category": category,
            "count": len(unique_films),
            # Keep the shelf useful while preventing the homepage from rendering
            # tens of thousands of hidden cards. The badge retains the full count.
            "titles": unique_films[:MAX_SHELF_CARDS],
        })
        used_shelf_ids.add(shelf_id)

    # 1. Special Curations
    best_4k = [
        t for t in titles_list 
        if "4k" in t["format_category"].lower() 
        and any(k in t["clean_title"].lower() for k in BEST_4K_TITLES)
    ]
    add_shelf("best-4k", "🌟 Reference 4K Ultra HD Showcases", "showcase", best_4k, prefer_format="4K UHD")

    controversial = [
        t for t in titles_list
        if any(k in t["clean_title"].lower() for k in CONTROVERSIAL_TITLES)
    ]
    add_shelf("controversial", "⚡ Infamous Transfer Controversies & DNR Debates", "controversy", controversial, prefer_format="DVD")

    cult_keywords = [
        "zombie", "dawn of the dead", "evil dead", "faces of death", "suspiria",
        "cannibal", "re-animator", "toxic avenger", "eraserhead", "basket case",
        "texas chain saw", "hellraiser", "videodrome", "scanners", "near dark"
    ]
    cult = [
        t for t in titles_list
        if any(k in t["clean_title"].lower() for k in cult_keywords)
    ]
    add_shelf("cult-vault", "🔥 Cult Cinema & Midnight Movie Vault", "cult", cult, prefer_format="DVD")

    # 2. Curated Franchises
    for canon_name, keywords, shelf_title, cat, s_id in TARGET_FRANCHISES:
        items = [
            t for t in titles_list
            if any(k in t["clean_title"].lower() for k in keywords)
        ]
        add_shelf(s_id, shelf_title, "franchise", items, prefer_format="DVD")

    # 3. Curated Directors
    for dir_name, shelf_title, cat, s_id in TARGET_DIRECTORS:
        items = [
            t for t in titles_list
            if dir_name.lower() in t["director"].lower()
        ]
        add_shelf(s_id, shelf_title, "director", items, prefer_format="DVD")

    # Dynamically any other director with at least 8 unique films in archive
    dir_counts = {}
    for t in titles_list:
        d = t["director"].strip()
        if d and len(d) > 2 and "various" not in d.lower():
            dir_counts[d] = dir_counts.get(d, 0) + 1
    
    dynamic_directors_added = 0
    for d, count in sorted(dir_counts.items(), key=lambda x: -x[1]):
        if dynamic_directors_added >= MAX_DYNAMIC_DIRECTOR_SHELVES:
            break
        if count >= MIN_SHELF_TITLES:
            slug = re.sub(r'[^a-z0-9]+', '-', d.lower()).strip('-')
            s_id = f"dir-{slug}"
            if s_id not in used_shelf_ids:
                before = len(shelves)
                items = [t for t in titles_list if t["director"] == d]
                add_shelf(s_id, f"🎬 {d} Spotlight", "director", items, prefer_format="DVD")
                if len(shelves) > before:
                    dynamic_directors_added += 1

    # 4. Boutique Labels
    for label_name, keywords, shelf_title, cat, s_id in TARGET_LABELS:
        items = [
            t for t in titles_list
            if any(k in t["distributors"] for k in keywords)
        ]
        add_shelf(s_id, shelf_title, "label", items, prefer_format="DVD")

    # 5. Genres
    for genre_name, keywords in GENRE_KEYWORDS.items():
        items = [
            t for t in titles_list
            if any(k in t["clean_title"].lower() for k in keywords)
        ]
        genre_slug = re.sub(r'[^a-z0-9]+', '-', genre_name.lower()).strip('-')
        add_shelf(f"genre-{genre_slug}", f"🍿 {genre_name} Archive", "genre", items, prefer_format="DVD")

    # 6. Decades
    decades = [
        ("The 1970s: Gritty Masterpieces", 1970, 1979, "decade-70s"),
        ("The 1980s: The Golden Physical Era", 1980, 1989, "decade-80s"),
        ("The 1990s: Cinema Renaissance", 1990, 1999, "decade-90s"),
        ("The 2000s & 2010s: Modern Reference", 2000, 2019, "decade-modern"),
        ("Classic 1950s & 1960s Cinema", 1950, 1969, "decade-classic"),
    ]
    for d_title, start_y, end_y, s_id in decades:
        items = [
            t for t in titles_list
            if t["year"] and start_y <= t["year"] <= end_y
        ]
        add_shelf(s_id, f"📼 {d_title}", "decade", items, prefer_format="DVD")

    _SHELF_CACHE_BUILT_AT = time.monotonic()
    _SHELF_CACHE = shelves
    return shelves
