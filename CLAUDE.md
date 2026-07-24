# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

QB Movie Manager (qb-movie-manager) — a qBittorrent + TMDB movie deduplication management tool. It scans the qBittorrent torrent library, auto-matches movies via TMDB, performs deep MediaInfo analysis, then applies a 5-layer priority chain (audio → subtitle → source → resolution → HDR) to select the best version of each movie and clean up duplicates. The UI is a Chinese-language single-page app (6-step sidebar workflow).

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# System dependencies (for MediaInfo analysis)
sudo apt-get install mediainfo

# Run the app (development, listens on 0.0.0.0:5000)
python3 app.py

# Production (gunicorn)
gunicorn -w 1 -b 0.0.0.0:5000 app:app

# SMB mount (for MediaInfo analysis of files on NAS)
sudo mkdir -p /mnt/qb_downloads
sudo mount -t cifs //<host>/<share> /mnt/qb_downloads \
  -o username=<user>,password=<pass>,iocharset=utf8,file_mode=0755,dir_mode=0755
```

No tests, linting, or build step is configured.

## Data Directory (`data/`)

| File | Purpose |
|---|---|
| `config.json` | Persistent config (passwords masked in API responses, stored in plaintext on disk) |
| `state.json` | Full step state persisted across restarts (torrents, matches, profiles, dedup results) |
| `cache/` | MediaInfo analysis results, keyed by `md5(file_path\|file_size\|mtime)` |

Config is overridable via `QB_MOVIE_CONFIG` env var. State auto-saves after each step completes.

## Architecture

### Tech Stack
- **Backend:** Flask (Python 3), single-process, threaded background tasks
- **Frontend:** Vanilla JS SPA (no framework), dark theme CSS, ~50KB app.js
- **External APIs:** qBittorrent Web API v2, TMDB v3 API, mediainfo CLI
- **Dependencies:** Flask, requests, guessit, gunicorn

### 6-Step Workflow & API Routes

```
步骤 0: 配置    → 步骤 1: 获取种子 → 步骤 2: TMDB匹配 → 步骤 3: 深度分析 → 步骤 4: 去重 → 步骤 5: 清理删除
```

Each step is a Flask route that starts a background thread (`_background_task`). The frontend polls `/api/progress` every ~1s and renders step-specific views.

| Method | Route | Purpose |
|---|---|---|
| GET/PUT | `/api/config` | Read/write config (passwords masked) |
| POST | `/api/config/test-qb` | Test QB connectivity |
| POST | `/api/config/test-smb` | Test SMB mount |
| POST | `/api/config/verify` | Verify SMB + TMDB + categories |
| GET | `/api/categories` | Fetch QB categories |
| POST | `/api/torrents/fetch` | Start fetch background task |
| GET | `/api/torrents` | Get fetched torrent list |
| POST | `/api/tmdb/match` | Start TMDB matching |
| GET | `/api/tmdb/results` | Get TMDB match results |
| GET | `/api/tmdb/live` | Get real-time matching progress |
| POST | `/api/tmdb/update` | Update single torrent's TMDB ID |
| POST | `/api/tmdb/pause` | Pause matching |
| POST | `/api/tmdb/resume` | Resume matching |
| POST | `/api/analyze/start` | Start deep MediaInfo analysis |
| GET | `/api/analyze/profiles` | Get analysis results |
| POST | `/api/dedup/run` | Run dedup engine |
| GET | `/api/dedup/results` | Get dedup groups + summary |
| POST | `/api/dedup/override` | Manually toggle keep/delete for a profile |
| POST | `/api/cleanup/delete` | Delete selected torrents from QB |
| GET | `/api/progress` | Poll current step progress |

### Key Modules

| Module | Role |
|---|---|
| `app.py` | Flask routes, global state (`_task_state`), background task scheduling, all API endpoints, state persistence |
| `config.py` | JSON config load/save with password masking, singleton `config` object, env-var overridable path |
| `qb_client.py` | qBittorrent Web API v2 wrapper (login, categories, torrents, files, delete) |
| `parser.py` | guessit + regex PT/BT filename parser (title, year, resolution, source, HDR, audio, codec) |
| `tmdb_client.py` | TMDB search with multi-strategy matching (Chinese → English → first segment), roman numeral/version filtering, rate-limited |
| `scoring_engine.py` | `MediaProfile` dataclass + `rank_profiles()` — 5-layer priority chain comparison |
| `media_analyzer.py` | SMB mount management, MediaInfo CLI analysis, cache by file hash, collection detection |
| `dedup_engine.py` | `DedupEngine` + `DedupResult` — groups by TMDB ID, applies priority chain, collection strategy |

### Global State (`_task_state` in app.py)

All step data lives in a shared dict with `threading.Lock`:

```python
_task_state = {
    "running": False,       # background task active
    "paused": False,        # TMDB matching paused
    "current_step": "",     # "fetch" | "tmdb" | "analyze" | "dedup"
    "progress": {"current": 0, "total": 0, "message": ""},
    "torrents": [],         # raw QB torrent list
    "tmdb_matches": [],     # per-torrent TMDB results
    "profiles": [],         # MediaProfile objects from deep analysis
    "dedup_results": [],    # DedupResult dicts
    "collection_flags": {}, # torrent_hash -> bool
    "lock": threading.Lock(),
}
```

State is serialized to `data/state.json` on each step completion and loaded on startup. `_store_result()` cascades: starting a new step clears all downstream data.

### `MediaProfile` Dataclass (scoring_engine.py)

Central data object for each video file. Key fields:

| Field | Values |
|---|---|
| `audio_level` | `chinese_atmos` > `chinese_audio` > `english_atmos` > `english_audio` > `none` |
| `subtitle_level` | `chinese_forced` > `chinese_sub` > `none` |
| `source` | `bluray` > `webdl` > `other` |
| `resolution` | `2160p` > `1080p` > `other` |
| `hdr_level` | `dv_p7` > `dv_p8` > `dv_p5` > `hdr10plus` > `hdr10` > `sdr` |

Each level has a human-readable `_detail` field (e.g. `"国语 TrueHD Atmos 7.1"`). The `score_tuple` property returns a 5-tuple for lexicographic comparison. `is_better_than(other)` implements the full priority chain.

### Priority Chain (scoring_engine.py)

5 layers evaluated in order — first layer to differentiate wins:
1. **Audio:** chinese_atmos > chinese_audio > english_atmos > english_audio > none
2. **Subtitle:** chinese_forced > chinese_sub > none
3. **Source:** bluray > webdl > other
4. **Resolution:** 2160p > 1080p > other
5. **HDR:** dv_p7 > dv_p8 > dv_p5 > hdr10plus > hdr10 > sdr

Layer order and per-layer priority values are configurable via `config.json` (`priority_layers` array, `priority_order` dict).

### Collection Strategy (dedup_engine.py)

Two modes in `config.json`:
- `"skip"` (default): protect collections, never delete them; only dedup among standalone versions
- `"prefer"`: collection version beats standalone versions

Collection detection: counts video files ≥300MB in a torrent, excluding extras/samples/trailers and multi-part movies (Part.1+Part.2 same prefix → single movie, not collection).

### Frontend (static/app.js)

- Single-page app with 6 sidebar steps: `{0: renderConfig, 1: renderFetch, 2: renderTmdb, 3: renderAnalyze, 4: renderDedup, 5: renderCleanup}`
- Polls `/api/progress` every ~1s via `setInterval` for real-time updates
- TMDB matching step uses `/api/tmdb/live` for per-torrent status updates (updates individual table rows)
- State object tracks: `step`, `torrents`, `matches`, `profiles`, `dedup`, `config`, `busy`, `keepOverrides`
- `fixStickyHeaders()` uses JS-based `position: fixed` clones to work around the sticky header penetration bug

### Known Issues

1. **Table header sticky penetration:** `th` with `position: sticky; z-index` still gets content overlaid — caused by `overflow: auto` on parent elements creating new stacking contexts. Fixed via JS `position: fixed` clone approach in `fixStickyHeaders()`.
2. **Deep analysis is slow:** ~2-3s per torrent via mediainfo CLI, serial. `ThreadPoolExecutor` exists in `media_analyzer.py` for file-level parallelism but the main analysis loop in `app.py` still processes one torrent at a time.
3. **TMDB matching is serial:** rate-limited (0.2s default), no parallel workers despite `tmdb_workers` config key. 1500+ torrents takes ~5 minutes.
4. **State.json can grow large:** `state.json` stores full serialized profiles including per-file mediainfo results. The `data/` directory is not in `.gitignore` and may contain sensitive paths from the NAS.