"""
Storage layer for the mixed-game hand tracker.
Everything lives in one SQLite file so the whole tool is portable.
"""
import sqlite3
import os
import shutil
import sys
import json
from contextlib import contextmanager

import version


def _default_db_path():
    """Where tracker.db lives.

    Running from source it sits next to the code, which is convenient. But a
    packaged build may be installed somewhere read-only (Program Files, or
    a macOS .app bundle), and its temp extraction folder is wiped on exit -
    writing the database there would lose every hand on close. So a frozen
    build keeps it in the user's own data directory instead.
    """
    if not getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")

    folder = os.path.join(base, "MixedGamesTracker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "tracker.db")


# Overridable so a second copy (or a test run) can point at its own file.
DB_PATH = os.environ.get("MGT_DB_PATH") or _default_db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS hands (
    hand_id       TEXT PRIMARY KEY,
    game_type     TEXT,
    limit_type    TEXT,
    stakes        TEXT,
    is_tournament INTEGER,
    tournament_id TEXT,
    table_name    TEXT,
    date_played   TEXT,
    hero_name     TEXT,
    hero_cards    TEXT,
    hero_invested REAL,
    hero_collected REAL,
    hero_net      REAL,
    pot_total     REAL,
    pot_type      TEXT,
    went_to_showdown INTEGER,
    vpip          INTEGER,
    -- Seat relative to the button (SB/BB/UTG/.../CO/BTN). NULL in stud and
    -- razz, which have no button at all, and in hands hero sat out.
    hero_position TEXT,
    -- Hero was still in when the SECOND betting round began: the flop in a
    -- flop game, 4th street in stud, the first draw in a draw game. One
    -- column rather than three because it's the denominator of the same
    -- stat in every game.
    saw_next_street INTEGER,
    -- How many betting rounds past the opening one hero was still in for.
    -- 1 is the flop / 4th street / first draw, so which street a number
    -- means depends on the game - see STREET_NAMES.
    streets_seen  INTEGER,
    raised_opening INTEGER,
    -- Bets/raises and calls after the opening round, kept as counts so they
    -- can be summed across hands and divided once for aggression factor.
    postflop_aggr INTEGER,
    postflop_calls INTEGER,
    is_allin_ev   INTEGER,
    equity_pct    REAL,
    ev_net        REAL,
    big_blind     REAL,
    num_players   INTEGER,
    source_file   TEXT,
    raw_text      TEXT,
    bounty_won    REAL,
    imported_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hand_id    TEXT NOT NULL,
    tag        TEXT NOT NULL,
    note       TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (hand_id) REFERENCES hands(hand_id) ON DELETE CASCADE,
    UNIQUE(hand_id, tag)
);

CREATE TABLE IF NOT EXISTS file_state (
    filepath TEXT PRIMARY KEY,
    mtime    REAL,
    size     INTEGER,
    hands_found INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Real-money tournament results, separate from the chip-level hero_net on
-- individual hands (tournament chips aren't cash until you finish and cash
-- out). One row per tournament_id, filled in as hands from it are parsed.
CREATE TABLE IF NOT EXISTS tournaments (
    tournament_id TEXT PRIMARY KEY,
    game_desc     TEXT,
    buy_in        REAL,
    date_played   TEXT,
    finish_place  INTEGER,
    payout        REAL,
    finished      INTEGER,
    entries       INTEGER,
    summary_source INTEGER,
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_hands_game_type ON hands(game_type);
CREATE INDEX IF NOT EXISTS idx_hands_date ON hands(date_played);
CREATE INDEX IF NOT EXISTS idx_tags_hand ON tags(hand_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first release, keyed by which table they belong
# on. Existing databases get them added in place on startup, so updating
# never means starting over.
_ADDED_COLUMNS = {
    "hands": [
        ("big_blind", "REAL"),
        ("pot_type", "TEXT"),
        ("went_to_showdown", "INTEGER"),
        ("is_allin_ev", "INTEGER"),
        ("equity_pct", "REAL"),
        ("ev_net", "REAL"),
        ("vpip", "INTEGER"),
        ("bounty_won", "REAL"),
        ("hero_position", "TEXT"),
        ("saw_next_street", "INTEGER"),
        ("streets_seen", "INTEGER"),
        ("raised_opening", "INTEGER"),
        ("postflop_aggr", "INTEGER"),
        ("postflop_calls", "INTEGER"),
    ],
    "tournaments": [
        ("finished", "INTEGER"),
        ("entries", "INTEGER"),
        ("summary_source", "INTEGER"),
    ],
}


def _pending_migrations(conn):
    """(table, column, sql_type) for every added column an existing database
    doesn't have yet."""
    pending = []
    for table, columns in _ADDED_COLUMNS.items():
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        pending += [(table, name, sql_type) for name, sql_type in columns if name not in cols]
    return pending


def _backup_database():
    """Copied before the first schema change of an update. Cheap insurance:
    if a migration ever goes wrong, the hands are still sitting right next
    to the database in a .bak file."""
    if not os.path.exists(DB_PATH):
        return
    try:
        shutil.copy2(DB_PATH, DB_PATH + ".bak")
    except OSError:
        pass  # a failed backup shouldn't stop the app opening


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        pending = _pending_migrations(conn)
        if pending:
            _backup_database()
            for table, name, sql_type in pending:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def reimport_needed():
    """True when this build parses hands differently from whatever imported
    the ones already stored, so the existing rows are out of date."""
    stored = get_setting("parser_version")
    if stored is None:
        return False  # nothing imported yet, or a database from before this was tracked
    try:
        return int(stored) < version.PARSER_VERSION
    except ValueError:
        return False


def mark_parser_version():
    set_setting("parser_version", str(version.PARSER_VERSION))
    set_setting("app_version", version.VERSION)


def clear_file_state():
    """Forget which files have already been read, so the next scan re-parses
    everything from scratch. Hand rows get rewritten in place - tags and
    notes live in their own table keyed by hand id, so they survive."""
    with get_conn() as conn:
        conn.execute("DELETE FROM file_state")


# Columns added after the first release. A hand dict that predates them (or
# comes from somewhere other than the parser) is filled in rather than
# rejected: sqlite raises on a missing named parameter, and refusing to
# store an otherwise-good hand over a stat column would be the wrong
# trade.
_OPTIONAL_HAND_FIELDS = {
    "bounty_won": 0.0,
    "hero_position": None,
    "saw_next_street": 0,
    "streets_seen": 0,
    "raised_opening": 0,
    "postflop_aggr": 0,
    "postflop_calls": 0,
}


def upsert_hand(hand: dict) -> bool:
    """Insert or update a parsed hand. Returns True if it was newly inserted
    (as opposed to overwriting a hand seen before, e.g. after a parser fix)."""
    hand = {**_OPTIONAL_HAND_FIELDS, **hand}
    with get_conn() as conn:
        existed = conn.execute("SELECT 1 FROM hands WHERE hand_id=?", (hand["hand_id"],)).fetchone() is not None
        conn.execute(
            """INSERT INTO hands
               (hand_id, game_type, limit_type, stakes, is_tournament, tournament_id,
                table_name, date_played, hero_name, hero_cards, hero_invested,
                hero_collected, hero_net, pot_total, pot_type, went_to_showdown, vpip,
                is_allin_ev, equity_pct, ev_net, big_blind, num_players, source_file, raw_text,
                bounty_won, hero_position, saw_next_street, streets_seen, raised_opening,
                postflop_aggr, postflop_calls)
               VALUES (:hand_id, :game_type, :limit_type, :stakes, :is_tournament, :tournament_id,
                       :table_name, :date_played, :hero_name, :hero_cards, :hero_invested,
                       :hero_collected, :hero_net, :pot_total, :pot_type, :went_to_showdown, :vpip,
                       :is_allin_ev, :equity_pct, :ev_net, :big_blind, :num_players, :source_file, :raw_text,
                       :bounty_won, :hero_position, :saw_next_street, :streets_seen, :raised_opening,
                       :postflop_aggr, :postflop_calls)
               ON CONFLICT(hand_id) DO UPDATE SET
                 game_type=excluded.game_type, limit_type=excluded.limit_type, stakes=excluded.stakes,
                 is_tournament=excluded.is_tournament, tournament_id=excluded.tournament_id,
                 table_name=excluded.table_name, date_played=excluded.date_played,
                 hero_name=excluded.hero_name, hero_cards=excluded.hero_cards,
                 hero_invested=excluded.hero_invested, hero_collected=excluded.hero_collected,
                 hero_net=excluded.hero_net, pot_total=excluded.pot_total, pot_type=excluded.pot_type,
                 went_to_showdown=excluded.went_to_showdown, vpip=excluded.vpip, is_allin_ev=excluded.is_allin_ev,
                 equity_pct=excluded.equity_pct, ev_net=excluded.ev_net, big_blind=excluded.big_blind,
                 num_players=excluded.num_players, source_file=excluded.source_file, raw_text=excluded.raw_text,
                 bounty_won=excluded.bounty_won, hero_position=excluded.hero_position,
                 saw_next_street=excluded.saw_next_street, streets_seen=excluded.streets_seen,
                 raised_opening=excluded.raised_opening,
                 postflop_aggr=excluded.postflop_aggr, postflop_calls=excluded.postflop_calls""",
            hand,
        )
        return not existed


def upsert_tournament_shell(tournament_id, game_desc, buy_in, date_played):
    """Record a tournament's identity/buy-in the first time we see any hand
    from it. game_desc is kept fresh on every later hand too (unlike
    buy_in/date_played, which are only ever set once) - it's cheap to
    recompute and the cleanup that strips level/stakes clutter off it was
    added after plenty of existing rows already had the messy version
    baked in, same as any other parsing improvement. Guarded the same way
    as everything else here: a Tournament Summary file's name always wins
    once one's been read for this tournament."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tournaments (tournament_id, game_desc, buy_in, date_played)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(tournament_id) DO UPDATE SET game_desc=excluded.game_desc
               WHERE tournaments.summary_source IS NULL OR tournaments.summary_source=0""",
            (tournament_id, game_desc, buy_in, date_played),
        )


def set_tournament_result(tournament_id, finish_place, payout, finished=True):
    """Hand-history-derived result. A no-op wherever a Tournament Summary
    file has already supplied an authoritative one for this tournament -
    summaries know about re-entries and always carry an exact finish place,
    which hand-history text alone can't reliably tell (see
    set_tournament_summary)."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE tournaments SET finish_place=?, payout=?, finished=?, updated_at=datetime('now')
               WHERE tournament_id=? AND (summary_source IS NULL OR summary_source=0)""",
            (finish_place, payout, 1 if finished else 0, tournament_id),
        )


def set_tournament_summary(tournament_id, game_desc, date_played, buy_in, entries, finish_place, payout):
    """Tournament Summary file result - PokerStars' own authoritative
    record, so this always overwrites (re-reading the same file is
    idempotent), and marks the row so hand-history parsing no longer
    touches its buy-in/finish/payout. Bounty cash is deliberately untouched
    here - summaries don't track it, it stays sourced from hand histories
    regardless of summary_source."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tournaments (tournament_id, game_desc, buy_in, date_played)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(tournament_id) DO NOTHING""",
            (tournament_id, game_desc, buy_in, date_played),
        )
        conn.execute(
            """UPDATE tournaments
               SET game_desc=?, buy_in=?, entries=?, finish_place=?, payout=?, finished=1,
                   summary_source=1, updated_at=datetime('now')
               WHERE tournament_id=?""",
            (game_desc, buy_in, entries, finish_place, payout, tournament_id),
        )


# Shared by every query below - the real payout is the recorded prize-pool
# cashout (from the finish line) plus any progressive-bounty cash won along
# the way. Bounty cash can land on any hand in the tournament, not just the
# last one, so it's summed fresh from the hands table rather than tracked
# as a running total anywhere.
_BOUNTY_SUBQUERY = """
    SELECT tournament_id, SUM(bounty_won) as bounty
    FROM hands WHERE tournament_id IS NOT NULL AND bounty_won > 0
    GROUP BY tournament_id
"""


def tournament_stats(order="desc"):
    sort_dir = "ASC" if order == "asc" else "DESC"
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT t.tournament_id, t.game_desc, t.buy_in, t.date_played, t.finish_place, t.finished,
                       (COALESCE(t.payout, 0) + COALESCE(b.bounty, 0)) as payout,
                       (COALESCE(t.payout, 0) + COALESCE(b.bounty, 0) - COALESCE(t.buy_in, 0)) as net
                FROM tournaments t
                LEFT JOIN ({_BOUNTY_SUBQUERY}) b ON b.tournament_id = t.tournament_id
                ORDER BY t.date_played {sort_dir}"""
        ).fetchall()
        return [dict(r) for r in rows]


def tournament_game_types():
    """Distinct (tournament_id, game_type, limit_type) triples - which games
    were played within each tournament. A mixed-rotation event touches
    several; this is how the dashboard's game checkboxes know whether a
    given tournament belongs in a filtered view of the results graph
    (client-side, since a tournament's buy-in/payout is one result, not
    something split per game the way a hand's net is)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT tournament_id, game_type, limit_type FROM hands
               WHERE tournament_id IS NOT NULL AND game_type IS NOT NULL"""
        ).fetchall()
        return [dict(r) for r in rows]


def tournament_list():
    """One row per tournament for the summary list on the Tournaments tab -
    buy-in/payout/net plus how long it actually took, timed from the hands
    themselves (first hand played to last) rather than tournaments.date_played,
    which is only ever set from whichever hand happened to be scanned first
    and so isn't reliably the tournament's actual start."""
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT t.tournament_id, t.game_desc, t.buy_in, t.finish_place, t.finished,
                      (COALESCE(t.payout, 0) + COALESCE(b.bounty, 0)) as payout,
                      (COALESCE(t.payout, 0) + COALESCE(b.bounty, 0) - COALESCE(t.buy_in, 0)) as net,
                      s.started_at, s.ended_at, s.num_hands
               FROM tournaments t
               JOIN (
                   SELECT tournament_id, MIN(date_played) as started_at,
                          MAX(date_played) as ended_at, COUNT(*) as num_hands
                   FROM hands WHERE tournament_id IS NOT NULL
                   GROUP BY tournament_id
               ) s ON s.tournament_id = t.tournament_id
               LEFT JOIN ({_BOUNTY_SUBQUERY}) b ON b.tournament_id = t.tournament_id
               ORDER BY s.started_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def tournament_overall():
    """Only counts tournaments the hero is actually done playing - an
    in-progress tournament's buy-in isn't a realized loss yet, so it
    shouldn't drag down ROI/avg-buy-in until it actually finishes. "Done"
    means a finish line was seen for them at all (tournaments.finished),
    whether or not it came with an exact placement - PokerStars sometimes
    omits the place when several players bust in the same hand."""
    with get_conn() as conn:
        completed = dict(conn.execute(
            f"""SELECT COUNT(*) as n,
                      SUM(t.buy_in) as total_buyin,
                      SUM(COALESCE(t.payout, 0) + COALESCE(b.bounty, 0)) as total_payout,
                      SUM(COALESCE(t.payout, 0) + COALESCE(b.bounty, 0) - COALESCE(t.buy_in, 0)) as net,
                      SUM(CASE WHEN COALESCE(t.payout, 0) + COALESCE(b.bounty, 0) > 0 THEN 1 ELSE 0 END) as itm
               FROM tournaments t
               LEFT JOIN ({_BOUNTY_SUBQUERY}) b ON b.tournament_id = t.tournament_id
               WHERE t.finished = 1"""
        ).fetchone())
        total_n = conn.execute("SELECT COUNT(*) as n FROM tournaments").fetchone()["n"] or 0
        completed["in_progress"] = total_n - (completed["n"] or 0)
        return completed


def get_file_state(filepath):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM file_state WHERE filepath=?", (filepath,)).fetchone()
        return dict(row) if row else None


def set_file_state(filepath, mtime, size, hands_found):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO file_state (filepath, mtime, size, hands_found)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(filepath) DO UPDATE SET mtime=excluded.mtime,
                   size=excluded.size, hands_found=excluded.hands_found""",
            (filepath, mtime, size, hands_found),
        )


SORT_COLUMNS = {
    "date": "h.date_played",
    "pot": "h.pot_total",
    "net": "h.hero_net",
    "pot_type": "h.pot_type",
    "game_type": "h.game_type",
}


def _hand_filters(game_type=None, limit_type=None, tag=None, date_from=None, date_to=None, search=None,
                   is_tournament=None, pot_type=None, pot_min=None, pot_max=None,
                   net_min=None, net_max=None, vpip=None):
    """Shared WHERE/JOIN builder so list_hands and count_hands can't drift
    apart - a count that didn't match the list would be worse than none."""
    joins, where, params = [], [], []

    if tag:
        joins.append("JOIN tags t ON t.hand_id = h.hand_id")
        where.append("t.tag = ?")
        params.append(tag)
    if game_type:
        where.append("h.game_type = ?")
        params.append(game_type)
    if limit_type:
        # Paired with game_type wherever both are known - "Hold'em" alone
        # covers NLHE and Limit Hold'em at once, two different games that
        # happen to share a name; passing this too narrows to just one.
        where.append("h.limit_type = ?")
        params.append(limit_type)
    if is_tournament is not None:
        where.append("h.is_tournament = ?")
        params.append(is_tournament)
    if pot_type:
        where.append("h.pot_type = ?")
        params.append(pot_type)
    if pot_min is not None:
        where.append("h.pot_total >= ?")
        params.append(pot_min)
    if pot_max is not None:
        where.append("h.pot_total <= ?")
        params.append(pot_max)
    if net_min is not None:
        where.append("h.hero_net >= ?")
        params.append(net_min)
    if net_max is not None:
        where.append("h.hero_net <= ?")
        params.append(net_max)
    if vpip:
        where.append("h.vpip = 1")
    if date_from:
        where.append("h.date_played >= ?")
        params.append(date_from)
    if date_to:
        where.append("h.date_played <= ?")
        params.append(date_to)
    if search:
        where.append("h.raw_text LIKE ?")
        params.append(f"%{search}%")

    clause = ""
    if joins:
        clause += " " + " ".join(joins)
    if where:
        clause += " WHERE " + " AND ".join(where)
    return clause, params


def count_hands(**filters):
    """How many hands match, ignoring any row limit."""
    clause, params = _hand_filters(**filters)
    with get_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) AS n FROM hands h{clause}", params).fetchone()["n"]


def list_hands(limit=200, offset=0, sort="date", order="desc", **filters):
    clause, params = _hand_filters(**filters)
    query = ("""SELECT h.*,
                       (SELECT GROUP_CONCAT(tag) FROM tags WHERE tags.hand_id = h.hand_id) as tag_list
                FROM hands h""" + clause)

    sort_col = SORT_COLUMNS.get(sort, SORT_COLUMNS["date"])
    sort_dir = "ASC" if order == "asc" else "DESC"
    query += f" ORDER BY {sort_col} {sort_dir}"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params = params + [limit, offset]

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# Preflop order of action, which is also how a positional report reads most
# naturally: earliest seat first, button last.
BLIND_POSITIONS = ["SB", "BB", "UTG", "UTG+1", "UTG+2", "UTG+3", "UTG+4", "LJ", "HJ", "CO", "BTN"]
# Stud has no button or blinds, so seats are counted round from the bring-in
# in the order they act: BI, +1, +2 ... The last number depends on how many
# were dealt in (+5 six-handed, +6 seven-handed).
BRING_IN_POSITIONS = ["BI"] + [f"+{i}" for i in range(1, 8)]
POSITION_ORDER = BLIND_POSITIONS + BRING_IN_POSITIONS

# Which betting rounds each family of games actually has, in order, so that
# "one round past the opening" can be printed as the street it really is.
STUD_GAMES = {"Razz", "Stud", "Stud Hi/Lo"}
SINGLE_DRAW_GAMES = {"2-7 Single Draw"}
MULTI_DRAW_GAMES = {"2-7 Triple Draw", "Badugi", "5 Card Draw"}
STREET_NAMES = {
    "flop": ["Flop", "Turn", "River"],
    "stud": ["4th street", "5th street", "6th street", "7th street"],
    "draw": ["1st draw", "2nd draw", "3rd draw"],
    "single-draw": ["The draw"],
}
FAMILY_NAMES = {
    "flop": "Flop games", "stud": "Stud games",
    "draw": "Draw games", "single-draw": "Single draw",
}


def game_family(game_type):
    if game_type in STUD_GAMES:
        return "stud"
    if game_type in SINGLE_DRAW_GAMES:
        return "single-draw"
    if game_type in MULTI_DRAW_GAMES:
        return "draw"
    return "flop"


def street_name(game_type, level):
    """What the nth betting round past the opening one is called in this
    game. Level 1 is the flop, 4th street or first draw."""
    names = STREET_NAMES[game_family(game_type)]
    return names[level - 1] if 1 <= level <= len(names) else f"round {level}"

# Everything the stat report needs, as sums that can be grouped by anything.
# Percentages are worked out afterwards in Python rather than here: each one
# has its own denominator (hands, hands that saw a flop, hands that reached
# showdown), and spelling those out in SQL makes the query unreadable
# without making it faster.
_STAT_SUMS = """
    COUNT(*) AS hands,
    SUM(h.hero_net) AS net,
    SUM(CASE WHEN h.big_blind > 0 THEN h.hero_net / h.big_blind END) AS net_bb,
    SUM(CASE WHEN h.big_blind > 0 THEN 1 ELSE 0 END) AS bb_hands,
    SUM(COALESCE(h.vpip, 0)) AS vpip_n,
    SUM(COALESCE(h.raised_opening, 0)) AS pfr_n,
    SUM(COALESCE(h.saw_next_street, 0)) AS saw_n,
    SUM(CASE WHEN h.saw_next_street = 1 AND h.hero_collected > 0 THEN 1 ELSE 0 END) AS won_saw_n,
    SUM(CASE WHEN h.went_to_showdown = 1 THEN 1 ELSE 0 END) AS showdown_n,
    SUM(CASE WHEN h.went_to_showdown = 1 AND h.hero_collected > 0 THEN 1 ELSE 0 END) AS won_showdown_n,
    SUM(COALESCE(h.postflop_aggr, 0)) AS aggr_n,
    SUM(COALESCE(h.postflop_calls, 0)) AS calls_n,
    SUM(COALESCE(h.pot_total, 0)) AS pot_sum,
    SUM(CASE WHEN h.big_blind > 0 THEN h.pot_total / h.big_blind END) AS pot_sum_bb,
    SUM(CASE WHEN h.hero_net > 0 THEN 1 ELSE 0 END) AS won_n
"""

# How each report groups its rows. The value is the SQL expression grouped
# on; "game" needs both columns because game_type alone can't tell No Limit
# Hold'em from Limit Hold'em.
STAT_GROUPINGS = {
    "position": "h.hero_position",
    "game": "h.game_type || '|' || COALESCE(h.limit_type, '')",
    "pot_type": "h.pot_type",
    "players": "h.num_players",
}


def _pct(numerator, denominator):
    return (numerator / denominator * 100) if denominator else None


def _derive_stats(row):
    """Turn the raw sums into the stats people actually read.

    Each denominator is the one the stat is defined against, and is carried
    alongside the percentage so the UI can grey out a number that rests on
    barely any hands - 100% W$SD off two showdowns is noise, not a read."""
    d = dict(row)
    hands = d["hands"] or 0
    saw = d["saw_n"] or 0
    showdowns = d["showdown_n"] or 0
    d["vpip_pct"] = _pct(d["vpip_n"], hands)
    d["pfr_pct"] = _pct(d["pfr_n"], hands)
    d["saw_pct"] = _pct(saw, hands)
    d["wwsf_pct"] = _pct(d["won_saw_n"], saw)
    d["wtsd_pct"] = _pct(showdowns, saw)
    d["wsd_pct"] = _pct(d["won_showdown_n"], showdowns)
    d["wwsf_n"], d["wtsd_n"], d["wsd_n"] = saw, saw, showdowns
    # Aggression factor is undefined with no calls rather than infinite, so
    # it stays None instead of being reported as a huge number.
    d["aggression"] = (d["aggr_n"] / d["calls_n"]) if d["calls_n"] else None
    d["bb_per_100"] = ((d["net_bb"] / d["bb_hands"]) * 100) if d["bb_hands"] else None
    d["win_rate"] = _pct(d["won_n"], hands)
    d["avg_pot"] = (d["pot_sum"] / hands) if hands else 0
    d["avg_pot_bb"] = (d["pot_sum_bb"] / d["bb_hands"]) if d["bb_hands"] else 0
    # A net of zero reads better than None in the banner, and "no hands" is
    # already obvious from the hand count next to it.
    d["net"] = d["net"] or 0
    d["net_bb"] = d["net_bb"] or 0
    return d


def aggregate_stats(group_by=None, **filters):
    """Stats over every hand matching the same filters the report list uses.

    Returns one row when group_by is None, otherwise a row per group with a
    "group" key. Grouping runs in SQL rather than over the hand list in
    Python because these reports are the one place that wants every matching
    hand, and pulling thousands of raw_text blobs back just to count them
    would dominate the page load."""
    clause, params = _hand_filters(**filters)
    if group_by is None:
        query = f"SELECT {_STAT_SUMS} FROM hands h{clause}"
        with get_conn() as conn:
            return _derive_stats(conn.execute(query, params).fetchone())

    expr = STAT_GROUPINGS[group_by]
    query = f'SELECT {expr} AS "group", {_STAT_SUMS} FROM hands h{clause} GROUP BY 1'
    with get_conn() as conn:
        rows = [_derive_stats(r) for r in conn.execute(query, params).fetchall()]

    if group_by == "position":
        # Stud has no button, and a hand hero sat out has no seat, so both
        # come back with a NULL group that says nothing about position.
        rows = [r for r in rows if r["group"]]
        order = {name: i for i, name in enumerate(POSITION_ORDER)}
        rows.sort(key=lambda r: order.get(r["group"], len(order)))
    else:
        rows.sort(key=lambda r: -(r["hands"] or 0))
    return rows


def street_funnel(**filters):
    """How far hands actually went, street by street, per family of games.

    Returns [{family, family_label, rows: [...]}]. Each row is one betting
    round: how many hands got that far, what share of the family's hands
    that is, and how often hero won the ones that did.

    Grouped by family rather than by game because "level 2" is the turn in
    one game and 5th street in another - a single table mixing them would
    have no honest column heading. Within a family the rounds line up, and
    the game filter is there for looking at one game on its own.

    Hands are counted cumulatively: a hand that reached 6th street also
    reached 4th and 5th, so it belongs in all three rows."""
    clause, params = _hand_filters(**filters)
    query = f"""
        SELECT h.game_type AS game_type, COALESCE(h.streets_seen, 0) AS depth,
               COUNT(*) AS n,
               SUM(CASE WHEN h.hero_collected > 0 THEN 1 ELSE 0 END) AS won,
               SUM(CASE WHEN h.went_to_showdown = 1 THEN 1 ELSE 0 END) AS showdowns,
               SUM(h.hero_net) AS net,
               SUM(CASE WHEN h.big_blind > 0 THEN h.hero_net / h.big_blind END) AS net_bb,
               SUM(CASE WHEN h.big_blind > 0 THEN 1 ELSE 0 END) AS bb_hands
        FROM hands h{clause} GROUP BY 1, 2
    """
    with get_conn() as conn:
        raw = [dict(r) for r in conn.execute(query, params).fetchall()]

    families = {}
    for row in raw:
        fam = game_family(row["game_type"])
        bucket = families.setdefault(fam, {"total": 0, "by_depth": {}, "sample": row["game_type"]})
        bucket["total"] += row["n"]
        d = bucket["by_depth"].setdefault(row["depth"], dict.fromkeys(
            ("n", "won", "showdowns", "net", "net_bb", "bb_hands"), 0))
        for key in d:
            d[key] += row[key] or 0

    out = []
    for fam, bucket in families.items():
        depths = bucket["by_depth"]
        levels = len(STREET_NAMES[fam])
        rows = []
        for level in range(1, levels + 1):
            deeper = [v for depth, v in depths.items() if depth >= level]
            reached = sum(v["n"] for v in deeper)
            if not reached:
                continue
            won = sum(v["won"] for v in deeper)
            bb_hands = sum(v["bb_hands"] for v in deeper)
            net_bb = sum(v["net_bb"] for v in deeper)
            rows.append({
                "level": level,
                "label": street_name(bucket["sample"], level),
                "reached": reached,
                "of_all_pct": _pct(reached, bucket["total"]),
                "won": won,
                "won_pct": _pct(won, reached),
                "showdowns": sum(v["showdowns"] for v in deeper),
                # As a rate, not a count: a hand that reaches a showdown has
                # by definition reached every street, so the raw count is
                # identical on every row of the family and says nothing. The
                # share of hands that got this far and then went all the way
                # does vary, and is the interesting half.
                "showdown_pct": _pct(sum(v["showdowns"] for v in deeper), reached),
                "net": sum(v["net"] for v in deeper),
                "net_bb": net_bb,
                "bb_hands": bb_hands,
                "bb_per_100": ((net_bb / bb_hands) * 100) if bb_hands else None,
            })
        if rows:
            out.append({
                "family": fam,
                "family_label": FAMILY_NAMES[fam],
                "hands": bucket["total"],
                "rows": rows,
            })
    order = list(STREET_NAMES)
    out.sort(key=lambda f: order.index(f["family"]))
    return out


def all_pot_types(is_tournament=None):
    query = "SELECT DISTINCT pot_type FROM hands WHERE pot_type IS NOT NULL"
    params = []
    if is_tournament is not None:
        query += " AND is_tournament = ?"
        params.append(is_tournament)
    query += " ORDER BY pot_type"
    with get_conn() as conn:
        return [r["pot_type"] for r in conn.execute(query, params).fetchall()]


def all_game_type_combos(is_tournament=None):
    """Distinct (game_type, limit_type) pairs actually played - e.g. Hold'em
    shows up twice if both No Limit and Limit hands exist, since those are
    really two different games that just happen to share a name."""
    query = "SELECT DISTINCT game_type, limit_type FROM hands WHERE game_type IS NOT NULL"
    params = []
    if is_tournament is not None:
        query += " AND is_tournament = ?"
        params.append(is_tournament)
    query += " ORDER BY game_type, limit_type"
    with get_conn() as conn:
        return [(r["game_type"], r["limit_type"]) for r in conn.execute(query, params).fetchall()]


def pot_net_bounds(is_tournament=None):
    """Min/max pot size and net result across the matching hands - used to
    size the Reports page's range sliders to the actual data."""
    query = "SELECT MIN(pot_total) as pot_min, MAX(pot_total) as pot_max, MIN(hero_net) as net_min, MAX(hero_net) as net_max FROM hands"
    params = []
    if is_tournament is not None:
        query += " WHERE is_tournament = ?"
        params.append(is_tournament)
    with get_conn() as conn:
        return dict(conn.execute(query, params).fetchone())


def get_hand(hand_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM hands WHERE hand_id=?", (hand_id,)).fetchone()
        if not row:
            return None
        hand = dict(row)
        tags = conn.execute("SELECT tag, note, created_at FROM tags WHERE hand_id=? ORDER BY created_at", (hand_id,)).fetchall()
        hand["tags"] = [dict(t) for t in tags]
        hand["tag_list"] = ",".join(t["tag"] for t in tags)  # same shape list_hands() uses, for the shared row template
        return hand


def add_tag(hand_id, tag, note=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tags (hand_id, tag, note) VALUES (?, ?, ?)",
            (hand_id, tag.strip(), note.strip()),
        )


def remove_tag(hand_id, tag):
    with get_conn() as conn:
        conn.execute("DELETE FROM tags WHERE hand_id=? AND tag=?", (hand_id, tag))


def all_tags(is_tournament=None):
    with get_conn() as conn:
        if is_tournament is not None:
            rows = conn.execute(
                """SELECT t.tag, COUNT(*) as n FROM tags t
                   JOIN hands h ON h.hand_id = t.hand_id
                   WHERE h.is_tournament = ?
                   GROUP BY t.tag ORDER BY n DESC""",
                (is_tournament,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT tag, COUNT(*) as n FROM tags GROUP BY tag ORDER BY n DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def stats_by_game_type(is_tournament=None):
    """One row per (game_type, limit_type) - grouping by game_type alone
    would silently merge, say, No Limit Hold'em and Limit Hold'em into one
    "Hold'em" card, hiding that they're really two different games that
    happen to share a name (same for Omaha/PLO, Omaha Hi/Lo/PLO8, etc.)."""
    query = """SELECT game_type, limit_type,
                      COUNT(*) as hands,
                      SUM(hero_net) as net,
                      AVG(hero_net) as avg_net,
                      SUM(CASE WHEN big_blind > 0 THEN hero_net / big_blind ELSE NULL END) as net_bb,
                      SUM(CASE WHEN hero_net > 0 THEN 1 ELSE 0 END) as won,
                      SUM(CASE WHEN hero_net < 0 THEN 1 ELSE 0 END) as lost
               FROM hands"""
    params = []
    if is_tournament is not None:
        query += " WHERE is_tournament = ?"
        params.append(is_tournament)
    query += " GROUP BY game_type, limit_type ORDER BY hands DESC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def overall_stats(is_tournament=None):
    query = """SELECT COUNT(*) as hands, SUM(hero_net) as net,
                      SUM(CASE WHEN big_blind > 0 THEN hero_net / big_blind ELSE NULL END) as net_bb,
                      SUM(CASE WHEN big_blind > 0 THEN 1 ELSE 0 END) as bb_hands
               FROM hands"""
    params = []
    if is_tournament is not None:
        query += " WHERE is_tournament = ?"
        params.append(is_tournament)
    with get_conn() as conn:
        row = dict(conn.execute(query, params).fetchone())
        row["bb_per_100"] = (row["net_bb"] / row["bb_hands"] * 100) if row["bb_hands"] else None
        return row


def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
