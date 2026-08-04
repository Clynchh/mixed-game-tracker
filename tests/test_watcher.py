"""watcher.py: folder scanning, dedup, and hand-history folder detection."""
import os
import shutil
import time

import watcher

from conftest import SAMPLE_HANDS_PATH

HERO = "Corey88"


def test_scan_folder_imports_sample_hands(tmp_path):
    dest = tmp_path / "session1.txt"
    shutil.copy(SAMPLE_HANDS_PATH, dest)
    n = watcher.scan_folder(str(tmp_path), hero_username=HERO)
    assert n == 7


def test_scan_folder_skips_unchanged_files_on_second_pass(tmp_path):
    dest = tmp_path / "session1.txt"
    shutil.copy(SAMPLE_HANDS_PATH, dest)
    first = watcher.scan_folder(str(tmp_path), hero_username=HERO)
    second = watcher.scan_folder(str(tmp_path), hero_username=HERO)
    assert first == 7
    assert second == 0  # same mtime/size -> skipped, not reparsed


def test_scan_folder_reparses_when_file_changes(tmp_path):
    dest = tmp_path / "session1.txt"
    shutil.copy(SAMPLE_HANDS_PATH, dest)
    watcher.scan_folder(str(tmp_path), hero_username=HERO)

    with open(SAMPLE_HANDS_PATH, encoding="utf-8") as f:
        extra_hand = f.read().split("PokerStars Hand #223344557")[1]
    with open(dest, "a", encoding="utf-8") as f:
        f.write("\nPokerStars Hand #223344558" + extra_hand.replace("223344557", "223344558"))
    os.utime(dest, (time.time() + 1, time.time() + 1))  # force a distinct mtime

    n = watcher.scan_folder(str(tmp_path), hero_username=HERO)
    assert n >= 1  # the appended file was re-read because it actually changed


def test_scan_folder_recurses_into_subfolders(tmp_path):
    sub = tmp_path / "2026" / "01"
    sub.mkdir(parents=True)
    shutil.copy(SAMPLE_HANDS_PATH, sub / "session1.txt")
    n = watcher.scan_folder(str(tmp_path), hero_username=HERO)
    assert n == 7


def test_scan_folder_missing_directory_is_a_harmless_noop(tmp_path):
    assert watcher.scan_folder(str(tmp_path / "does_not_exist"), hero_username=HERO) == 0
    assert watcher.scan_folder("", hero_username=HERO) == 0


def test_scan_folder_survives_unparseable_and_unreadable_files(tmp_path):
    (tmp_path / "not_poker.txt").write_text("hello world, not a hand history\n")
    shutil.copy(SAMPLE_HANDS_PATH, tmp_path / "session1.txt")
    n = watcher.scan_folder(str(tmp_path), hero_username=HERO)
    assert n == 7  # the garbage file is silently skipped, not fatal


def test_detect_hand_history_folders_returns_list_without_crashing():
    # Best-effort on whatever this machine actually has - just needs to
    # return a well-formed list (real content depends on what's installed).
    result = watcher.detect_hand_history_folders()
    assert isinstance(result, list)
    for entry in result:
        assert set(entry.keys()) >= {"path", "username", "client", "files"}
