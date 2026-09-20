"""Aggregate player stats: position, "saw a flop", and the report sums.

The hand texts here are trimmed copies of real PokerStars output, not the
tidy sample file. Every one of them is a case that actually broke position
detection while it was being written: a player sitting out between the
button and the blinds, a button parked on an empty seat, heads-up (where
the button posts the small blind), stud (no button at all), and Single Draw
(no header between its two betting rounds).
"""
import db
import parser as hh_parser


def _parse(text, hero="clynchh"):
    return hh_parser.parse_hand(text, hero_username=hero)


def _position(text, name):
    """Position for any seat at the table, not just hero's.

    Going through parse_hand for this wouldn't work: in a flop game only
    hero gets a "Dealt to" line, so parsing as anyone else finds no hero at
    all. The position itself is seat geometry and is worth checking for
    every seat, which is what makes the blind labels in the hand's own
    SUMMARY usable as an independent answer to compare against."""
    return hh_parser._hero_position(text, name)


HOLDEM_6MAX = """PokerStars Hand #1: Tournament #99, $9.80+$1.20 USD 8-Game (Hold'em Limit) - Level XVIII (300/600) - 2026/08/31 21:11:06 WET
Table '99 6' 6-max Seat #1 is the button
Seat 1: Grampa55 (954 in chips) 
Seat 2: James UK7 (5000 in chips) 
Seat 3: SEE_AcCEpt (20613 in chips) 
Seat 4: sotelo0720 (9493 in chips) 
Seat 5: clynchh (5000 in chips) 
Seat 6: DoktorOden (5034 in chips) 
James UK7: posts small blind 150
SEE_AcCEpt: posts big blind 300
*** HOLE CARDS ***
Dealt to clynchh [5c Qc]
sotelo0720: folds 
clynchh: raises 300 to 600
DoktorOden: folds 
Grampa55: folds 
James UK7: folds 
SEE_AcCEpt: calls 300
*** FLOP *** [Jd Jh 5s]
SEE_AcCEpt: checks 
clynchh: bets 300
SEE_AcCEpt: calls 300
*** TURN *** [Jd Jh 5s] [6h]
SEE_AcCEpt: checks 
clynchh: checks 
*** RIVER *** [Jd Jh 5s 6h] [Ah]
SEE_AcCEpt: bets 600
clynchh: calls 600
*** SHOW DOWN ***
SEE_AcCEpt: shows [Ad Kc] (two pair)
clynchh: mucks hand 
SEE_AcCEpt collected 3000 from pot
*** SUMMARY ***
Total pot 3000 | Rake 0 
Seat 1: Grampa55 (button) folded before Flop (didn't bet)
Seat 2: James UK7 (small blind) folded before Flop
Seat 3: SEE_AcCEpt (big blind) collected (3000)
Seat 4: sotelo0720 folded before Flop (didn't bet)
Seat 5: clynchh showed [5c Qc] and lost
Seat 6: DoktorOden folded before Flop (didn't bet)
"""


def test_position_counts_only_players_actually_dealt_in():
    # Six-handed, hero is HJ. Take out a seat between hero and the button
    # and hero becomes CO, because the late names are filled backwards from
    # the button - so a seat wrongly left in the count shifts hero earlier
    # than they really were.
    assert _parse(HOLDEM_6MAX)["hero_position"] == "HJ"
    text = HOLDEM_6MAX.replace(
        "Seat 6: DoktorOden (5034 in chips) ",
        "Seat 6: DoktorOden (5034 in chips) is sitting out",
    ).replace("DoktorOden: folds \n", "")
    assert len(hh_parser._dealt_in_seats(text)) == 5
    assert _parse(text)["hero_position"] == "CO"


def test_position_matches_the_blinds_the_hand_itself_names():
    # The SUMMARY block labels these three outright, so they're the one part
    # of the layout that doesn't have to be inferred.
    assert _position(HOLDEM_6MAX, "James UK7") == "SB"
    assert _position(HOLDEM_6MAX, "SEE_AcCEpt") == "BB"
    assert _position(HOLDEM_6MAX, "Grampa55") == "BTN"


def test_six_handed_starts_at_lj_with_no_utg():
    # The four seats before the blinds are always LJ/HJ/CO/BTN. Six-handed
    # there's no room for anything earlier, so the first seat to act is LJ -
    # UTG names only appear once a table is deep enough to need them.
    assert _position(HOLDEM_6MAX, "sotelo0720") == "LJ"
    assert _position(HOLDEM_6MAX, "clynchh") == "HJ"
    assert _position(HOLDEM_6MAX, "DoktorOden") == "CO"


def test_utg_names_appear_only_on_tables_deep_enough_to_need_them():
    # Seats between the big blind and the button, by table size. LJ/HJ/CO
    # are filled backwards from the button first; UTG, UTG+1 and UTG+2 take
    # whatever is left at 7, 8 and 9 handed.
    assert hh_parser._middle_labels(0) == []                       # 3-handed
    assert hh_parser._middle_labels(1) == ["CO"]                   # 4-handed
    assert hh_parser._middle_labels(2) == ["HJ", "CO"]             # 5-handed
    assert hh_parser._middle_labels(3) == ["LJ", "HJ", "CO"]       # 6-handed
    assert hh_parser._middle_labels(4) == ["UTG", "LJ", "HJ", "CO"]            # 7-handed
    assert hh_parser._middle_labels(5) == ["UTG", "UTG+1", "LJ", "HJ", "CO"]   # 8-handed
    assert hh_parser._middle_labels(6) == [
        "UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO"]                             # 9-handed


def test_heads_up_button_is_the_small_blind():
    text = """PokerStars Hand #2:  Hold'em Limit ($1/$2 USD) - 2026/09/03 18:05:50 WET
Table 'Cepheus VI' 6-max Seat #5 is the button
Seat 1: toky909 ($24.29 in chips) 
Seat 5: clynchh ($20 in chips) 
clynchh: posts small blind $0.50
toky909: posts big blind $1
*** HOLE CARDS ***
Dealt to clynchh [4h 3c]
clynchh: folds 
Uncalled bet ($0.50) returned to toky909
toky909 collected $1 from pot
*** SUMMARY ***
Total pot $1 | Rake $0 
Seat 1: toky909 (big blind) collected ($1)
Seat 5: clynchh (button) folded before Flop
"""
    assert _parse(text)["hero_position"] == "SB"
    assert _position(text, "toky909") == "BB"


def test_dead_button_still_gives_everyone_else_a_position():
    # The button can sit on a seat that's sitting out. Nobody holds that
    # position, but the blinds and the seats before them are unaffected.
    text = HOLDEM_6MAX.replace(
        "Seat 1: Grampa55 (954 in chips) ",
        "Seat 1: Grampa55 (954 in chips) is sitting out",
    )
    assert _position(text, "James UK7") == "SB"
    assert _position(text, "SEE_AcCEpt") == "BB"
    assert _parse(text)["hero_position"] is not None


STUD = """PokerStars Hand #3: Tournament #99, $9.80+$1.20 USD 8-Game (Razz Limit) - Level XX (400/800) - 2026/08/31 21:19:18 WET
Table '99 6' 6-max
Seat 1: polentani (4835 in chips) 
Seat 4: sotelo0720 (5947 in chips) 
Seat 5: clynchh (1600 in chips) 
polentani: posts the ante 80
sotelo0720: posts the ante 80
clynchh: posts the ante 80
*** 3rd STREET ***
Dealt to polentani [Tc]
Dealt to sotelo0720 [4s]
Dealt to clynchh [4c 2h 6s]
polentani: brings in for 120
sotelo0720: raises 280 to 400
clynchh: calls 400
polentani: folds 
*** 4th STREET ***
Dealt to sotelo0720 [4s] [7c]
Dealt to clynchh [4c 2h 6s] [Th]
sotelo0720: bets 400
clynchh: folds 
Uncalled bet (400) returned to sotelo0720
sotelo0720 collected 1560 from pot
*** SUMMARY ***
Total pot 1560 | Rake 0 
Seat 1: polentani folded on the 3rd Street
Seat 4: sotelo0720 collected (1560)
Seat 5: clynchh folded on the 4th Street
"""


def test_stud_positions_are_counted_round_from_the_bring_in():
    # The bring-in acts first, then the seats after it in the order they
    # act: BI, +1, +2 ... How far the numbering runs depends on how many
    # were dealt in - +4 five-handed, +5 six-handed.
    assert _position(STUD, "polentani") == "BI"      # brings in
    assert _position(STUD, "sotelo0720") == "+1"
    assert _parse(STUD)["hero_position"] == "+2"     # three-handed, so +2 is last


def test_stud_numbering_runs_as_far_as_the_table_is_deep():
    five_handed = STUD.replace(
        "Seat 5: clynchh (1600 in chips) ",
        "Seat 2: extra_one (5000 in chips) \nSeat 3: extra_two (5000 in chips) "
        "\nSeat 5: clynchh (1600 in chips) ",
    ).replace(
        "polentani: posts the ante 80",
        "polentani: posts the ante 80\nextra_one: posts the ante 80\nextra_two: posts the ante 80",
    ).replace(
        "sotelo0720: raises 280 to 400",
        "extra_one: folds \nextra_two: folds \nsotelo0720: raises 280 to 400",
    )
    assert len(hh_parser._dealt_in_seats(five_handed)) == 5
    # Seat order from the bring-in, so the deepest number is one less than
    # the number of players.
    assert _position(five_handed, "polentani") == "BI"
    assert _position(five_handed, "extra_one") == "+1"
    assert _position(five_handed, "extra_two") == "+2"
    assert _position(five_handed, "sotelo0720") == "+3"
    assert _position(five_handed, "clynchh") == "+4"


def test_stud_position_falls_back_to_the_first_actor_without_a_bring_in():
    # At antes big enough that the low card is already all-in, PokerStars
    # prints no bring-in and 3rd street just opens with a bet.
    text = STUD.replace("polentani: brings in for 120", "polentani: bets 400")
    assert "brings in" not in text
    assert _position(text, "polentani") == "BI"
    assert _parse(text)["hero_position"] == "+2"


def test_a_sitting_out_player_who_was_dealt_in_still_counts():
    # PokerStars sets "is sitting out" on a player who is away but was still
    # dealt this hand - they post, they get cards, they auto-fold. Dropping
    # them shifts everyone after them round by one.
    text = HOLDEM_6MAX.replace(
        "Seat 4: sotelo0720 (9493 in chips) ",
        "Seat 4: sotelo0720 (9493 in chips) is sitting out",
    )
    assert "sotelo0720: folds" in text          # still acted, so still in the hand
    assert _position(text, "clynchh") == "HJ"   # unchanged by the flag
    assert _position(text, "sotelo0720") == "LJ"


def test_stud_fourth_street_is_the_saw_flop_equivalent():
    hand = _parse(STUD)
    assert hand["saw_next_street"] == 1   # hero folded ON 4th, so did see it
    assert hand["streets_seen"] == 1
    assert _parse(STUD, hero="polentani")["saw_next_street"] == 0  # folded on 3rd


def test_saw_next_street_needs_the_hand_to_actually_get_there():
    # Everyone folding to hero's raise ends it preflop. Hero won, but never
    # saw a flop, so this must not land in the WWSF denominator.
    text = """PokerStars Hand #4:  Hold'em Limit ($1/$2 USD) - 2026/09/03 18:05:50 WET
Table 'Cepheus VI' 6-max Seat #1 is the button
Seat 1: toky909 ($24.29 in chips) 
Seat 2: ultimo_ospite ($51.10 in chips) 
Seat 3: clynchh ($20 in chips) 
ultimo_ospite: posts small blind $0.50
clynchh: posts big blind $1
*** HOLE CARDS ***
Dealt to clynchh [4h 3c]
toky909: folds 
ultimo_ospite: folds 
Uncalled bet ($0.50) returned to clynchh
clynchh collected $1.50 from pot
*** SUMMARY ***
Total pot $1.50 | Rake $0 
Seat 1: toky909 (button) folded before Flop (didn't bet)
Seat 2: ultimo_ospite (small blind) folded before Flop
Seat 3: clynchh (big blind) collected ($1.50)
"""
    hand = _parse(text)
    assert hand["hero_collected"] > 0      # hero did win the pot
    assert hand["saw_next_street"] == 0    # but there was never a flop


SINGLE_DRAW = """PokerStars Hand #5: Tournament #99, $4.90+$0.60 USD Single Draw 2-7 Lowball No Limit - Level V (350/700) - 2026/09/11 17:44:33 WET
Table '99 19' 6-max Seat #2 is the button
Seat 2: Alehan124 (22461 in chips) 
Seat 3: Charbel.Zaha (28699 in chips) 
Seat 4: clynchh (24720 in chips) 
Charbel.Zaha: posts small blind 350
clynchh: posts big blind 700
*** DEALING HANDS ***
Dealt to clynchh [Jd 9h Td 6h Th]
Alehan124: raises 1400 to 2100
Charbel.Zaha: folds 
clynchh: calls 1400
clynchh: discards 1 card [Th]
Dealt to clynchh [Jd 9h Td 6h] [8c]
Alehan124: discards 1 card
clynchh: bets 700
Alehan124: calls 700
*** SHOW DOWN ***
clynchh: shows [Jd 9h Td 6h 8c] (Lo: J,T,9,8,6)
Alehan124: mucks hand 
clynchh collected 5390 from pot
*** SUMMARY ***
Total pot 5390 | Rake 0 
Seat 2: Alehan124 (button) mucked [5h 3h 6c 4c Kc]
Seat 3: Charbel.Zaha (small blind) folded before Flop
Seat 4: clynchh (big blind) showed [Jd 9h Td 6h 8c] and won (5390)
"""


def test_single_draw_splits_its_two_betting_rounds_without_a_header():
    # Single Draw prints "*** DEALING HANDS ***" and then nothing until the
    # showdown, so the discard is the only marker that the second round
    # happened. Without that split, the post-draw bet would be counted as
    # opening-round action and the draw would look like it never occurred.
    hand = _parse(SINGLE_DRAW)
    assert hand["saw_next_street"] == 1
    assert hand["raised_opening"] == 0          # hero called pre-draw
    assert hand["postflop_aggr"] == 1           # the post-draw bet
    assert hand["postflop_calls"] == 0


def test_opening_round_raise_is_not_counted_as_postflop_aggression():
    hand = _parse(HOLDEM_6MAX)
    assert hand["raised_opening"] == 1
    assert hand["postflop_aggr"] == 1           # the flop bet
    assert hand["postflop_calls"] == 1          # the river call


def test_stud_complete_counts_as_an_opening_raise():
    # Completing in stud is the open-raise, not a call - it belongs with
    # raises or stud's PFR reads as near zero.
    text = STUD.replace("sotelo0720: raises 280 to 400", "clynchh: completes it to 400")
    assert _parse(text)["raised_opening"] == 1


# --------------------------------------------------------------- aggregates

def _store(hand_id, **overrides):
    hand = dict(
        hand_id=hand_id, game_type="Hold'em", limit_type="NL", stakes="1/2",
        is_tournament=0, tournament_id=None, table_name="T", date_played="2026/01/01 00:00:00",
        hero_name="Hero", hero_cards="Ah Kd", hero_invested=10.0, hero_collected=0.0,
        hero_net=-10.0, pot_total=20.0, pot_type="Raised (SRP)", went_to_showdown=0,
        vpip=1, is_allin_ev=0, equity_pct=None, ev_net=None, big_blind=2.0,
        num_players=6, source_file="t.txt", raw_text="x", bounty_won=0.0,
        hero_position="BTN", saw_next_street=1, raised_opening=1,
        postflop_aggr=2, postflop_calls=1,
    )
    hand.update(overrides)
    db.upsert_hand(hand)


def test_aggregate_uses_each_stats_own_denominator():
    # Two hands saw a flop; one of those won; only one reached showdown and
    # it lost. So WWSF is 1/2, WTSD is 1/2, and W$SD is 0/1 - three
    # different denominators over the same three hands.
    _store("a", saw_next_street=1, hero_collected=50.0, hero_net=40.0, went_to_showdown=0)
    _store("b", saw_next_street=1, hero_collected=0.0, went_to_showdown=1)
    _store("c", saw_next_street=0, hero_collected=0.0, went_to_showdown=0)

    s = db.aggregate_stats()
    assert s["hands"] == 3
    assert s["saw_n"] == 2
    assert s["wwsf_pct"] == 50.0
    assert s["wtsd_pct"] == 50.0
    assert s["wsd_pct"] == 0.0


def test_aggression_factor_sums_actions_rather_than_averaging_ratios():
    # One busy hand and one quiet one: 6 aggressive actions against 3 calls
    # is 2.0. Averaging the per-hand ratios would give 1.5 and let the quiet
    # hand count for as much as the busy one.
    _store("a", postflop_aggr=5, postflop_calls=2)
    _store("b", postflop_aggr=1, postflop_calls=1)
    assert db.aggregate_stats()["aggression"] == 2.0


def test_aggression_factor_is_undefined_rather_than_infinite_without_calls():
    _store("a", postflop_aggr=3, postflop_calls=0)
    assert db.aggregate_stats()["aggression"] is None


def test_position_breakdown_leaves_out_hands_that_have_no_position():
    _store("a", hero_position="BTN")
    _store("b", hero_position="SB")
    _store("c", hero_position=None)  # a stud hand
    rows = db.aggregate_stats(group_by="position")
    assert [r["group"] for r in rows] == ["SB", "BTN"]  # preflop order, no blank row
    assert sum(r["hands"] for r in rows) == 2


def test_breakdown_respects_the_same_filters_as_the_hand_list():
    _store("a", hero_position="BTN", game_type="Hold'em")
    _store("b", hero_position="BTN", game_type="Razz")
    rows = db.aggregate_stats(group_by="position", game_type="Hold'em")
    assert len(rows) == 1
    assert rows[0]["hands"] == 1


def test_bb_per_100_ignores_hands_with_no_known_big_blind():
    # Stud levels don't always give one. Counting those hands in the
    # denominator would quietly dilute the win rate towards zero.
    _store("a", hero_net=10.0, big_blind=2.0)    # +5bb
    _store("b", hero_net=-100.0, big_blind=None)
    s = db.aggregate_stats()
    assert s["bb_hands"] == 1
    assert s["bb_per_100"] == 500.0


def test_reports_page_survives_a_filter_that_matches_nothing(tmp_path):
    # Every stat's denominator is zero here, so each one is None rather than
    # a number. Formatting those straight into the page used to 500.
    import app as flask_app
    # An unconfigured install redirects everything to setup, so there'd be
    # nothing to render without this.
    db.set_setting("hand_history_dir", str(tmp_path))
    db.set_setting("hero_username", "Hero")
    client = flask_app.app.test_client()
    for url in ("/reports?search=nothingmatchesthis",
                "/reports?search=nothingmatchesthis&breakdown=game",
                "/reports?search=nothingmatchesthis&breakdown=position"):
        assert client.get(url).status_code == 200


def test_empty_aggregate_reports_no_hands_rather_than_failing():
    s = db.aggregate_stats()
    assert s["hands"] == 0
    assert s["net"] == 0
    assert s["win_rate"] is None
    assert s["bb_per_100"] is None
    assert db.aggregate_stats(group_by="position") == []


# ------------------------------------------------------------ street depth

def test_streets_seen_counts_rounds_past_the_opening_one():
    # Hero calls preflop, bets the flop, checks the turn and calls the
    # river: three rounds past the opening one.
    assert _parse(HOLDEM_6MAX)["streets_seen"] == 3


def test_folding_on_a_street_still_counts_as_having_seen_it():
    # Hero folds to the flop bet. They saw the flop and made a decision on
    # it, which is exactly what a "saw the flop" stat is asking.
    text = HOLDEM_6MAX.replace("clynchh: bets 300", "clynchh: folds")
    assert _parse(text)["streets_seen"] == 1


def test_single_draw_counts_its_one_extra_round():
    # Its second betting round shares a block with the first, so without the
    # discard split this reads as zero rounds past the opening.
    assert _parse(SINGLE_DRAW)["streets_seen"] == 1


def test_street_names_follow_the_game_rather_than_the_number():
    assert db.street_name("Hold'em", 1) == "Flop"
    assert db.street_name("Hold'em", 3) == "River"
    assert db.street_name("Razz", 1) == "4th street"
    assert db.street_name("Stud Hi/Lo", 4) == "7th street"
    assert db.street_name("2-7 Triple Draw", 1) == "1st draw"
    assert db.street_name("2-7 Triple Draw", 3) == "3rd draw"


def test_street_funnel_counts_hands_cumulatively():
    # A hand that reached the river also reached the flop and the turn, so
    # it belongs in all three rows - the funnel is "got at least this far",
    # not "stopped exactly here".
    _store("a", game_type="Hold'em", streets_seen=3, hero_collected=10.0)
    _store("b", game_type="Hold'em", streets_seen=1, hero_collected=0.0)
    _store("c", game_type="Hold'em", streets_seen=0, hero_collected=0.0)

    fam = db.street_funnel()[0]
    assert fam["family"] == "flop"
    assert fam["hands"] == 3
    assert [(r["label"], r["reached"]) for r in fam["rows"]] == [
        ("Flop", 2), ("Turn", 1), ("River", 1)
    ]
    assert fam["rows"][0]["won_pct"] == 50.0   # one of the two that saw a flop


def test_street_funnel_keeps_game_families_apart():
    # "Level 2" is the turn in one game and 5th street in another, so they
    # can't share a row.
    _store("a", game_type="Hold'em", streets_seen=2)
    _store("b", game_type="Razz", streets_seen=4)
    families = {f["family"]: f for f in db.street_funnel()}
    assert set(families) == {"flop", "stud"}
    assert [r["label"] for r in families["flop"]["rows"]] == ["Flop", "Turn"]
    assert [r["label"] for r in families["stud"]["rows"]] == [
        "4th street", "5th street", "6th street", "7th street"
    ]


def test_street_funnel_leaves_out_rounds_nobody_reached():
    _store("a", game_type="Hold'em", streets_seen=1)
    assert [r["label"] for r in db.street_funnel()[0]["rows"]] == ["Flop"]


# ------------------------------------------------------------ All-In Poker

ALL_IN_POKER = """PokerStars Hand #6:  All-In Poker No Limit ($0.10/$0.20 - $5 Cap -  USD) - 2026/08/31 13:07:52 WET
Table 'Zwicky V' 6-max Seat #3 is the button
Seat 1: paulparadiis ($1 in chips) 
Seat 3: clynchh ($1 in chips) 
Seat 4: Cochise17 ($2.87 in chips) 
Cochise17: posts small blind $0.10
paulparadiis: posts big blind $0.20
*** HOLE CARDS ***
Dealt to clynchh [Qh Qd]
clynchh: raises $0.80 to $1 and is all-in
Cochise17: calls $0.80 and is all-in
paulparadiis: folds 
*** FLOP *** [3s Ks 7c]
*** TURN *** [3s Ks 7c] [7d]
*** RIVER *** [3s Ks 7c 7d] [6h]
*** SHOW DOWN ***
Cochise17: shows [Js Jc] (two pair)
clynchh: shows [Qh Qd] (two pair, Queens and Sevens)
clynchh collected $2.15 from pot
*** SUMMARY ***
Total pot $2.20 | Rake $0.05 
Board [3s Ks 7c 7d 6h]
Seat 1: paulparadiis (big blind) folded before Flop
Seat 3: clynchh (button) showed [Qh Qd] and won ($2.15)
Seat 4: Cochise17 (small blind) showed [Js Jc] and lost
"""


def test_all_in_poker_is_holdem_with_its_own_betting_structure():
    # PokerStars never writes "Hold'em" in this game's description, so it
    # used to classify as Unknown - which meant no replayer and no all-in EV
    # for any of those hands. It is Hold'em: two hole cards, same board,
    # same rankings. Only the betting differs, and that's what limit_type is
    # for, so it doesn't get averaged into real No Limit Hold'em either.
    hand = _parse(ALL_IN_POKER)
    assert hand["game_type"] == "Hold'em"
    assert hand["limit_type"] == "AI"
    assert hand["big_blind"] == 0.20
    assert hand["streets_seen"] == 3
    assert hand["hero_position"] == "BTN"
