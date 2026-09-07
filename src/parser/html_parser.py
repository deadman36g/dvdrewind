import re
from typing import Any, Dict, List, Optional, Union
from bs4 import BeautifulSoup

from src.parser.normalizer import (
    parse_audio_tracks,
    parse_cuts_section,
    parse_release_header,
    parse_subtitle_tracks,
    parse_title_header,
)
from src.parser.warnings import WarningCollector


class DVDCompareParser:
    def __init__(self, warning_collector: Optional[WarningCollector] = None):
        self.collector = warning_collector or WarningCollector()

    def parse(self, html_content: Union[str, bytes], fid: Optional[int] = None) -> Dict[str, Any]:
        if isinstance(html_content, bytes):
            # Try utf-8 first, fallback to windows-1252
            try:
                html_text = html_content.decode("utf-8")
            except UnicodeDecodeError:
                html_text = html_content.decode("cp1252", errors="replace")
        else:
            html_text = html_content

        # 1. Negative / Missing Check
        if "FILMID NOT FOUND" in html_text or "Unable to find film details" in html_text:
            return {
                "fid": fid,
                "is_missing": True,
                "raw_title": "FILMID NOT FOUND",
                "clean_title": "FILMID NOT FOUND",
                "aka_titles": [],
                "year": None,
                "format_category": "Unknown",
                "imdb_id": None,
                "releases": [],
                "recommendation": None,
                "cuts": [],
                "update_log": [],
                "added_by": None,
                "added_date": None,
                "updated_by": None,
                "updated_date": None,
            }

        soup = BeautifulSoup(html_text, "html.parser")

        # 2. Title & Format
        h2 = soup.find("h2")
        if h2:
            raw_title = h2.get_text(" ", strip=True)
        else:
            title_tag = soup.find("title")
            raw_title = title_tag.get_text(" ", strip=True) if title_tag else "Unknown Title"
            # Strip "Rewind @ www.dvdcompare.net - "
            raw_title = re.sub(r'^Rewind\s*@\s*www\.dvdcompare\.net\s*-\s*', '', raw_title)
            self.collector.warn(fid, "title", "No <h2> found, fell back to <title>", raw_title)

        title_info = parse_title_header(raw_title)

        # 3. IMDb Link
        imdb_id = None
        imdb_link = soup.find("a", href=re.compile(r'imdb\.com/title/(tt\d+)', re.IGNORECASE))
        if imdb_link:
            m = re.search(r'(tt\d+)', imdb_link["href"])
            if m:
                imdb_id = m.group(1)

        # 4. Partition ul.dvd into Releases and Conclusion
        uls = soup.find_all("ul", class_="dvd")
        releases_data = []
        conclusion_ul = None

        for ul_idx, ul in enumerate(uls):
            h3 = ul.find("h3")
            h3_text = h3.get_text(" ", strip=True) if h3 else ""
            if "OVERALL" in h3_text or "CUTS" in h3_text:
                conclusion_ul = ul
            elif ul_idx > 0 and h3:
                # This is a release
                release_dict = self._parse_release_ul(ul, release_index=len(releases_data) + 1, fid=fid)
                releases_data.append(release_dict)

        # 5. Conclusion: Overall, Cuts, Update Log, Contributors
        rec_data = None
        cuts_data = []
        update_log_data = []
        added_by, added_date = None, None
        updated_by, updated_date = None, None

        if conclusion_ul:
            conclusion_info = self._parse_conclusion_ul(conclusion_ul, fid=fid)
            rec_data = conclusion_info["recommendation"]
            cuts_data = conclusion_info["cuts"]
            update_log_data = conclusion_info["update_log"]
            added_by = conclusion_info["added_by"]
            added_date = conclusion_info["added_date"]
            updated_by = conclusion_info["updated_by"]
            updated_date = conclusion_info["updated_date"]

        return {
            "fid": fid,
            "is_missing": False,
            "raw_title": title_info["raw_title"],
            "clean_title": title_info["clean_title"],
            "aka_titles": title_info["aka_titles"],
            "year": title_info["year"],
            "format_category": title_info["format_category"],
            "imdb_id": imdb_id,
            "releases": releases_data,
            "recommendation": rec_data,
            "cuts": cuts_data,
            "update_log": update_log_data,
            "added_by": added_by,
            "added_date": added_date,
            "updated_by": updated_by,
            "updated_date": updated_date,
        }

    def _parse_release_ul(self, ul, release_index: int, fid: Optional[int]) -> Dict[str, Any]:
        first_li = ul.find("li")
        h3 = first_li.find("h3") if first_li else None
        h3_text = h3.get_text(" ", strip=True) if h3 else ""

        header_info = parse_release_header(h3_text)

        fields: Dict[str, str] = {}
        contributor = None

        lis = ul.find_all("li", recursive=False)
        for li in lis:
            label_div = li.find("div", class_="label")
            desc_div = li.find("div", class_="description")

            if label_div and desc_div:
                label_key = label_div.get_text(strip=True).rstrip(":")
                desc_text = desc_div.get_text(separator="\n", strip=True)
                if label_key:
                    fields[label_key] = desc_text

            # Check for contributor line
            li_text = li.get_text(" ", strip=True)
            contrib_match = re.search(r'Special thanks to Rewind user (.*?) for providing these specifications', li_text, re.IGNORECASE)
            if contrib_match:
                contributor = contrib_match.group(1).strip()

        # Extract structured audio and subtitle tracks
        soundtracks_raw = fields.get("Soundtrack(s)", "")
        subtitles_raw = fields.get("Subtitles", "")
        extras_raw = fields.get("Extras", "")

        audio_tracks = parse_audio_tracks(soundtracks_raw)
        subtitle_tracks = parse_subtitle_tracks(subtitles_raw)

        # Parse extras into itemized entries
        extras_items = self._parse_extras(extras_raw)

        return {
            "release_index": release_index,
            "header_raw": header_info["raw_header"],
            "media_format": header_info["media_format"],
            "region": header_info["region"],
            "country": header_info["country"],
            "distributor": header_info["distributor"],
            "release_year": header_info["release_year"],
            "edition_name": header_info["edition_name"],
            "aspect_ratio": fields.get("Aspect Ratio"),
            "picture_format": fields.get("Picture Format"),
            "hdr_format": fields.get("HDR"),
            "tv_system": fields.get("TV System"),
            "case_type": fields.get("Case type"),
            "upc_number": fields.get("UPC Number"),
            "notes_raw": fields.get("Notes"),
            "easter_eggs": fields.get("Easter eggs"),
            "commentaries_raw": fields.get("Commentaries"),
            "extras_raw": extras_raw,
            "subtitles_raw": subtitles_raw,
            "soundtracks_raw": soundtracks_raw,
            "audio_tracks": audio_tracks,
            "subtitle_tracks": subtitle_tracks,
            "extras": extras_items,
            "contributor": contributor,
        }

    def _parse_extras(self, extras_raw: str) -> List[Dict[str, Any]]:
        extras = []
        if not extras_raw or extras_raw.strip().lower() in ["none", ""]:
            return extras

        lines = [l.strip() for l in extras_raw.replace("\r", "\n").split("\n") if l.strip()]
        for idx, line in enumerate(lines):
            # Check for runtime e.g. (12:34) or (15 mins)
            rt_match = re.search(r'\(([\d:]+|\d+\s*mins?)\)', line)
            runtime = rt_match.group(1) if rt_match else None

            # Detect extra type
            extra_type = "extra"
            if re.search(r'\b(audio\s+commentary|commentary)\b', line, re.IGNORECASE):
                extra_type = "commentary"
            elif re.search(r'\b(trailer|teaser|tv\s*spot)\b', line, re.IGNORECASE):
                extra_type = "trailer"
            elif re.search(r'\b(featurette|documentary|interview|making\s*of|behind\s*the\s*scenes)\b', line, re.IGNORECASE):
                extra_type = "featurette"
            elif re.search(r'\b(deleted\s*scene|outtake|gag\s*reel|alternate\s*ending)\b', line, re.IGNORECASE):
                extra_type = "deleted_scene"

            extras.append({
                "extra_type": extra_type,
                "title": line,
                "runtime": runtime,
                "source_order": idx + 1,
                "raw_text": line,
            })
        return extras

    def _parse_conclusion_ul(self, ul, fid: Optional[int]) -> Dict[str, Any]:
        result = {
            "recommendation": None,
            "cuts": [],
            "update_log": [],
            "added_by": None,
            "added_date": None,
            "updated_by": None,
            "updated_date": None,
        }

        # 1. Overall Recommendation
        overall_h3 = ul.find(lambda t: t.name == "h3" and "OVERALL:" in t.get_text())
        if overall_h3:
            h3_text = overall_h3.get_text(" ", strip=True)
            winner_match = re.search(r'OVERALL:\s*(.*)', h3_text, re.IGNORECASE)
            winner_summary = winner_match.group(1).strip() if winner_match else None

            parent_desc = overall_h3.find_parent("div", class_="description")
            rec_text = ""
            if parent_desc:
                # Get text excluding h3
                p_tags = parent_desc.find_all(["p", "strong"])
                rec_parts = [p.get_text(" ", strip=True) for p in p_tags if p.get_text(" ", strip=True)]
                rec_text = " ".join(rec_parts).strip()
                if not rec_text:
                    # Fallback to description text after h3
                    rec_text = parent_desc.get_text(" ", strip=True).replace(h3_text, "").strip()

            result["recommendation"] = {
                "overall_winner": winner_summary,
                "recommendation_text": rec_text,
                "raw_text": f"{h3_text}\n{rec_text}".strip(),
            }

        # 2. Cuts
        cuts_h3 = ul.find(lambda t: t.name == "h3" and "CUTS:" in t.get_text())
        if cuts_h3:
            parent_desc = cuts_h3.find_parent("div", class_="description")
            if parent_desc:
                result["cuts"] = parse_cuts_section(parent_desc)

        # 3. Update Log & Contributor Metadata
        full_text = ul.get_text(separator="\n", strip=True)

        added_match = re.search(r'Comparison added by (.*?) on (\d{2}/\d{2}/\d{2,4})', full_text, re.IGNORECASE)
        if added_match:
            result["added_by"] = added_match.group(1).strip()
            result["added_date"] = added_match.group(2).strip()

        updated_match = re.search(r'Comparison last updated by (.*?) on (\d{2}/\d{2}/\d{2,4})', full_text, re.IGNORECASE)
        if updated_match:
            result["updated_by"] = updated_match.group(1).strip()
            result["updated_date"] = updated_match.group(2).strip()

        # Update log lines
        log_h3 = ul.find(lambda t: t.name == "h3" and "UPDATE LOG:" in t.get_text())
        if log_h3:
            parent_desc = log_h3.find_parent("div", class_="description")
            if parent_desc:
                desc_text = parent_desc.get_text(separator="\n", strip=True)
                lines = [l.strip() for l in desc_text.split("\n") if l.strip()]
                in_log = False
                for l in lines:
                    if "UPDATE LOG:" in l:
                        in_log = True
                        continue
                    if in_log:
                        if "Please ensure you read our disclaimer" in l:
                            break
                        # Date match: DD/MM/YY: text
                        log_entry_match = re.match(r'^(\d{2}/\d{2}/\d{2,4}):\s*(.*)$', l)
                        if log_entry_match:
                            result["update_log"].append({
                                "entry_date": log_entry_match.group(1).strip(),
                                "entry_text": log_entry_match.group(2).strip(),
                            })
                        elif l:
                            result["update_log"].append({
                                "entry_date": None,
                                "entry_text": l,
                            })

        return result
