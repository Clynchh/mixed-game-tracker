"""Regression tests for this project's security review.

Covers what a Flask test client can actually exercise server-side: the
same-origin check on state-changing routes, and that hand data embedded
into <script> blocks via Jinja's tojson can't break out of the script tag.
The client-side innerHTML-escaping fix in graph.js/replayer.js (the more
serious half of the XSS finding) was verified with a real browser during
the review - see the session notes - since a Flask test client never
executes JavaScript and so can't exercise that path itself.
"""
import shutil

import app as flask_app_module
import db

from conftest import SAMPLE_HANDS_PATH

flask_app = flask_app_module.app
HERO = "Corey88"
OWN_ORIGIN = f"http://{flask_app_module.HOST}:{flask_app_module.PORT}"


def client():
    return flask_app.test_client()


def configure(folder):
    db.set_setting("hand_history_dir", str(folder))
    db.set_setting("hero_username", HERO)


def test_get_requests_are_never_blocked_regardless_of_origin():
    r = client().get("/setup", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 200


def test_post_with_matching_origin_is_allowed(tmp_path):
    configure(tmp_path)
    r = client().post("/api/rescan", headers={"Origin": OWN_ORIGIN})
    assert r.status_code == 200


def test_post_with_no_origin_or_referer_is_allowed(tmp_path):
    # A same-origin form submission or an older browser may not send
    # either header - only an explicit MISMATCH should be rejected, so
    # normal use of the app is never at risk of breaking.
    configure(tmp_path)
    r = client().post("/api/rescan")
    assert r.status_code == 200


def test_post_with_foreign_origin_is_rejected():
    r = client().post("/api/rescan", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_post_with_foreign_referer_is_rejected_when_no_origin_sent():
    r = client().post(
        "/hand/some-id/tag",
        data={"tag": "x"},
        headers={"Referer": "https://evil.example.com/attack.html"},
    )
    assert r.status_code == 403


def test_state_changing_routes_all_reject_foreign_origin(tmp_path):
    configure(tmp_path)
    shutil.copy(SAMPLE_HANDS_PATH, tmp_path / "session1.txt")
    import watcher
    watcher.scan_folder(str(tmp_path), hero_username=HERO)
    hand_id = db.list_hands(limit=1)[0]["hand_id"]

    foreign = {"Origin": "https://evil.example.com"}
    c = client()
    assert c.post("/api/reimport", headers=foreign).status_code == 403
    assert c.post("/api/rescan", headers=foreign).status_code == 403
    assert c.post(f"/hand/{hand_id}/tag", data={"tag": "x"}, headers=foreign).status_code == 403
    assert c.post(f"/hand/{hand_id}/untag", data={"tag": "x"}, headers=foreign).status_code == 403
    assert c.post("/settings", data={"hand_history_dir": "x", "hero_username": "y"},
                  headers=foreign).status_code == 403
    assert c.post("/setup", data={"hand_history_dir": "x", "hero_username": "y"},
                  headers=foreign).status_code == 403
    # And confirm none of those forbidden requests actually changed anything.
    assert [t["tag"] for t in db.get_hand(hand_id)["tags"]] == []


def test_replay_data_in_script_block_cannot_break_out_of_the_tag(tmp_path):
    """A hand-history file is just a .txt file someone could hand-craft -
    a player name or card containing "</script>" must not be able to end
    the inline <script> block early and inject sibling HTML/script."""
    configure(tmp_path)
    payload_file = tmp_path / "malicious.txt"
    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        text = f.read()
    hand = [h for h in text.split("PokerStars Hand")[1:] if "Hold'em No Limit" in h][0]
    hand = "PokerStars Hand" + hand
    hand = hand.replace("Villain2", "</script><script>alert(1)</script>")
    payload_file.write_text(hand)

    import watcher
    watcher.scan_folder(str(tmp_path), hero_username=HERO)
    hand_id = db.list_hands(limit=1)[0]["hand_id"]

    r = client().get(f"/hand/{hand_id}")
    body = r.get_data(as_text=True)
    assert "</script><script>alert(1)</script>" not in body
    # Jinja's tojson filter escapes '<' to \u003c specifically so a value
    # like this can never prematurely close the surrounding <script> tag.
    assert "\\u003c/script\\u003e" in body
