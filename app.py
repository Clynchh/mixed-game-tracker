import os
import time
from flask import Flask, render_template, request, jsonify, redirect, url_for

import db
import watcher

app = Flask(__name__)
db.init_db()

DEFAULT_FOLDER = os.environ.get("HAND_HISTORY_DIR", "")


def get_folder():
    return db.get_setting("hand_history_dir", DEFAULT_FOLDER)


fw = watcher.FolderWatcher(get_folder)


@app.route("/")
def dashboard():
    folder = get_folder()
    game_type = request.args.get("game_type") or None
    tag = request.args.get("tag") or None
    search = request.args.get("search") or None
    hands = db.list_hands(game_type=game_type, tag=tag, search=search, limit=100)
    stats = db.stats_by_game_type()
    overall = db.overall_stats()
    tags = db.all_tags()
    game_types = sorted({s["game_type"] for s in stats})
    return render_template(
        "dashboard.html",
        hands=hands,
        stats=stats,
        overall=overall,
        tags=tags,
        game_types=game_types,
        folder=folder,
        selected_game_type=game_type,
        selected_tag=tag,
        search=search or "",
        last_scan_new=fw.last_scan_new,
        last_scan_time=fw.last_scan_time,
    )


@app.route("/hand/<hand_id>")
def hand_detail(hand_id):
    hand = db.get_hand(hand_id)
    if not hand:
        return "Hand not found", 404
    return render_template("hand.html", hand=hand)


@app.route("/hand/<hand_id>/tag", methods=["POST"])
def add_tag(hand_id):
    tag = request.form.get("tag", "").strip()
    note = request.form.get("note", "").strip()
    if tag:
        db.add_tag(hand_id, tag, note)
    return redirect(url_for("hand_detail", hand_id=hand_id))


@app.route("/hand/<hand_id>/untag", methods=["POST"])
def remove_tag(hand_id):
    tag = request.form.get("tag", "").strip()
    if tag:
        db.remove_tag(hand_id, tag)
    return redirect(url_for("hand_detail", hand_id=hand_id))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        folder = request.form.get("hand_history_dir", "").strip()
        db.set_setting("hand_history_dir", folder)
        watcher.scan_folder(folder)  # scan immediately so it's not a 15s wait
        return redirect(url_for("dashboard"))
    return render_template("settings.html", folder=get_folder())


@app.route("/api/scan-status")
def scan_status():
    return jsonify(
        {
            "last_scan_new": fw.last_scan_new,
            "last_scan_time": fw.last_scan_time,
            "folder": get_folder(),
        }
    )


@app.route("/api/rescan", methods=["POST"])
def rescan():
    folder = get_folder()
    n = watcher.scan_folder(folder)
    return jsonify({"new_hands": n})


if __name__ == "__main__":
    fw.start()
    app.run(host="127.0.0.1", port=5151, debug=False)
