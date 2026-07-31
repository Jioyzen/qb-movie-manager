"""Flask web application - QB 影视管理工具 v2.0"""
import json
import os
import sys
import threading
import time

from flask import Flask, render_template, request, jsonify, Response

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import config, PASSWORD_FIELDS
from qb_client import QBClient
from parser import parse_filename
from tmdb_client import TMDBClient
from scoring_engine import MediaProfile, rank_profiles
from media_analyzer import analyze_torrents, unmount_smb
from dedup_engine import DedupEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))

# ─── 全局状态 ───────────────────────────────────────────────

_task_state = {
    "running": False,
    "paused": False,
    "current_step": "",
    "progress": {"current": 0, "total": 0, "message": ""},
    "torrents": [],
    "tmdb_matches": [],
    "profiles": [],
    "dedup_results": [],
    "collection_flags": {},
    "lock": threading.Lock(),
}

# ─── 状态持久化 ───────────────────────────────────────────────

# 启动时清除旧的任务数据，确保每次刷新都是全新开始
def _clear_task_data():
    with _task_state["lock"]:
        _task_state["current_step"] = ""
        _task_state["progress"] = {"current": 0, "total": 0, "message": ""}
        _task_state["torrents"] = []
        _task_state["tmdb_matches"] = []
        _task_state["profiles"] = []
        _task_state["dedup_results"] = []
        _task_state["collection_flags"] = {}
        _task_state["running"] = False
        _task_state["paused"] = False

_clear_task_data()

# ─── 辅助函数 ───────────────────────────────────────────────

def _mask_config(cfg: dict) -> dict:
    d = dict(cfg)
    for key in PASSWORD_FIELDS:
        if key in d and d[key]:
            d[key] = "********"
    return d



def _format_error(e: Exception, step: str = "") -> str:
    """将异常转换为用户友好的中文错误信息。"""
    msg = str(e)
    em = msg.lower()
    if "no such file or directory" in em or "cannot find" in em or "not found" in em:
        return f"文件未找到: {msg[:120]}"
    if "permission denied" in em:
        return f"权限不足，无法读取文件: {msg[:120]}"
    if "mediainfo" in str(e).lower() or "command not found" in em:
        return "MediaInfo 未安装或未找到，请执行: sudo apt-get install mediainfo"
    if "connection refused" in em or "connection reset" in em:
        return f"连接被拒绝，请检查 qBittorrent 是否运行: {msg[:100]}"
    if "connection timeout" in em or "timed out" in em:
        return f"连接超时，请检查网络: {msg[:100]}"
    if "invalid apikey" in em or "invalid api key" in em or "unauthorized" in em:
        return "TMDB API Key 无效，请在配置页面重新填写"
    if "mount error" in em:
        return f"SMB 挂载失败: {msg[:120]}"
    return f"错误: {msg[:200]}"

def _background_task(step: str, func, *args, **kwargs):
    with _task_state["lock"]:
        _task_state["running"] = True
        _task_state["current_step"] = step
        _task_state["progress"] = {"current": 0, "total": 0, "message": "准备中..."}

    def _run():
        try:
            result = func(*args, **kwargs)
            with _task_state["lock"]:
                _store_result(step, result)
                _task_state["running"] = False
                if _task_state.pop("canceled", False):
                    _task_state["progress"]["message"] = "已停止"
                else:
                    _task_state["progress"]["message"] = "完成"
        except Exception as e:
            import traceback
            with _task_state["lock"]:
                _task_state["running"] = False
                _task_state["progress"]["message"] = _format_error(e, step)
                print(f"[{step}] Error: {e}\n{traceback.format_exc()}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


def _store_result(step: str, result):
    if step == "fetch":
        _task_state["torrents"] = result or []
        _task_state["tmdb_matches"] = []
        _task_state["profiles"] = []
        _task_state["dedup_results"] = []
    elif step == "tmdb":
        _task_state["tmdb_matches"] = result or []
        _task_state["profiles"] = []
        _task_state["dedup_results"] = []
    elif step == "analyze":
        _task_state["profiles"] = result or []
        _task_state["dedup_results"] = []
    elif step == "dedup":
        _task_state["dedup_results"] = result or []


def _progress_callback(current: int, total: int, message: str):
    with _task_state["lock"]:
        _task_state["progress"] = {"current": current, "total": total, "message": message}


def _update_collection_flags(torrents: list[dict]):
    """通过 qBittorrent 文件列表判断每个种子是否为合集（>=2个不同视频文件）。"""
    import requests as req
    import re as _re
    qb_url = config.qb_url
    username = config.get("qb_username")
    password = config.get("qb_password")
    min_size = config.get("min_file_size_mb", 300) * 1024 * 1024

    try:
        session = req.Session()
        session.post(f"{qb_url}/api/v2/auth/login",
                     data={"username": username, "password": password}, timeout=10)
    except Exception:
        return

    def _is_extras(fname: str) -> bool:
        """排除花絮、删减片段、Sample 等非正片文件。"""
        n = fname.lower()
        return any(kw in n for kw in ['删减', 'deleted.scene', 'deleted_scene', 'extra', 'sample',
                                       'trailer', 'featurette', 'behind.the.scenes', 'making.of',
                                       'interview', 'bts', 'short', '预告', '花絮', '拍摄花絮'])

    def _is_multi_part(names: list[str]) -> bool:
        """2-4 个视频文件且共享相同前缀 → 分卷电影，非合集。"""
        if len(names) < 2 or len(names) > 4:
            return False
        stripped = set()
        for n in names:
            s = _re.sub(r'[.\s_-]*(part|pt|cd|disc)[.\s_-]*\d+.*', '', n, flags=_re.I)
            s = _re.sub(r'[.\s_-]*[ⅠⅡⅢⅣⅤⅥ]', '', s)
            # 去掉 .mkv 后缀后的文件名
            s = _re.sub(r'\.\w+$', '', s.strip().lower())
            stripped.add(s)
        return len(stripped) == 1

    flags = {}
    for t in torrents:
        h = t.get("hash", "")
        if not h:
            continue
        try:
            r = session.get(f"{qb_url}/api/v2/torrents/files",
                            params={"hash": h}, timeout=30)
            if r.status_code != 200:
                continue
            files = r.json()
            # 筛选视频文件（排除小文件和花絮）
            video_files = [f for f in files
                           if f.get("name", "").lower().endswith((".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov"))
                           and f.get("size", 0) >= min_size
                           and not _is_extras(f.get("name", ""))]
            # 2+ 个视频文件且不是同一部电影的分卷 → 合集
            if len(video_files) >= 2:
                names = [f.get("name", "") for f in video_files]
                flags[h] = not _is_multi_part(names)
            else:
                flags[h] = False
        except Exception:
            continue

    with _task_state["lock"]:
        _task_state["collection_flags"] = flags


def is_collection(torrent_hash: str) -> bool:
    """检查当前缓存中该种子是否为合集。"""
    with _task_state["lock"]:
        return _task_state["collection_flags"].get(torrent_hash, False)


# ─── 配置 API ───────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify({"status": "ok", "config": _mask_config(config.all())})


@app.route("/api/config", methods=["PUT"])
def api_set_config():
    data = request.get_json(silent=True) or {}
    for key in PASSWORD_FIELDS:
        if key in data and data[key] == "********":
            del data[key]
    config.set_multi(data)
    return jsonify({"status": "ok", "config": _mask_config(config.all())})


@app.route("/api/config/test-qb", methods=["POST"])
def api_test_qb():
    data = request.get_json(silent=True) or {}
    orig_host = config.get("qb_host")
    orig_port = config.get("qb_port")
    orig_user = config.get("qb_username")
    orig_pass = config.get("qb_password")

    if "qb_host" in data:
        config.set("qb_host", data["qb_host"])
    if "qb_port" in data:
        config.set("qb_port", data["qb_port"])
    if "qb_username" in data:
        config.set("qb_username", data["qb_username"])
    if "qb_password" in data and data["qb_password"] != "********":
        config.set("qb_password", data["qb_password"])

    try:
        ok, msg = QBClient().test_connection()
        return jsonify({"status": "ok" if ok else "error", "message": msg})
    finally:
        config.set("qb_host", orig_host)
        config.set("qb_port", orig_port)
        config.set("qb_username", orig_user)
        config.set("qb_password", orig_pass)


@app.route("/api/config/test-smb", methods=["POST"])
def api_test_smb():
    """Test SMB connection by attempting to mount and list files."""
    import subprocess, os
    data = request.get_json(silent=True) or {}
    host = data.get("smb_host", config.get("smb_host"))
    share = data.get("smb_share", config.get("smb_share"))
    username = data.get("smb_username", config.get("smb_username"))
    password = data.get("smb_password", config.get("smb_password"))
    mount_point = data.get("smb_mount_point", config.get("smb_mount_point"))

    # Try mount
    if os.path.ismount(mount_point):
        try:
            dirs = [d for d in os.listdir(mount_point) if os.path.isdir(os.path.join(mount_point, d))]
            return jsonify({"status": "ok", "message": f"已挂载，找到 {len(dirs)} 个目录", "dirs": dirs[:20]})
        except Exception as e:
            return jsonify({"status": "error", "message": f"挂载异常: {e}"})

    os.makedirs(mount_point, exist_ok=True)
    try:
        opts = f"username={username},password={password},iocharset=utf8,file_mode=0755,dir_mode=0755,noexec,nosuid,nodev"
        r = subprocess.run(["mount", "-t", "cifs", f"//{host}/{share}", mount_point, "-o", opts],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            r = subprocess.run(["sudo", "mount", "-t", "cifs", f"//{host}/{share}", mount_point, "-o", opts],
                capture_output=True, text=True, timeout=15)
    except Exception as e:
        return jsonify({"status": "error", "message": f"挂载失败: {e}"})

    if not os.path.ismount(mount_point):
        return jsonify({"status": "error", "message": "挂载失败，请检查地址和认证信息"})

    try:
        dirs = [d for d in os.listdir(mount_point) if os.path.isdir(os.path.join(mount_point, d))]
        return jsonify({"status": "ok", "message": f"挂载成功，找到 {len(dirs)} 个目录", "dirs": dirs[:20]})
    except Exception as e:
        return jsonify({"status": "ok", "message": f"挂载成功但无法读取目录: {e}"})
    finally:
        try:
            subprocess.run(["umount", mount_point], capture_output=True, timeout=10)
        except Exception:
            pass


@app.route("/api/config/verify", methods=["POST"])
def api_verify_config():
    """Verify all config: SMB mount + TMDB API key."""
    import subprocess, os
    api_key = config.get("tmdb_api_key")
    use_local = config.get("use_local_path", False)

    issues = []

    # 1. Test path accessibility
    if use_local:
        local_path = config.get("local_path", "")
        if not local_path:
            issues.append("请配置本地路径")
        elif not os.path.isdir(local_path):
            issues.append(f"本地路径不存在: {local_path}")
        else:
            try:
                dirs = [d for d in os.listdir(local_path) if os.path.isdir(os.path.join(local_path, d))]
                if not dirs:
                    issues.append("本地路径下未找到子目录，请确认路径是否正确")
            except Exception as e:
                issues.append(f"本地路径读取失败: {e}")
    else:
        smb_host = config.get("smb_host")
        smb_share = config.get("smb_share")
        username = config.get("smb_username")
        password = config.get("smb_password")
        mount_point = config.get("smb_mount_point")

        if os.path.ismount(mount_point):
            pass
        else:
            os.makedirs(mount_point, exist_ok=True)
            try:
                opts = f"username={username},password={password},iocharset=utf8,file_mode=0755,dir_mode=0755,noexec,nosuid,nodev"
                r = subprocess.run(["mount", "-t", "cifs", f"//{smb_host}/{smb_share}", mount_point, "-o", opts],
                    capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    r = subprocess.run(["sudo", "mount", "-t", "cifs", f"//{smb_host}/{smb_share}", mount_point, "-o", opts],
                        capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    err_msg = r.stderr.strip() if r.stderr else "未知错误"
                    if "Permission denied" in err_msg or "not permitted" in err_msg:
                        err_msg = "用户无挂载权限，可尝试在宿主机手动挂载后使用本地路径模式"
                    elif "mount error(2)" in err_msg or "No such file" in err_msg:
                        err_msg = f"无法连接到 {smb_host}，请检查地址和网络"
                    elif "mount error(13)" in err_msg:
                        err_msg = "SMB 认证失败，请检查用户名和密码"
                    elif "mount error(112)" in err_msg:
                        err_msg = f"连接 {smb_host} 超时，请检查网络和防火墙"
                    issues.append(f"SMB 挂载失败: {err_msg}")
                elif not os.path.ismount(mount_point):
                    issues.append("SMB 挂载失败，请检查地址和认证信息")
            except Exception as e:
                issues.append(f"SMB 测试异常: {e}")

    # 2. Check TMDB key
    if not api_key or len(api_key) < 10:
        issues.append("TMDB API Key 无效")

    # 3. Check categories
    cats = config.get("categories", [])
    if not cats:
        issues.append("请至少选择一个分类")

    return jsonify({
        "status": "ok" if not issues else "error",
        "issues": issues,
        "message": "配置验证通过" if not issues else "；".join(issues),
    })


# ─── 种子 API ───────────────────────────────────────────────

@app.route("/api/categories", methods=["GET"])
def api_get_categories():
    try:
        cats = QBClient().get_categories()
        return jsonify({"status": "ok", "categories": cats})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/torrents/fetch", methods=["POST"])
def api_fetch_torrents():
    if _task_state["running"]:
        return jsonify({"status": "error", "error": "后台任务正在运行"}), 400

    data = request.get_json(silent=True) or {}
    categories = data.get("categories", config.get("categories", []))

    def _fetch():
        qb = QBClient()
        all_torrents = []
        for cat in categories:
            try:
                all_torrents.extend(qb.get_torrents(category=cat))
            except Exception as e:
                _progress_callback(0, 0, f"获取 {cat} 失败: {e}")
                return
        seen = set()
        unique = []
        for t in all_torrents:
            h = t.get("hash", "")
            if h and h not in seen:
                seen.add(h)
                unique.append(t)
        unique.sort(key=lambda x: x.get("name", "").lower())

        # 通过文件数量判断合集
        _update_collection_flags(unique)

        _progress_callback(len(unique), len(unique), f"获取完成，共 {len(unique)} 个种子")
        return unique

    _background_task("fetch", _fetch)
    return jsonify({"status": "ok", "message": "开始获取种子列表"})


@app.route("/api/torrents", methods=["GET"])
def api_get_torrents():
    with _task_state["lock"]:
        torrents = list(_task_state["torrents"])
    result = []
    for t in torrents:
        result.append({
            "hash": t.get("hash", ""),
            "name": t.get("name", ""),
            "category": t.get("category", ""),
            "size": t.get("size", 0),
            "save_path": t.get("save_path", ""),
            "is_collection": is_collection(t.get("hash", "")),
        })
    return jsonify({"status": "ok", "torrents": result, "count": len(result)})


# ─── TMDB 匹配 API ──────────────────────────────────────────

@app.route("/api/tmdb/match", methods=["POST"])
def api_tmdb_match():
    if _task_state["running"]:
        step_label = "深度分析" if _task_state.get("current_step") == "analyze" else "后台任务"
        return jsonify({"status": "error", "error": f"{step_label}正在进行中，请等待完成后再开始TMDB匹配"}), 400

    with _task_state["lock"]:
        torrents = list(_task_state["torrents"])

    if not torrents:
        return jsonify({"status": "error", "error": "请先获取种子列表"}), 400

    def _run_tmdb():
        client = TMDBClient()
        collection_strategy = config.get("collection_strategy", "skip")
        total = len(torrents)
        matches = []
        for idx, t in enumerate(torrents):
            # 检查暂停
            while True:
                with _task_state["lock"]:
                    if not _task_state["paused"]:
                        break
                time.sleep(1)

            # 检查停止请求
            with _task_state["lock"]:
                if _task_state.get("stop_requested"):
                    _task_state["stop_requested"] = False
                    _task_state["canceled"] = True
                    _task_state["progress"] = {"current": idx, "total": total, "message": f"匹配已停止，已匹配 {len(matches)} 个"}
                    return matches

            if idx > 0 and idx % 10 == 0:
                _progress_callback(idx, total, f"匹配中 ({idx}/{total})")
            try:
                seed_name = t.get("name", "")
                is_col = is_collection(t.get("hash", ""))
                # 初始化 year，确保即使 try 块失败也有值
                year = ""

                if is_col and collection_strategy == "skip":
                    continue

                parsed = parse_filename(seed_name)
                title = parsed.get("guess_title", "") or parsed.get("chinese_title", "")
                year = parsed.get("year", "")

                tmdb_id = ""
                tmdb_title_cn = ""
                tmdb_title_en = ""
                tmdb_year = ""
                tmdb_rating = ""

                if title:
                    result = client.match_entry(seed_name, title, year)
                    if result and result.get("tmdb_id"):
                        tmdb_id = result["tmdb_id"]
                        tmdb_title_cn = result["tmdb_title_cn"]
                        tmdb_title_en = result["tmdb_title_en"]
                        tmdb_year = result.get("tmdb_year", "")
                        tmdb_rating = result.get("tmdb_rating", "")
            except Exception as e:
                print(f"[tmdb] Error {t.get('name','')[:40]}: {e}", flush=True)

            entry = {
                "torrent_hash": t.get("hash", ""),
                "torrent_name": t.get("name", ""),
                "category": t.get("category", ""),
                "parsed_title": title if 'title' in dir() else "",
                "parsed_year": year if 'year' in dir() else "",
                "tmdb_id": tmdb_id if 'tmdb_id' in dir() else "",
                "tmdb_title_cn": tmdb_title_cn if 'tmdb_title_cn' in dir() else "",
                "tmdb_title_en": tmdb_title_en if 'tmdb_title_en' in dir() else "",
                "tmdb_year": tmdb_year if 'tmdb_year' in dir() else "",
                "tmdb_rating": tmdb_rating if 'tmdb_rating' in dir() else "",
                "is_collection": is_col if 'is_col' in dir() else False,
            }
            matches.append(entry)
            # 每匹配一个就实时更新到全局状态
            with _task_state["lock"]:
                _task_state["tmdb_matches"] = list(matches)

        _progress_callback(total, total, f"TMDB 匹配完成，共 {total} 个种子")
        return matches

    _background_task("tmdb", _run_tmdb)
    return jsonify({"status": "ok", "message": "开始 TMDB 匹配"})


@app.route("/api/tmdb/results", methods=["GET"])
def api_tmdb_results():
    with _task_state["lock"]:
        matches = list(_task_state["tmdb_matches"])
    total = len(matches)
    protected = sum(1 for m in matches if m.get("tmdb_id", "").startswith("protected:"))
    matched = sum(1 for m in matches if m.get("tmdb_id") and not m.get("tmdb_id", "").startswith("protected:"))
    return jsonify({
        "status": "ok",
        "matches": matches,
        "total": total,
        "total_to_match": total - protected,
        "matched": matched,
        "unmatched": total - protected - matched,
        "protected": protected,
    })


@app.route("/api/tmdb/update", methods=["POST"])
def api_tmdb_update():
    """手动更新某个种子的 TMDB 匹配结果。"""
    data = request.get_json(silent=True) or {}
    torrent_hash = data.get("torrent_hash", "")
    tmdb_id = data.get("tmdb_id", "")
    tmdb_title_cn = data.get("tmdb_title_cn", "")
    tmdb_title_en = data.get("tmdb_title_en", "")
    tmdb_rating = data.get("tmdb_rating", "")

    if not torrent_hash or not tmdb_id:
        return jsonify({"status": "error", "error": "参数不完整"}), 400

    with _task_state["lock"]:
        for m in _task_state["tmdb_matches"]:
            if m.get("torrent_hash") == torrent_hash:
                m["tmdb_id"] = tmdb_id
                m["tmdb_title_cn"] = tmdb_title_cn or m.get("parsed_title", "")
                m["tmdb_title_en"] = tmdb_title_en or m.get("parsed_title", "")
                m["tmdb_rating"] = tmdb_rating
                break

    return jsonify({"status": "ok", "message": f"已更新 {torrent_hash[:16]} -> TMDB ID {tmdb_id}"})


@app.route("/api/tmdb/fetch", methods=["POST"])
def api_tmdb_fetch():
    """通过 TMDB ID 获取电影信息。"""
    data = request.get_json(silent=True) or {}
    tmdb_id = data.get("tmdb_id", "")
    if not tmdb_id or not tmdb_id.isdigit():
        return jsonify({"status": "error", "error": "无效的 TMDB ID"}), 400
    from tmdb_client import TMDBClient
    client = TMDBClient()
    tid, tcn, ten, tr = client.fetch_by_id(tmdb_id)
    if tid:
        return jsonify({"status": "ok", "tmdb_id": str(tid), "tmdb_title_cn": tcn, "tmdb_title_en": ten, "tmdb_rating": tr})
    return jsonify({"status": "error", "error": "未找到该 ID 对应的电影"}), 404


@app.route("/api/tmdb/pause", methods=["POST"])
def api_tmdb_pause():
    """Toggle pause/resume for TMDB matching."""
    with _task_state["lock"]:
        _task_state["paused"] = not _task_state["paused"]
        paused = _task_state["paused"]
    return jsonify({"status": "ok", "paused": paused})


@app.route("/api/tmdb/stop", methods=["POST"])
def api_tmdb_stop():
    """Stop TMDB matching immediately."""
    with _task_state["lock"]:
        _task_state["stop_requested"] = True
    return jsonify({"status": "ok", "message": "正在停止匹配..."})


@app.route("/api/tmdb/live", methods=["GET"])
def api_tmdb_live():
    """实时返回当前匹配进度和结果。"""
    with _task_state["lock"]:
        matches = list(_task_state["tmdb_matches"])
        running = _task_state["running"]
        paused = _task_state["paused"]
        progress = dict(_task_state["progress"])
    total = len(matches)
    protected = sum(1 for m in matches if m.get("tmdb_id", "").startswith("protected:"))
    matched = sum(1 for m in matches if m.get("tmdb_id") and not m.get("tmdb_id", "").startswith("protected:"))
    return jsonify({
        "status": "ok",
        "running": running,
        "paused": paused,
        "progress": progress,
        "matches": matches,
        "total": total,
        "matched": matched,
        "protected": protected,
    })


# ─── 深度分析 API ───────────────────────────────────────────

@app.route("/api/analyze/start", methods=["POST"])
def api_analyze_start():
    if _task_state["running"]:
        step_label = "TMDB匹配" if _task_state.get("current_step") == "tmdb" else "后台任务"
        return jsonify({"status": "error", "error": f"{step_label}正在进行中，请等待完成后再开始深度分析"}), 400

    with _task_state["lock"]:
        torrents = list(_task_state["torrents"])

    if not torrents:
        return jsonify({"status": "error", "error": "请先获取种子列表"}), 400

    def _run_analyze():
        collection_strategy = config.get("collection_strategy", "skip")
        # 合集模式下跳过合集种子
        if collection_strategy == "skip":
            analyze_list = [t for t in torrents if not is_collection(t.get("hash", ""))]
            skipped = sum(1 for t in torrents if is_collection(t.get("hash", "")))
            if skipped:
                print(f"[analyze] 跳过 {skipped} 个合集种子（保护模式）", flush=True)
            analyze_list = [t for t in torrents if not is_collection(t.get("hash", ""))]
        else:
            analyze_list = torrents

        def _control_check():
            """检查暂停和停止。返回 True 表示应停止分析。"""
            # 暂停处理
            while True:
                with _task_state["lock"]:
                    if not _task_state.get("paused"):
                        break
                time.sleep(1)
            # 停止处理
            with _task_state["lock"]:
                if _task_state.get("stop_requested"):
                    _task_state["stop_requested"] = False
                    _task_state["canceled"] = True
                    return True
            return False

        profiles = analyze_torrents(
            analyze_list,
            progress_callback=_progress_callback,
            control_callback=_control_check,
            collection_check=is_collection,
        )

        _progress_callback(len(profiles), len(profiles), f"分析完成，共 {len(profiles)} 个视频文件")
        return profiles

    _background_task("analyze", _run_analyze)
    return jsonify({"status": "ok", "message": "开始深度分析"})


@app.route("/api/analyze/profiles", methods=["GET"])
def api_get_profiles():
    with _task_state["lock"]:
        profiles = [p.to_dict() for p in _task_state["profiles"]]
    return jsonify({"status": "ok", "profiles": profiles, "count": len(profiles)})


@app.route("/api/analyze/stop", methods=["POST"])
def api_analyze_stop():
    """Stop deep analysis immediately."""
    with _task_state["lock"]:
        _task_state["stop_requested"] = True
    return jsonify({"status": "ok", "message": "正在停止分析..."})


@app.route("/api/analyze/pause", methods=["POST"])
def api_analyze_pause():
    """Toggle pause/resume for deep analysis."""
    with _task_state["lock"]:
        _task_state["paused"] = not _task_state["paused"]
        paused = _task_state["paused"]
    return jsonify({"status": "ok", "paused": paused})


# ─── 去重 API ───────────────────────────────────────────────

@app.route("/api/dedup/run", methods=["POST"])
def api_dedup_run():
    if _task_state["running"]:
        return jsonify({"status": "error", "error": "后台任务正在运行"}), 400

    data = request.get_json(silent=True) or {}
    priority_layers = data.get("priority_layers")
    priority_order = data.get("priority_order")

    with _task_state["lock"]:
        profiles = list(_task_state["profiles"])
        tmdb_matches = list(_task_state["tmdb_matches"])

    if not profiles:
        return jsonify({"status": "error", "error": "请先完成深度分析"}), 400

    def _run_dedup():
        engine = DedupEngine(profiles, tmdb_matches,
                             priority_layers=priority_layers,
                             priority_order=priority_order)
        results = engine.to_dict()
        summary = engine.get_summary()
        _progress_callback(0, 0, f"去重完成，发现 {summary['duplicate_groups']} 组重复，{summary['delete_candidates']} 个待删除")
        return results

    _background_task("dedup", _run_dedup)
    return jsonify({"status": "ok", "message": "开始去重计算"})


@app.route("/api/dedup/results", methods=["GET"])
def api_get_dedup():
    with _task_state["lock"]:
        results = list(_task_state["dedup_results"])
        summary = _compute_summary(results)
    return jsonify({"status": "ok", "summary": summary, "groups": results})


def _compute_summary(results: list[dict]) -> dict:
    dup_groups = [g for g in results if g.get("delete")]
    total_delete = sum(len(g.get("delete", [])) for g in dup_groups)
    return {
        "total_groups": len(results),
        "duplicate_groups": len(dup_groups),
        "delete_candidates": total_delete,
    }


# ─── 删除 API ───────────────────────────────────────────────

@app.route("/api/torrents/delete", methods=["POST"])
def api_delete_torrents():
    data = request.get_json(silent=True) or {}
    hashes = data.get("hashes", [])
    delete_files = data.get("delete_files", True)

    if not hashes:
        return jsonify({"status": "error", "error": "未提供种子 hash"}), 400

    try:
        client = QBClient()
        client.delete_torrents(hashes, delete_files=delete_files)
        with _task_state["lock"]:
            _task_state["torrents"] = [
                t for t in _task_state["torrents"]
                if t.get("hash", "") not in hashes
            ]
            _task_state["profiles"] = [
                p for p in _task_state["profiles"]
                if p.torrent_hash not in hashes
            ]
        return jsonify({"status": "ok", "deleted": len(hashes)})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# ─── 进度 API ───────────────────────────────────────────────

@app.route("/api/progress", methods=["GET"])
def api_get_progress():
    with _task_state["lock"]:
        return jsonify({
            "running": _task_state["running"],
            "current_step": _task_state["current_step"],
            "progress": dict(_task_state["progress"]),
        })


@app.route("/api/status", methods=["GET"])
def api_get_status():
    """返回各步骤数据状态（用于前端初始化检查）。"""
    with _task_state["lock"]:
        return jsonify({
            "running": _task_state["running"],
            "current_step": _task_state["current_step"],
            "progress": dict(_task_state["progress"]),
            "has_torrents": len(_task_state["torrents"]) > 0,
            "has_tmdb": len(_task_state["tmdb_matches"]) > 0,
            "has_profiles": len(_task_state["profiles"]) > 0,
            "has_dedup": len(_task_state["dedup_results"]) > 0,
        })


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Clear all cached data. Called on page refresh."""
    with _task_state["lock"]:
        _task_state["torrents"] = []
        _task_state["tmdb_matches"] = []
        _task_state["profiles"] = []
        _task_state["dedup_results"] = []
        _task_state["collection_flags"] = {}
        _task_state["running"] = False
        _task_state["current_step"] = ""
        _task_state["progress"] = {"current": 0, "total": 0, "message": ""}
    return jsonify({"status": "ok"})


# ─── 前端入口 ───────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─── 清理 ───────────────────────────────────────────────────

@app.teardown_appcontext
def cleanup(exception=None):
    pass


if __name__ == "__main__":
    import os
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    try:
        app.run(host="0.0.0.0", port=5000, debug=debug)
    finally:
        unmount_smb()