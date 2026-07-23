"""Dedup Engine - deduplication decision engine.

Groups by TMDB ID (primary) or (title, year) fallback,
then applies 5-layer priority chain to select one keeper per movie.
"""

import json
import re
from collections import defaultdict
from typing import Optional

from scoring_engine import MediaProfile, rank_profiles
from config import config


def _normalize_title(title: str) -> str:
    if not title:
        return ""
    # Remove quality markers and common noise
    t = re.sub(r'\[.*?\]', '', title)
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(
        r'(bluray|web[\s\-]?dl|webrip|2160p|1080p|720p|4k|uhd|hdr|dv|'
        r'dovi|x265|x264|hevc|av1|aac|dts|ddp|ac3|truehd|atmos|'
        r'\d+audios|mUHD|FRDS|CMCT|CMCTV|MNHD|ADE|tinyAV|hq|sphd)',
        '', t, flags=re.I
    )
    t = re.sub(r'[._\-]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip().lower()
    return t


class DedupResult:
    """Result for one deduplication group."""

    def __init__(self, group_key: str, profiles: list[MediaProfile],
                 collection_strategy: str,
                 tmdb_title_cn: str = "", tmdb_title_en: str = ""):
        self.group_key = group_key
        self.profiles = profiles
        self.collection_strategy = collection_strategy
        self.tmdb_title_cn = tmdb_title_cn
        self.tmdb_title_en = tmdb_title_en
        self._decide()

    def _decide(self):
        strategy = self.collection_strategy
        profiles = self.profiles
        if len(profiles) <= 1:
            for p in profiles:
                p._keep = True
            return

        has_collection = any(p.is_collection for p in profiles)

        if has_collection and strategy == "skip":
            coll = [p for p in profiles if p.is_collection]
            alone = [p for p in profiles if not p.is_collection]
            for p in coll:
                p._keep = True
            if len(alone) <= 1:
                for p in alone:
                    p._keep = True
            else:
                ranked = rank_profiles(alone)
                ranked[0]._keep = True
                for p in ranked[1:]:
                    p._keep = False
        elif has_collection and strategy == "prefer":
            coll = [p for p in profiles if p.is_collection]
            alone = [p for p in profiles if not p.is_collection]
            ranked_coll = rank_profiles(coll)
            ranked_coll[0]._keep = True
            for p in ranked_coll[1:]:
                p._keep = False
            for p in alone:
                p._keep = False
        else:
            ranked = rank_profiles(profiles)
            ranked[0]._keep = True
            for p in ranked[1:]:
                p._keep = False

    @property
    def keep_profile(self) -> Optional[MediaProfile]:
        for p in self.profiles:
            if getattr(p, "_keep", True):
                return p
        return self.profiles[0] if self.profiles else None

    @property
    def delete_profiles(self) -> list[MediaProfile]:
        return [p for p in self.profiles if not getattr(p, "_keep", True)]

    def to_dict(self) -> dict:
        return {
            "group_key": self.group_key,
            "tmdb_title_cn": self.tmdb_title_cn,
            "tmdb_title_en": self.tmdb_title_en,
            "keep": self.keep_profile.to_dict() if self.keep_profile else None,
            "delete": [p.to_dict() for p in self.delete_profiles],
            "total": len(self.profiles),
            "collection_strategy": self.collection_strategy,
        }


class DedupEngine:
    def __init__(self, profiles: list[MediaProfile], tmdb_matches: list[dict] = None):
        self.profiles = profiles
        self.tmdb_matches = tmdb_matches or []
        # Build lookup: torrent_hash -> tmdb_match
        self._tmdb_lookup = {m["torrent_hash"]: m for m in self.tmdb_matches if m.get("tmdb_id")}
        self.groups: list[DedupResult] = []
        self._group()

    def _group(self):
        """Group by TMDB ID first, then fallback to (title, year)."""
        groups = defaultdict(list)
        # Track which TMDB ID each group has
        group_tmdb_info = {}

        for p in self.profiles:
            tmdb_match = self._tmdb_lookup.get(p.torrent_hash)

            if tmdb_match and tmdb_match.get("tmdb_id"):
                tmdb_id = tmdb_match["tmdb_id"]
                # 合集保护：每个合集种子独立分组
                if tmdb_id.startswith("protected:"):
                    key = f"collection:{p.torrent_hash}"
                else:
                    key = f"tmdb:{tmdb_id}"
                # Store TMDB title info
                if key not in group_tmdb_info:
                    group_tmdb_info[key] = {
                        "cn": tmdb_match.get("tmdb_title_cn", ""),
                        "en": tmdb_match.get("tmdb_title_en", ""),
                    }
            else:
                # Fallback: title + year
                key = _normalize_title(p.title)
                if p.year:
                    key = f"{key}|{p.year}"
                if not key:
                    key = p.torrent_hash

            groups[key].append(p)

        strategy = config.get("collection_strategy", "skip")
        self.groups = []
        for key, members in groups.items():
            info = group_tmdb_info.get(key, {"cn": "", "en": ""})
            self.groups.append(DedupResult(
                key, members, strategy,
                tmdb_title_cn=info["cn"],
                tmdb_title_en=info["en"],
            ))

        self.groups.sort(key=lambda g: g.group_key)

    def get_duplicates(self) -> list[DedupResult]:
        return [g for g in self.groups if len(g.profiles) > 1]

    def get_summary(self) -> dict:
        dup_groups = self.get_duplicates()
        total_delete = sum(len(g.delete_profiles) for g in dup_groups)
        total_keep = sum(1 for g in dup_groups if g.keep_profile)
        return {
            "total_profiles": len(self.profiles),
            "total_groups": len(self.groups),
            "duplicate_groups": len(dup_groups),
            "delete_candidates": total_delete,
            "keep_groups": total_keep,
        }

    def to_dict(self) -> list[dict]:
        return [g.to_dict() for g in self.groups]