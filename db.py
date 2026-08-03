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


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def upsert_hand(hand: dict) -> bool:
    """Insert a parsed hand. Returns True if it was newly inserted."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO hands
               (hand_id, game_type, limit_type, stakes, is_tournament, tournament_id,
                table_name, date_played, hero_name, hero_cards, hero_invested,
                hero_collected, hero_net, pot_total, num_players, source_file, raw_text)
               VALUES (:hand_id, :game_type, :limit_type, :stakes, :is_tournament, :tournament_id,
                       :table_name, :date_played, :hero_name, :hero_cards, :hero_invested,
                       :hero_collected, :hero_net, :pot_total, :num_players, :source_file, :raw_text)""",
            hand,
        )
        return cur.rowcount > 0


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


def list_hands(game_type=None, tag=None, date_from=None, date_to=None, limit=200, offset=0, search=None, is_tournament=None):
    query = "SELECT h.* FROM hands h"
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
    query += " ORDER BY h.date_played DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


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
    query = "SELECT COUNT(*) as hands, SUM(hero_net) as net FROM hands"
    params = []
    if is_tournament is not None:
        query += " WHERE is_tournament = ?"
        params.append(is_tournament)
    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row)


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
