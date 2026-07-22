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
from media_analyzer import analyze_with_mediainfo, unmount_smb, is_collection_seed
from dedup_engine import DedupEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))

# ─── 全局状态 ───────────────────────────────────────────────

_task_state = {
    "running": False,
    "current_step": "",       # fetch | analyze | dedup
    "progress": {"current": 0, "total": 0, "message": ""},
    "torrents": [],           # 获取的种子列表
    "profiles": [],           # 分析后的 MediaProfile 列表
    "dedup_results": [],      # 去重结果
    "lock": threading.Lock(),
}

# ─── 辅助函数 ───────────────────────────────────────────────

def _mask_config(cfg: dict) -> dict:
    """掩码密码字段"""
    d = dict(cfg)
    for key in PASSWORD_FIELDS:
        if key in d and d[key]:
            d[key] = "********"
    return d


def _background_task(step: str, func, *args, **kwargs):
    """在后台线程中执行任务。"""
    with _task_state["lock"]:
        _task_state["running"] = True
        _task_state["current_step"] = step
        _task_state["progress"] = {"current": 0, "total": 0, "message": "准备中..."}

    def _run():
        try:
            result = func(*args, **kwargs)
            with _task_state["lock"]:
                if step == "fetch":
                    _task_state["torrents"] = result
                elif step == "analyze":
                    _task_state["profiles"] = result
                elif step == "dedup":
                    _task_state["dedup_results"] = result
                _task_state["running"] = False
                _task_state["progress"]["message"] = "完成"
        except Exception as e:
            with _task_state["lock"]:
                _task_state["running"] = False
                _task_state["progress"]["message"] = f"错误: {e}"

    threading.Thread(target=_run, daemon=True).start()


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
    # 如果密码字段是 "********" 则不覆盖
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
        # 去重（按 hash）
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
    # 精简返回
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


# ─── 分析 API ───────────────────────────────────────────────

@app.route("/api/analyze/start", methods=["POST"])
def api_analyze_start():
    if _task_state["running"]:
        return jsonify({"status": "error", "error": "后台任务正在运行"}), 400

    with _task_state["lock"]:
        torrents = list(_task_state["torrents"])

    if not torrents:
        return jsonify({"status": "error", "error": "请先获取种子列表"}), 400

    def _run_analyze():
        profiles = analyze_with_mediainfo(
            torrents,
            progress_callback=_progress_callback,
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


# ─── 去重 API ───────────────────────────────────────────────

@app.route("/api/dedup/run", methods=["POST"])
def api_dedup_run():
    if _task_state["running"]:
        return jsonify({"status": "error", "error": "后台任务正在运行"}), 400

    with _task_state["lock"]:
        profiles = list(_task_state["profiles"])

    if not profiles:
        return jsonify({"status": "error", "error": "请先完成深度分析"}), 400

    def _run_dedup():
        engine = DedupEngine(profiles)
        results = engine.to_dict()
        _progress_callback(0, 0, f"去重完成，发现 {engine.get_summary()['duplicate_groups']} 组重复")
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
    """计算去重摘要。"""
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
        # 从缓存中删除
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