import os
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for

import db
import replay
import support_config
import tourn_summary
import version
import watcher

HOST = "127.0.0.1"
PORT = int(os.environ.get("MGT_PORT", "5151"))


def _flask_app():
    """A packaged build unpacks templates/static into a temp folder, so Flask
    has to be pointed at that rather than at the (non-existent) source tree."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return Flask(
            __name__,
            template_folder=os.path.join(base, "templates"),
            static_folder=os.path.join(base, "static"),
        )
    return Flask(__name__)


def _ordinal(n):
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# Betting structure changes what game is actually being played, not just
# how it's played - No Limit Hold'em and Limit Hold'em are as different
# from each other as they are from Omaha, so grouping/filtering by
# game_type alone (which is all "Hold'em" either way) would silently merge
# them. These are the short names players actually use; anything not
# listed falls back to "<structure> <game_type>", e.g. "Limit Razz".
DISPLAY_GAME_SHORT_NAMES = {
    ("Hold'em", "NL"): "NLHE",
    ("Hold'em", "PL"): "PLH",
    ("Omaha", "PL"): "PLO",
    ("Omaha", "NL"): "NLO",
    ("Omaha Hi/Lo", "PL"): "PLO8",
    ("Omaha Hi/Lo", "NL"): "NLO8",
    ("Omaha Hi/Lo", "FL"): "Limit O8",
    ("Hold'em", "AI"): "All-In Hold'em",
}
LIMIT_TYPE_PREFIX = {"NL": "No Limit", "PL": "Pot Limit", "FL": "Limit", "AI": "All-In"}


def display_game_type(game_type, limit_type):
    if not game_type:
        return game_type
    short = DISPLAY_GAME_SHORT_NAMES.get((game_type, limit_type))
    if short:
        return short
    prefix = LIMIT_TYPE_PREFIX.get(limit_type)
    return f"{prefix} {game_type}" if prefix else game_type


def _duration_minutes(start_iso, end_iso):
    """Raw minutes between two date_played ISO strings, or 0 if either is
    missing or unparsable - the numeric twin of _format_duration, so a total
    row (or client-side recompute after a game-filter change) can sum
    durations before formatting the sum."""
    if not start_iso or not end_iso:
        return 0
    try:
        delta = datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)
    except ValueError:
        return 0
    return max(0, round(delta.total_seconds() / 60))


def _format_minutes(minutes):
    """"1h 24m" for a minute count, or "-" for zero/negative (either too
    short to time, or the pair of timestamps it came from were missing)."""
    if minutes <= 0:
        return "—"
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def _format_duration(start_iso, end_iso):
    """"1h 24m" between two date_played ISO strings, or "-" if either is
    missing or they're the same instant (a single-hand tournament/session -
    not enough to time)."""
    return _format_minutes(_duration_minutes(start_iso, end_iso))


# A new session starts once this long has passed since the previous hand -
# cash hand histories carry no explicit session marker, so this is a
# judgment call standing in for "sat down to play" vs. "same sitting,
# just a slow hand or two."
SESSION_GAP = timedelta(minutes=60)


def _group_into_sessions(cash_hands, unit):
    """cash_hands: hero's cash hands, oldest first. Returns one summary dict
    per session: when it ran, how long, which games, hands played, net."""
    sessions = []
    current = None
    for h in cash_hands:
        if not h["date_played"]:
            continue
        try:
            played = datetime.fromisoformat(h["date_played"])
        except ValueError:
            continue
        if current is None or played - current["_last"] > SESSION_GAP:
            current = {
                "started_at": h["date_played"], "ended_at": h["date_played"], "_last": played,
                "num_hands": 0, "net": 0.0, "game_types": [],
            }
            sessions.append(current)
        current["ended_at"] = h["date_played"]
        current["_last"] = played
        current["num_hands"] += 1
        current["net"] += (h["hero_net"] / h["big_blind"]) if (unit == "bb" and h["big_blind"]) else h["hero_net"]
        if h["game_type"] not in current["game_types"]:
            current["game_types"].append(h["game_type"])
    sessions.reverse()  # most recent first, matching every other list on the page
    for s in sessions:
        del s["_last"]
    return sessions

app = _flask_app()
db.init_db()
# An install from before parser versioning existed has no stored value.
# Adopt the current one rather than assuming it's stale, so nobody is asked
# to re-import hands that are already up to date.
if db.get_setting("parser_version") is None:
    db.mark_parser_version()

DEFAULT_FOLDER = os.environ.get("HAND_HISTORY_DIR", "")
DEFAULT_USERNAME = os.environ.get("POKERSTARS_USERNAME", "")


def get_folder():
    return db.get_setting("hand_history_dir", DEFAULT_FOLDER)


def get_username():
    return db.get_setting("hero_username", DEFAULT_USERNAME)


def get_tourn_summary_dir():
    return db.get_setting("tourn_summary_dir", "")


def suggest_tourn_summary_dir(hand_history_dir):
    """Tournament Summary files live in a folder that's always a sibling of
    the hand history one - same PokerStars install, same screen name,
    "HandHistory" swapped for "TournSummary". Offered as a one-click
    suggestion in Setup/Settings rather than assumed automatically, so nobody
    who doesn't want this second data source is surprised by it turning on."""
    if not hand_history_dir or "HandHistory" not in hand_history_dir:
        return ""
    candidate = hand_history_dir.replace("HandHistory", "TournSummary")
    return candidate if os.path.isdir(candidate) else ""


def get_export_dir():
    """Where to keep the browsable copy on disk, or "" for not doing that.
    Set by `./mixtrack export --auto`."""
    return db.get_setting("static_export_dir", "")


def refresh_static_export(new_hands):
    """Rebuild the on-disk copy after a scan that actually imported
    something. Skipped when nothing changed - a full rebuild is a few
    seconds of CPU, which is not worth spending every 15 seconds to
    reproduce byte-identical files."""
    if not new_hands or not get_export_dir():
        return
    try:
        import export_static
        export_static.Exporter(out_dir=get_export_dir()).plan().run(progress=False)
        print(f"[export] refreshed {get_export_dir()}")
    except Exception as e:
        print(f"[export] refresh failed: {e}")


fw = watcher.FolderWatcher(get_folder, get_username, get_tourn_summary_dir,
                           on_scan_complete=refresh_static_export)


def scan_in_background(folder, username):
    """Kick off a scan without making the browser wait on it - a first
    import can be thousands of hands, and blocking the request until it's
    done would leave the page looking hung the whole time. The UI instead
    polls /api/scan-status (watcher.SCAN_PROGRESS) for a spinner/progress
    bar and reloads once it's finished.

    "active" is set here, synchronously, before the request even returns -
    not left for the background thread to set once it gets around to it.
    Otherwise the page's first poll can land in the gap before the thread
    has actually started, see the *previous* scan's finished state, and
    conclude nothing is happening. The thread's finally block is what
    guarantees it gets cleared again, however scan_folder exits - including
    the early "not a real folder" return, which never touches this flag
    itself.

    If a scan (this app only ever runs one folder, so it's always this same
    one) is already under way - most likely the periodic background watcher
    - this is a no-op rather than a second thread racing it: scan_folder()
    itself would just skip the redundant work via its own lock anyway, but
    doing nothing here also avoids this thread's own finally clearing
    "active" the moment IT finishes, while the real scan is still running."""
    if watcher.SCAN_PROGRESS["active"]:
        return
    watcher.SCAN_PROGRESS["active"] = True
    watcher.SCAN_PROGRESS["done"] = 0
    watcher.SCAN_PROGRESS["total"] = 0

    def go():
        try:
            watcher.scan_folder(folder, hero_username=username)
            # Quick second pass - authoritative buy-in/finish/payout for any
            # tournament with a summary file, overriding whatever hand-history
            # parsing guessed. Runs after so it has the freshly-imported
            # tournament shells to attach to.
            tourn_summary.scan_folder(get_tourn_summary_dir(), hero_username=username)
        except Exception as e:
            print(f"[app] background scan failed: {e}")
        finally:
            watcher.SCAN_PROGRESS["active"] = False
    threading.Thread(target=go, daemon=True).start()


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

def is_configured():
    """Both of these are needed before any number the app shows is
    trustworthy - without the username it can't tell which player is you in
    stud, and without the folder there's nothing to read."""
    return bool(get_folder() and get_username())


@app.before_request
def require_same_origin():
    """This server has no login and no secret key, but its POST routes still
    change local state (settings, tags, which folder gets scanned) - and a
    plain HTTP server has no built-in defense against a completely unrelated
    website, open in another tab in the same browser while this is running,
    silently POSTing to http://127.0.0.1:<port> in the background. Browsers
    always attach Origin (or, failing that, Referer) to a cross-origin POST,
    so rejecting anything whose Origin doesn't match this app's own origin
    closes that off without affecting normal use of the app itself."""
    if request.method == "GET" or request.endpoint == "static":
        return None
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if origin and not origin.startswith(f"http://{HOST}:{PORT}"):
        return "Forbidden", 403
    return None


@app.before_request
def require_setup():
    """Send a fresh install to the welcome screen rather than an empty
    dashboard that silently reports nothing."""
    if is_configured():
        return None
    allowed = {"setup", "save_setup", "settings", "static"}
    if request.endpoint in allowed:
        return None
    return redirect(url_for("setup"))


@app.context_processor
def inject_globals():
    """Available to every template - the Ko-fi button needs to know whether
    it's configured, and every page can flag a pending re-import."""
    return {
        "kofi_username": support_config.KOFI_USERNAME,
        "app_version": version.VERSION,
        "reimport_needed": db.reimport_needed(),
    }


@app.route("/api/reimport", methods=["POST"])
def reimport():
    """Re-read every hand history from scratch. Needed after an update that
    changes how hands are parsed, because the scanner otherwise skips files
    it has already seen and the old (wrong) numbers would stick around.

    Hand rows are rewritten in place; tags and notes are stored separately
    against the hand id, so they come through untouched."""
    db.clear_file_state()
    db.mark_parser_version()
    scan_in_background(get_folder(), get_username())
    return jsonify({"ok": True})


@app.route("/setup")
def setup():
    return render_template(
        "setup.html",
        candidates=watcher.detect_hand_history_folders(),
        folder=get_folder(),
        username=get_username(),
        reconfiguring=is_configured(),
    )


@app.route("/setup", methods=["POST"], endpoint="save_setup")
def save_setup():
    folder = request.form.get("hand_history_dir", "").strip()
    username = request.form.get("hero_username", "").strip()
    errors = []
    if not username:
        errors.append("Enter the exact screen name you play under.")
    if not folder:
        errors.append("Choose or enter your hand history folder.")
    elif not os.path.isdir(folder):
        errors.append(f"That folder doesn't exist: {folder}")

    if errors:
        return render_template(
            "setup.html",
            candidates=watcher.detect_hand_history_folders(),
            folder=folder,
            username=username,
            errors=errors,
            reconfiguring=is_configured(),
        )

    db.set_setting("hand_history_dir", folder)
    db.set_setting("hero_username", username)
    scan_in_background(folder, username)
    return redirect(url_for("dashboard"))


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
    limit_type = request.args.get("limit_type") or None
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
    hand_filters = dict(game_type=game_type, limit_type=limit_type, tag=tag, search=search, is_tournament=is_tournament)
    matching_hands = db.count_hands(**hand_filters)
    hands = db.list_hands(limit=None if show_all else DASHBOARD_ROW_LIMIT, **hand_filters)
    stats = [
        {**s, "display": display_game_type(s["game_type"], s["limit_type"])}
        for s in db.stats_by_game_type(is_tournament=is_tournament)
    ]
    overall = db.overall_stats(is_tournament=is_tournament)
    tags = db.all_tags(is_tournament=is_tournament)

    # Chronological (not the date-desc order the table uses), unlimited, and
    # scoped to whatever filters are active - the graph should show the same
    # slice of hands the table below it does.
    series_hands = db.list_hands(game_type=game_type, limit_type=limit_type, tag=tag, search=search,
                                  is_tournament=is_tournament, limit=None, sort="date", order="asc")
    if unit == "bb":
        series_hands = [h for h in series_hands if h["big_blind"]]
    hand_values = [
        (h["hero_net"] / h["big_blind"]) if (unit == "bb") else h["hero_net"]
        for h in series_hands
    ]

    # All-in EV line: same as main, except all-in-before-completion hands use
    # their equity-adjusted result instead of what the runout actually paid,
    # so short-term variance from lucky/unlucky cards washes out of this line.
    ev_values = [
        ((h["ev_net"] / h["big_blind"]) if (unit == "bb") else h["ev_net"]) if h["is_allin_ev"] else v
        for h, v in zip(series_hands, hand_values)
    ]
    allin_ev_hands = [h for h in series_hands if h["is_allin_ev"]]
    if unit == "bb":
        ev_luck = sum((h["hero_net"] - h["ev_net"]) / h["big_blind"] for h in allin_ev_hands)
    else:
        ev_luck = sum((h["hero_net"] - h["ev_net"]) for h in allin_ev_hands)

    # Points for the client-side chart: one per hand, carrying the hand_id so
    # clicking a point looks that hand up, plus enough display info for the
    # hover tooltip and the game checkboxes (which filter and recompute the
    # cumulative lines in the browser, without a page reload) to work from.
    hand_points = [
        {
            "hand_id": h["hand_id"],
            "date": h["date_played"][:16].replace("T", " ") if h["date_played"] else "",
            "game_type": h["game_type"],
            "display": display_game_type(h["game_type"], h["limit_type"]),
            "stakes": h["stakes"],
            "delta": round(hand_values[i], 4),
            "ev_delta": round(ev_values[i], 4),
            # Always bb-scaled regardless of the raw/bb unit toggle above -
            # the bb/100 banner stat is itself always in bb terms, so it
            # needs this even when `delta` is showing $ instead.
            "bb_delta": round(h["hero_net"] / h["big_blind"], 4) if h["big_blind"] else None,
            "is_allin_ev": bool(h["is_allin_ev"]),
            "went_to_showdown": bool(h["went_to_showdown"]),
        }
        for i, h in enumerate(series_hands)
    ]

    tourney_results = None
    tourney_roi = None
    tourney_avg_buyin = None
    tourney_points = []
    tournament_list = []
    sessions = []
    tourney_graph_count = 0
    if mode == "tournament":
        tourney_results = db.tournament_overall()

        # Which games each tournament touched, as the same friendly labels
        # the game boxes use - a mixed-rotation event touches several, so
        # they filter both the results graph and the tournaments list
        # client-side by "played any of these" rather than a per-game split
        # of a result that isn't actually divisible that way.
        tourney_games = {}
        for row in db.tournament_game_types():
            label = display_game_type(row["game_type"], row["limit_type"])
            tourney_games.setdefault(row["tournament_id"], set()).add(label)

        if tourney_results.get("n"):
            if tourney_results.get("total_buyin"):
                tourney_roi = (tourney_results["net"] or 0) / tourney_results["total_buyin"] * 100
                tourney_avg_buyin = tourney_results["total_buyin"] / tourney_results["n"]
            completed = [t for t in db.tournament_stats(order="asc") if t["finished"]]
            tourney_graph_count = len(completed)
            t_deltas = [(t["net"] or 0) for t in completed]
            tourney_points = [
                {"date": t["date_played"][:16].replace("T", " ") if t["date_played"] else "",
                 "buy_in": t["buy_in"], "finish_place": t["finish_place"],
                 "delta": round(t_deltas[i], 4),
                 "games": sorted(tourney_games.get(t["tournament_id"], ()))}
                for i, t in enumerate(completed)
            ]
        tournament_list = [
            {
                **t,
                "duration": _format_duration(t["started_at"], t["ended_at"]),
                "duration_minutes": _duration_minutes(t["started_at"], t["ended_at"]),
                # PokerStars doesn't always give an exact place when several
                # players bust in the same hand - "finished" is still known
                # even then, so that's not the same as truly still playing.
                "status": (
                    _ordinal(t["finish_place"]) if t["finish_place"] is not None
                    else "busted" if t["finished"]
                    else "in progress"
                ),
                "games": sorted(tourney_games.get(t["tournament_id"], ())),
            }
            for t in db.tournament_list()
        ]
    else:
        sessions = [
            {**s, "duration": _format_duration(s["started_at"], s["ended_at"])}
            for s in _group_into_sessions(series_hands, unit)
        ]

    overall_net = overall["net_bb"] if (unit == "bb" and overall.get("net_bb") is not None) else overall["net"]

    # The "Non-NLH" quick-exclude box needs its own hands/net figures (total
    # minus whatever NLHE contributed) to look and behave like the other
    # game boxes - there's no single (game_type, limit_type) row for it since
    # it's everything except one.
    nlhe_stat = next((s for s in stats if s["display"] == "NLHE"), None)
    nlhe_hands = nlhe_stat["hands"] if nlhe_stat else 0
    nlhe_net = 0
    if nlhe_stat:
        nlhe_net = nlhe_stat["net_bb"] if (unit == "bb" and nlhe_stat.get("net_bb") is not None) else nlhe_stat["net"]
    non_nlh_hands = (overall["hands"] or 0) - nlhe_hands
    non_nlh_net = (overall_net or 0) - (nlhe_net or 0)

    return render_template(
        "dashboard.html",
        hands=hands,
        stats=stats,
        overall=overall,
        overall_net=overall_net,
        non_nlh_hands=non_nlh_hands,
        non_nlh_net=non_nlh_net,
        tags=tags,
        folder=folder,
        selected_game_type=game_type,
        selected_limit_type=limit_type,
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
        tourney_graph_count=tourney_graph_count,
        tournament_list=tournament_list,
        sessions=sessions,
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

# How the stat breakdown can be sliced, in the order the buttons appear.
BREAKDOWN_LABELS = [
    ("position", "By position"),
    ("street", "By street"),
    ("game", "By game"),
    ("pot_type", "By pot type"),
    ("players", "By table size"),
]
VALID_BREAKDOWNS = {key for key, _ in BREAKDOWN_LABELS}

# Below this many hands in a stat's own denominator, the number is shown but
# dimmed: W$SD off three showdowns is noise, and presenting it at the same
# weight as one off six hundred is the main way a stats page misleads.
STAT_MIN_SAMPLE = 30


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
    # The game filter dropdown shows/submits the friendly combined name
    # ("NLHE", "Limit Hold'em") rather than raw game_type, since game_type
    # alone can't tell No Limit Hold'em from Limit Hold'em - they're really
    # two different games that happen to share a name. Resolved back to the
    # (game_type, limit_type) pair the database actually needs here, once,
    # rather than changing every other link on the page to carry both.
    game_combos = db.all_game_type_combos(is_tournament=is_tournament)
    display_to_pair = {display_game_type(gt, lt): (gt, lt) for gt, lt in game_combos}
    selected_game_type = request.args.get("game_type") or None
    game_type, limit_type = display_to_pair.get(selected_game_type, (None, None))
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
        game_type=game_type, limit_type=limit_type, tag=tag, search=search, is_tournament=is_tournament,
        pot_type=pot_type, pot_min=pot_min, pot_max=pot_max, net_min=net_min, net_max=net_max,
        vpip=vpip_only,
    )

    hands = db.list_hands(sort=sort, order=order, limit=REPORT_ROW_LIMIT, **filters)
    total = db.count_hands(**filters)

    # Summary and breakdown both come from the same aggregate so the banner
    # can't disagree with the table under it, and so neither has to pull
    # every matching hand (raw_text included) back into Python to add up.
    summary = db.aggregate_stats(**filters)
    summary["count"] = total

    breakdown_by = request.args.get("breakdown")
    if breakdown_by not in VALID_BREAKDOWNS:
        breakdown_by = "position"

    # The street breakdown counts each hand once per round it reached, so it
    # can't be a GROUP BY over one column like the others - it has its own
    # query and its own table shape.
    funnel = db.street_funnel(**filters) if breakdown_by == "street" else []
    breakdown = [] if breakdown_by == "street" else db.aggregate_stats(group_by=breakdown_by, **filters)

    # Blinds and the bring-in are two different ideas of position that don't
    # belong in one column, so they're shown as separate tables rather than
    # sorted into a single list where BTN and +3 would sit next to each
    # other as if they were comparable.
    position_groups = []
    if breakdown_by == "position":
        for title, members in (
            ("Blind games \u2014 seats from the button", db.BLIND_POSITIONS),
            ("Stud games \u2014 seats from the bring-in", db.BRING_IN_POSITIONS),
        ):
            rows = [r for r in breakdown if r["group"] in members]
            if rows:
                position_groups.append({"title": title, "rows": rows})

    if breakdown_by == "game":
        # The group key is "game_type|limit_type" so the two stay paired;
        # only the label needs the friendly combined name.
        for row in breakdown:
            game_type_, _, limit_type_ = (row["group"] or "").partition("|")
            row["label"] = display_game_type(game_type_, limit_type_)
    elif breakdown_by == "players":
        for row in breakdown:
            row["label"] = f"{row['group']}-handed" if row["group"] else "unknown"
    else:
        for row in breakdown:
            row["label"] = row["group"] or "unknown"

    bounds = db.pot_net_bounds(is_tournament=is_tournament)

    # Every link on the page is the current view with one thing changed, so
    # they all start from this. url_for drops the None values, which is what
    # keeps an unset filter out of the query string entirely. Unit is only
    # pinned once it differs from the mode's default, or switching mode
    # couldn't pick up its own (tournaments in BB, cash in $).
    report_args = dict(
        mode=mode or None,
        game_type=selected_game_type,
        pot_type=pot_type,
        tag=tag,
        search=search or None,
        pot_min=pot_min, pot_max=pot_max, net_min=net_min, net_max=net_max,
        vpip=("1" if vpip_only else None),
        sort=sort,
        order=order,
        unit=(unit if unit != default_unit else None),
        breakdown=breakdown_by,
    )

    return render_template(
        "reports.html",
        hands=hands,
        total=total,
        shown=len(hands),
        summary=summary,
        breakdown=breakdown,
        breakdown_by=breakdown_by,
        position_groups=position_groups,
        funnel=funnel,
        breakdowns=BREAKDOWN_LABELS,
        min_sample=STAT_MIN_SAMPLE,
        report_args=report_args,
        unit=unit,
        default_unit=default_unit,
        game_types=sorted(display_to_pair.keys()),
        pot_types=db.all_pot_types(is_tournament=is_tournament),
        tags=db.all_tags(is_tournament=is_tournament),
        mode=mode or "",
        selected_game_type=selected_game_type,
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
        tourn_summary_dir = request.form.get("tourn_summary_dir", "").strip()
        db.set_setting("hand_history_dir", folder)
        db.set_setting("hero_username", username)
        db.set_setting("tourn_summary_dir", tourn_summary_dir)
        scan_in_background(folder, username)  # scan immediately so it's not a 15s wait
        return redirect(url_for("dashboard"))
    return render_template(
        "settings.html", folder=get_folder(), username=get_username(), db_path=db.DB_PATH,
        tourn_summary_dir=get_tourn_summary_dir() or suggest_tourn_summary_dir(get_folder()),
    )


@app.route("/api/scan-status")
def scan_status():
    return jsonify(
        {
            "last_scan_new": fw.last_scan_new,
            "last_scan_time": fw.last_scan_time,
            "folder": get_folder(),
            "active": watcher.SCAN_PROGRESS["active"],
            "done": watcher.SCAN_PROGRESS["done"],
            "total": watcher.SCAN_PROGRESS["total"],
            "hands": db.overall_stats()["hands"],
        }
    )


@app.route("/api/rescan", methods=["POST"])
def rescan():
    scan_in_background(get_folder(), get_username())
    return jsonify({"ok": True})


def _open_browser_when_ready(url):
    """Give the server a moment to bind, then open the page. Packaged builds
    are launched by double-clicking, where nobody is going to go and type a
    localhost URL themselves."""
    def go():
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass  # no browser available - the URL is printed anyway
    threading.Thread(target=go, daemon=True).start()


def main():
    url = f"http://{HOST}:{PORT}"
    print("\n  Mixed Games Tracker")
    print(f"  Open {url} in your browser if it doesn't open by itself.")
    print(f"  Your data: {db.DB_PATH}")
    print("  Close this window to stop.\n")

    if os.environ.get("MGT_NO_BROWSER") != "1":
        _open_browser_when_ready(url)

    fw.start()
    try:
        app.run(host=HOST, port=PORT, debug=False)
    except OSError as e:
        # Nearly always "port already in use" - usually a copy already running.
        print(f"\n  Couldn't start on port {PORT}: {e}")
        print(f"  If Mixed Games Tracker is already open, use {url}.")
        print("  Otherwise set a different port, e.g. MGT_PORT=5152.\n")
        if getattr(sys, "frozen", False):
            input("  Press Enter to close.")
        sys.exit(1)


if __name__ == "__main__":
    main()
