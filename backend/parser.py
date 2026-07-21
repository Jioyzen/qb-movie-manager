"""Robust filename parser for PT/BT movie filenames.

Extracts: title, year, resolution, source, codec, hdr, dovi, audio.
Priority: Chinese text in [brackets] > guessit English > regex fallback.
"""
import re
from typing import Optional

try:
    from guessit import guessit
except ImportError:
    guessit = None


# ── regex patterns ──────────────────────────────────────────────
RES_PAT = re.compile(
    r"(2160p|1080p|1080i|720p|576p|480p|4K|UHD|8K)", re.IGNORECASE
)
SOURCE_PAT = re.compile(
    r"(BluRay|Blu-ray|BDRip|BRRip|WEB-DL|WEBRip|WEB|HDTV|DVDRip|"
    r"DSNP|HMAX|NF|AMZN|ATVP|iTunes|IT|HULU|HBO|Peacock|"
    r"DVD|HDDVD|VHS)", re.IGNORECASE
)
CODEC_PAT = re.compile(
    r"(x264|x265|h\.264|h\.265|HEVC|AVC|AV1|VC-1|MPEG-4|MPEG2|MPEG)", re.IGNORECASE
)
HDR_PAT = re.compile(
    r"(HDR10\+?|HDR|HLG|PQ|SDR)", re.IGNORECASE
)
DOVI_PAT = re.compile(
    r"(Dolby[.\s]?Vision|DOVI|DV|DoVi)", re.IGNORECASE
)
AUDIO_PAT = re.compile(
    r"(DTS(-HD)?[.\s]?(MA|HR|X)?|TrueHD|Atmos|AC3[.\s]?\d?\.?\d?|E-?AC3[.\s]?\d?\.?\d?|"
    r"AAC[.\s]?\d?\.?\d?|FLAC|LPCM|PCM|MP3|OGG|Opus|WMA)", re.IGNORECASE
)
YEAR_PAT = re.compile(r"(19\d\d|20\d\d)")


def extract_chinese(text: str) -> str:
    """Extract contiguous Chinese text, prioritizing text in [brackets] then leading chunk."""
    # Priority 1: [brackets] containing Chinese
    for m in re.finditer(r"\[([^\]]*[\u4e00-\u9fff][^\]]*)\]", text):
        cn = "".join(c for c in m.group(1) if "\u4e00" <= c <= "\u9fff")
        if len(cn) >= 2:
            return cn
    # Priority 2: leading segment before first dot
    leading = text.split(".")[0]
    cn = "".join(c for c in leading if "\u4e00" <= c <= "\u9fff")
    if len(cn) >= 2:
        return cn
    # Priority 3: first dot-separated part with Chinese
    for part in text.replace("[", "").replace("]", "").split("."):
        cn = "".join(c for c in part if "\u4e00" <= c <= "\u9fff")
        if len(cn) >= 2:
            return cn
    return ""


def extract_first_english(text: str) -> str:
    """Extract first substantial English segment from filename."""
    parts = text.replace("[", "").replace("]", "").split(".")
    for p in parts:
        p = p.strip()
        if re.match(r"^[A-Za-z][A-Za-z\s'\-]+$", p) and len(p) >= 3:
            return p
    return ""


def parse_filename(filename: str) -> dict:
    """Parse a movie filename into structured fields.

    Returns dict with keys:
      original_name, guess_title, year, resolution, source, codec,
      hdr, dovi, audio_info, chinese_title, english_segment
    """
    result = {
        "original_name": filename,
        "chinese_title": "",
        "english_segment": "",
        "guess_title": "",
        "guess_year": "",
        "year": "",
        "resolution": "",
        "source": "",
        "codec": "",
        "hdr": "",
        "dovi": "",
        "audio_info": "",
    }

    cn = extract_chinese(filename)
    eng = extract_first_english(filename)
    result["chinese_title"] = cn
    result["english_segment"] = eng

    # Use guessit as primary title extractor
    guess = {}
    if guessit:
        try:
            guess = guessit(filename)
        except Exception:
            pass

    if guessit and guess:
        result["guess_title"] = guess.get("title", "")
        result["year"] = str(guess.get("year", ""))
        if not result["year"]:
            ym = YEAR_PAT.search(filename)
            if ym:
                result["year"] = ym.group(1)
        result["guess_year"] = result["year"]
    else:
        # Fallback: extract year, then guess title from remaining
        if not result["year"]:
            ym = YEAR_PAT.search(filename)
            if ym:
                result["year"] = ym.group(1)
        result["guess_year"] = result["year"]
        # Try to extract title from leading part
        base = filename.replace("[", "").replace("]", "")
        parts = base.split(".")
        name_parts = []
        for p in parts:
            is_year = YEAR_PAT.fullmatch(p.strip())
            if is_year:
                break
            name_parts.append(p.strip())
        if name_parts:
            result["guess_title"] = " ".join(
                p for p in name_parts if not re.match(r"^[A-Za-z]$", p)
            )

    if guess:
        result["resolution"] = guess.get("screen_size", "")
        src = guess.get("source", "")
        result["source"] = str(src) if src else ""
        result["codec"] = guess.get("video_codec", "")
        result["audio_info"] = guess.get("audio_codec", "")
        # Normalize codec
        if result["codec"].lower() in ("h264", "h.264", "avc"):
            result["codec"] = "H.264"
        elif result["codec"].lower() in ("h265", "h.265", "hevc"):
            result["codec"] = "H.265"

    # Regex fallback for missing fields
    if not result["resolution"]:
        m = RES_PAT.search(filename)
        if m:
            result["resolution"] = m.group(1).upper().replace("4K", "2160p").replace("UHD", "2160p")

    if not result["source"]:
        m = SOURCE_PAT.search(filename)
        if m:
            result["source"] = m.group(1)

    if not result["codec"]:
        m = CODEC_PAT.search(filename)
        if m:
            codec = m.group(1)
            codec_map = {
                "x264": "x264", "x265": "x265",
                "h.264": "H.264", "h264": "H.264", "avc": "H.264",
                "h.265": "H.265", "h265": "H.265", "hevc": "H.265",
                "av1": "AV1",
            }
            result["codec"] = codec_map.get(codec.lower(), codec)

    # HDR
    m = HDR_PAT.search(filename)
    if m:
        result["hdr"] = m.group(1).upper()
    m = DOVI_PAT.search(filename)
    if m:
        result["dovi"] = "Dolby Vision"

    # Audio
    audio_matches = AUDIO_PAT.findall(filename)
    if audio_matches:
        audios = []
        for am in audio_matches:
            audios.append(am[0] if isinstance(am, tuple) else am)
        result["audio_info"] = " + ".join(
            sorted(set(audios), key=str.lower)
        )

    return result


def is_video_file(filename: str) -> bool:
    """Check if filename is a playable video file."""
    return bool(re.search(r"\.(mkv|mp4|avi|ts|m2ts|iso|bdmv|m4v|mov|wmv)$", filename, re.IGNORECASE))
