import os
import time
from flask import Flask, render_template, request, jsonify, redirect, url_for

import charts
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

    # Chronological (not the date-desc order the table uses), unlimited, and
    # scoped to whatever filters are active - the graph should show the same
    # slice of hands the table below it does.
    series_hands = db.list_hands(game_type=game_type, tag=tag, search=search, is_tournament=is_tournament,
                                  limit=None, sort="date", order="asc")
    hand_values = [
        (h["hero_net"] / h["big_blind"]) if (unit == "bb" and h["big_blind"]) else h["hero_net"]
        for h in series_hands
        if unit != "bb" or h["big_blind"]
    ]
    hand_graph_svg = charts.line_svg(charts.cumulative(hand_values))

    tourney_results = None
    tourney_roi = None
    tourney_avg_buyin = None
    tourney_graph_svg = None
    if mode == "tournament":
        tourney_results = db.tournament_overall()
        if tourney_results.get("n"):
            if tourney_results.get("total_buyin"):
                tourney_roi = (tourney_results["net"] or 0) / tourney_results["total_buyin"] * 100
                tourney_avg_buyin = tourney_results["total_buyin"] / tourney_results["n"]
            completed = [t for t in db.tournament_stats(order="asc") if t["finish_place"] is not None]
            t_values = [(t["net"] or 0) for t in completed]
            tourney_graph_svg = charts.line_svg(charts.cumulative(t_values))

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
        tourney_roi=tourney_roi,
        tourney_avg_buyin=tourney_avg_buyin,
        tourney_graph_svg=tourney_graph_svg,
        hand_graph_svg=hand_graph_svg,
        hand_graph_count=len(hand_values),
        last_scan_new=fw.last_scan_new,
        last_scan_time=fw.last_scan_time,
    )


REPORT_SORT_COLUMNS = {"date", "pot", "net", "pot_type", "game_type"}
REPORT_ROW_LIMIT = 300


def _report_float(name):
    v = request.args.get(name)
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


@app.route("/reports")
def reports():
    mode = request.args.get("mode") or None
    is_tournament = {"cash": 0, "tournament": 1}.get(mode)
    game_type = request.args.get("game_type") or None
    pot_type = request.args.get("pot_type") or None
    tag = request.args.get("tag") or None
    search = request.args.get("search") or None
    sort = request.args.get("sort", "date")
    if sort not in REPORT_SORT_COLUMNS:
        sort = "date"
    order = "asc" if request.args.get("order") == "asc" else "desc"

    pot_min = _report_float("pot_min")
    pot_max = _report_float("pot_max")
    net_min = _report_float("net_min")
    net_max = _report_float("net_max")

    filters = dict(
        game_type=game_type, tag=tag, search=search, is_tournament=is_tournament,
        pot_type=pot_type, pot_min=pot_min, pot_max=pot_max, net_min=net_min, net_max=net_max,
    )

    all_matching = db.list_hands(sort=sort, order=order, limit=None, **filters)
    total = len(all_matching)
    hands = all_matching[:REPORT_ROW_LIMIT]
    summary = {
        "count": total,
        "net": sum(h["hero_net"] for h in all_matching),
        "avg_pot": (sum((h["pot_total"] or 0) for h in all_matching) / total) if total else 0,
        "win_rate": (sum(1 for h in all_matching if h["hero_net"] > 0) / total * 100) if total else 0,
    }

    return render_template(
        "reports.html",
        hands=hands,
        total=total,
        shown=len(hands),
        summary=summary,
        game_types=db.all_game_types(is_tournament=is_tournament),
        pot_types=db.all_pot_types(is_tournament=is_tournament),
        tags=db.all_tags(is_tournament=is_tournament),
        mode=mode or "",
        selected_game_type=game_type,
        selected_pot_type=pot_type,
        selected_tag=tag,
        search=search or "",
        pot_min=pot_min, pot_max=pot_max, net_min=net_min, net_max=net_max,
        sort=sort, order=order,
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
