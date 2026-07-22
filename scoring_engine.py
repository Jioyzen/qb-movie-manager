"""Scoring Engine - 五层优先级链比较器。

音轨 > 字幕 > 来源 > 分辨率 > HDR
每层内部按优先级排序，第一层分出胜负即停止。
"""

from dataclasses import dataclass, field
from typing import Any, Optional

# ─── 层级得分定义 ───────────────────────────────────────────

# 音轨: 中文全景声 > 其他中文音轨 > 无中文音轨
AUDIO_SCORES = {
    "chinese_atmos": 100,  # 中文全景声
    "chinese_audio": 50,   # 其他中文音轨
    "none": 0,              # 无中文音轨
}

# 字幕: 中文特效字幕 > 其他中文字幕 > 无中文字幕
SUBTITLE_SCORES = {
    "chinese_forced": 100,  # 中文特效/强制字幕
    "chinese_sub": 50,      # 其他中文字幕
    "none": 0,               # 无中文字幕
}

# 来源: BluRay > WEB-DL > 其他
SOURCE_SCORES = {
    "bluray": 100,
    "webdl": 50,
    "other": 0,
}

# 分辨率: 4K > 1080p > 更低
RESOLUTION_SCORES = {
    "2160p": 100,
    "1080p": 50,
    "other": 0,
}

# HDR: DV P7 > DV P8 > DV P5 > HDR10+ > HDR10 > SDR
HDR_SCORES = {
    "dv_p7": 100,      # 杜比视界双层
    "dv_p8": 80,       # 杜比视界 p8 (可fallback HDR10)
    "dv_p5": 60,       # 杜比视界 p5
    "hdr10plus": 40,   # HDR10+
    "hdr10": 20,       # HDR10
    "sdr": 0,          # SDR
}


@dataclass
class MediaProfile:
    """单个视频文件的完整画像，由 MediaAnalyzer 填充。"""
    # 标识
    torrent_hash: str = ""
    file_index: int = 0
    file_path: str = ""          # SMB 上的完整路径
    file_size: int = 0
    # 来自文件名解析
    title: str = ""
    year: Optional[str] = None
    # 来自 MediaInfo 深度分析
    audio_level: str = "none"          # chinese_atmos | chinese_audio | none
    subtitle_level: str = "none"       # chinese_forced | chinese_sub | none
    source: str = "other"              # bluray | webdl | other
    resolution: str = "other"          # 2160p | 1080p | other
    hdr_level: str = "sdr"             # dv_p7 | dv_p8 | dv_p5 | hdr10plus | hdr10 | sdr
    # 元数据（Display 用）
    audio_detail: str = ""             # 人类可读: "国语 TrueHD Atmos 7.1"
    subtitle_detail: str = ""          # 人类可读: "中文特效 (ASS)"
    source_detail: str = ""            # 人类可读: "BluRay"
    resolution_detail: str = ""        # 人类可读: "4K"
    hdr_detail: str = ""               # 人类可读: "Dolby Vision P7"
    # 合集标记
    is_collection: bool = False
    collection_name: str = ""          # 合集种子名称
    # 种子名（Display 用）
    torrent_name: str = ""
    category: str = ""

    @property
    def score_tuple(self) -> tuple:
        """返回五层得分元组，用于直接比较。"""
        return (
            AUDIO_SCORES.get(self.audio_level, 0),
            SUBTITLE_SCORES.get(self.subtitle_level, 0),
            SOURCE_SCORES.get(self.source, 0),
            RESOLUTION_SCORES.get(self.resolution, 0),
            HDR_SCORES.get(self.hdr_level, 0),
        )

    @property
    def is_better_than(self, other: "MediaProfile") -> Optional[bool]:
        """
        比较两个 MediaProfile，按五层优先级链逐层决胜。
        返回 True 表示 self 更好，False 表示 other 更好，None 表示平手。
        """
        if not other:
            return True
        s1 = self.score_tuple
        s2 = other.score_tuple
        for i in range(5):
            if s1[i] > s2[i]:
                return True
            elif s1[i] < s2[i]:
                return False
        return None  # 平手

    def to_dict(self) -> dict:
        return {
            "torrent_hash": self.torrent_hash,
            "file_index": self.file_index,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "title": self.title,
            "year": self.year,
            "audio_level": self.audio_level,
            "subtitle_level": self.subtitle_level,
            "source": self.source,
            "resolution": self.resolution,
            "hdr_level": self.hdr_level,
            "audio_detail": self.audio_detail,
            "subtitle_detail": self.subtitle_detail,
            "source_detail": self.source_detail,
            "resolution_detail": self.resolution_detail,
            "hdr_detail": self.hdr_detail,
            "is_collection": self.is_collection,
            "collection_name": self.collection_name,
            "torrent_name": self.torrent_name,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MediaProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def rank_profiles(profiles: list[MediaProfile]) -> list[MediaProfile]:
    """按五层优先级链从好到差排序。"""
    return sorted(profiles, key=lambda p: p.score_tuple, reverse=True)