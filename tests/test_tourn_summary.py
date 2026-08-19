"""tourn_summary.py: parsing PokerStars Tournament Summary files.

Formats below are drawn directly from real summary files this feature was
built against - a HORSE tournament with a single entry (should match hand
history parsing exactly), and two genuine re-entries (the case hand history
alone can't detect at all).
"""
import db
import tourn_summary

HERO = "clynchh"

SINGLE_ENTRY = """PokerStars Tournament #4020145831, HORSE
Buy-In: $2.94/$0.36 USD
77 players
Total Prize Pool: $226.38 USD
Tournament started 2026/08/05 21:03:00 WET [2026/08/05 16:03:00 ET]

  8: thefisherman67 (Germany), still playing
  9: clynchh (United Kingdom), $8.23 (3.635%)
  10: lucasi1 (Austria),

You finished in 9th place (eliminated at hand #261658020303).
"""

RE_ENTRY = """PokerStars Tournament #4020145723, 8-Game
Buy-In: $4.90/$0.60 USD
176 players
Total Prize Pool: $422.40 USD
Tournament started 2026/08/05 18:01:00 WET [2026/08/05 13:01:00 ET]

  18: clynchh [2] (United Kingdom), $5.40 (1.278%)
  129: clynchh (United Kingdom),

You finished the tournament (eliminated at hand #261647765463).
"""

SATELLITE_WON_TICKET = """PokerStars Tournament #4021002252, No Limit Single Draw 2-7 Lowball
Super Satellite
Buy-In: $4.90/$0.60 USD
54 players
Total Prize Pool: $264.60 USD
Target Tournament #4019178096 Buy-In: $109.00 USD
Tournament started 2026/08/05 15:40:00 WET [2026/08/05 10:40:00 ET]

  1: clynchh (United Kingdom), still playing
"""

NOT_IN_IT = """PokerStars Tournament #999, HORSE
Buy-In: $2.94/$0.36 USD
10 players
Total Prize Pool: $29.40 USD
Tournament started 2026/08/05 21:03:00 WET [2026/08/05 16:03:00 ET]

  1: SomeoneElse (Germany), $29.40 (100%)
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_single_entry_matches_hand_history_style_result(tmp_path):
    f = _write(tmp_path, "ts1.txt", SINGLE_ENTRY)
    result = tourn_summary.parse_summary_file(f, HERO)
    assert result["tournament_id"] == "4020145831"
    assert result["finish_place"] == 9
    assert result["payout"] == 8.23
    assert result["entries"] == 1
    assert result["buy_in"] == 3.30  # 2.94 + 0.36


def test_re_entry_sums_payout_and_doubles_buy_in(tmp_path):
    # This is the bug report this feature exists for: hand-history parsing
    # only ever saw one buy-in for this tournament and net looked +$3.03;
    # the summary reveals two entries, so the real cost is double.
    f = _write(tmp_path, "ts2.txt", RE_ENTRY)
    result = tourn_summary.parse_summary_file(f, HERO)
    assert result["entries"] == 2
    assert result["buy_in"] == 11.00  # (4.90+0.60) x 2 entries
    assert result["finish_place"] == 18  # best of the two (18th and 129th)
    assert result["payout"] == 5.40  # only the entry that cashed


def test_satellite_won_ticket_counts_target_buyin_as_payout(tmp_path):
    # "still playing" on the hero's own line only happens when a satellite
    # ends by field size rather than elimination - they won a seat, not
    # cash, and the seat's value is the target tournament's buy-in.
    f = _write(tmp_path, "ts3.txt", SATELLITE_WON_TICKET)
    result = tourn_summary.parse_summary_file(f, HERO)
    assert result["finish_place"] == 1
    assert result["payout"] == 109.00


def test_hero_not_in_file_returns_none(tmp_path):
    f = _write(tmp_path, "ts4.txt", NOT_IN_IT)
    assert tourn_summary.parse_summary_file(f, HERO) is None


def test_not_a_summary_file_returns_none(tmp_path):
    f = _write(tmp_path, "junk.txt", "just some random text\n")
    assert tourn_summary.parse_summary_file(f, HERO) is None


def test_no_hero_username_returns_none(tmp_path):
    f = _write(tmp_path, "ts1.txt", SINGLE_ENTRY)
    assert tourn_summary.parse_summary_file(f, "") is None
    assert tourn_summary.parse_summary_file(f, None) is None


def test_game_desc_and_date_captured_for_a_fresh_shell(tmp_path):
    f = _write(tmp_path, "ts1.txt", SINGLE_ENTRY)
    result = tourn_summary.parse_summary_file(f, HERO)
    assert result["game_desc"] == "HORSE"
    assert result["date_played"] == "2026-08-05T21:03:00"


# --- scan_folder + db integration -------------------------------------------------

def _tournament_row(tournament_id):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM tournaments WHERE tournament_id=?", (tournament_id,)
        ).fetchone()
        return dict(row) if row else None


def test_scan_folder_applies_results_and_marks_summary_source(tmp_path):
    _write(tmp_path, "ts1.txt", SINGLE_ENTRY)
    _write(tmp_path, "ts2.txt", RE_ENTRY)
    n = tourn_summary.scan_folder(str(tmp_path), hero_username=HERO)
    assert n == 2

    row1 = _tournament_row("4020145831")
    row2 = _tournament_row("4020145723")
    assert row1["finish_place"] == 9
    assert row1["summary_source"] == 1
    assert row2["buy_in"] == 11.00
    assert row2["summary_source"] == 1


def test_scan_folder_missing_or_unconfigured_folder_is_a_noop(tmp_path):
    assert tourn_summary.scan_folder(str(tmp_path / "nope"), hero_username=HERO) == 0
    assert tourn_summary.scan_folder("", hero_username=HERO) == 0
    assert tourn_summary.scan_folder(str(tmp_path), hero_username=None) == 0


def test_summary_result_overrides_a_hand_history_derived_one(tmp_path):
    # Hand-history parsing runs first in the real app (scan_folder, then
    # tourn_summary.scan_folder) and might have already guessed wrong -
    # summary import must win regardless of write order.
    db.upsert_tournament_shell("4020145723", "8-Game", 5.50, "2026-08-05T18:01:00")
    db.set_tournament_result("4020145723", 129, 0.0)  # hand-history's guess: only saw entry #2

    _write(tmp_path, "ts2.txt", RE_ENTRY)
    tourn_summary.scan_folder(str(tmp_path), hero_username=HERO)

    row = _tournament_row("4020145723")
    assert row["finish_place"] == 18  # summary's answer, not hand-history's
    assert row["buy_in"] == 11.00

    # And hand-history parsing must not be able to overwrite it back after -
    # this is what set_tournament_result's summary_source guard is for.
    db.set_tournament_result("4020145723", 129, 0.0)
    row = _tournament_row("4020145723")
    assert row["finish_place"] == 18
