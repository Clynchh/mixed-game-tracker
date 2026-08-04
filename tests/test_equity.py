"""equity.py: hand evaluation and Monte Carlo equity.

The All-in EV feature had a real preflop-lock leak bug earlier in this
project (the board-state helper had no case for "the lock happened
preflop", so it leaked the real future board into the simulation as if it
were already known). These tests cover the hand evaluator directly (fast,
deterministic) plus one Monte Carlo sanity check for a known-symmetric spot.
"""
import equity as eq


def cards(s):
    return eq.parse_cards(s)


def test_parse_and_format_card_roundtrip():
    assert eq.parse_card("Kc") == (13, "c")
    assert eq.parse_card("Th") == (10, "h")
    assert eq.card_str((13, "c")) == "Kc"
    assert eq.parse_card("") is None
    assert eq.parse_card("Zx") is None


def test_hand_ranking_order_high():
    straight_flush = eq.best_high(cards("Ah Kh Qh Jh Th"))
    quads = eq.best_high(cards("9s 9h 9d 9c 2h"))
    full_house = eq.best_high(cards("8s 8h 8d 2c 2h"))
    flush = eq.best_high(cards("Ah Kh 9h 5h 2h"))
    straight = eq.best_high(cards("9s 8h 7d 6c 5h"))
    trips = eq.best_high(cards("5s 5h 5d Kc 2h"))
    two_pair = eq.best_high(cards("Js Jh 4d 4c 2h"))
    one_pair = eq.best_high(cards("Qs Qh 9d 5c 2h"))
    high_card = eq.best_high(cards("Ks Jh 8d 5c 2h"))

    ranked = [straight_flush, quads, full_house, flush, straight, trips, two_pair, one_pair, high_card]
    assert ranked == sorted(ranked, reverse=True), "hand categories are out of order"


def test_wheel_is_a_straight_not_ace_high():
    wheel = eq.best_high(cards("Ah 2h 3d 4c 5s"))
    assert wheel[0] == 4  # straight category
    assert wheel[1] == 5  # 5-high, not ace-high


def test_best_high_picks_best_5_of_7():
    # Trip nines available; a random 6th/7th card shouldn't out-rank it.
    key = eq.best_high(cards("9s 9h 9d Kc 2h 3d 4c"))
    assert key[0] == 3  # trips
    assert key[1] == 9


def test_omaha_high_requires_exactly_2_hole_3_board():
    # Hole has quad-adjacent cards, but Omaha can't use 4 hole cards - only
    # a pair from hole + trips from board should be reachable here.
    hole = cards("Ah Ad Kc Kd")
    board = cards("As 2c 3d 4h 5s")
    key, combo = eq.best_omaha_high_cards(hole, board)
    assert len(combo) == 5
    hole_used = [c for c in combo if c in hole]
    board_used = [c for c in combo if c in board]
    assert len(hole_used) == 2
    assert len(board_used) == 3


def test_ace_to_five_low_wheel_is_the_nuts():
    nut_low = eq.best_low(cards("Ah 2h 3d 4c 5s"))
    worse_low = eq.best_low(cards("2h 3d 4c 5s 7d"))
    assert nut_low < worse_low  # smaller key = better low


def test_low_qualifier_8_or_better_rejects_nothing_under_8_and_pairs():
    no_qualifying_low = eq.best_low(cards("9h Th Jd Qc Ks"), qualify_8=True)
    assert no_qualifying_low is None
    qualifying = eq.best_low(cards("Ah 2h 3d 4c 8s"), qualify_8=True)
    assert qualifying is not None


def test_score_showdown_splits_pot_on_exact_tie():
    hands = {"A": cards("Ah Kh Qh Jh Th"), "B": cards("As Ks Qs Js Ts")}
    shares = eq.score_showdown("Hold'em", hands, [])
    assert shares["A"] == shares["B"] == 0.5


def test_score_showdown_hi_lo_splits_the_pot_when_both_sides_qualify():
    # A: trip nines, no qualifying low (only 3 cards <= 8).
    # B: a pair of nines (worse high than A's trips) but 5 distinct cards
    # <=8 for a qualifying low. Hi/lo should give each exactly half.
    hands = {
        "A": cards("9h 9d 9s Kc 2c 5d 7h"),
        "B": cards("Ah 2h 3d 4c 8s 9d 9s"),
    }
    shares = eq.score_showdown("Stud Hi/Lo", hands, [])
    assert shares == {"A": 0.5, "B": 0.5}


def test_score_showdown_hi_lo_no_qualifying_low_hi_scoops():
    # Nobody has 5 cards <=8 for a low, so the high hand should take 100%,
    # not just half of a nonexistent low side.
    hands = {
        "A": cards("9h 9d 9s Kc 2d 5c 7h"),
        "B": cards("2h 2d 3s Kc Qd Jc Th"),
    }
    shares = eq.score_showdown("Stud Hi/Lo", hands, [])
    assert shares["A"] == 1.0
    assert shares["B"] == 0.0


def test_simulate_equity_symmetric_coinflip_is_roughly_even():
    # Two disjoint 5-card-vs-5-card-equivalent hands with no shared
    # information and a full random board are equity-symmetric by
    # construction (same category, mirrored ranks) - low trial count kept
    # for test speed, so allow a wide tolerance rather than a tight one.
    hands = {
        "A": cards("Ks Qs"),
        "B": cards("Kh Qh"),
    }
    result = eq.simulate_equity("Hold'em", hands, [], board_to_deal=5, cards_per_player=0, trials=400)
    assert result is not None
    assert abs(result["A"] - result["B"]) < 0.15
    assert round(result["A"] + result["B"], 2) == 1.0


def test_build_side_pots_short_stack_only_wins_own_layer():
    investments = {"short": 10, "mid": 30, "big": 30}
    eligible_to_win = {"short", "mid", "big"}
    layers = eq.build_side_pots(investments, eligible_to_win)
    # Total across all layers must equal total chips in - nothing invented
    # or lost when the pot gets carved up.
    assert sum(amount for amount, _ in layers) == sum(investments.values())
    # The short stack can only be eligible for the first (smallest) layer -
    # everything above their all-in amount is a side pot they have no claim on.
    short_layers = [names for _, names in layers if "short" in names]
    assert len(short_layers) == 1
