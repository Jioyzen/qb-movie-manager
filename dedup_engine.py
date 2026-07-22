"""Dedup Engine - 去重决策引擎。

按 TMDB ID 或 (title+year) 对 MediaProfile 分组，
五层优先级链排序，标记保留/删除。
"""

import json
from collections import defaultdict
from typing import Optional

from scoring_engine import MediaProfile, rank_profiles
from config import config


@staticmethod
def _normalize_title(title: str) -> str:
    """标准化标题用于分组。"""
    import re
    # 去特殊字符、空格
    t = re.sub(r"[^\w\u4e00-\u9fff\s]", "", title).strip().lower()
    # 去多余空格
    t = re.sub(r"\s+", " ", t)
    return t


class DedupResult:
    """一个去重组的结果。"""

    def __init__(self, group_key: str, profiles: list[MediaProfile], collection_strategy: str):
        self.group_key = group_key
        self.profiles = profiles
        self.collection_strategy = collection_strategy
        self._decide()

    def _decide(self):
        """执行去重决策。"""
        strategy = self.collection_strategy
        profiles = self.profiles

        if len(profiles) <= 1:
            # 单条记录，直接保留
            for p in profiles:
                p._keep = True
            return

        # 按策略处理合集
        has_collection = any(p.is_collection for p in profiles)

        if has_collection and strategy == "skip":
            # 跳过合集：所有合集种子标记保留，只处理独立种子之间的重复
            collection_profiles = [p for p in profiles if p.is_collection]
            standalone_profiles = [p for p in profiles if not p.is_collection]

            for p in collection_profiles:
                p._keep = True

            if len(standalone_profiles) <= 1:
                for p in standalone_profiles:
                    p._keep = True
            else:
                ranked = rank_profiles(standalone_profiles)
                ranked[0]._keep = True
                for p in ranked[1:]:
                    p._keep = False

        elif has_collection and strategy == "prefer":
            # 合集优先：有合集时直接保留合集版本，删除独立种子
            collection_profiles = [p for p in profiles if p.is_collection]
            standalone_profiles = [p for p in profiles if not p.is_collection]

            # 合集之间比较，保留最好的
            ranked_collection = rank_profiles(collection_profiles)
            ranked_collection[0]._keep = True
            for p in ranked_collection[1:]:
                p._keep = False

            # 独立种子全部删除
            for p in standalone_profiles:
                p._keep = False

        else:
            # 没有合集，或策略不涉及合集：正常排序
            ranked = rank_profiles(profiles)
            ranked[0]._keep = True
            for p in ranked[1:]:
                p._keep = False

    @property
    def keep_profile(self) -> Optional[MediaProfile]:
        """返回被保留的 MediaProfile。"""
        for p in self.profiles:
            if getattr(p, "_keep", True):
                return p
        return self.profiles[0] if self.profiles else None

    @property
    def delete_profiles(self) -> list[MediaProfile]:
        """返回被标记为删除的 MediaProfile 列表。"""
        return [p for p in self.profiles if not getattr(p, "_keep", True)]

    def to_dict(self) -> dict:
        return {
            "group_key": self.group_key,
            "keep": self.keep_profile.to_dict() if self.keep_profile else None,
            "delete": [p.to_dict() for p in self.delete_profiles],
            "total": len(self.profiles),
            "collection_strategy": self.collection_strategy,
        }


class DedupEngine:
    """去重引擎主类。"""

    def __init__(self, profiles: list[MediaProfile]):
        self.profiles = profiles
        self.groups: list[DedupResult] = []
        self._group()

    def _group(self):
        """按 TMDB ID 或 (title+year) 分组。"""
        groups = defaultdict(list)

        for p in self.profiles:
            key = _normalize_title(p.title)
            if p.year:
                key = f"{key}|{p.year}"
            groups[key].append(p)

        strategy = config.get("collection_strategy", "skip")

        self.groups = []
        for key, members in groups.items():
            self.groups.append(DedupResult(key, members, strategy))

        # 按标题排序
        self.groups.sort(key=lambda g: g.group_key)

    def get_duplicates(self) -> list[DedupResult]:
        """返回有重复的组（至少 2 个版本）。"""
        return [g for g in self.groups if len(g.profiles) > 1]

    def get_summary(self) -> dict:
        """返回去重摘要。"""
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
        """返回所有去重组（含无重复的单条）。"""
        return [g.to_dict() for g in self.groups]