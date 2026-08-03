import os
import time
from flask import Flask, render_template, request, jsonify, redirect, url_for

import db
import watcher

app = Flask(__name__)
db.init_db()

DEFAULT_FOLDER = os.environ.get("HAND_HISTORY_DIR", "")
DEFAULT_USERNAME = os.environ.get("POKERSTARS_USERNAME", "")


def get_folder():
    return db.get_setting("hand_history_dir", DEFAULT_FOLDER)


def get_username():
    return db.get_setting("hero_username", DEFAULT_USERNAME)


fw = watcher.FolderWatcher(get_folder, get_username)


@app.route("/")
def dashboard():
    folder = get_folder()
    game_type = request.args.get("game_type") or None
    tag = request.args.get("tag") or None
    search = request.args.get("search") or None

    cash_count = db.overall_stats(is_tournament=0)["hands"] or 0
    tourney_count = db.overall_stats(is_tournament=1)["hands"] or 0

    mode = request.args.get("mode")
    if mode not in ("cash", "tournament"):
        mode = "tournament" if tourney_count > cash_count else "cash"
    is_tournament = 1 if mode == "tournament" else 0

    unit = request.args.get("unit")
    if unit not in ("raw", "bb"):
        unit = "raw"

    hands = db.list_hands(game_type=game_type, tag=tag, search=search, is_tournament=is_tournament, limit=100)
    stats = db.stats_by_game_type(is_tournament=is_tournament)
    overall = db.overall_stats(is_tournament=is_tournament)
    tags = db.all_tags(is_tournament=is_tournament)
    game_types = sorted({s["game_type"] for s in stats})

    tourney_results = db.tournament_overall() if mode == "tournament" else None

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
        mode=mode,
        unit=unit,
        mode_counts={"cash": cash_count, "tournament": tourney_count},
        total_hands=cash_count + tourney_count,
        tourney_results=tourney_results,
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
        username = request.form.get("hero_username", "").strip()
        db.set_setting("hand_history_dir", folder)
        db.set_setting("hero_username", username)
        watcher.scan_folder(folder, hero_username=username)  # scan immediately so it's not a 15s wait
        return redirect(url_for("dashboard"))
    return render_template("settings.html", folder=get_folder(), username=get_username())


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
    n = watcher.scan_folder(folder, hero_username=get_username())
    return jsonify({"new_hands": n})


if __name__ == "__main__":
    fw.start()
    app.run(host="127.0.0.1", port=5151, debug=False)
