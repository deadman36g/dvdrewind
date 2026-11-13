"""Deterministic collector-facing intelligence built from archived release data.

These helpers never invent a verdict. They summarize DVDCompare recommendations and
compare the physical-media data already present in the local archive.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def _format_rank(value: str) -> int:
    text = (value or "").lower()
    if "4k" in text or "uhd" in text:
        return 1
    if "blu" in text:
        return 2
    if "dvd" in text and "hd" not in text:
        return 3
    if "hd dvd" in text:
        return 4
    return 9


def _format_label(value: str) -> str:
    text = (value or "").lower()
    if "4k" in text or "uhd" in text:
        return "4K UHD"
    if "blu" in text:
        return "Blu-ray"
    if "hd dvd" in text:
        return "HD DVD"
    if "dvd" in text:
        return "DVD"
    return value or "Other"


def _compact_text(value: str, limit: int = 420) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{clipped}…"


def _normalize_extra(value: str) -> str:
    text = re.sub(r"^[*\-•\s]+", "", value or "").lower()
    text = re.sub(r"\([^)]*\d{1,3}:\d{2}[^)]*\)", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _is_meaningful_extra(value: str) -> bool:
    text = (value or "").lower()
    signals = (
        "commentary", "documentary", "making of", "featurette", "interview",
        "deleted", "alternate", "extended", "cut", "version", "isolated",
        "booklet", "essay", "retrospective", "q&a", "conversation",
    )
    return any(signal in text for signal in signals)


def _original_audio_mixes(releases: list[dict[str, Any]]) -> set[str]:
    mixes: set[str] = set()
    for release in releases:
        for track in release.get("audio_tracks", []):
            text = (track.get("raw_text") or "").lower()
            if "english" not in text:
                continue
            if "mono" in text or re.search(r"\b1\.0\b", text):
                mixes.add("mono track")
            if "stereo" in text or re.search(r"\b2\.0\b", text):
                mixes.add("stereo track")
    return mixes


def _winner_releases(title: dict[str, Any], releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommendation = title.get("recommendation") or {}
    winner = str(recommendation.get("overall_winner") or "").strip()
    if not winner or winner.lower() == "draw":
        return []

    current_fid = int(title.get("fid") or 0)
    current = [r for r in releases if int(r.get("parent_fid") or current_fid) == current_fid]
    match = re.fullmatch(r"r\s*(\d+)", winner, flags=re.I)
    if match:
        wanted = int(match.group(1))
        return [
            r for r in current
            if int(r.get("original_release_index") or r.get("release_index") or -1) == wanted
        ]

    needle = winner.lower()
    matched = []
    for release in current:
        haystack = " ".join(
            str(release.get(key) or "")
            for key in ("header_raw", "country", "distributor", "edition_name")
        ).lower()
        if needle and needle in haystack:
            matched.append(release)
    return matched


def _verdict_insight(title: dict[str, Any], releases: list[dict[str, Any]]) -> dict[str, Any]:
    recommendation = title.get("recommendation") or {}
    winner = str(recommendation.get("overall_winner") or "Draw").strip() or "Draw"
    text = _compact_text(str(recommendation.get("recommendation_text") or ""))
    is_draw = winner.lower() == "draw"

    if is_draw:
        headline = "DVDCompare calls this one a draw."
        if not text:
            text = "No single edition is identified as the clear overall winner in the archived comparison."
    else:
        headline = f"{winner} is the archived recommendation."
        if not text:
            text = "DVDCompare identifies this edition as the overall recommendation for this comparison."

    signals: list[str] = []
    for release in _winner_releases(title, releases):
        for badge in release.get("badges", []):
            if badge.get("category") in {"scan", "lineage", "hdr", "audio"}:
                label = str(badge.get("label") or "").strip()
                if label and label not in signals:
                    signals.append(label)
        extra_count = len(release.get("extras", []))
        if extra_count:
            label = f"{extra_count} cataloged extras"
            if label not in signals:
                signals.append(label)
        if release.get("region"):
            label = f"Region {release['region']}"
            if label not in signals:
                signals.append(label)
        if len(signals) >= 3:
            break

    return {
        "winner": winner,
        "is_draw": is_draw,
        "headline": headline,
        "text": text,
        "signals": signals[:3],
    }


def _keep_old_disc_insight(releases: list[dict[str, Any]], cuts: list[dict[str, Any]]) -> dict[str, Any]:
    by_rank: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for release in releases:
        by_rank[_format_rank(str(release.get("parent_format") or release.get("media_format") or ""))].append(release)

    valid_ranks = sorted(rank for rank in by_rank if rank < 9)
    if len(valid_ranks) < 2:
        return {
            "available": False,
            "protect": False,
            "headline": "No older-format comparison to protect yet.",
            "detail": "DVD Rewind needs at least two archived physical formats before it can check what an upgrade leaves behind.",
            "target_format": _format_label(str(releases[0].get("parent_format") or "")) if releases else "Current format",
            "warnings": [],
        }

    top_rank = valid_ranks[0]
    top_releases = by_rank[top_rank]
    older_releases = [release for rank in valid_ranks[1:] for release in by_rank[rank]]
    top_format = _format_label(str(top_releases[0].get("parent_format") or top_releases[0].get("media_format") or ""))

    top_extras: set[str] = set()
    older_extras: dict[str, str] = {}
    for release in top_releases:
        for extra in release.get("extras", []):
            key = _normalize_extra(str(extra.get("title") or ""))
            if key:
                top_extras.add(key)
    for release in older_releases:
        for extra in release.get("extras", []):
            title = str(extra.get("title") or "").strip()
            key = _normalize_extra(title)
            if key and _is_meaningful_extra(title):
                older_extras.setdefault(key, re.sub(r"^[*\-•\s]+", "", title).strip())

    missing_extra_keys = [key for key in older_extras if key not in top_extras]
    warnings: list[dict[str, str]] = []
    if missing_extra_keys:
        samples = [older_extras[key] for key in missing_extra_keys[:3]]
        sample_text = "; ".join(samples)
        remainder = len(missing_extra_keys) - len(samples)
        if remainder > 0:
            sample_text += f"; plus {remainder} more"
        warnings.append({
            "icon": "🎁",
            "title": f"{len(missing_extra_keys)} older-only bonus feature{'s' if len(missing_extra_keys) != 1 else ''}",
            "detail": sample_text,
        })

    top_mixes = _original_audio_mixes(top_releases)
    older_mixes = _original_audio_mixes(older_releases)
    missing_mixes = sorted(older_mixes - top_mixes)
    if missing_mixes:
        warnings.append({
            "icon": "🎙️",
            "title": "Legacy audio may still matter",
            "detail": f"Older discs list an English {', '.join(missing_mixes)} that is not listed on any archived {top_format} edition.",
        })

    top_cut_text = " ".join(
        f"{cut.get('cut_status', '')} {cut.get('description', '')}".lower()
        for cut in cuts
        if _format_rank(str(cut.get("parent_format") or "")) == top_rank
    )
    older_version_notes = []
    for cut in cuts:
        if _format_rank(str(cut.get("parent_format") or "")) <= top_rank:
            continue
        raw = f"{cut.get('cut_status', '')} {cut.get('description', '')}".strip()
        low = raw.lower()
        if any(token in low for token in ("director", "theatrical", "extended", "unrated", "alternate version")):
            normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", low).split())
            if normalized and normalized not in top_cut_text and raw not in older_version_notes:
                older_version_notes.append(raw)
    if older_version_notes:
        warnings.append({
            "icon": "✂️",
            "title": "Older disc has version/cut information worth keeping",
            "detail": _compact_text(older_version_notes[0], 220),
        })

    if warnings:
        headline = f"Keep an older disc beside the {top_format}."
        detail = "The archive found material on earlier formats that is not represented on the top-format releases."
    else:
        headline = f"The {top_format} looks self-contained."
        detail = "DVD Rewind did not find a meaningful older-only extra, original mix, or alternate-version warning in the archived data."

    return {
        "available": True,
        "protect": bool(warnings),
        "headline": headline,
        "detail": detail,
        "target_format": top_format,
        "warnings": warnings,
    }


def _winner_releases_for_recommendation(
    recommendation: dict[str, Any],
    releases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    winner = str(recommendation.get("overall_winner") or "").strip()
    if not winner or winner.lower() == "draw":
        return []
    parent_fid = int(recommendation.get("parent_fid") or 0)
    candidates = [r for r in releases if int(r.get("parent_fid") or 0) == parent_fid] if parent_fid else list(releases)
    match = re.fullmatch(r"r\s*(\d+)", winner, flags=re.I)
    if match:
        wanted = int(match.group(1))
        return [
            r for r in candidates
            if int(r.get("original_release_index") or r.get("release_index") or -1) == wanted
        ]

    needle = winner.lower()
    matched: list[dict[str, Any]] = []
    for release in candidates:
        haystack = " ".join(
            str(release.get(key) or "")
            for key in ("header_raw", "country", "distributor", "edition_name")
        ).lower()
        if needle and needle in haystack:
            matched.append(release)
    return matched


def _timeline(
    releases: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for release in releases:
        rank = _format_rank(str(release.get("parent_format") or release.get("media_format") or ""))
        if rank < 9:
            grouped[rank].append(release)

    items: list[dict[str, Any]] = []
    for rank, group in grouped.items():
        years = []
        distributors = []
        tags = []
        for release in group:
            year = release.get("release_year")
            try:
                if year:
                    years.append(int(year))
            except (TypeError, ValueError):
                pass
            distributor = str(release.get("distributor") or "").strip()
            if distributor and distributor not in distributors:
                distributors.append(distributor)
            for badge in release.get("badges", []):
                if badge.get("category") in {"scan", "lineage", "hdr"}:
                    label = str(badge.get("label") or "").strip()
                    if label and label not in tags:
                        tags.append(label)
            notes = (release.get("notes_raw") or "").lower()
            if "restor" in notes and "Restoration" not in tags:
                tags.append("Restoration")

        earliest = min(years) if years else None
        latest = max(years) if years else None
        span = str(earliest or "Year unknown")
        if earliest and latest and latest != earliest:
            span = f"{earliest}–{latest}"
        fmt = _format_label(str(group[0].get("parent_format") or group[0].get("media_format") or ""))
        parent_fid = int(group[0].get("parent_fid") or 0)
        recommendation = next(
            (rec for rec in recommendations if int(rec.get("parent_fid") or 0) == parent_fid),
            None,
        )
        winner_name = str((recommendation or {}).get("overall_winner") or "").strip()
        winner_release = None
        winner_cover = None
        if recommendation and winner_name.lower() != "draw":
            winner_matches = _winner_releases_for_recommendation(recommendation, releases)
            winner_release = winner_matches[0] if len(winner_matches) == 1 else None
            if winner_release and winner_release.get("cover_art"):
                winner_cover = winner_release["cover_art"][0]

        items.append({
            "format": fmt,
            "rank": rank,
            "earliest": earliest,
            "latest": latest,
            "span": span,
            "count": len(group),
            "distributors": distributors[:3],
            "tags": tags[:4],
            "winner": winner_name or None,
            "winner_release": winner_release,
            "winner_cover": winner_cover,
        })

    # Chronological when years exist; format rank is the deterministic fallback.
    items.sort(key=lambda item: (item["earliest"] if item["earliest"] is not None else 9999, -item["rank"]))
    return items


def build_collector_insights(title: dict[str, Any], family_context: dict[str, Any]) -> dict[str, Any]:
    releases = family_context.get("releases", []) or title.get("releases", [])
    cuts = family_context.get("cuts", []) or title.get("cuts", [])
    recommendations = family_context.get("recommendations", [])
    return {
        "verdict": _verdict_insight(title, releases),
        "keep_old_disc": _keep_old_disc_insight(releases, cuts),
        "timeline": _timeline(releases, recommendations),
    }
