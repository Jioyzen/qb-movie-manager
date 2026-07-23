"""Media Analyzer - SMB mount + MediaInfo deep analysis.

Uses qBittorrent API to get per-torrent file lists, then runs mediainfo
on each video file via SMB mount. Results are cached.
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
from scoring_engine import MediaProfile

# ─── Collection seed keywords ───────────────────────────────────

COLLECTION_KEYWORDS = re.compile(
    r"(合集|collection|\bpack\b|box.?set|trilogy|\bseries\b|全集|系列|"
    r"\b\d{1,2}\s*(film|movie|disc|dvd)\s*(collection|box|pack)?\b|"
    r"\b\d{1,2}-Film\b|\b\d{1,2}-\d{1,2}\b|"
    r"\bComplete\s+(Collection|Series|Box|Pack|Set|Bundle)\b|"
    r"Anthology|Bundle|套装|全集?)",
    re.I,
)

# ─── Cache ──────────────────────────────────────────────────────

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


# ─── SMB mount management ───────────────────────────────────────

def _ensure_mount(mount_point: str) -> bool:
    """Ensure SMB share is mounted."""
    if os.path.ismount(mount_point):
        return True
    host = config.get("smb_host")
    share = config.get("smb_share")
    username = config.get("smb_username")
    password = config.get("smb_password")
    os.makedirs(mount_point, exist_ok=True)
    try:
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


def _smb_path(qb_save_path: str, torrent_name: str) -> Optional[str]:
    """Map qBittorrent save_path to local SMB mount path."""
    mount_point = config.get("smb_mount_point")
    prefix = config.get("qb_download_prefix")
    if qb_save_path.startswith(prefix):
        relative = qb_save_path[len(prefix):].lstrip("/")
    else:
        relative = qb_save_path.lstrip("/")
    # The torrent content is at save_path/torrent_name
    return os.path.join(mount_point, relative, torrent_name)


# ─── qBittorrent file list API ──────────────────────────────────

def _get_torrent_files(hash: str) -> list[dict]:
    """Fetch file list for a torrent via qBittorrent API."""
    import requests
    session = requests.Session()
    qb_url = config.qb_url
    try:
        # Login
        r = session.post(
            f"{qb_url}/api/v2/auth/login",
            data={"username": config.get("qb_username"), "password": config.get("qb_password")},
            timeout=10
        )
        # Get files
        r = session.get(
            f"{qb_url}/api/v2/torrents/files",
            params={"hash": hash},
            timeout=30
        )
        if r.status_code != 200:
            return []
        files = r.json()
        # Add index
        for i, f in enumerate(files):
            f["index"] = i
        return files
    except Exception as e:
        print(f"[qb_files] Error for {hash}: {e}", flush=True)
        return []


# ─── MediaInfo parsing ──────────────────────────────────────────

def _run_mediainfo(file_path: str) -> Optional[dict]:
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
    """Detect HDR type from video tracks."""
    for track in video_tracks:
        hdr_format = (track.get("HDR_Format", "") or "").lower()
        hdr_profile = (track.get("HDR_Format_Profile", "") or "").lower()
        hdr_compat = (track.get("HDR_Format_Compatibility", "") or "").lower()

        if "dolby vision" in hdr_format:
            if "08" in hdr_profile or "p8" in hdr_profile:
                return "dv_p8", "Dolby Vision P8"
            if "07" in hdr_profile or "p7" in hdr_profile:
                return "dv_p7", "Dolby Vision P7 (双层)"
            if "05" in hdr_profile or "p5" in hdr_profile:
                return "dv_p5", "Dolby Vision P5"
            if "smpte st 2086" in hdr_compat:
                return "dv_p8", "Dolby Vision P8"
            return "dv_p7", "Dolby Vision (未知 Profile)"

        if "hdr10+" in hdr_format:
            return "hdr10plus", "HDR10+"

        if "hdr10" in hdr_format or "hdr10" in hdr_compat or "smpte st 2086" in hdr_compat:
            return "hdr10", "HDR10"

    return "sdr", "SDR"


def _detect_audio_level(audio_tracks: list) -> tuple[str, str]:
    """Detect Chinese audio from audio tracks."""
    for track in audio_tracks:
        lang = (track.get("Language") or "").lower()
        codec = (track.get("Format") or "").lower()
        commercial = (track.get("Commercial_name") or "").lower()

        is_chinese = lang in ("zh", "chi", "cn", "zho", "zh-cn", "zh-tw")
        if not is_chinese:
            continue

        is_atmos = "atmos" in commercial.lower()

        if is_atmos:
            return "chinese_atmos", f"国语 {commercial} Atmos"
        else:
            channels = track.get("Channel(s)", "?")
            return "chinese_audio", f"国语 {commercial or codec} {channels}ch"

    return "none", ""


def _detect_subtitle_level(text_tracks: list) -> tuple[str, str]:
    """Detect Chinese subtitle from text tracks."""
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


# ─── Filename helpers ───────────────────────────────────────────

def _guess_source_from_filename(filename: str) -> str:
    name_lower = filename.lower()
    if any(kw in name_lower for kw in ["bluray", "blu-ray", "bdrip", "bd-rip", "蓝光"]):
        return "bluray"
    elif any(kw in name_lower for kw in ["web-dl", "webdl", "webrip", "web-rip", "web"]):
        return "webdl"
    return "other"


def _guess_resolution_from_filename(filename: str) -> str:
    name_lower = filename.lower()
    if any(kw in name_lower for kw in ["2160", "4k", "uhd"]):
        return "2160p"
    elif "1080" in name_lower:
        return "1080p"
    return "other"


def _guess_hdr_from_filename(filename: str) -> str:
    name_lower = filename.lower()
    if "dolby" in name_lower or "dovi" in name_lower or "dv" in name_lower:
        if "p7" in name_lower:
            return "dv_p7"
        elif "p8" in name_lower:
            return "dv_p8"
        elif "p5" in name_lower:
            return "dv_p5"
        return "dv_p8"
    if "hdr10plus" in name_lower or "hdr10+" in name_lower:
        return "hdr10plus"
    if "hdr" in name_lower:
        return "hdr10"
    return "sdr"


def is_collection_seed(seed_name: str) -> bool:
    return bool(COLLECTION_KEYWORDS.search(seed_name))


# ─── Main analysis ──────────────────────────────────────────────

def analyze_torrents(
    torrents: list[dict],
    progress_callback=None,
) -> list[MediaProfile]:
    """
    Analyze a list of torrents.

    For each torrent:
      1. Get file list from qBittorrent API
      2. Filter video files (> min_size)
      3. For single-movie torrents: analyze the largest video file
      4. For collection torrents: analyze each video file as a separate movie

    Returns a list of MediaProfile, one per movie (not per torrent).
    """
    mount_point = config.get("smb_mount_point")
    min_size_mb = config.get("min_file_size_mb", 300)
    min_size_bytes = min_size_mb * 1024 * 1024

    # Mount SMB
    if not _ensure_mount(mount_point):
        print("[media_analyzer] SMB mount failed, falling back to filename-only", flush=True)
        return _analyze_filename_only(torrents, progress_callback)

    all_profiles = []
    total = len(torrents)

    for idx, torrent in enumerate(torrents):
        if progress_callback:
            progress_callback(idx, total, torrent.get("name", ""))

        hash_val = torrent.get("hash", "")
        seed_name = torrent.get("name", "")
        save_path = torrent.get("save_path", "")
        category = torrent.get("category", "")
        is_collection = is_collection_seed(seed_name)

        # Get file list from qBittorrent API
        files = _get_torrent_files(hash_val)
        if not files:
            # Fallback: filename-only analysis
            mp = _analyze_by_filename(torrent)
            if mp:
                all_profiles.append(mp)
            continue

        # Filter video files
        video_files = []
        for f in files:
            fname = f.get("name", "")
            if not fname.lower().endswith((".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov")):
                continue
            fsize = f.get("size", 0)
            if fsize < min_size_bytes:
                continue
            video_files.append(f)

        if not video_files:
            # No video files found, fallback
            mp = _analyze_by_filename(torrent)
            if mp:
                all_profiles.append(mp)
            continue

        if is_collection:
            # Collection: analyze each video file as a separate movie
            for vf in video_files:
                mp = _analyze_single_video_file(torrent, vf, mount_point, save_path)
                if mp:
                    mp.is_collection = True
                    mp.collection_name = seed_name
                    all_profiles.append(mp)
        else:
            # Single movie: analyze the largest video file
            best_vf = max(video_files, key=lambda x: x.get("size", 0))
            mp = _analyze_single_video_file(torrent, best_vf, mount_point, save_path)
            if mp:
                all_profiles.append(mp)

    if progress_callback:
        progress_callback(total, total, "分析完成")

    return all_profiles


def _analyze_single_video_file(torrent: dict, vf: dict, mount_point: str, save_path: str) -> Optional[MediaProfile]:
    """Analyze a single video file with MediaInfo."""
    hash_val = torrent.get("hash", "")
    seed_name = torrent.get("name", "")
    category = torrent.get("category", "")

    # File path on SMB
    # qB file "name" is relative to save_path/torrent_name
    # e.g. "Casino.Royale.2006/Casino.Royale.2006.mkv" or just "movie.mkv"
    file_rel_path = vf.get("name", "")
    
    # Build SMB path: mount_point + save_path_without_prefix + file_rel_path
    prefix = config.get("qb_download_prefix")
    if save_path.startswith(prefix):
        relative = save_path[len(prefix):].lstrip("/")
    else:
        relative = save_path.lstrip("/")
    
    # If file_rel_path already starts with torrent name, don't add it again
    full_path = os.path.join(mount_point, relative, file_rel_path)

    file_name = os.path.basename(file_rel_path)
    file_name_noext = os.path.splitext(file_name)[0]
    file_size = vf.get("size", 0)

    # Parse filename for title/year
    parsed = parse_filename(file_name_noext)
    title = parsed.get("guess_title", "") or parsed.get("chinese_title", "") or file_name_noext
    year = parsed.get("year", "")

    # If title is empty or just the filename, try parsing torrent name
    if not title or title == file_name_noext or len(title) < 3:
        parsed2 = parse_filename(seed_name)
        title = parsed2.get("guess_title", "") or parsed2.get("chinese_title", "") or file_name_noext
        if not year and parsed2.get("year"):
            year = parsed2.get("year")

    mp = MediaProfile(
        torrent_hash=hash_val,
        file_index=vf.get("index", 0),
        file_path=full_path,
        file_size=file_size,
        title=title,
        year=year,
        torrent_name=seed_name,
        category=category,
    )

    # Source / Resolution from filename
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

    # Check cache
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

    # Run MediaInfo
    mi_data = _run_mediainfo(full_path)
    if not mi_data:
        # MediaInfo failed, use filename fallback
        mp.hdr_level = _guess_hdr_from_filename(file_name_noext)
        mp.hdr_detail = {
            "dv_p7": "Dolby Vision P7", "dv_p8": "Dolby Vision P8",
            "dv_p5": "Dolby Vision P5", "hdr10plus": "HDR10+",
            "hdr10": "HDR10", "sdr": "SDR",
        }.get(mp.hdr_level, "SDR")
        return mp

    # Parse MediaInfo JSON
    try:
        tracks = mi_data.get("media", {}).get("track", [])
        video_tracks = [t for t in tracks if t.get("@type") == "Video"]
        audio_tracks = [t for t in tracks if t.get("@type") == "Audio"]
        text_tracks = [t for t in tracks if t.get("@type") == "Text"]

        mp.hdr_level, mp.hdr_detail = _detect_hdr_level(video_tracks)
        mp.audio_level, mp.audio_detail = _detect_audio_level(audio_tracks)
        mp.subtitle_level, mp.subtitle_detail = _detect_subtitle_level(text_tracks)

        # Write cache
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


def _analyze_by_filename(torrent: dict) -> Optional[MediaProfile]:
    """Fallback: analyze using only filename (no MediaInfo)."""
    seed_name = torrent.get("name", "")
    parsed = parse_filename(seed_name)

    mp = MediaProfile(
        torrent_hash=torrent.get("hash", ""),
        file_index=0,
        file_path="",
        file_size=torrent.get("size", 0),
        title=parsed.get("guess_title", "") or parsed.get("chinese_title", "") or seed_name,
        year=parsed.get("year", ""),
        torrent_name=seed_name,
        category=torrent.get("category", ""),
    )

    mp.source = _guess_source_from_filename(seed_name)
    mp.source_detail = {
        "bluray": "BluRay", "webdl": "WEB-DL", "other": parsed.get("source", "未知"),
    }.get(mp.source, "未知")
    mp.resolution = _guess_resolution_from_filename(seed_name)
    mp.resolution_detail = {
        "2160p": "4K", "1080p": "1080p", "other": parsed.get("screen_size", "未知"),
    }.get(mp.resolution, "未知")
    mp.hdr_level = _guess_hdr_from_filename(seed_name)
    mp.hdr_detail = {
        "dv_p7": "Dolby Vision P7", "dv_p8": "Dolby Vision P8",
        "dv_p5": "Dolby Vision P5", "hdr10plus": "HDR10+",
        "hdr10": "HDR10", "sdr": "SDR",
    }.get(mp.hdr_level, "SDR")

    return mp


def _analyze_filename_only(
    torrents: list[dict],
    progress_callback=None,
) -> list[MediaProfile]:
    """Fallback when SMB is unavailable."""
    all_profiles = []
    total = len(torrents)
    for idx, torrent in enumerate(torrents):
        if progress_callback:
            progress_callback(idx, total, torrent.get("name", ""))
        mp = _analyze_by_filename(torrent)
        if mp:
            all_profiles.append(mp)
    if progress_callback:
        progress_callback(total, total, "分析完成")
    return all_profiles


def unmount_smb():
    mount_point = config.get("smb_mount_point")
    if os.path.ismount(mount_point):
        try:
            subprocess.run(["sudo", "umount", mount_point], capture_output=True, timeout=10)
        except Exception:
            pass
