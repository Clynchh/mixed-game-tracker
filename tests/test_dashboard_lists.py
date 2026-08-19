"""The Tournaments-tab tournament list and Cash-tab session list."""
import app as flask_app_module
import db

flask_app = flask_app_module.app
HERO = "Corey88"


def client():
    return flask_app.test_client()


def configure():
    db.set_setting("hand_history_dir", "/anywhere")
    db.set_setting("hero_username", HERO)


def _make_cash_hand(hand_id, date_played, hero_net, game_type="Hold'em", big_blind=2.0):
    return dict(
        hand_id=hand_id, game_type=game_type, limit_type="NL", stakes="1/2",
        is_tournament=0, tournament_id=None, table_name="Table 1", date_played=date_played,
        hero_name="Hero", hero_cards="Ah Kd", hero_invested=0, hero_collected=0, hero_net=hero_net,
        pot_total=25.0, pot_type="Raised (SRP)", went_to_showdown=1, vpip=1, is_allin_ev=0,
        equity_pct=None, ev_net=None, big_blind=big_blind, num_players=6,
        source_file="test.txt", raw_text="PokerStars Hand ...", bounty_won=0.0,
    )


# --- _format_duration -------------------------------------------------

def test_format_duration_under_an_hour():
    assert flask_app_module._format_duration("2026-01-01T20:00:00", "2026-01-01T20:24:00") == "24m"


def test_format_duration_over_an_hour():
    assert flask_app_module._format_duration("2026-01-01T20:00:00", "2026-01-01T21:36:00") == "1h 36m"


def test_format_duration_missing_or_instant_is_a_dash():
    assert flask_app_module._format_duration(None, "2026-01-01T20:00:00") == "—"
    assert flask_app_module._format_duration("2026-01-01T20:00:00", "2026-01-01T20:00:00") == "—"


def test_duration_minutes_matches_the_formatted_string():
    # The dashboard's tournament-list total row sums these client-side then
    # formats the sum, rather than trying to add "1h 36m" + "24m" as text -
    # so the raw minutes and the formatted string must agree on the same pair.
    assert flask_app_module._duration_minutes("2026-01-01T20:00:00", "2026-01-01T21:36:00") == 96
    assert flask_app_module._duration_minutes(None, "2026-01-01T20:00:00") == 0


# --- _ordinal -----------------------------------------------------------

def test_ordinal_common_cases():
    assert flask_app_module._ordinal(1) == "1st"
    assert flask_app_module._ordinal(2) == "2nd"
    assert flask_app_module._ordinal(3) == "3rd"
    assert flask_app_module._ordinal(4) == "4th"
    assert flask_app_module._ordinal(11) == "11th"  # not "11st"
    assert flask_app_module._ordinal(22) == "22nd"
    assert flask_app_module._ordinal(101) == "101st"


# --- _group_into_sessions -------------------------------------------------

def test_group_into_sessions_splits_on_a_long_gap():
    hands = [
        _make_cash_hand("h1", "2026-01-01T20:00:00", 10.0),
        _make_cash_hand("h2", "2026-01-01T20:08:00", -5.0),
        _make_cash_hand("h3", "2026-01-02T10:00:00", 20.0),  # >1hr later - new session
    ]
    sessions = flask_app_module._group_into_sessions(hands, unit="raw")
    assert len(sessions) == 2
    # Most recent first.
    assert sessions[0]["started_at"] == "2026-01-02T10:00:00"
    assert sessions[0]["num_hands"] == 1
    assert sessions[0]["net"] == 20.0
    assert sessions[1]["started_at"] == "2026-01-01T20:00:00"
    assert sessions[1]["ended_at"] == "2026-01-01T20:08:00"
    assert sessions[1]["num_hands"] == 2
    assert sessions[1]["net"] == 5.0


def test_group_into_sessions_keeps_close_hands_together():
    hands = [
        _make_cash_hand("h1", "2026-01-01T20:00:00", 5.0),
        _make_cash_hand("h2", "2026-01-01T20:59:00", 5.0),  # 59 min - just inside the gap
    ]
    sessions = flask_app_module._group_into_sessions(hands, unit="raw")
    assert len(sessions) == 1
    assert sessions[0]["num_hands"] == 2


def test_group_into_sessions_tracks_distinct_game_types_in_order_seen():
    hands = [
        _make_cash_hand("h1", "2026-01-01T20:00:00", 1.0, game_type="Hold'em"),
        _make_cash_hand("h2", "2026-01-01T20:05:00", 1.0, game_type="Omaha"),
        _make_cash_hand("h3", "2026-01-01T20:10:00", 1.0, game_type="Hold'em"),
    ]
    sessions = flask_app_module._group_into_sessions(hands, unit="raw")
    assert sessions[0]["game_types"] == ["Hold'em", "Omaha"]


def test_group_into_sessions_bb_unit_divides_by_big_blind():
    hands = [_make_cash_hand("h1", "2026-01-01T20:00:00", 10.0, big_blind=2.0)]
    sessions = flask_app_module._group_into_sessions(hands, unit="bb")
    assert sessions[0]["net"] == 5.0


def test_group_into_sessions_empty_input():
    assert flask_app_module._group_into_sessions([], unit="raw") == []


# --- dashboard integration -------------------------------------------------

def test_dashboard_tournament_tab_shows_tournament_list():
    configure()
    db.upsert_hand(dict(
        hand_id="th1", game_type="Hold'em", limit_type="NL", stakes="100/200",
        is_tournament=1, tournament_id="t1", table_name="T1", date_played="2026-01-01T20:00:00",
        hero_name=HERO, hero_cards="Ah Kd", hero_invested=0, hero_collected=0, hero_net=0,
        pot_total=25.0, pot_type="Raised (SRP)", went_to_showdown=1, vpip=1, is_allin_ev=0,
        equity_pct=None, ev_net=None, big_blind=200, num_players=6,
        source_file="test.txt", raw_text="...", bounty_won=0.0,
    ))
    db.upsert_tournament_shell("t1", "Hold'em Tournament", 10.0, "2026-01-01T20:00:00")
    db.set_tournament_result("t1", 2, 30.0)

    r = client().get("/?mode=tournament")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Tournaments" in body
    assert "2nd" in body


def test_dashboard_tournament_list_payload_carries_games_for_client_side_filtering():
    # The game boxes filter the tournament list (and both graphs) entirely
    # in the browser - the row limit is a display cap, not a filter, so the
    # full list plus which games each tournament touched has to reach the
    # page as data even though only the 20 most recent are rendered by
    # default.
    configure()
    db.upsert_hand(dict(
        hand_id="th2", game_type="Hold'em", limit_type="NL", stakes="100/200",
        is_tournament=1, tournament_id="t2", table_name="T1", date_played="2026-01-01T20:00:00",
        hero_name=HERO, hero_cards="Ah Kd", hero_invested=0, hero_collected=0, hero_net=0,
        pot_total=25.0, pot_type="Raised (SRP)", went_to_showdown=1, vpip=1, is_allin_ev=0,
        equity_pct=None, ev_net=None, big_blind=200, num_players=6,
        source_file="test.txt", raw_text="...", bounty_won=0.0,
    ))
    db.upsert_tournament_shell("t2", "Hold'em Tournament", 10.0, "2026-01-01T20:00:00")
    db.set_tournament_result("t2", 5, 0.0)

    r = client().get("/?mode=tournament")
    body = r.get_data(as_text=True)
    assert "window.TOURNAMENT_LIST" in body
    assert '"games": ["NLHE"]' in body
    assert '"duration_minutes"' in body


def test_dashboard_cash_tab_shows_sessions_list():
    configure()
    for hid, date in [("c1", "2026-01-01T20:00:00"), ("c2", "2026-01-01T20:08:00")]:
        db.upsert_hand(_make_cash_hand(hid, date, 5.0))

    r = client().get("/?mode=cash")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Sessions" in body
    assert "1 sessions" in body
