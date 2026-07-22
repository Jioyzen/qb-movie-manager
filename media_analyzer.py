"""Media Analyzer - SMB 挂载 + MediaInfo 深度分析。

通过 SMB 挂载 qBittorrent 下载目录，使用 mediainfo CLI
提取视频文件的音轨、字幕、HDR 详细信息，并缓存结果。
"""

import json
import os
import re
import subprocess
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from config import config
from parser import parse_filename
from scoring_engine import MediaProfile, AUDIO_SCORES, SUBTITLE_SCORES, SOURCE_SCORES, RESOLUTION_SCORES, HDR_SCORES

# ─── 合集种子关键词 ────────────────────────────────────────

COLLECTION_KEYWORDS = re.compile(
    r"(合集|collection|pack|box.?set|trilogy|series|全集|系列|"
    r"1-\d{1,2}\s*(film|movie|disc|dvd)|"
    r"\d{1,2}-Film|Complete|Anthology|Bundle|套装|全集的?)",
    re.I,
)

# ─── 缓存 ───────────────────────────────────────────────────

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_key(file_path: str, file_size: int, mtime: float) -> str:
    h = hashlib.md5(f"{file_path}|{file_size}|{mtime}".encode()).hexdigest()
    return f"{h}.json"


def _read_cache(file_path: str, file_size: int, mtime: float) -> Optional[dict]:
    key = _cache_key(file_path, file_size, mtime)
    cache_file = os.path.join(CACHE_DIR, key)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _write_cache(file_path: str, file_size: int, mtime: float, data: dict):
    key = _cache_key(file_path, file_size, mtime)
    cache_file = os.path.join(CACHE_DIR, key)
    try:
        with open(cache_file, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


# ─── SMB 挂载管理 ──────────────────────────────────────────

def _ensure_mount(mount_point: str) -> bool:
    """确保 SMB 共享已挂载到指定路径。"""
    if os.path.ismount(mount_point):
        return True
    host = config.get("smb_host")
    share = config.get("smb_share")
    username = config.get("smb_username")
    password = config.get("smb_password")
    os.makedirs(mount_point, exist_ok=True)
    try:
        # 使用 sudo 挂载（密码用明文，mount.cifs 不支持输入密码重定向）
        cmd = [
            "sudo", "mount", "-t", "cifs",
            f"//{host}/{share}",
            mount_point,
            "-o", f"username={username},password={password},iocharset=utf8,file_mode=0755,dir_mode=0755,noexec,nosuid,nodev"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"[smb] Mount error: {result.stderr}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[smb] Mount exception: {e}", flush=True)
        return False


def _smb_path(torrent_save_path: str) -> Optional[str]:
    """将 qBittorrent 的 save_path 映射到本地 SMB 挂载路径。"""
    mount_point = config.get("smb_mount_point")
    prefix = config.get("qb_download_prefix")
    if torrent_save_path.startswith(prefix):
        relative = torrent_save_path[len(prefix):].lstrip("/")
    else:
        # 如果路径不以 /downloads 开头，尝试直接使用
        relative = torrent_save_path.lstrip("/")
    return os.path.join(mount_point, relative)


# ─── MediaInfo 解析 ────────────────────────────────────────

def _run_mediainfo(file_path: str) -> Optional[dict]:
    """运行 mediainfo CLI 并返回 JSON 输出。"""
    try:
        result = subprocess.run(
            ["mediainfo", "--Output=JSON", file_path],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def _detect_hdr_level(video_tracks: list) -> tuple[str, str]:
    """从视频轨中检测 HDR 类型，返回 (hdr_level, hdr_detail)。"""
    for track in video_tracks:
        # Dolby Vision
        dv_version = track.get("HDR_Format_Version", "") or ""
        dv_compat = track.get("HDR_Format_Compatibility", "") or ""
        hdr_format = track.get("HDR_Format", "") or ""

        if "Dolby Vision" in hdr_format or "Dolby Vision" in dv_version:
            # 判断 profile
            if "7.6" in dv_version or "p7" in dv_version.lower():
                return "dv_p7", "Dolby Vision P7 (双层)"
            if "8" in dv_version or "p8" in dv_version.lower():
                return "dv_p8", "Dolby Vision P8"
            if "5" in dv_version or "p5" in dv_version.lower():
                return "dv_p5", "Dolby Vision P5"
            # 通过兼容性判断
            if "SMPTE ST 2094" in dv_compat:
                return "dv_p8", "Dolby Vision P8"
            return "dv_p7", "Dolby Vision (未知 Profile)"

        if "HDR10+" in hdr_format or "HDR10+" in str(track):
            return "hdr10plus", "HDR10+"

        if hdr_format == "HDR10" or "HDR10" in str(track.get("HDR_Format")) or "SMPTE ST 2086" in str(track):
            return "hdr10", "HDR10"

    return "sdr", "SDR"


def _detect_audio_level(audio_tracks: list) -> tuple[str, str]:
    """从音轨中检测中文音轨情况，返回 (audio_level, audio_detail)。"""
    for track in audio_tracks:
        lang = (track.get("Language") or "").lower()
        codec = (track.get("Format") or "").lower()
        profile = (track.get("Format_AdditionalFeatures") or "").lower()
        commercial = (track.get("Commercial_name") or "").lower()

        # 判断是否中文
        is_chinese = lang in ("zh", "chi", "cn", "zho", "zh-cn", "zh-tw")

        if not is_chinese:
            continue

        # 判断是否 Atmos
        is_atmos = "atmos" in profile or "atmos" in commercial or "atmos" in codec

        if is_atmos:
            detail = f"国语 {commercial or codec} Atmos"
            return "chinese_atmos", detail
        else:
            detail = f"国语 {commercial or codec} / {track.get('Channel(s)', '?')}ch"
            return "chinese_audio", detail

    return "none", ""


def _detect_subtitle_level(text_tracks: list) -> tuple[str, str]:
    """从字幕轨中检测中文字幕情况，返回 (subtitle_level, subtitle_detail)。"""
    has_forced = False
    has_normal = False
    forced_detail = ""
    normal_detail = ""

    for track in text_tracks:
        lang = (track.get("Language") or "").lower()
        codec = (track.get("Format") or "").lower()
        forced = (track.get("Forced") or "").lower()

        is_chinese = lang in ("zh", "chi", "cn", "zho", "zh-cn", "zh-tw")
        if not is_chinese:
            continue

        # 判断是否特效字幕 (ASS/SSA 通常支持特效)
        is_advanced = codec in ("ass", "ssa", "pgs")
        is_forced = forced in ("yes", "true")

        if is_forced:
            has_forced = True
            forced_detail = f"中文强制 ({codec.upper()})"
        elif is_advanced:
            has_forced = True
            forced_detail = f"中文特效 ({codec.upper()})"
        else:
            has_normal = True
            normal_detail = f"中文 ({codec.upper()})"

    if has_forced:
        return "chinese_forced", forced_detail
    if has_normal:
        return "chinese_sub", normal_detail
    return "none", ""


def _guess_source_from_filename(filename: str) -> str:
    """从文件名猜测来源。"""
    name_lower = filename.lower()
    if any(kw in name_lower for kw in ["bluray", "blu-ray", "bdrip", "bd-rip", "蓝光"]):
        return "bluray"
    elif any(kw in name_lower for kw in ["web-dl", "webdl", "webrip", "web-rip", "web"]):
        return "webdl"
    return "other"


def _guess_resolution_from_filename(filename: str) -> str:
    """从文件名猜测分辨率。"""
    name_lower = filename.lower()
    if any(kw in name_lower for kw in ["2160", "4k", "uhd"]):
        return "2160p"
    elif "1080" in name_lower:
        return "1080p"
    return "other"


def _guess_hdr_from_filename(filename: str) -> str:
    """从文件名猜测 HDR 类型（备用，当 MediaInfo 不可用时）。"""
    name_lower = filename.lower()
    if "dolby" in name_lower or "dv" in name_lower.split():
        if "p7" in name_lower:
            return "dv_p7"
        elif "p8" in name_lower:
            return "dv_p8"
        elif "p5" in name_lower:
            return "dv_p5"
        return "dv_p7"  # 默认为最好
    if "hdr10plus" in name_lower or "hdr10+" in name_lower:
        return "hdr10plus"
    if "hdr" in name_lower:
        return "hdr10"
    return "sdr"


def is_collection_seed(seed_name: str) -> bool:
    """判断种子名称是否为合集。"""
    return bool(COLLECTION_KEYWORDS.search(seed_name))


# ─── 主分析函数 ────────────────────────────────────────────

def analyze_torrent(
    torrent: dict,
    video_files: list[dict] = None,
    use_mediainfo: bool = True,
) -> list[MediaProfile]:
    """
    分析一个种子，返回 MediaProfile 列表。
    - 单个电影种子：返回 1 个 MediaProfile
    - 合集种子：每个视频文件返回 1 个 MediaProfile
    """
    seed_name = torrent.get("name", "")
    save_path = torrent.get("save_path", "")
    category = torrent.get("category", "")
    is_collection = is_collection_seed(seed_name)

    results = []
    files_to_analyze = []

    if is_collection and video_files:
        # 合集种子：分析每个视频文件
        for vf in video_files:
            vname = os.path.basename(vf.get("name", ""))
            vname_noext = os.path.splitext(vname)[0]
            files_to_analyze.append({
                "file_name": vname,
                "file_name_noext": vname_noext,
                "file_index": vf.get("index", 0),
                "file_size": vf.get("size", 0),
                "file_path": vf.get("name", ""),
            })
    else:
        # 单个电影种子
        files_to_analyze.append({
            "file_name": seed_name,
            "file_name_noext": seed_name,
            "file_index": 0,
            "file_size": torrent.get("size", 0),
            "file_path": torrent.get("content_path", seed_name),
        })

    for f_info in files_to_analyze:
        # 文件名解析
        parsed = parse_filename(f_info["file_name_noext"])

        # 构建 MediaProfile
        mp = MediaProfile(
            torrent_hash=torrent.get("hash", ""),
            file_index=f_info["file_index"],
            file_path=f_info["file_path"],
            file_size=f_info["file_size"],
            title=parsed.get("title", f_info["file_name_noext"]),
            year=parsed.get("year", ""),
            torrent_name=seed_name,
            category=category,
            is_collection=is_collection,
            collection_name=seed_name if is_collection else "",
        )

        # 文件名来源猜测
        mp.source = _guess_source_from_filename(f_info["file_name_noext"])
        mp.source_detail = {
            "bluray": "BluRay",
            "webdl": "WEB-DL",
            "other": parsed.get("source", "未知"),
        }.get(mp.source, "未知")

        mp.resolution = _guess_resolution_from_filename(f_info["file_name_noext"])
        mp.resolution_detail = {
            "2160p": "4K",
            "1080p": "1080p",
            "other": parsed.get("screen_size", "未知"),
        }.get(mp.resolution, "未知")

        # 文件名 HDR 猜测（作为后备）
        if use_mediainfo:
            # 先标记文件名猜测的 HDR，后面 MediaInfo 会覆盖
            mp.hdr_level = _guess_hdr_from_filename(f_info["file_name_noext"])
            mp.hdr_detail = {
                "dv_p7": "Dolby Vision P7 (文件名猜测)",
                "dv_p8": "Dolby Vision P8 (文件名猜测)",
                "dv_p5": "Dolby Vision P5 (文件名猜测)",
                "hdr10plus": "HDR10+ (文件名猜测)",
                "hdr10": "HDR10 (文件名猜测)",
                "sdr": "SDR",
            }.get(mp.hdr_level, "SDR")
        else:
            mp.hdr_level = _guess_hdr_from_filename(f_info["file_name_noext"])
            mp.hdr_detail = {
                "dv_p7": "Dolby Vision P7",
                "dv_p8": "Dolby Vision P8",
                "dv_p5": "Dolby Vision P5",
                "hdr10plus": "HDR10+",
                "hdr10": "HDR10",
                "sdr": "SDR",
            }.get(mp.hdr_level, "SDR")

        results.append(mp)

    return results


def analyze_with_mediainfo(
    torrents: list[dict],
    progress_callback=None,
) -> list[MediaProfile]:
    """
    对种子列表执行完整分析（文件名解析 + MediaInfo）。
    返回所有视频文件的 MediaProfile 列表。
    """
    mount_point = config.get("smb_mount_point")
    min_size_mb = config.get("min_file_size_mb", 300)
    min_size_bytes = min_size_mb * 1024 * 1024

    # 挂载 SMB
    if not _ensure_mount(mount_point):
        print("[media_analyzer] SMB mount failed, falling back to filename-only analysis", flush=True)
        return _analyze_filename_only(torrents, progress_callback)

    all_profiles = []
    total = len(torrents)

    for idx, torrent in enumerate(torrents):
        if progress_callback:
            progress_callback(idx, total, torrent.get("name", ""))

        seed_name = torrent.get("name", "")
        save_path = torrent.get("save_path", "")
        category = torrent.get("category", "")
        is_collection = is_collection_seed(seed_name)

        # 获取 SMB 上的文件列表
        torrent_dir = _smb_path(save_path)
        if not torrent_dir or not os.path.isdir(torrent_dir):
            # 目录不存在，用文件名分析兜底
            profiles = analyze_torrent(torrent, use_mediainfo=False)
            all_profiles.extend(profiles)
            continue

        # 收集视频文件
        video_files = []
        for root, dirs, files in os.walk(torrent_dir):
            for f in files:
                if f.lower().endswith((".mkv", ".mp4", ".avi", ".ts", ".m2ts")):
                    full_path = os.path.join(root, f)
                    try:
                        fsize = os.path.getsize(full_path)
                    except OSError:
                        continue
                    if fsize < min_size_bytes:
                        continue
                    # 相对路径（用于 qB 文件列表匹配）
                    rel_path = os.path.relpath(full_path, torrent_dir)
                    video_files.append({
                        "name": rel_path,
                        "size": fsize,
                        "index": len(video_files),
                        "full_path": full_path,
                    })

        if not video_files:
            profiles = analyze_torrent(torrent, use_mediainfo=False)
            all_profiles.extend(profiles)
            continue

        if is_collection:
            # 合集种子：每个文件单独分析
            for vf in video_files:
                mp = _analyze_single_file(torrent, vf)
                if mp:
                    mp.is_collection = True
                    mp.collection_name = seed_name
                    all_profiles.append(mp)
        else:
            # 单个电影：取最大的视频文件分析
            best_vf = max(video_files, key=lambda x: x["size"])
            mp = _analyze_single_file(torrent, best_vf)
            if mp:
                all_profiles.append(mp)

    if progress_callback:
        progress_callback(total, total, "分析完成")

    return all_profiles


def _analyze_single_file(torrent: dict, vf: dict) -> Optional[MediaProfile]:
    """分析单个视频文件（文件名解析 + MediaInfo）。"""
    full_path = vf["full_path"]
    file_name = os.path.basename(full_path)
    file_name_noext = os.path.splitext(file_name)[0]

    # 文件名解析
    parsed = parse_filename(file_name_noext)

    mp = MediaProfile(
        torrent_hash=torrent.get("hash", ""),
        file_index=vf["index"],
        file_path=full_path,
        file_size=vf["size"],
        title=parsed.get("title", file_name_noext),
        year=parsed.get("year", ""),
        torrent_name=torrent.get("name", ""),
        category=torrent.get("category", ""),
    )

    # 来源 / 分辨率（文件名）
    mp.source = _guess_source_from_filename(file_name_noext)
    mp.source_detail = {
        "bluray": "BluRay",
        "webdl": "WEB-DL",
        "other": parsed.get("source", "未知"),
    }.get(mp.source, "未知")
    mp.resolution = _guess_resolution_from_filename(file_name_noext)
    mp.resolution_detail = {
        "2160p": "4K",
        "1080p": "1080p",
        "other": parsed.get("screen_size", "未知"),
    }.get(mp.resolution, "未知")

    # 检查缓存
    try:
        stat = os.stat(full_path)
        cached = _read_cache(full_path, stat.st_size, stat.st_mtime)
    except OSError:
        cached = None

    if cached:
        mp.audio_level = cached.get("audio_level", "none")
        mp.subtitle_level = cached.get("subtitle_level", "none")
        mp.hdr_level = cached.get("hdr_level", "sdr")
        mp.audio_detail = cached.get("audio_detail", "")
        mp.subtitle_detail = cached.get("subtitle_detail", "")
        mp.hdr_detail = cached.get("hdr_detail", "")
        return mp

    # 运行 MediaInfo
    mi_data = _run_mediainfo(full_path)
    if not mi_data:
        # MediaInfo 失败，用文件名猜测
        mp.hdr_level = _guess_hdr_from_filename(file_name_noext)
        mp.hdr_detail = {"dv_p7": "Dolby Vision P7", "dv_p8": "Dolby Vision P8",
                         "dv_p5": "Dolby Vision P5", "hdr10plus": "HDR10+",
                         "hdr10": "HDR10", "sdr": "SDR"}.get(mp.hdr_level, "SDR")
        return mp

    # 解析 MediaInfo JSON
    try:
        tracks = mi_data.get("media", {}).get("track", [])
        video_tracks = [t for t in tracks if t.get("@type") == "Video"]
        audio_tracks = [t for t in tracks if t.get("@type") == "Audio"]
        text_tracks = [t for t in tracks if t.get("@type") == "Text"]

        # HDR
        mp.hdr_level, mp.hdr_detail = _detect_hdr_level(video_tracks)

        # 音轨
        mp.audio_level, mp.audio_detail = _detect_audio_level(audio_tracks)

        # 字幕
        mp.subtitle_level, mp.subtitle_detail = _detect_subtitle_level(text_tracks)

        # 写入缓存
        try:
            stat = os.stat(full_path)
            _write_cache(full_path, stat.st_size, stat.st_mtime, {
                "audio_level": mp.audio_level,
                "subtitle_level": mp.subtitle_level,
                "hdr_level": mp.hdr_level,
                "audio_detail": mp.audio_detail,
                "subtitle_detail": mp.subtitle_detail,
                "hdr_detail": mp.hdr_detail,
            })
        except OSError:
            pass
    except Exception as e:
        print(f"[media_analyzer] Parse error: {e}", flush=True)

    return mp


def _analyze_filename_only(
    torrents: list[dict],
    progress_callback=None,
) -> list[MediaProfile]:
    """仅使用文件名解析（无 MediaInfo），作为降级方案。"""
    all_profiles = []
    total = len(torrents)
    for idx, torrent in enumerate(torrents):
        if progress_callback:
            progress_callback(idx, total, torrent.get("name", ""))
        profiles = analyze_torrent(torrent, use_mediainfo=False)
        all_profiles.extend(profiles)
    if progress_callback:
        progress_callback(total, total, "分析完成")
    return all_profiles


def unmount_smb():
    """卸载 SMB 挂载。"""
    mount_point = config.get("smb_mount_point")
    if os.path.ismount(mount_point):
        try:
            subprocess.run(["sudo", "umount", mount_point], capture_output=True, timeout=10)
        except Exception:
            pass