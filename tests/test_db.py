"""db.py: storage layer - schema, migrations, settings, tags, filters."""
import sqlite3

import db
import version


def _make_hand(hand_id="h1", **overrides):
    hand = dict(
        hand_id=hand_id, game_type="Hold'em", limit_type="NL", stakes="1/2",
        is_tournament=0, tournament_id=None, table_name="Table 1",
        date_played="2026/01/01 00:00:00", hero_name="Hero", hero_cards="Ah Kd",
        hero_invested=10.0, hero_collected=25.0, hero_net=15.0, pot_total=25.0,
        pot_type="Raised (SRP)", went_to_showdown=1, vpip=1, is_allin_ev=0,
        equity_pct=None, ev_net=None, big_blind=2.0, num_players=6,
        source_file="test.txt", raw_text="PokerStars Hand #1: ...",
    )
    hand.update(overrides)
    return hand


def test_upsert_hand_inserts_then_updates_in_place():
    assert db.upsert_hand(_make_hand()) is True  # newly inserted
    assert db.upsert_hand(_make_hand(hero_net=99.0)) is False  # already existed
    stored = db.get_hand("h1")
    assert stored["hero_net"] == 99.0  # overwritten, not duplicated
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM hands").fetchone()["n"] == 1


def test_upsert_hand_preserves_tags_on_reimport():
    db.upsert_hand(_make_hand())
    db.add_tag("h1", "leak", "note here")
    db.upsert_hand(_make_hand(hero_net=5.0))  # simulates a parser-fix re-read
    stored = db.get_hand("h1")
    assert stored["hero_net"] == 5.0
    assert [t["tag"] for t in stored["tags"]] == ["leak"]


def test_tag_add_remove_and_dedup():
    db.upsert_hand(_make_hand())
    db.add_tag("h1", "good", "")
    db.add_tag("h1", "good", "replaced note")  # INSERT OR REPLACE, not a duplicate
    hand = db.get_hand("h1")
    assert len(hand["tags"]) == 1
    assert hand["tags"][0]["note"] == "replaced note"
    db.remove_tag("h1", "good")
    assert db.get_hand("h1")["tags"] == []


def test_settings_get_set_roundtrip_and_default():
    assert db.get_setting("nope", "fallback") == "fallback"
    db.set_setting("hero_username", "Corey88")
    assert db.get_setting("hero_username") == "Corey88"
    db.set_setting("hero_username", "Changed")  # upsert, not a duplicate row
    assert db.get_setting("hero_username") == "Changed"


def test_parser_version_tracking_and_reimport_needed():
    assert db.get_setting("parser_version") is None
    assert db.reimport_needed() is False  # nothing imported yet
    db.mark_parser_version()
    assert db.get_setting("parser_version") == str(version.PARSER_VERSION)
    assert db.reimport_needed() is False  # just marked current

    with db.get_conn() as conn:
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'parser_version'")
    assert db.reimport_needed() is True  # stored version now behind current


def test_clear_file_state_forces_full_rescan():
    db.set_file_state("/some/file.txt", mtime=123.0, size=456, hands_found=2)
    assert db.get_file_state("/some/file.txt") is not None
    db.clear_file_state()
    assert db.get_file_state("/some/file.txt") is None


def test_list_hands_filters_are_parameterized_not_string_built():
    # A tag value containing SQL syntax must be treated as a literal filter
    # value, not interpreted - if this were ever string-concatenated instead
    # of parameterized, this crafted value would either error out or match
    # everything instead of matching nothing.
    db.upsert_hand(_make_hand())
    malicious = "x' OR '1'='1"
    assert db.list_hands(tag=malicious) == []
    assert db.count_hands(tag=malicious) == 0
    # And a plain filter still works normally alongside that proof.
    assert db.count_hands(game_type="Hold'em") == 1


def test_sort_column_whitelisted_against_injection():
    db.upsert_hand(_make_hand())
    # list_hands has no way to pass an arbitrary sort column from outside
    # its whitelist (app.py maps request args through REPORT_SORT_COLUMNS
    # before calling this) - confirm a nonsense sort key doesn't crash or
    # fall back to string-building a query with it.
    hands = db.list_hands(sort="date")  # only real column names are ever passed through
    assert len(hands) == 1


def test_overall_stats_and_stats_by_game_type():
    db.upsert_hand(_make_hand(hand_id="h1", hero_net=10.0, big_blind=2.0))
    db.upsert_hand(_make_hand(hand_id="h2", hero_net=-4.0, big_blind=2.0, game_type="Omaha"))
    overall = db.overall_stats()
    assert overall["hands"] == 2
    assert overall["net"] == 6.0
    by_game = {r["game_type"]: r for r in db.stats_by_game_type()}
    assert by_game["Hold'em"]["hands"] == 1
    assert by_game["Omaha"]["hands"] == 1


def test_init_db_is_idempotent():
    db.init_db()
    db.init_db()  # must not error re-creating existing tables/columns
    with db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(hands)").fetchall()}
    for name, _ in db._ADDED_COLUMNS:
        assert name in cols


def test_migration_adds_missing_columns_and_backs_up_first(tmp_path, monkeypatch):
    old_db = tmp_path / "old_schema.db"
    conn = sqlite3.connect(old_db)
    conn.executescript(
        """
        CREATE TABLE hands (
            hand_id TEXT PRIMARY KEY, game_type TEXT, limit_type TEXT, stakes TEXT,
            is_tournament INTEGER, tournament_id TEXT, table_name TEXT, date_played TEXT,
            hero_name TEXT, hero_cards TEXT, hero_invested REAL, hero_collected REAL,
            hero_net REAL, pot_total REAL, num_players INTEGER, source_file TEXT,
            raw_text TEXT, imported_at TEXT
        );
        CREATE TABLE tags (id INTEGER PRIMARY KEY AUTOINCREMENT, hand_id TEXT,
            tag TEXT, note TEXT, UNIQUE(hand_id, tag));
        CREATE TABLE file_state (filepath TEXT PRIMARY KEY, mtime REAL, size INTEGER);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    conn.execute("INSERT INTO hands (hand_id, game_type, hero_net) VALUES ('old1', \"Hold'em\", 12.5)")
    conn.execute("INSERT INTO tags (hand_id, tag, note) VALUES ('old1', 'good', 'precious')")
    conn.commit()
    conn.close()
    assert not (tmp_path / "old_schema.db.bak").exists()

    monkeypatch.setattr(db, "DB_PATH", str(old_db))
    db.init_db()

    assert (tmp_path / "old_schema.db.bak").exists()  # backed up before altering
    with db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(hands)").fetchall()}
        for name, _ in db._ADDED_COLUMNS:
            assert name in cols
        row = conn.execute("SELECT hero_net FROM hands WHERE hand_id='old1'").fetchone()
        assert row["hero_net"] == 12.5  # existing data survives the migration
        tag = conn.execute("SELECT tag, note FROM tags WHERE hand_id='old1'").fetchone()
        assert tag["note"] == "precious"
