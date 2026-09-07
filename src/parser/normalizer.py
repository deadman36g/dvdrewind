import re
from typing import Dict, List, Optional, Tuple

FORMAT_PATTERNS = [
    (re.compile(r'\b(?:Blu-ray 4K|4K Ultra HD|4K UHD|UHD)\b', re.IGNORECASE), "4K UHD"),
    (re.compile(r'\b(?:Blu-ray 3D)\b', re.IGNORECASE), "Blu-ray 3D"),
    (re.compile(r'\b(?:Blu-ray)\b', re.IGNORECASE), "Blu-ray"),
    (re.compile(r'\b(?:HD DVD)\b', re.IGNORECASE), "HD DVD"),
    (re.compile(r'\b(?:Laserdisc)\b', re.IGNORECASE), "LaserDisc"),
    (re.compile(r'\b(?:UMD)\b', re.IGNORECASE), "UMD"),
    (re.compile(r'\b(?:DVD)\b', re.IGNORECASE), "DVD"),
]

def parse_title_header(raw_title: str) -> Dict:
    """
    Parses a raw title string like:
      'Blade Runner (Blu-ray 4K) (1982)'
      'Zombie AKA Zombie Flesh Eaters AKA Zombi 2 AKA Zombies 2 (1979)'
      'Batman: The Long Halloween, Part One (Blu-ray) (2021)'
    """
    clean_text = raw_title.strip()
    
    # 1. Extract trailing year e.g. (1982)
    year = None
    year_match = re.search(r'\((\d{4})\)\s*$', clean_text)
    if year_match:
        year = int(year_match.group(1))
        clean_text = clean_text[:year_match.start()].strip()

    # 2. Extract format category e.g. (Blu-ray 4K), (Blu-ray), (HD DVD)
    detected_format = "DVD" # default format on dvdcompare
    for pattern, fmt_name in FORMAT_PATTERNS:
        match = re.search(r'\(([^)]*' + pattern.pattern + r'[^)]*)\)\s*$', clean_text, re.IGNORECASE)
        if match:
            detected_format = fmt_name
            clean_text = clean_text[:match.start()].strip()
            break

    # 3. Extract AKA alternate titles
    # Titles are often formatted as: Title AKA Alt1 AKA Alt2
    parts = re.split(r'\s+AKA\s+', clean_text, flags=re.IGNORECASE)
    primary_title = parts[0].strip()
    aka_titles = [p.strip() for p in parts[1:] if p.strip()]

    return {
        "raw_title": raw_title,
        "clean_title": primary_title,
        "aka_titles": aka_titles,
        "year": year,
        "format_category": detected_format,
    }

def parse_release_header(header_text: str) -> Dict:
    """
    Parses a release header such as:
      'Blu-ray ALL America - Warner Home Video[2017 Release]Special Edition'
      'R1 America - Warner Home Video[2007 Release]Collector's Edition'
      'R0 America - Blue Underground[2019 Release]40th Anniversary Edition'
      'R2 Germany - Splendid Film'
      'Blu-ray A America - Lionsgate Home Entertainment'
    """
    raw_header = header_text.strip()
    result = {
        "raw_header": raw_header,
        "media_format": None,
        "region": None,
        "country": None,
        "distributor": None,
        "release_year": None,
        "edition_name": None,
    }

    # Extract [YYYY Release]
    year_match = re.search(r'\[(\d{4})\s+Release\]', raw_header, re.IGNORECASE)
    if year_match:
        result["release_year"] = int(year_match.group(1))
        # Text after [YYYY Release] is usually edition name
        edition_part = raw_header[year_match.end():].strip()
        if edition_part:
            result["edition_name"] = edition_part
        # Strip the [YYYY Release]... part for left-side parsing
        left_side = raw_header[:year_match.start()].strip()
    else:
        left_side = raw_header

    # Format is usually: <Region/Format> <Country> - <Distributor> [Edition]
    if " - " in left_side:
        prefix_part, dist_part = left_side.split(" - ", 1)
        result["distributor"] = dist_part.strip()

        # Parse prefix_part e.g. "Blu-ray ALL America" or "R1 America" or "R0 United Kingdom"
        # Common known countries:
        countries = [
            "America", "United Kingdom", "Germany", "France", "Japan", "Australia", 
            "Canada", "Italy", "Holland", "Netherlands", "Spain", "Scandinavia", 
            "Hong Kong", "South Korea", "Sweden", "Denmark", "Norway", "Finland",
            "Poland", "Czech Republic", "Hungary", "Romania", "Brazil", "Mexico",
            "Austria", "Switzerland", "Belgium", "Taiwan", "Thailand", "China"
        ]
        found_country = None
        for c in countries:
            if prefix_part.endswith(" " + c) or prefix_part == c:
                found_country = c
                prefix_part = prefix_part[:-len(c)].strip()
                break

        result["country"] = found_country or "Unknown"

        # Now prefix_part contains region/format e.g. "Blu-ray ALL", "R1", "R0", "Blu-ray A", "4K ALL"
        region_str = prefix_part.strip()
        result["region"] = region_str if region_str else None

        if "Blu-ray" in region_str:
            result["media_format"] = "Blu-ray"
        elif "4K" in region_str:
            result["media_format"] = "4K UHD"
        elif region_str.startswith("R"):
            result["media_format"] = "DVD"
        else:
            result["media_format"] = None
    else:
        # Fallback if no " - "
        result["distributor"] = left_side

    return result

def parse_audio_tracks(soundtracks_text: str) -> List[Dict]:
    """
    Parses audio tracks, including multi-disc listings:
    '4K: English Dolby Atmos | French Dolby Digital 5.1 | Blu-ray: English Dolby TrueHD 5.1'
    """
    tracks = []
    if not soundtracks_text:
        return tracks

    # Check for disc prefixes like '4K:', 'Blu-ray (Final Cut):'
    lines = [l.strip() for l in soundtracks_text.replace("\r", "\n").split("\n") if l.strip()]
    current_disc = "Main Feature"

    for line in lines:
        parts = [p.strip() for p in line.split("|") if p.strip()]
        for part in parts:
            disc_match = re.match(r'^(4K|Blu-ray|DVD|Disc\s+\d+|DISC\s+[A-Z]+(?:\s*\([^)]+\))?)\s*:\s*(.*)$', part, re.IGNORECASE)
            if disc_match:
                current_disc = disc_match.group(1).strip()
                part = disc_match.group(2).strip()
            
            if not part:
                continue

            # Extract language, codec, channels
            # e.g. "English Dolby Atmos", "French Dolby Digital 5.1", "English DTS-HD Master Audio 5.1"
            channel_match = re.search(r'\b(Dolby Atmos|Atmos|Auro-3D|Mono|Stereo|\d\.\d(?:\s*surround)?)\b', part, re.IGNORECASE)
            channels = channel_match.group(1) if channel_match else None

            # Detect codec
            codec_match = re.search(r'\b(Dolby TrueHD|Dolby Digital Plus|Dolby Digital|DTS-HD Master Audio|DTS-HD High Resolution|DTS-HD|DTS:X|DTS|LPCM|PCM|MPEG Audio)\b', part, re.IGNORECASE)
            codec = codec_match.group(1) if codec_match else None

            # Language usually starts the track description
            lang_match = re.match(r'^([A-Za-z]+(?:\s*\([^)]+\))?)', part)
            language = lang_match.group(1) if lang_match else "Unknown"

            tracks.append({
                "disc_or_version": current_disc,
                "language": language,
                "codec": codec,
                "channels": channels,
                "raw_text": part
            })
    return tracks

def parse_subtitle_tracks(subtitles_text: str) -> List[Dict]:
    """
    Parses subtitles text, detecting languages and HoH/SDH/Forced flags.
    'English HoH, French, German HoH, Spanish (Castilian)'
    """
    tracks = []
    if not subtitles_text or subtitles_text.strip().lower() in ['none', 'no', '']:
        return tracks

    current_disc = "Main Feature"
    lines = [l.strip() for l in subtitles_text.replace("\r", "\n").split("\n") if l.strip()]

    for line in lines:
        parts = [p.strip() for p in line.split("|") if p.strip()]
        for part in parts:
            disc_match = re.match(r'^(4K|Blu-ray|DVD|Disc\s+\d+|DISC\s+[A-Z]+(?:\s*\([^)]+\))?)\s*:\s*(.*)$', part, re.IGNORECASE)
            if disc_match:
                current_disc = disc_match.group(1).strip()
                part = disc_match.group(2).strip()

            if not part:
                continue

            # Subtitles are usually comma-separated within each part
            sub_items = [s.strip() for s in part.split(",") if s.strip()]
            for item in sub_items:
                is_hoh = bool(re.search(r'\b(HoH|SDH)\b', item, re.IGNORECASE))
                is_forced = bool(re.search(r'\b(Forced)\b', item, re.IGNORECASE))
                clean_lang = re.sub(r'\b(HoH|SDH|Forced)\b', '', item, flags=re.IGNORECASE).strip()
                tracks.append({
                    "disc_or_version": current_disc,
                    "language": clean_lang or item,
                    "is_hoh": is_hoh,
                    "is_forced": is_forced,
                    "raw_text": item
                })
    return tracks

def parse_cuts_section(cuts_soup) -> List[Dict]:
    """
    Parses the CUTS section into per-release or overall cut status.
    """
    cuts = []
    if not cuts_soup:
        return cuts

    # Often list items <li> or <p> tags
    items = cuts_soup.find_all('li')
    if not items:
        # Fallback to paragraph or raw text
        raw = cuts_soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in raw.split("\n") if l.strip()]
        for l in lines:
            cuts.append(_parse_single_cut_line(l))
    else:
        for li in items:
            cuts.append(_parse_single_cut_line(li.get_text(" ", strip=True)))
    return cuts

def _parse_single_cut_line(line: str) -> Dict:
    raw = line.strip()
    status = "Unknown"
    if re.search(r'\b(No cuts|Uncut)\b', raw, re.IGNORECASE):
        status = "Uncut"
    elif re.search(r'\b(Cut|Censored|Cut for|Missing)\b', raw, re.IGNORECASE):
        status = "Cut"

    # Try to extract runtime difference e.g. (90:06 NTSC->PAL) or (86:24 PAL)
    rt_match = re.search(r'\(([\d:]+(?:\s*PAL|\s*NTSC|\s*->\s*PAL)?)\)', raw)
    runtime_diff = rt_match.group(1) if rt_match else None

    # Separate release label from cut text if formatted as: Label - Cut description
    parts = raw.split(" - ", 1)
    if len(parts) == 2:
        release_label, desc = parts[0].strip(), parts[1].strip()
    else:
        release_label, desc = None, raw

    return {
        "release_label": release_label,
        "cut_status": status,
        "description": desc,
        "runtime_diff": runtime_diff,
        "raw_text": raw
    }
