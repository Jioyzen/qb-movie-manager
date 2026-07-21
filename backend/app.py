"""Flask web application for QB Movie Manager."""
import json
import os
import sys
import threading
import time

from flask import Flask, render_template, request, jsonify, Response

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import config
from backend.qb_client import QBClient
from backend.parser import parse_filename
from backend.tmdb_client import TMDBClient
from backend.dedup_engine import DedupEngine

# Point Flask at the project-root templates/static
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))

# Background task state
_task_state = {
    "running": False,
    "progress": {"current": 0, "total": 0, "message": ""},
    "result": None,
    "live_results": [],
    "lock": threading.Lock(),
}


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------

@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(config.all())


@app.route("/api/config", methods=["PUT"])
def api_set_config():
    data = request.get_json(silent=True) or {}
    config.set_multi(data)
    return jsonify({"status": "ok", "config": config.all()})


@app.route("/api/config/test-qb", methods=["POST"])
def api_test_qb():
    orig_host = config.get("qb_host")
    orig_port = config.get("qb_port")
    data = request.get_json(silent=True) or {}
    if "qb_host" in data:
        config.set("qb_host", data["qb_host"])
    if "qb_port" in data:
        config.set("qb_port", data["qb_port"])
    if "qb_username" in data:
        config.set("qb_username", data["qb_username"])
    if "qb_password" in data:
        config.set("qb_password", data["qb_password"])
    try:
        ok, msg = QBClient().test_connection()
        return jsonify({"status": "ok" if ok else "error", "message": msg})
    finally:
        config.set("qb_host", orig_host)
        config.set("qb_port", orig_port)


# ---------------------------------------------------------------------------
# qBittorrent data endpoints
# ---------------------------------------------------------------------------

@app.route("/api/categories", methods=["GET"])
def api_get_categories():
    try:
        cats = QBClient().get_categories()
        return jsonify({"categories": cats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/torrents", methods=["POST"])
def api_get_torrents():
    data = request.get_json(silent=True) or {}
    categories = data.get("categories", [])
    try:
        qb = QBClient()
        all_torrents = []
        if categories:
            for cat in categories:
                all_torrents.extend(qb.get_torrents(category=cat))
        else:
            all_torrents = qb.get_torrents()
        # Deduplicate by hash
        seen = set()
        unique = []
        for t in all_torrents:
            h = t.get("hash", "")
            if h and h not in seen:
                seen.add(h)
                unique.append(t)
        # Sort by name
        unique.sort(key=lambda x: x.get("name", "").lower())
        return jsonify({"torrents": unique, "count": len(unique)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/torrents/files", methods=["POST"])
def api_get_torrent_files():
    data = request.get_json(silent=True) or {}
    hashes = data.get("hashes", [])
    if not hashes:
        return jsonify({"error": "No hashes provided"}), 400
    try:
        qb = QBClient()
        result = {}
        for h in hashes:  # process all hashes
            try:
                files = qb.get_torrent_files(h)
                result[h] = files
            except Exception as e:
                result[h] = {"error": str(e)}
        return jsonify({"files": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Parse & match pipeline
# ---------------------------------------------------------------------------

@app.route("/api/parse", methods=["POST"])
def api_parse():
    """Parse all torrent video files into structured entries."""
    data = request.get_json(silent=True) or {}
    torrents = data.get("torrents", [])
    torrent_files = data.get("torrent_files", {})
    if not torrents:
        return jsonify({"error": "No torrents provided"}), 400

    entries = []
    for t in torrents:
        thash = t.get("hash", "")
        category = t.get("category", "")
        name = t.get("name", "")
        save_path = t.get("save_path", "")
        size = t.get("total_size", 0)
        ratio = t.get("ratio", 0)
        state = t.get("state", "")

        files = torrent_files.get(thash, [])
        if isinstance(files, dict) and "error" in files:
            continue
        if not files:
            # Single-file torrent - treat the torrent name as the filename
            parsed = parse_filename(name)
            parsed["seed_name"] = name
            parsed["hash"] = thash
            parsed["category"] = category
            parsed["save_path"] = save_path
            parsed["size_gb"] = round(size / (1024**3), 2)
            parsed["ratio"] = round(ratio, 2)
            parsed["state"] = state
            parsed["is_collection"] = "N"
            entries.append(parsed)
        else:
            video_exts = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".iso", ".bdmv", ".mpls"}
            video_files = [f for f in files if any(f.get("name", "").lower().endswith(ext) for ext in video_exts)]
            if not video_files:
                video_files = files[:1]  # fallback: first file

            is_multi = len(video_files) > 1
            min_size_bytes = config.get("min_file_size_mb", 300) * 1024 * 1024
            for vf in video_files:
                fname = vf.get("name", "")
                fsize = vf.get("size", 0)
                # Skip small files (samples, etc.)
                if fsize > 0 and fsize < min_size_bytes:
                    continue
                parsed = parse_filename(fname or name)
                parsed["seed_name"] = name
                parsed["hash"] = thash
                parsed["category"] = category
                parsed["save_path"] = save_path
                parsed["size_gb"] = round(fsize / (1024**3), 2)
                parsed["ratio"] = round(ratio, 2)
                parsed["state"] = state
                parsed["is_collection"] = "Y" if is_multi else "N"
                parsed["torrent_file_name"] = fname
                entries.append(parsed)

    # Merge exact duplicate parses (same hash + same filename)
    merged = {}
    for e in entries:
        key = e.get("hash", "") + "|" + e.get("torrent_file_name", e.get("seed_name", ""))
        if key not in merged:
            merged[key] = e
    entries = list(merged.values())

    return jsonify({"entries": entries, "count": len(entries)})


@app.route("/api/match", methods=["POST"])
def api_match():
    """Batch TMDB match. Returns immediately; progress via SSE /api/matching/stream."""
    data = request.get_json(silent=True) or {}
    entries = data.get("entries", [])
    if not entries:
        return jsonify({"error": "No entries provided"}), 400

    with _task_state["lock"]:
        if _task_state["running"]:
            return jsonify({"error": "A matching task is already running"}), 409
        _task_state["running"] = True
        _task_state["progress"] = {"current": 0, "total": len(entries), "message": "Starting..."}
        _task_state["result"] = None

    def _run():
        try:
            client = TMDBClient()
            total = len(entries)
            def _progress(idx, cur, tot, md):
                with _task_state["lock"]:
                    _task_state["progress"]["current"] = cur
                    _task_state["progress"]["total"] = tot
                    _task_state["progress"]["message"] = f"Matched {cur}/{tot}"
                    _task_state["live_results"].append({
                        "index": idx,
                        "tmdb_id": md.get("tmdb_id", ""),
                        "tmdb_title_cn": md.get("tmdb_title_cn", ""),
                        "tmdb_title_en": md.get("tmdb_title_en", ""),
                        "matched_by": md.get("matched_by", ""),
                    })
            result = client.batch_match(entries, progress_callback=_progress)
            with _task_state["lock"]:
                _task_state["result"] = result
                _task_state["progress"]["message"] = "Complete"
        except Exception as e:
            with _task_state["lock"]:
                _task_state["progress"]["message"] = f"Error: {e}"
        finally:
            with _task_state["lock"]:
                _task_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "total": len(entries)})


@app.route("/api/matching/progress", methods=["GET"])
def api_match_progress():
    with _task_state["lock"]:
        return jsonify({
            "running": _task_state["running"],
            "progress": _task_state["progress"],
        })


@app.route("/api/matching/stream", methods=["GET"])
def api_match_stream():
    def generate():
        last_count = 0
        while True:
            done = False
            is_error = False
            with _task_state["lock"]:
                if not _task_state["running"] and _task_state["result"] is not None:
                    done = True
                elif not _task_state["running"] and _task_state["progress"]["message"].startswith("Error"):
                    done = True
                    is_error = True

                p = _task_state["progress"]
                yield f"event: progress\ndata: {json.dumps(p)}\n\n"

                new_results = _task_state["live_results"][last_count:]
                for r in new_results:
                    yield f"event: matched\ndata: {json.dumps(r)}\n\n"
                last_count += len(new_results)

            if done:
                if is_error:
                    yield "event: error\ndata: {}\n\n"
                else:
                    yield "event: complete\ndata: {}\n\n"
                break

            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# ---------------------------------------------------------------------------
# Dedup endpoints
# ---------------------------------------------------------------------------

@app.route("/api/duplicates", methods=["POST"])
def api_get_duplicates():
    data = request.get_json(silent=True) or {}
    entries = data.get("entries", [])
    rules = data.get("priority_rules", {})
    if not entries:
        return jsonify({"error": "No entries provided"}), 400
    engine = DedupEngine(entries)
    if rules:
        engine.set_priority_rules(rules)
    summary = engine.get_duplicate_summary()
    groups = engine.get_duplicates()
    return jsonify({
        "duplicates": summary,
        "group_count": len(groups),
        "delete_candidates": sum(1 for e in summary if not e["is_keep"]),
    })


@app.route("/api/torrents/delete", methods=["POST"])
def api_delete_torrents():
    data = request.get_json(silent=True) or {}
    hashes = data.get("hashes", [])
    delete_files = data.get("delete_files", True)
    if not hashes:
        return jsonify({"error": "No hashes provided"}), 400
    try:
        QBClient().delete_torrents(hashes, delete_files=delete_files)
        return jsonify({"status": "ok", "deleted": len(hashes)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)