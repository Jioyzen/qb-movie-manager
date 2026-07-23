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
from media_analyzer import analyze_torrents, unmount_smb, is_collection_seed
from dedup_engine import DedupEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))

# ─── 全局状态 ───────────────────────────────────────────────

_task_state = {
    "running": False,
    "current_step": "",
    "progress": {"current": 0, "total": 0, "message": ""},
    "torrents": [],
    "tmdb_matches": [],       # [{tmdb_id, title_cn, title_en, torrent_hash, ...}]
    "profiles": [],
    "dedup_results": [],
    "lock": threading.Lock(),
}

# ─── 辅助函数 ───────────────────────────────────────────────

def _mask_config(cfg: dict) -> dict:
    d = dict(cfg)
    for key in PASSWORD_FIELDS:
        if key in d and d[key]:
            d[key] = "********"
    return d


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
                _task_state["progress"]["message"] = "完成"
        except Exception as e:
            import traceback
            with _task_state["lock"]:
                _task_state["running"] = False
                _task_state["progress"]["message"] = f"错误: {e}"
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
    os.makedirs(mount_point, exist_ok=True)
    try:
        subprocess.run(["sudo", "mount", "-t", "cifs",
            f"//{host}/{share}", mount_point,
            "-o", f"username={username},password={password},iocharset=utf8,file_mode=0755,dir_mode=0755,noexec,nosuid,nodev"],
            capture_output=True, text=True, timeout=15)
    except Exception as e:
        return jsonify({"status": "error", "message": f"挂载失败: {e}"})

    # Check if mounted
    if not os.path.ismount(mount_point):
        return jsonify({"status": "error", "message": "挂载失败，请检查地址和认证信息"})

    # List files
    try:
        dirs = [d for d in os.listdir(mount_point) if os.path.isdir(os.path.join(mount_point, d))]
        return jsonify({"status": "ok", "message": f"挂载成功，找到 {len(dirs)} 个目录", "dirs": dirs[:20]})
    except Exception as e:
        return jsonify({"status": "ok", "message": f"挂载成功但无法读取目录: {e}"})
    finally:
        try:
            subprocess.run(["sudo", "umount", mount_point], capture_output=True, timeout=10)
        except Exception:
            pass


@app.route("/api/config/verify", methods=["POST"])
def api_verify_config():
    """Verify all config: SMB mount + TMDB API key."""
    import subprocess, os
    smb_host = config.get("smb_host")
    smb_share = config.get("smb_share")
    username = config.get("smb_username")
    password = config.get("smb_password")
    mount_point = config.get("smb_mount_point")
    api_key = config.get("tmdb_api_key")

    issues = []

    # 1. Test SMB mount
    os.makedirs(mount_point, exist_ok=True)
    try:
        r = subprocess.run(["sudo", "mount", "-t", "cifs",
            f"//{smb_host}/{smb_share}", mount_point,
            "-o", f"username={username},password={password},iocharset=utf8,file_mode=0755,dir_mode=0755,noexec,nosuid,nodev"],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            issues.append(f"SMB 挂载失败: {r.stderr.strip()}")
        elif not os.path.ismount(mount_point):
            issues.append("SMB 挂载失败，请检查地址和认证信息")
        else:
            # Unmount
            subprocess.run(["sudo", "umount", mount_point], capture_output=True, timeout=10)
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
            "is_collection": is_collection_seed(t.get("name", "")),
        })
    return jsonify({"status": "ok", "torrents": result, "count": len(result)})


# ─── TMDB 匹配 API ──────────────────────────────────────────

@app.route("/api/tmdb/match", methods=["POST"])
def api_tmdb_match():
    if _task_state["running"]:
        return jsonify({"status": "error", "error": "后台任务正在运行"}), 400

    with _task_state["lock"]:
        torrents = list(_task_state["torrents"])

    if not torrents:
        return jsonify({"status": "error", "error": "请先获取种子列表"}), 400

    def _run_tmdb():
        client = TMDBClient()
        collection_strategy = config.get("collection_strategy", "skip")
        total = len(torrents)
        matches = []
        last_report = 0
        for idx, t in enumerate(torrents):
            if idx > 0 and idx - last_report >= 10:
                _progress_callback(idx, total, f"匹配中 ({idx}/{total})")
                last_report = idx
            try:
                seed_name = t.get("name", "")
                is_col = is_collection_seed(seed_name)

                # 跳过合集种子（保护模式）
                if is_col and collection_strategy == "skip":
                    matches.append({
                        "torrent_hash": t.get("hash", ""),
                        "torrent_name": seed_name,
                        "category": t.get("category", ""),
                        "parsed_title": "",
                        "parsed_year": "",
                        "tmdb_id": "protected:collection",
                        "tmdb_title_cn": "合集种子（保护）",
                        "tmdb_title_en": "Collection (Protected)",
                        "is_collection": True,
                    })
                    continue

                parsed = parse_filename(seed_name)
                title = parsed.get("guess_title", "") or parsed.get("chinese_title", "")
                year = parsed.get("year", "")

                tmdb_id = ""
                tmdb_title_cn = ""
                tmdb_title_en = ""

                if title:
                    result = client.match_entry(seed_name, title, year)
                    if result and result.get("tmdb_id"):
                        tmdb_id = result["tmdb_id"]
                        tmdb_title_cn = result["tmdb_title_cn"]
                        tmdb_title_en = result["tmdb_title_en"]
            except Exception as e:
                print(f"[tmdb] Error {t.get('name','')[:40]}: {e}", flush=True)

            matches.append({
                "torrent_hash": t.get("hash", ""),
                "torrent_name": t.get("name", ""),
                "category": t.get("category", ""),
                "parsed_title": title if 'title' in dir() else "",
                "parsed_year": year if 'year' in dir() else "",
                "tmdb_id": tmdb_id if 'tmdb_id' in dir() else "",
                "tmdb_title_cn": tmdb_title_cn if 'tmdb_title_cn' in dir() else "",
                "tmdb_title_en": tmdb_title_en if 'tmdb_title_en' in dir() else "",
                "is_collection": is_col if 'is_col' in dir() else False,
            })

        _progress_callback(total, total, f"TMDB 匹配完成，共 {total} 个种子")
        return matches

    _background_task("tmdb", _run_tmdb)
    return jsonify({"status": "ok", "message": "开始 TMDB 匹配"})


@app.route("/api/tmdb/results", methods=["GET"])
def api_tmdb_results():
    with _task_state["lock"]:
        matches = list(_task_state["tmdb_matches"])
    matched = sum(1 for m in matches if m.get("tmdb_id"))
    return jsonify({
        "status": "ok",
        "matches": matches,
        "total": len(matches),
        "matched": matched,
        "unmatched": len(matches) - matched,
    })


# ─── 深度分析 API ───────────────────────────────────────────

@app.route("/api/analyze/start", methods=["POST"])
def api_analyze_start():
    if _task_state["running"]:
        return jsonify({"status": "error", "error": "后台任务正在运行"}), 400

    with _task_state["lock"]:
        torrents = list(_task_state["torrents"])

    if not torrents:
        return jsonify({"status": "error", "error": "请先获取种子列表"}), 400

    def _run_analyze():
        collection_strategy = config.get("collection_strategy", "skip")
        # 合集模式下跳过合集种子
        if collection_strategy == "skip":
            analyze_list = [t for t in torrents if not is_collection_seed(t.get("name", ""))]
            collection_list = [t for t in torrents if is_collection_seed(t.get("name", ""))]
            skipped = len(collection_list)
            if skipped:
                print(f"[analyze] 跳过 {skipped} 个合集种子（保护模式）", flush=True)
        else:
            analyze_list = torrents
            collection_list = []

        profiles = analyze_torrents(
            analyze_list,
            progress_callback=_progress_callback,
        )

        # 为合集种子创建最小 profile（仅文件名分析，无需 SMB/MediaInfo）
        for t in collection_list:
            from media_analyzer import _analyze_by_filename
            mp = _analyze_by_filename(t)
            if mp:
                mp.is_collection = True
                profiles.append(mp)

        _progress_callback(len(profiles), len(profiles), f"分析完成，共 {len(profiles)} 个视频文件")
        return profiles

    _background_task("analyze", _run_analyze)
    return jsonify({"status": "ok", "message": "开始深度分析"})


@app.route("/api/analyze/profiles", methods=["GET"])
def api_get_profiles():
    with _task_state["lock"]:
        profiles = [p.to_dict() for p in _task_state["profiles"]]
    return jsonify({"status": "ok", "profiles": profiles, "count": len(profiles)})


# ─── 去重 API ───────────────────────────────────────────────

@app.route("/api/dedup/run", methods=["POST"])
def api_dedup_run():
    if _task_state["running"]:
        return jsonify({"status": "error", "error": "后台任务正在运行"}), 400

    with _task_state["lock"]:
        profiles = list(_task_state["profiles"])
        tmdb_matches = list(_task_state["tmdb_matches"])

    if not profiles:
        return jsonify({"status": "error", "error": "请先完成深度分析"}), 400

    def _run_dedup():
        engine = DedupEngine(profiles, tmdb_matches)
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
    try:
        app.run(host="0.0.0.0", port=5000, debug=True)
    finally:
        unmount_smb()