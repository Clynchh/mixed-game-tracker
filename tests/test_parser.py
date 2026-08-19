"""parser.py: hand-history parsing correctness.

Money-conservation and hero-detection bugs are the two classes of parser
bug that have actually bitten this app before (see git history: hero
misidentification in stud, big-blind-vs-big-bet confusion) - both are
checked here across every hand in the sample file, not spot-checked on one.
"""
import parser as hh_parser

from conftest import SAMPLE_HANDS_PATH

HERO = "Corey88"


def _parse_sample():
    return hh_parser.parse_file(SAMPLE_HANDS_PATH, hero_username=HERO)


def test_sample_file_parses_every_hand():
    hands = _parse_sample()
    assert len(hands) == 7  # one of each game in the 8-Game rotation sample


def test_every_hand_has_required_fields():
    for h in _parse_sample():
        assert h["hand_id"]
        assert h["game_type"] and h["game_type"] != "Unknown"
        assert h["date_played"]
        assert h["num_players"] >= 2


def test_hero_identified_by_exact_username_in_every_hand():
    # Stud/razz hands show every player's up-cards, so without an exact
    # username match the parser could silently attribute an opponent's
    # result to the hero - this was a real bug earlier in this project.
    for h in _parse_sample():
        assert h["hero_name"] == HERO, f"hand {h['hand_id']} ({h['game_type']}) misidentified hero"


def test_no_hero_match_when_username_is_wrong():
    hands = hh_parser.parse_file(SAMPLE_HANDS_PATH, hero_username="SomeoneElse")
    for h in hands:
        assert h["hero_name"] == ""
        assert h["hero_invested"] == 0
        assert h["hero_collected"] == 0
        assert h["hero_net"] == 0


def test_money_conservation_hero_net_matches_invested_and_collected():
    for h in _parse_sample():
        assert round(h["hero_collected"] - h["hero_invested"], 4) == h["hero_net"]


def test_big_blind_is_the_actually_posted_amount_not_a_bet_size():
    # Regression: fixed-limit hold'em/omaha bet in small-bet/big-bet units,
    # which is NOT the same number as the big blind - the parser must read
    # the actually-posted "posts the big blind" line, not infer it from the
    # level notation, wherever a blind is actually posted.
    hands = {h["hand_id"]: h for h in _parse_sample()}
    holdem = hands["223344551"]
    assert holdem["big_blind"] == 20  # "Corey88: posts big blind 20"


def test_game_types_cover_the_rotation():
    game_types = {h["game_type"] for h in _parse_sample()}
    assert game_types == {
        "Hold'em", "Omaha Hi/Lo", "Razz", "Stud", "Stud Hi/Lo",
        "2-7 Triple Draw", "2-7 Single Draw",
    }


def test_2_7_draw_games_classified_regardless_of_word_order():
    # Real PokerStars text is "Triple Draw 2-7 Lowball" / "Single Draw 2-7
    # Lowball" - reversed from what an earlier, never-verified-against-a-
    # real-file assumption expected ("2-7 Triple/Single Draw"), so every
    # draw hand from an actual PokerStars client silently classified as
    # "Unknown" and the replayer correctly refused to render any of them.
    real_wording = [
        ("Triple Draw 2-7 Lowball Limit", "2-7 Triple Draw"),
        ("Single Draw 2-7 Lowball No Limit", "2-7 Single Draw"),
        # Either order is accepted, in case some client/format uses the other.
        ("2-7 Triple Draw Limit", "2-7 Triple Draw"),
        ("2-7 Single Draw No Limit", "2-7 Single Draw"),
    ]
    for desc, expected in real_wording:
        assert hh_parser._classify(hh_parser.GAME_TYPE_MAP, desc) == expected


def test_tournament_hands_flagged_and_cash_hand_is_not():
    hands = {h["hand_id"]: h for h in _parse_sample()}
    assert hands["223344551"]["is_tournament"] == 1
    assert hands["223344551"]["tournament_id"] == "1234567"
    assert hands["223344557"]["is_tournament"] == 0  # the standalone cash hand
    assert hands["223344557"]["tournament_id"] is None


def test_tournament_finish_payout_survives_trailing_sentence_period():
    # PokerStars writes this as a full sentence - "...received $5.40." -
    # with the period landing right against the amount, no space. A money
    # regex of [\d,.]+ is greedy enough to swallow that period as part of
    # the number, and float("5.40.") blows up. Caught from a real hand
    # history file this app failed to import.
    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        holdem_hand = hh_parser.split_hands(f.read())[0]
    hand_with_finish = holdem_hand + "\nCorey88 finished the tournament in 5th place and received $5.40.\n"
    parsed = hh_parser.parse_hand(hand_with_finish, hero_username=HERO)
    assert parsed["tourney_finish_place"] == 5
    assert parsed["tourney_payout"] == 5.40


def test_finish_line_with_no_place_still_marks_tournament_finished():
    # Real PokerStars text, seen when several players bust in the same
    # all-in and the exact order between them isn't resolved in this hand:
    # just "X finished the tournament", no "in Nth place" at all. Before
    # this was handled, FINISH_RE simply never matched such a hand, so the
    # tournament looked like it was still being played indefinitely.
    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        holdem_hand = hh_parser.split_hands(f.read())[0]
    hand = holdem_hand + "\nCorey88 finished the tournament\n"
    parsed = hh_parser.parse_hand(hand, hero_username=HERO)
    assert parsed["tourney_finished"] is True
    assert parsed["tourney_finish_place"] is None
    assert parsed["tourney_payout"] == 0.0


def test_finish_line_ignores_other_players():
    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        holdem_hand = hh_parser.split_hands(f.read())[0]
    hand = holdem_hand + "\nVillain1 finished the tournament in 3rd place and received $50.00\n"
    parsed = hh_parser.parse_hand(hand, hero_username=HERO)
    assert parsed["tourney_finished"] is False
    assert parsed["tourney_finish_place"] is None


def test_bounty_wins_are_summed_and_scoped_to_hero():
    # Real PokerStars progressive-knockout text: "X wins $Y for eliminating
    # Z and their own bounty increases by $Y to $W" - Y is real cash, and
    # can appear on any hand in the tournament (a hero can even eliminate
    # more than one player in a single multi-way all-in), not just the
    # hand that ends the hero's own tournament.
    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        holdem_hand = hh_parser.split_hands(f.read())[0]
    hand = holdem_hand + (
        "\nCorey88 wins $7.35 for eliminating Villain1 and their own bounty increases by $7.35 to $12.25"
        "\nCorey88 wins $2.45 for eliminating Villain2 and their own bounty increases by $2.45 to $14.70"
        "\nVillain3 wins $3.00 for eliminating Villain4 and their own bounty increases by $3.00 to $9.00\n"
    )
    parsed = hh_parser.parse_hand(hand, hero_username=HERO)
    assert parsed["bounty_won"] == 9.80  # only Corey88's two wins, not Villain3's


def test_no_bounty_lines_is_zero_not_none():
    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        holdem_hand = hh_parser.split_hands(f.read())[0]
    parsed = hh_parser.parse_hand(holdem_hand, hero_username=HERO)
    assert parsed["bounty_won"] == 0.0


# --- tournament name cleanup -------------------------------------------------

def test_clean_tourney_name_strips_specific_game_and_level():
    # Real examples: the parenthesised part names whichever single game in
    # the rotation happens to be running at the level the hero busted at -
    # not the tournament's own identity, so a tournament-level list (like
    # the Tournaments tab) shouldn't show it next to the tournament's name.
    cases = [
        ("$2.40+$2.50+$0.60 USD 8-Game (Triple Draw 2-7 Lowball Limit) - Level XVII (500/1000)", "8-Game"),
        ("$9.80+$1.20 USD Hold'em No Limit - Level XIV (1500/3000)", "Hold'em No Limit"),
        ("$50+$5 USD HORSE (7 Card Stud Limit) - Level IX (300/600)", "HORSE"),
        ("$20.95+$1.05 USD Hold'em No Limit - Level VII (80/160)", "Hold'em No Limit"),
    ]
    for raw, expected in cases:
        assert hh_parser._clean_tourney_name(raw) == expected


def test_tourney_game_desc_is_cleaned_for_tournament_hands_only():
    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        text = f.read()
    hands = hh_parser.split_hands(text)
    tourney_hand = hh_parser.parse_hand(hands[0], hero_username=HERO)
    cash_hand = hh_parser.parse_hand(hands[-1], hero_username=HERO)  # the standalone cash hand

    assert tourney_hand["tourney_game_desc"] == "Hold'em No Limit"
    assert cash_hand["is_tournament"] == 0  # cleanup only applies to tournament hands


def test_unparseable_text_yields_no_hands(tmp_path):
    junk = tmp_path / "not_a_hand_history.txt"
    junk.write_text("this is just some random text, not a PokerStars hand\n")
    assert hh_parser.parse_file(str(junk), hero_username=HERO) == []


def test_parse_file_skips_unparseable_blocks_but_keeps_valid_ones(tmp_path):
    mixed = tmp_path / "mixed.txt"
    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        real_hand = hh_parser.split_hands(f.read())[0]
    mixed.write_text("garbage garbage garbage\n\n" + real_hand + "\n")
    hands = hh_parser.parse_file(str(mixed), hero_username=HERO)
    assert len(hands) == 1
