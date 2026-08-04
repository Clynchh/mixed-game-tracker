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


def test_tournament_hands_flagged_and_cash_hand_is_not():
    hands = {h["hand_id"]: h for h in _parse_sample()}
    assert hands["223344551"]["is_tournament"] == 1
    assert hands["223344551"]["tournament_id"] == "1234567"
    assert hands["223344557"]["is_tournament"] == 0  # the standalone cash hand
    assert hands["223344557"]["tournament_id"] is None


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
