"""Deduplication engine - find duplicate movies and apply priority rules."""
import json
from typing import Optional

# Priority weights for different quality dimensions
RES_WEIGHTS = {
    "2160p": 100, "4K": 95, "UHD": 90,
    "1080p": 70, "1080i": 60, "720p": 40, "576p": 20, "480p": 10,
}

SOURCE_WEIGHTS = {
    "BluRay": 100, "Blu-ray": 100, "BDRip": 90,
    "WEB-DL": 80, "WEBRip": 60, "WEB": 60,
    "HDTV": 50, "DVDRip": 30, "DVD": 20,
}

CODEC_WEIGHTS = {
    "x265": 100, "H.265": 100, "HEVC": 100,
    "AV1": 80, "x264": 60, "H.264": 60, "AVC": 60,
}

HDR_BONUS = 15
DOVI_BONUS = 20
MULTI_AUDIO_BONUS = 10
ATMOS_BONUS = 15

QUALITY_WEIGHT_MAPS = {
    "resolution": RES_WEIGHTS,
    "source": SOURCE_WEIGHTS,
    "codec": CODEC_WEIGHTS,
}


class DedupEngine:
    """Identify duplicate movies and determine which to keep/delete."""

    def __init__(self, entries: list[dict]):
        self.entries = entries
        self.priority_rules: dict[str, bool] = {
            "resolution": True,
            "source": True,
            "codec": True,
            "hdr": True,
            "dovi": True,
            "multi_audio": False,
            "atmos": False,
        }

    def set_priority_rules(self, rules: dict[str, bool]):
        self.priority_rules.update(rules)

    def _score(self, entry: dict) -> int:
        """Calculate quality score for an entry based on active priority rules."""
        score = 0
        for field, weight_map in QUALITY_WEIGHT_MAPS.items():
            if not self.priority_rules.get(field, True):
                continue
            val = entry.get(field, "")
            for prefix, weight in weight_map.items():
                if val.lower().startswith(prefix.lower()):
                    score += weight
                    break
        if self.priority_rules.get("hdr", True) and entry.get("hdr", ""):
            score += HDR_BONUS
        if self.priority_rules.get("dovi", True) and entry.get("dovi", ""):
            score += DOVI_BONUS
        if self.priority_rules.get("multi_audio", False):
            ma = str(entry.get("multi_audio", "")).upper()
            if ma == "Y" or ma == "YES":
                score += MULTI_AUDIO_BONUS
        if self.priority_rules.get("atmos", False):
            audio = entry.get("audio_info", "").lower()
            if "atmos" in audio:
                score += ATMOS_BONUS
        return score

    def get_duplicates(self) -> dict[str, list[dict]]:
        """Group entries by tmdb_id and return groups with size > 1.

        Returns {tmdb_id: [entries sorted by quality desc]}
        """
        groups: dict[str, list[dict]] = {}
        for e in self.entries:
            tid = e.get("tmdb_id", "").strip()
            if not tid:
                continue
            groups.setdefault(tid, []).append(e)

        dupes = {}
        for tid, group in groups.items():
            if len(group) > 1:
                scored = [(self._score(e), e) for e in group]
                scored.sort(key=lambda x: -x[0])
                dupes[tid] = [e for _, e in scored]
        return dupes

    def get_duplicate_summary(self) -> list[dict]:
        """Return a flat list with keep/delete flags.

        Each dict: {**entry, duplicate_group_size, is_keep, score}
        """
        dupes = self.get_duplicates()
        flat = []
        for tid, group in dupes.items():
            for i, e in enumerate(group):
                e = dict(e)
                e["duplicate_group_size"] = len(group)
                e["is_keep"] = (i == 0)
                e["score"] = self._score(e)
                e["tmdb_title_display"] = e.get("tmdb_title_cn", "") or e.get("tmdb_title_en", "")
                flat.append(e)
        return flat