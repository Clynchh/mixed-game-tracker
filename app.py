import os
import time
from flask import Flask, render_template, request, jsonify, redirect, url_for

import db
import replay
import watcher


def _cumsum(values):
    total = 0.0
    out = []
    for v in values:
        total += v
        out.append(total)
    return out

app = Flask(__name__)
db.init_db()

DEFAULT_FOLDER = os.environ.get("HAND_HISTORY_DIR", "")
DEFAULT_USERNAME = os.environ.get("POKERSTARS_USERNAME", "")


def get_folder():
    return db.get_setting("hand_history_dir", DEFAULT_FOLDER)


def get_username():
    return db.get_setting("hero_username", DEFAULT_USERNAME)


fw = watcher.FolderWatcher(get_folder, get_username)

# Quick-tag presets available as hover buttons on any hand row. Order here is
# the display order (also the hover-button order).
PRESET_TAGS = [
    ("superb", "#a855f7"),
    ("good", "#5fae6e"),
    ("review", "#d9b23a"),
    ("bad", "#c0564f"),
    ("punt", "#4a90d9"),
]
PRESET_TAG_COLORS = dict(PRESET_TAGS)
DASHBOARD_ROW_LIMIT = 100

def default_unit_for(mode):
    """Tournament chip counts only mean something relative to the blind level
    they were won at - the same 5,000 is a big pot at level 1 and a fold at
    level 20 - so tournaments read in BB by default. Cash money is already a
    fixed unit, so it stays in $."""
    return "bb" if mode == "tournament" else "raw"


PRESET_TAG_DESCRIPTIONS = {
    "superb": "Superb — excellent, textbook play",
    "good": "Good — solid, correct decision",
    "review": "Review — worth a second look later",
    "bad": "Bad — a clear mistake",
    "punt": "Punt — a big blunder or tilt play",
}


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
        unit = default_unit_for(mode)

    # The list is capped by default so a big session doesn't render thousands
    # of rows, but the cap has to be visible - otherwise a hand you tagged
    # that falls outside it just looks like it lost its tag.
    show_all = request.args.get("show_all") == "1"
    hand_filters = dict(game_type=game_type, tag=tag, search=search, is_tournament=is_tournament)
    matching_hands = db.count_hands(**hand_filters)
    hands = db.list_hands(limit=None if show_all else DASHBOARD_ROW_LIMIT, **hand_filters)
    stats = db.stats_by_game_type(is_tournament=is_tournament)
    overall = db.overall_stats(is_tournament=is_tournament)
    tags = db.all_tags(is_tournament=is_tournament)
    game_types = sorted({s["game_type"] for s in stats})

    # Chronological (not the date-desc order the table uses), unlimited, and
    # scoped to whatever filters are active - the graph should show the same
    # slice of hands the table below it does.
    series_hands = db.list_hands(game_type=game_type, tag=tag, search=search, is_tournament=is_tournament,
                                  limit=None, sort="date", order="asc")
    if unit == "bb":
        series_hands = [h for h in series_hands if h["big_blind"]]
    hand_values = [
        (h["hero_net"] / h["big_blind"]) if (unit == "bb") else h["hero_net"]
        for h in series_hands
    ]
    main_cum = _cumsum(hand_values)

    # Showdown/non-showdown split, running cumulative on the same x-axis as
    # the main line (each only advances on its own hand type, flat otherwise)
    # so at any point sd_cum[i] + nonsd_cum[i] == main_cum[i].
    sd_cum, nonsd_cum = [], []
    sd_running = nonsd_running = 0.0
    for h, v in zip(series_hands, hand_values):
        if h["went_to_showdown"]:
            sd_running += v
        else:
            nonsd_running += v
        sd_cum.append(sd_running)
        nonsd_cum.append(nonsd_running)

    # All-in EV line: same as main, except all-in-before-completion hands use
    # their equity-adjusted result instead of what the runout actually paid,
    # so short-term variance from lucky/unlucky cards washes out of this line.
    ev_values = [
        ((h["ev_net"] / h["big_blind"]) if (unit == "bb") else h["ev_net"]) if h["is_allin_ev"] else v
        for h, v in zip(series_hands, hand_values)
    ]
    ev_cum = _cumsum(ev_values)
    allin_ev_hands = [h for h in series_hands if h["is_allin_ev"]]
    if unit == "bb":
        ev_luck = sum((h["hero_net"] - h["ev_net"]) / h["big_blind"] for h in allin_ev_hands)
    else:
        ev_luck = sum((h["hero_net"] - h["ev_net"]) for h in allin_ev_hands)

    # Points for the client-side chart: one per hand, carrying the hand_id so
    # clicking a point looks that hand up, plus a bit of display info for the
    # hover tooltip so it's clear which hand a given point is before clicking.
    hand_points = [
        {
            "hand_id": h["hand_id"],
            "date": h["date_played"][:16].replace("T", " ") if h["date_played"] else "",
            "game_type": h["game_type"],
            "stakes": h["stakes"],
            "delta": round(hand_values[i], 4),
            "main": round(main_cum[i], 4), "sd": round(sd_cum[i], 4),
            "nonsd": round(nonsd_cum[i], 4), "ev": round(ev_cum[i], 4),
        }
        for i, h in enumerate(series_hands)
    ]

    tourney_results = None
    tourney_roi = None
    tourney_avg_buyin = None
    tourney_points = []
    if mode == "tournament":
        tourney_results = db.tournament_overall()
        if tourney_results.get("n"):
            if tourney_results.get("total_buyin"):
                tourney_roi = (tourney_results["net"] or 0) / tourney_results["total_buyin"] * 100
                tourney_avg_buyin = tourney_results["total_buyin"] / tourney_results["n"]
            completed = [t for t in db.tournament_stats(order="asc") if t["finish_place"] is not None]
            t_deltas = [(t["net"] or 0) for t in completed]
            t_cum = _cumsum(t_deltas)
            tourney_points = [
                {"date": t["date_played"][:16].replace("T", " ") if t["date_played"] else "",
                 "buy_in": t["buy_in"], "finish_place": t["finish_place"],
                 "delta": round(t_deltas[i], 4), "main": round(t_cum[i], 4)}
                for i, t in enumerate(completed)
            ]

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
        matching_hands=matching_hands,
        show_all=show_all,
        tourney_results=tourney_results,
        tourney_roi=tourney_roi,
        tourney_avg_buyin=tourney_avg_buyin,
        tourney_points=tourney_points,
        hand_points=hand_points,
        hand_graph_count=len(hand_values),
        allin_ev_count=len(allin_ev_hands),
        ev_luck=ev_luck,
        last_scan_new=fw.last_scan_new,
        last_scan_time=fw.last_scan_time,
        preset_tags=PRESET_TAGS,
        preset_colors=PRESET_TAG_COLORS,
        preset_descriptions=PRESET_TAG_DESCRIPTIONS,
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

    default_unit = default_unit_for(mode)
    unit = request.args.get("unit")
    if unit not in ("raw", "bb"):
        unit = default_unit

    pot_min = _report_float("pot_min")
    pot_max = _report_float("pot_max")
    net_min = _report_float("net_min")
    net_max = _report_float("net_max")
    vpip_only = request.args.get("vpip") == "1"

    filters = dict(
        game_type=game_type, tag=tag, search=search, is_tournament=is_tournament,
        pot_type=pot_type, pot_min=pot_min, pot_max=pot_max, net_min=net_min, net_max=net_max,
        vpip=vpip_only,
    )

    all_matching = db.list_hands(sort=sort, order=order, limit=None, **filters)
    total = len(all_matching)
    hands = all_matching[:REPORT_ROW_LIMIT]

    bb_rows = [h for h in all_matching if h["big_blind"]]
    net_bb_total = sum(h["hero_net"] / h["big_blind"] for h in bb_rows)
    avg_pot_bb = (sum((h["pot_total"] or 0) / h["big_blind"] for h in bb_rows) / len(bb_rows)) if bb_rows else 0

    summary = {
        "count": total,
        "net": sum(h["hero_net"] for h in all_matching),
        "net_bb": net_bb_total,
        "avg_pot": (sum((h["pot_total"] or 0) for h in all_matching) / total) if total else 0,
        "avg_pot_bb": avg_pot_bb,
        "win_rate": (sum(1 for h in all_matching if h["hero_net"] > 0) / total * 100) if total else 0,
        "bb_per_100": (net_bb_total / len(bb_rows) * 100) if bb_rows else None,
    }

    bounds = db.pot_net_bounds(is_tournament=is_tournament)

    return render_template(
        "reports.html",
        hands=hands,
        total=total,
        shown=len(hands),
        summary=summary,
        unit=unit,
        default_unit=default_unit,
        game_types=db.all_game_types(is_tournament=is_tournament),
        pot_types=db.all_pot_types(is_tournament=is_tournament),
        tags=db.all_tags(is_tournament=is_tournament),
        mode=mode or "",
        selected_game_type=game_type,
        selected_pot_type=pot_type,
        selected_tag=tag,
        search=search or "",
        pot_min=pot_min, pot_max=pot_max, net_min=net_min, net_max=net_max,
        bounds=bounds,
        vpip_only=vpip_only,
        sort=sort, order=order,
        preset_tags=PRESET_TAGS,
        preset_colors=PRESET_TAG_COLORS,
        preset_descriptions=PRESET_TAG_DESCRIPTIONS,
    )


@app.route("/api/hand/<hand_id>/row")
def hand_row_partial(hand_id):
    """Renders a single hand as a table row - used to pin a hand clicked on
    the dashboard graph to the top of the hand list without a full reload."""
    hand = db.get_hand(hand_id)
    if not hand:
        return "", 404
    unit = request.args.get("unit")
    if unit not in ("raw", "bb"):
        unit = "raw"
    return render_template("_hand_row_only.html", h=hand, unit=unit, preset_tags=PRESET_TAGS,
                            preset_colors=PRESET_TAG_COLORS, preset_descriptions=PRESET_TAG_DESCRIPTIONS)


@app.route("/hand/<hand_id>")
def hand_detail(hand_id):
    hand = db.get_hand(hand_id)
    if not hand:
        return "Hand not found", 404
    replay_data = replay.build_replay(hand["raw_text"], hand["game_type"], hand["hero_name"],
                                       big_blind=hand["big_blind"])
    return render_template("hand.html", hand=hand, replay_data=replay_data, preset_tags=PRESET_TAGS,
                            preset_colors=PRESET_TAG_COLORS, preset_descriptions=PRESET_TAG_DESCRIPTIONS)


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
