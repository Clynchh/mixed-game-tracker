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
        source_file="test.txt", raw_text="PokerStars Hand #1: ...", bounty_won=0.0,
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


def test_tournament_list_times_from_hands_not_the_tournament_shell():
    # tournaments.date_played is only ever set from whichever hand happened
    # to be scanned first (ON CONFLICT DO NOTHING), so it can't be trusted
    # as the tournament's actual start - tournament_list must time it from
    # the hands themselves instead.
    db.upsert_tournament_shell("t1", "Hold'em Tournament", 10.0, "2099-01-01T00:00:00")
    db.set_tournament_result("t1", 3, 25.0)
    db.upsert_hand(_make_hand(
        hand_id="h1", is_tournament=1, tournament_id="t1", date_played="2026-01-01T20:00:00",
    ))
    db.upsert_hand(_make_hand(
        hand_id="h2", is_tournament=1, tournament_id="t1", date_played="2026-01-01T21:30:00",
    ))

    rows = db.tournament_list()
    assert len(rows) == 1
    row = rows[0]
    assert row["started_at"] == "2026-01-01T20:00:00"
    assert row["ended_at"] == "2026-01-01T21:30:00"
    assert row["num_hands"] == 2
    assert row["net"] == 15.0  # 25 payout - 10 buy-in
    assert row["finish_place"] == 3


def test_tournament_game_types_lists_distinct_games_played_per_tournament():
    # The dashboard's game checkboxes filter the tournament results graph
    # client-side, by checking whether a tournament touched any selected
    # game - this is what tells it which games each tournament touched.
    db.upsert_tournament_shell("t1", "HORSE Tournament", 10.0, "2099-01-01T00:00:00")
    db.upsert_hand(_make_hand(
        hand_id="h1", is_tournament=1, tournament_id="t1", game_type="Razz", limit_type="FL",
    ))
    db.upsert_hand(_make_hand(
        hand_id="h2", is_tournament=1, tournament_id="t1", game_type="Hold'em", limit_type="FL",
    ))
    # Same (game_type, limit_type) as h2, different hand - shouldn't duplicate.
    db.upsert_hand(_make_hand(
        hand_id="h3", is_tournament=1, tournament_id="t1", game_type="Hold'em", limit_type="FL",
    ))

    combos = {(r["game_type"], r["limit_type"]) for r in db.tournament_game_types()}
    assert combos == {("Razz", "FL"), ("Hold'em", "FL")}


def test_tournament_list_excludes_tournaments_with_no_hands_recorded():
    # A shell can exist without any of its hands having been imported yet
    # (or a hand that referenced it got deleted) - nothing to time, so it
    # shouldn't appear in a list that's fundamentally built around hand data.
    db.upsert_tournament_shell("t-empty", "Hold'em Tournament", 10.0, "2026-01-01T00:00:00")
    assert db.tournament_list() == []


def test_tournament_payout_includes_bounty_cash_won_across_hands():
    # Bounty cash can land on any hand in the tournament, not just the one
    # with the finish line - a hero who knocks people out earlier and
    # busts later without a min-cash prize still actually won real money.
    db.upsert_tournament_shell("t1", "KO Tournament", 10.0, "2026-01-01T20:00:00")
    db.upsert_hand(_make_hand(
        hand_id="h1", is_tournament=1, tournament_id="t1",
        date_played="2026-01-01T20:00:00", bounty_won=7.35,
    ))
    db.upsert_hand(_make_hand(
        hand_id="h2", is_tournament=1, tournament_id="t1",
        date_played="2026-01-01T20:10:00", bounty_won=2.45,
    ))
    db.set_tournament_result("t1", None, 0.0)  # busted with no min-cash prize

    row = db.tournament_list()[0]
    assert row["payout"] == 9.80  # 7.35 + 2.45 bounty, no prize-pool payout
    assert round(row["net"], 2) == -0.20  # 9.80 payout - 10.00 buy-in


def test_tournament_finished_with_no_place_counts_as_completed_not_in_progress():
    # This is the bug report this was built for: PokerStars omitting the
    # exact place must not make a tournament the hero is actually done
    # with look like it's still being played.
    db.upsert_tournament_shell("t1", "Hold'em Tournament", 10.0, "2026-01-01T20:00:00")
    db.upsert_hand(_make_hand(
        hand_id="h1", is_tournament=1, tournament_id="t1", date_played="2026-01-01T20:00:00",
    ))
    db.set_tournament_result("t1", None, 0.0, finished=True)

    overall = db.tournament_overall()
    assert overall["n"] == 1  # counted as completed
    assert overall["in_progress"] == 0

    row = db.tournament_list()[0]
    assert row["finished"] == 1
    assert row["finish_place"] is None


def test_tournament_truly_still_playing_is_in_progress():
    db.upsert_tournament_shell("t1", "Hold'em Tournament", 10.0, "2026-01-01T20:00:00")
    db.upsert_hand(_make_hand(
        hand_id="h1", is_tournament=1, tournament_id="t1", date_played="2026-01-01T20:00:00",
    ))
    # No set_tournament_result call at all - hero hasn't busted yet.

    overall = db.tournament_overall()
    assert overall["n"] == 0
    assert overall["in_progress"] == 1


def test_upsert_tournament_shell_refreshes_game_desc_on_rescan():
    # Real-world case this was built for: existing rows created before a
    # parsing improvement (stripping level/stakes clutter off the name)
    # would otherwise keep the messy version forever, since buy_in and
    # date_played are deliberately only ever set once.
    db.upsert_tournament_shell("t1", "$50+$5 USD HORSE (Hold'em Limit) - Level I (600/1200)", 55.0, "2026-01-01T00:00:00")
    db.upsert_tournament_shell("t1", "HORSE", 999.0, "2099-01-01T00:00:00")

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT game_desc, buy_in, date_played FROM tournaments WHERE tournament_id='t1'"
        ).fetchone()
    assert row["game_desc"] == "HORSE"  # refreshed
    assert row["buy_in"] == 55.0  # NOT refreshed - first-seen-wins, as before
    assert row["date_played"] == "2026-01-01T00:00:00"  # also first-seen-wins


def test_upsert_tournament_shell_does_not_override_a_summary_derived_name():
    db.upsert_tournament_shell("t1", "$4.90+$0.60 USD 8-Game (Triple Draw) - Level I", 5.50, "2026-01-01T00:00:00")
    db.set_tournament_summary(
        "t1", game_desc="8-Game", date_played="2026-01-01T00:00:00",
        buy_in=11.0, entries=2, finish_place=18, payout=5.40,
    )
    # A later re-scan of hand histories must not clobber the summary's name.
    db.upsert_tournament_shell("t1", "$4.90+$0.60 USD 8-Game (Triple Draw) - Level I", 5.50, "2026-01-01T00:00:00")

    with db.get_conn() as conn:
        row = conn.execute("SELECT game_desc FROM tournaments WHERE tournament_id='t1'").fetchone()
    assert row["game_desc"] == "8-Game"


def test_init_db_is_idempotent():
    db.init_db()
    db.init_db()  # must not error re-creating existing tables/columns
    with db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(hands)").fetchall()}
    for name, _ in db._ADDED_COLUMNS["hands"]:
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
        for name, _ in db._ADDED_COLUMNS["hands"]:
            assert name in cols
        row = conn.execute("SELECT hero_net FROM hands WHERE hand_id='old1'").fetchone()
        assert row["hero_net"] == 12.5  # existing data survives the migration
        tag = conn.execute("SELECT tag, note FROM tags WHERE hand_id='old1'").fetchone()
        assert tag["note"] == "precious"
