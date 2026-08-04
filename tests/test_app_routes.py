"""app.py: Flask routes - the setup gate, dashboard, tagging, and settings."""
import shutil

import app as flask_app_module
import db

from conftest import SAMPLE_HANDS_PATH

flask_app = flask_app_module.app
HERO = "Corey88"


def client():
    return flask_app.test_client()


def configure(folder):
    db.set_setting("hand_history_dir", str(folder))
    db.set_setting("hero_username", HERO)


def test_unconfigured_install_redirects_dashboard_to_setup():
    r = client().get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/setup" in r.headers["Location"]


def test_setup_page_itself_stays_reachable_when_unconfigured():
    r = client().get("/setup")
    assert r.status_code == 200


def test_settings_and_static_stay_reachable_when_unconfigured():
    # These are explicitly exempted so a not-yet-configured install can
    # still reach the page that lets it get configured.
    assert client().get("/settings").status_code == 200


def test_dashboard_reachable_once_configured(tmp_path):
    configure(tmp_path)
    r = client().get("/")
    assert r.status_code == 200


def test_save_setup_rejects_nonexistent_folder():
    r = client().post("/setup", data={"hand_history_dir": "/definitely/not/a/real/path", "hero_username": HERO})
    assert r.status_code == 200  # re-rendered with an error, not redirected
    assert b"doesn" in r.data or b"exist" in r.data.lower()
    assert db.get_setting("hand_history_dir") is None


def test_save_setup_accepts_real_folder_and_scans_it(tmp_path):
    shutil.copy(SAMPLE_HANDS_PATH, tmp_path / "session1.txt")
    r = client().post("/setup", data={"hand_history_dir": str(tmp_path), "hero_username": HERO})
    assert r.status_code == 302
    assert db.get_setting("hero_username") == HERO
    assert db.overall_stats()["hands"] == 7


def test_hand_detail_and_tagging_roundtrip(tmp_path):
    configure(tmp_path)
    shutil.copy(SAMPLE_HANDS_PATH, tmp_path / "session1.txt")
    import watcher
    watcher.scan_folder(str(tmp_path), hero_username=HERO)
    hand_id = db.list_hands(limit=1)[0]["hand_id"]

    r = client().get(f"/hand/{hand_id}")
    assert r.status_code == 200

    c = client()
    c.post(f"/hand/{hand_id}/tag", data={"tag": "reviewed", "note": "looked at it"})
    assert [t["tag"] for t in db.get_hand(hand_id)["tags"]] == ["reviewed"]
    c.post(f"/hand/{hand_id}/untag", data={"tag": "reviewed"})
    assert db.get_hand(hand_id)["tags"] == []


def test_hand_detail_404s_for_unknown_id(tmp_path):
    configure(tmp_path)
    r = client().get("/hand/nope-not-a-real-id")
    assert r.status_code == 404


def test_api_reimport_rewrites_hands_but_keeps_tags(tmp_path):
    configure(tmp_path)
    shutil.copy(SAMPLE_HANDS_PATH, tmp_path / "session1.txt")
    import watcher
    watcher.scan_folder(str(tmp_path), hero_username=HERO)
    hand_id = db.list_hands(limit=1)[0]["hand_id"]
    db.add_tag(hand_id, "keep-me", "")

    r = client().post("/api/reimport")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert db.overall_stats()["hands"] == 7
    assert [t["tag"] for t in db.get_hand(hand_id)["tags"]] == ["keep-me"]


def test_reports_and_hand_row_partial_endpoints(tmp_path):
    configure(tmp_path)
    shutil.copy(SAMPLE_HANDS_PATH, tmp_path / "session1.txt")
    import watcher
    watcher.scan_folder(str(tmp_path), hero_username=HERO)
    hand_id = db.list_hands(limit=1)[0]["hand_id"]

    assert client().get("/reports").status_code == 200
    assert client().get(f"/api/hand/{hand_id}/row").status_code == 200
    assert client().get("/api/hand/does-not-exist/row").status_code == 404
