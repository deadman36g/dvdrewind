"""
DVDRewind Master Provenance and Collector Badges Engine
Extracts fine-grained physical media badges from release notes, headers, and audio/video specs:
- Transfer and Scan: 4K Scan, 2K Scan, 8K Scan, 4K Restoration
- Source Lineage: OCN (Original Camera Negative), Interpositive (IP)
- Supervision: Approved by Director / Cinematographer (e.g. Dean Cundey, John Carpenter)
- HDR Presentation: Dolby Vision, HDR10+, HDR10
- Audio Purist: Original Theatrical Mono, Original Theatrical Stereo, Dolby Atmos, DTS:X
- Censorship and Cut Status: 100% Uncut, Cut
- Collector Packaging: SteelBook, Digipak, Slipcover, Multi-Disc
"""

import re
from typing import Any, Dict, List, Optional


def extract_release_badges(release: Dict[str, Any], cuts: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, str]]:
    """
    Extracts structured, high-value badges for a physical edition.
    Returns a list of dicts with:
    - category: 'scan', 'lineage', 'hdr', 'audio', 'cut', 'packaging'
    - type: unique CSS class identifier
    - icon: display emoji or symbol
    - label: short human-readable label
    - tooltip: detailed description
    """
    badges: List[Dict[str, str]] = []

    header = release.get("header_raw") or ""
    notes = release.get("notes_raw") or ""
    case = release.get("case_type") or ""
    hdr = release.get("hdr_format") or ""
    pic = release.get("picture_format") or ""
    dist = release.get("distributor") or ""

    combined = f"{header} {notes} {case} {hdr} {pic} {dist}"
    combined_lower = combined.lower()

    # 1. TRANSFER & RESOLUTION SCAN
    if re.search(r"\b4K\s*(?:scan|restoration|master|transfer|remaster|sourced\s+from\s+4k)\b", combined, re.I):
        badges.append({
            "category": "scan",
            "type": "badge-scan-4k",
            "icon": "💎",
            "label": "4K Scan",
            "tooltip": "Tier 1 Reference: Native 4K digital restoration scan from original film elements"
        })
    elif re.search(r"\b2K\s*(?:scan|restoration|master|transfer|remaster)\b", combined, re.I):
        badges.append({
            "category": "scan",
            "type": "badge-scan-2k",
            "icon": "🔬",
            "label": "2K Scan",
            "tooltip": "Tier 2 Preservation: Mastered from a dedicated 2K digital film scan"
        })
    elif re.search(r"\b8K\s*(?:scan|restoration|master)\b", combined, re.I):
        badges.append({
            "category": "scan",
            "type": "badge-scan-8k",
            "icon": "👑",
            "label": "8K Scan",
            "tooltip": "Tier 1 Ultra-Reference: Mastered from an 8K ultra-resolution film scan"
        })
    elif re.search(r"\b(?:new\s+restoration|digital\s+restoration|fully\s+restored)\b", combined, re.I):
        badges.append({
            "category": "scan",
            "type": "badge-restored",
            "icon": "✨",
            "label": "Restored Master",
            "tooltip": "Tier 2 Preservation: Digital restoration from archival studio elements"
        })

    # 2. SOURCE ELEMENT LINEAGE & PROVENANCE
    if re.search(r"\b(?:original\s+(?:camera\s+)?negative|camera\s+negative|\bOCN\b)", combined, re.I):
        badges.append({
            "category": "lineage",
            "type": "badge-ocn",
            "icon": "🎞️",
            "label": "OCN Master",
            "tooltip": "Tier 1 Reference: Struck directly from the Original Camera Negative (OCN) — highest fidelity Generation 1 film element"
        })
    elif re.search(r"\b(?:interpositive|\bIP\s+(?:master|transfer)\b)", combined, re.I):
        badges.append({
            "category": "lineage",
            "type": "badge-ip",
            "icon": "📼",
            "label": "Interpositive",
            "tooltip": "Tier 2 Preservation: Struck from a 35mm Interpositive (IP) film element"
        })

    # Director / DP Approval
    if re.search(r"\b(?:supervised|approved)\s+by\b", combined, re.I):
        m = re.search(r"(?:supervised|approved)\s+by\s+([^.;\n]{3,85})", combined, re.I)
        if m:
            who = m.group(1).strip()
            # Clean up trailing phrases like "and features", "with the", etc.
            who = re.sub(r",?\s+(?:and\s+features|features|for|the\s+film|in\s+\d{4}).*$", "", who, flags=re.I).strip()
            # Shorten "director of photography" to "DP" or clean title words
            who = re.sub(r"\bdirector\s+of\s+photography\b", "DP", who, flags=re.I)
            who = re.sub(r"\bdirector\b", "", who, flags=re.I).strip()
            who = re.sub(r"\s+", " ", who)
            # Capitalize neatly
            if len(who) > 3:
                badges.append({
                    "category": "lineage",
                    "type": "badge-supervised",
                    "icon": "👑",
                    "label": f"Approved by {who}",
                    "tooltip": f"Transfer supervised and approved by {who}"
                })

    # 3. HDR & ADVANCED VIDEO SPECS
    if "dolby vision" in combined_lower or hdr.lower() == "dv":
        badges.append({
            "category": "hdr",
            "type": "badge-dv",
            "icon": "✨",
            "label": "Dolby Vision",
            "tooltip": "Tier 1 Premium HDR: Dynamic grading with Dolby Vision frame-by-frame metadata"
        })
    if "hdr10+" in combined_lower:
        badges.append({
            "category": "hdr",
            "type": "badge-hdr10plus",
            "icon": "✨",
            "label": "HDR10+",
            "tooltip": "Tier 1 Premium HDR: Dynamic HDR10+ frame-by-frame metadata presentation"
        })
    elif "hdr10" in combined_lower or "hdr" in hdr.lower():
        badges.append({
            "category": "hdr",
            "type": "badge-hdr10",
            "icon": "✨",
            "label": "HDR10",
            "tooltip": "Tier 2 Standard HDR: Static 10-bit High Dynamic Range color"
        })

    # 4. AUDIO PURIST & ADVANCED TRACKS
    audio_tracks = release.get("audio_tracks", [])
    audio_texts = [a.get("raw_text", "").lower() for a in audio_tracks]
    audio_combined = " ".join(audio_texts) + " " + combined_lower

    if re.search(r"\btheatrical\s+(?:mono|mix)\b|\boriginal\s+(?:theatrical\s+)?mono\b", audio_combined, re.I):
        badges.append({
            "category": "audio",
            "type": "badge-purist",
            "icon": "🎙️",
            "label": "Theatrical Mono",
            "tooltip": "Tier 1 Purist: Authentic original theatrical mono mix (unaltered original dynamics & Foley)"
        })
    elif re.search(r"\btheatrical\s+(?:stereo|mix)\b|\boriginal\s+(?:theatrical\s+)?stereo\b", audio_combined, re.I):
        badges.append({
            "category": "audio",
            "type": "badge-purist",
            "icon": "🎙️",
            "label": "Theatrical Stereo",
            "tooltip": "Tier 1 Purist: Authentic original theatrical stereo mix (unaltered original dynamics & Foley)"
        })
    elif any("mono" in at for at in audio_texts if "english" in at):
        badges.append({
            "category": "audio",
            "type": "badge-mono",
            "icon": "🎙️",
            "label": "Original Mono 1.0",
            "tooltip": "Tier 1 Purist: Authentic single-channel mono soundtrack"
        })

    if any("atmos" in at for at in audio_texts):
        badges.append({
            "category": "audio",
            "type": "badge-atmos",
            "icon": "🔊",
            "label": "Dolby Atmos",
            "tooltip": "Tier 1 Immersive: Spatial 3D object-based audio presentation with height channels"
        })
    elif any("dts:x" in at for at in audio_texts):
        badges.append({
            "category": "audio",
            "type": "badge-dtsx",
            "icon": "🔊",
            "label": "DTS:X",
            "tooltip": "Tier 1 Immersive: DTS:X spatial 3D object-based audio presentation"
        })

    # 5. CENSORSHIP / CUT STATUS FOR THIS EDITION
    if cuts:
        rel_label = (release.get("country") or "").lower()
        rel_dist = (release.get("distributor") or "").lower()
        matched_cut = None
        for c in cuts:
            c_label = (c.get("release_label") or "").lower()
            if rel_label and rel_label in c_label:
                matched_cut = c
                break
            elif rel_dist and rel_dist in c_label:
                matched_cut = c
                break

        if matched_cut:
            status = (matched_cut.get("cut_status") or "").lower()
            desc = (matched_cut.get("description") or "").lower()
            if "cut" in status and "uncut" not in status and "no cut" not in desc:
                bbfc = "bbfc" in desc or "bbfc" in (matched_cut.get("release_label") or "").lower()
                label = "Cut (BBFC)" if bbfc else "Cut"
                badges.append({
                    "category": "cut",
                    "type": "badge-cut-danger",
                    "icon": "✂️",
                    "label": label,
                    "tooltip": f"Censored edition: {matched_cut.get('description', 'Censorship trims applied')}"
                })
            else:
                badges.append({
                    "category": "cut",
                    "type": "badge-cut-safe",
                    "icon": "🛡️",
                    "label": "Uncut",
                    "tooltip": "Verified 100% uncut presentation"
                })

    # 6. COLLECTOR PACKAGING & PHYSICAL MEDIA SPECS
    if re.search(r"\bsteelbook\b", combined, re.I):
        badges.append({
            "category": "packaging",
            "type": "badge-steelbook",
            "icon": "🛡️",
            "label": "SteelBook",
            "tooltip": "Limited edition metallic SteelBook packaging"
        })
    elif re.search(r"\bdigipak\b|\bdigipack\b", combined, re.I):
        badges.append({
            "category": "packaging",
            "type": "badge-digipak",
            "icon": "📦",
            "label": "Digipak",
            "tooltip": "Collector fold-out book packaging"
        })
    elif re.search(r"\bslipcover\b|\bslipcase\b", combined, re.I):
        badges.append({
            "category": "packaging",
            "type": "badge-slipcover",
            "icon": "📦",
            "label": "Slipcover",
            "tooltip": "Includes illustrated protective O-card slipcover"
        })
    elif re.search(r"\bmediabook\b", combined, re.I):
        badges.append({
            "category": "packaging",
            "type": "badge-mediabook",
            "icon": "📖",
            "label": "Mediabook",
            "tooltip": "Hardcover book packaging with integrated disc trays"
        })

    # Disc count
    disc_m = re.search(r"\b([2-9]|\d{2})\s*(?:-|\s*)discs?\b", combined, re.I)
    if disc_m:
        badges.append({
            "category": "packaging",
            "type": "badge-discs",
            "icon": "💿",
            "label": f"{disc_m.group(1)}-Disc",
            "tooltip": f"Multi-disc edition containing {disc_m.group(1)} physical media discs"
        })

    return badges


def format_video_framing(release: Dict[str, Any]) -> Dict[str, str]:
    """
    Parses picture_format and aspect_ratio into structured, clean framing components:
    - framing: clean resolution/framing label (e.g. '1080p', '4K UHD', 'Anamorphic')
    - aspect: clean aspect ratio (e.g. '1.85:1', '2.35:1')
    - codec: clean video codec if detected (e.g. 'AVC', 'VC-1', 'HEVC', 'MPEG-2')
    - display_line: polished display string (e.g. '1080p • 1.85:1')
    """
    raw_pic = release.get("picture_format") or ""
    clean_pic = " ".join(raw_pic.split())
    aspect = (release.get("aspect_ratio") or "").strip()

    # Detect video codec
    codec = ""
    if re.search(r"\bAVC\b|\bMPEG-4\s*AVC\b", clean_pic, re.I):
        codec = "AVC"
    elif re.search(r"\bVC-1\b", clean_pic, re.I):
        codec = "VC-1"
    elif re.search(r"\bHEVC\b|\bH\.265\b", clean_pic, re.I):
        codec = "HEVC"
    elif re.search(r"\bMPEG-2\b", clean_pic, re.I):
        codec = "MPEG-2"

    framing = clean_pic
    m_res = re.match(
        r"^(4K\s*2160p|4K\s*UHD|2160p|4K|1080[pi](?:24)?|720p|480[ip]|576[ip]|Anamorphic|Non-Anamorphic)",
        clean_pic,
        re.I
    )
    if m_res:
        matched = m_res.group(1).strip()
        if matched.lower() == "1080p24":
            framing = "1080p"
        elif matched.lower() == "4k":
            framing = "4K UHD"
        else:
            framing = matched
    elif not framing and aspect:
        framing = ""

    parts = []
    if framing:
        parts.append(framing)
    if aspect:
        parts.append(aspect)

    display_line = " • ".join(parts) if parts else (clean_pic or aspect or "—")

    return {
        "framing": framing,
        "aspect": aspect,
        "codec": codec,
        "display_line": display_line,
    }