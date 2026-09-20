"""replay.py: chip-conservation regression check.

This is the standing check used throughout this project's development
(originally caught a real bug: *** HOLE CARDS ***/*** PRE-DRAW *** headers
wiping out blinds posted just above them). For every hand: total chips on
the table never change hand-to-hand (nothing leaves the system - the sample
hands all show Rake 0), no seat ever goes negative, and the replay's own
tally of the hero's result agrees with what the parser computed
independently from the same text.
"""
import parser as hh_parser
import replay

from conftest import SAMPLE_HANDS_PATH

HERO = "Corey88"


def _all_hands():
    return hh_parser.parse_file(SAMPLE_HANDS_PATH, hero_username=HERO)


def test_replay_builds_for_every_sample_hand():
    for h in _all_hands():
        data = replay.build_replay(h["raw_text"], h["game_type"], h["hero_name"], big_blind=h["big_blind"])
        assert data is not None, f"hand {h['hand_id']} ({h['game_type']}) failed to replay"
        assert len(data["frames"]) > 0


def test_chips_conserved_and_no_negative_stacks():
    for h in _all_hands():
        data = replay.build_replay(h["raw_text"], h["game_type"], h["hero_name"], big_blind=h["big_blind"])
        start_total = sum(s["start_stack"] for s in data["seats"])
        last_frame = data["frames"][-1]
        end_total = sum(s["stack"] for s in last_frame["seats"])
        assert round(start_total, 2) == round(end_total, 2), (
            f"hand {h['hand_id']}: {start_total} chips in, {end_total} out"
        )
        for frame in data["frames"]:
            for seat in frame["seats"]:
                assert seat["stack"] >= 0, f"hand {h['hand_id']}: stack went negative"


def test_hero_replayed_net_matches_parsed_net():
    for h in _all_hands():
        data = replay.build_replay(h["raw_text"], h["game_type"], h["hero_name"], big_blind=h["big_blind"])
        hero_idx = next(i for i, s in enumerate(data["seats"]) if s["is_hero"])
        start_stack = data["seats"][hero_idx]["start_stack"]
        end_stack = data["frames"][-1]["seats"][hero_idx]["stack"]
        replayed_net = round(end_stack - start_stack, 2)
        assert replayed_net == round(h["hero_net"], 2), (
            f"hand {h['hand_id']}: replay says {replayed_net}, parser says {h['hero_net']}"
        )


def test_hero_always_seated_first_and_bottom_centre():
    for h in _all_hands():
        data = replay.build_replay(h["raw_text"], h["game_type"], h["hero_name"], big_blind=h["big_blind"])
        assert data["seats"][0]["is_hero"] is True
        assert data["seats"][0]["x"] == 50  # bottom-centre of the ellipse


def test_no_hero_name_returns_none():
    h = _all_hands()[0]
    assert replay.build_replay(h["raw_text"], h["game_type"], "", big_blind=h["big_blind"]) is None


def test_single_draw_opponents_get_hidden_card_backs():
    # Single Draw hands only ever print "*** DEALING HANDS ***" - never
    # "*** PRE-DRAW ***"/"*** DRAW ***" like Triple Draw does - so opponents
    # (whose cards we never see) relied on a street-key case that didn't
    # recognise "dealing hands" and left their card count at 0 for the whole
    # hand: they'd fold or muck without a single card ever appearing in
    # front of them in the replayer.
    h = next(h for h in _all_hands() if h["hand_id"] == "223344557")
    data = replay.build_replay(h["raw_text"], h["game_type"], h["hero_name"], big_blind=h["big_blind"])
    # Villain2 folds pre-draw and never shows - their cards stay hidden for
    # the rest of the hand, so this frame is a clean check on total_cards
    # alone (no interference from a later "shows" line revealing them).
    villain_idx = next(i for i, s in enumerate(data["seats"]) if s["name"] == "Villain2")
    assert data["frames"][-1]["seats"][villain_idx]["hidden"] == 5


def test_draw_redeal_swaps_cards_instead_of_adding_to_them():
    # A draw redeal line - "Dealt to X [cards kept] [new cards]" - is
    # self-contained, not a running history the way stud's re-deal is.
    # Treating it like stud's (appending the new card onto whatever was
    # already known) left the discarded card in place instead of replacing
    # it, so hero ended up holding 6 cards after a 1-card draw instead of 5.
    h = next(h for h in _all_hands() if h["hand_id"] == "223344557")
    data = replay.build_replay(h["raw_text"], h["game_type"], h["hero_name"], big_blind=h["big_blind"])
    hero_idx = next(i for i, s in enumerate(data["seats"]) if s["is_hero"])
    final_cards = data["frames"][-1]["seats"][hero_idx]["cards"]
    assert len(final_cards) == 5
    assert "Kd" not in final_cards  # discarded - must not still be held
    assert "9c" in final_cards  # the replacement actually dealt


def test_bounty_tournament_seat_lines_still_parse():
    # A progressive-bounty tournament's seat line carries extra text after
    # the stack size: "Seat 1: per98 (26615 in chips, $4.90 bounty)" - the
    # closing paren lands after the bounty clause, not right after "in
    # chips". Caught from a real hand history: every seat line in every
    # bounty tournament failed to match at all, so build_replay always
    # returned None for them, regardless of game type.
    raw = (
        "PokerStars Hand #1: Tournament #1, $4.90+$4.90+$1.20 USD Hold'em No Limit"
        " - Level I (125/250) - 2026/01/01 12:00:00 ET\n"
        "Table '1 1' 3-max Seat #1 is the button\n"
        "Seat 1: Hero (25000 in chips, $4.90 bounty) \n"
        "Seat 2: Villain (25000 in chips, $4.90 bounty) \n"
        "Villain: posts small blind 125\n"
        "Hero: posts big blind 250\n"
        "*** HOLE CARDS ***\n"
        "Dealt to Hero [Ah Kd]\n"
        "Villain: folds\n"
        "Uncalled bet (125) returned to Hero\n"
        "Hero collected 250 from pot\n"
        "*** SUMMARY ***\n"
        "Total pot 250 | Rake 0\n"
    )
    data = replay.build_replay(raw, "Hold'em", "Hero", big_blind=250)
    assert data is not None
    assert len(data["seats"]) == 2
    assert data["seats"][0]["start_stack"] == 25000


def test_opponent_with_a_space_in_their_name_actually_acts():
    # PokerStars screen names can contain spaces ("James UK7"). The seat line
    # regex already allowed it, but every action regex matched only \S+, so
    # such an opponent sat frozen on the felt - no raise, call, draw or
    # showdown frame - and chips didn't balance because their bets never
    # left their stack.
    raw = (
        "PokerStars Hand #2: Triple Draw 2-7 Lowball Limit ($1/$2 USD)"
        " - 2026/09/03 18:15:01 ET\n"
        "Table 'T' 6-max Seat #1 is the button\n"
        "Seat 1: clynchh ($25 in chips)\n"
        "Seat 2: James UK7 ($25 in chips)\n"
        "clynchh: posts small blind $0.50\n"
        "James UK7: posts big blind $1\n"
        "*** DEALING HANDS ***\n"
        "Dealt to clynchh [Qs 7s 3c 9c 8d]\n"
        "clynchh: raises $1 to $2\n"
        "James UK7: raises $1 to $3\n"
        "clynchh: calls $1\n"
        "*** FIRST DRAW ***\n"
        "James UK7: discards 1 card\n"
        "clynchh: discards 2 cards [Qs 9c]\n"
        "Dealt to clynchh [7s 3c 8d] [2h 5h]\n"
        "James UK7: bets $1\n"
        "clynchh: folds\n"
        "Uncalled bet ($1) returned to James UK7\n"
        "James UK7 collected $6 from pot\n"
        "*** SUMMARY ***\n"
        "Total pot $6 | Rake $0\n"
    )
    data = replay.build_replay(raw, "2-7 Triple Draw", "clynchh", big_blind=1.0)
    assert data is not None
    labels = [f["label"] for f in data["frames"]]
    assert "James UK7 raises $1 to $3" in labels
    assert "James UK7 discards 1" in labels
    assert any(l.startswith("James UK7 wins") for l in labels)
    # chips in == chips out (Rake 0)
    start_total = sum(s["start_stack"] for s in data["seats"])
    end_total = sum(s["stack"] for s in data["frames"][-1]["seats"])
    assert round(start_total, 2) == round(end_total, 2)
