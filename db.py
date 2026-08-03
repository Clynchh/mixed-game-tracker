"""
Storage layer for the mixed-game hand tracker.
Everything lives in one SQLite file so the whole tool is portable.
"""
import sqlite3
import os
import json
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")

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
    big_blind     REAL,
    num_players   INTEGER,
    source_file   TEXT,
    raw_text      TEXT,
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


def _migrate(conn):
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(hands)").fetchall()]
    if "big_blind" not in cols:
        conn.execute("ALTER TABLE hands ADD COLUMN big_blind REAL")
    if "pot_type" not in cols:
        conn.execute("ALTER TABLE hands ADD COLUMN pot_type TEXT")


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def upsert_hand(hand: dict) -> bool:
    """Insert or update a parsed hand. Returns True if it was newly inserted
    (as opposed to overwriting a hand seen before, e.g. after a parser fix)."""
    with get_conn() as conn:
        existed = conn.execute("SELECT 1 FROM hands WHERE hand_id=?", (hand["hand_id"],)).fetchone() is not None
        conn.execute(
            """INSERT INTO hands
               (hand_id, game_type, limit_type, stakes, is_tournament, tournament_id,
                table_name, date_played, hero_name, hero_cards, hero_invested,
                hero_collected, hero_net, pot_total, pot_type, big_blind, num_players, source_file, raw_text)
               VALUES (:hand_id, :game_type, :limit_type, :stakes, :is_tournament, :tournament_id,
                       :table_name, :date_played, :hero_name, :hero_cards, :hero_invested,
                       :hero_collected, :hero_net, :pot_total, :pot_type, :big_blind, :num_players, :source_file, :raw_text)
               ON CONFLICT(hand_id) DO UPDATE SET
                 game_type=excluded.game_type, limit_type=excluded.limit_type, stakes=excluded.stakes,
                 is_tournament=excluded.is_tournament, tournament_id=excluded.tournament_id,
                 table_name=excluded.table_name, date_played=excluded.date_played,
                 hero_name=excluded.hero_name, hero_cards=excluded.hero_cards,
                 hero_invested=excluded.hero_invested, hero_collected=excluded.hero_collected,
                 hero_net=excluded.hero_net, pot_total=excluded.pot_total, pot_type=excluded.pot_type,
                 big_blind=excluded.big_blind, num_players=excluded.num_players,
                 source_file=excluded.source_file, raw_text=excluded.raw_text""",
            hand,
        )
        return not existed


def upsert_tournament_shell(tournament_id, game_desc, buy_in, date_played):
    """Record a tournament's identity/buy-in the first time we see any hand from it."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tournaments (tournament_id, game_desc, buy_in, date_played)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(tournament_id) DO NOTHING""",
            (tournament_id, game_desc, buy_in, date_played),
        )


def set_tournament_result(tournament_id, finish_place, payout):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tournaments SET finish_place=?, payout=?, updated_at=datetime('now') WHERE tournament_id=?",
            (finish_place, payout, tournament_id),
        )


def tournament_stats(order="desc"):
    sort_dir = "ASC" if order == "asc" else "DESC"
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT tournament_id, game_desc, buy_in, date_played, finish_place, payout,
                       (COALESCE(payout, 0) - COALESCE(buy_in, 0)) as net
                FROM tournaments ORDER BY date_played {sort_dir}"""
        ).fetchall()
        return [dict(r) for r in rows]


def tournament_overall():
    """Only counts tournaments with a recorded finish - an in-progress
    tournament's buy-in isn't a realized loss yet, so it shouldn't drag
    down ROI/avg-buy-in until it actually finishes."""
    with get_conn() as conn:
        completed = dict(conn.execute(
            """SELECT COUNT(*) as n,
                      SUM(buy_in) as total_buyin,
                      SUM(payout) as total_payout,
                      SUM(COALESCE(payout, 0) - COALESCE(buy_in, 0)) as net,
                      SUM(CASE WHEN payout > 0 THEN 1 ELSE 0 END) as itm
               FROM tournaments WHERE finish_place IS NOT NULL"""
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


def list_hands(game_type=None, tag=None, date_from=None, date_to=None, limit=200, offset=0, search=None,
                is_tournament=None, pot_type=None, pot_min=None, pot_max=None, net_min=None, net_max=None,
                sort="date", order="desc"):
    query = """SELECT h.*,
                      (SELECT GROUP_CONCAT(tag) FROM tags WHERE tags.hand_id = h.hand_id) as tag_list
               FROM hands h"""
    params = []
    joins = []
    where = []

    if tag:
        joins.append("JOIN tags t ON t.hand_id = h.hand_id")
        where.append("t.tag = ?")
        params.append(tag)
    if game_type:
        where.append("h.game_type = ?")
        params.append(game_type)
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
    if date_from:
        where.append("h.date_played >= ?")
        params.append(date_from)
    if date_to:
        where.append("h.date_played <= ?")
        params.append(date_to)
    if search:
        where.append("h.raw_text LIKE ?")
        params.append(f"%{search}%")

    if joins:
        query += " " + " ".join(joins)
    if where:
        query += " WHERE " + " AND ".join(where)

    sort_col = SORT_COLUMNS.get(sort, SORT_COLUMNS["date"])
    sort_dir = "ASC" if order == "asc" else "DESC"
    query += f" ORDER BY {sort_col} {sort_dir}"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params += [limit, offset]

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def all_pot_types(is_tournament=None):
    query = "SELECT DISTINCT pot_type FROM hands WHERE pot_type IS NOT NULL"
    params = []
    if is_tournament is not None:
        query += " AND is_tournament = ?"
        params.append(is_tournament)
    query += " ORDER BY pot_type"
    with get_conn() as conn:
        return [r["pot_type"] for r in conn.execute(query, params).fetchall()]


def all_game_types(is_tournament=None):
    query = "SELECT DISTINCT game_type FROM hands WHERE game_type IS NOT NULL"
    params = []
    if is_tournament is not None:
        query += " AND is_tournament = ?"
        params.append(is_tournament)
    query += " ORDER BY game_type"
    with get_conn() as conn:
        return [r["game_type"] for r in conn.execute(query, params).fetchall()]


def get_hand(hand_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM hands WHERE hand_id=?", (hand_id,)).fetchone()
        if not row:
            return None
        hand = dict(row)
        tags = conn.execute("SELECT tag, note, created_at FROM tags WHERE hand_id=? ORDER BY created_at", (hand_id,)).fetchall()
        hand["tags"] = [dict(t) for t in tags]
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
    query = """SELECT game_type,
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
    query += " GROUP BY game_type ORDER BY hands DESC"
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
