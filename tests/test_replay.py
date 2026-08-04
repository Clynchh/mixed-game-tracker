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
