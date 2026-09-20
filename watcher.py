"""
Passive folder scanner. Does NOT touch the PokerStars client in any way -
it only reads .txt files you already have PokerStars configured to save
hand histories to (Settings > Hand History in the client).

Runs a full scan on startup, then re-scans on an interval + on filesystem
events (if watchdog is available) to pick up newly-saved hands.
"""
import os
import time
import threading
import glob

import db
import parser as hh_parser
import tourn_summary

SCAN_INTERVAL_SECONDS = 15

# Read by the UI (via /api/scan-status) to show a spinner/progress bar while
# a big first import is running, rather than leaving the page looking stuck
# for however long a folder full of hand histories takes to read.
SCAN_PROGRESS = {"active": False, "done": 0, "total": 0}

# The periodic background scan (FolderWatcher, every 15s) and an on-demand
# one (setup, a folder change, "scan now", "re-read every hand") can land at
# almost the same moment. Without this, two scan_folder() calls running at
# once would both reset/increment the same SCAN_PROGRESS dict, corrupting
# the numbers the progress bar reads. A non-blocking lock means a scan that
# overlaps an already-running one just no-ops - it would only be re-covering
# the same files the running one is already about to get to.
_scan_lock = threading.Lock()

# Where the PokerStars clients keep hand histories. Each regional client
# (.UK, .EU, .ES ...) installs to its own folder, and inside HandHistory
# there's one folder per screen name - so finding these also tells us what
# the player's username is, which is the other thing setup needs.
_HH_SEARCH_ROOTS = [
    os.path.expandvars(r"%LOCALAPPDATA%"),
    os.path.expandvars(r"%APPDATA%"),
    os.path.expanduser("~/Library/Application Support"),
    os.path.expanduser("~"),
]


def detect_hand_history_folders():
    """Best-effort scan for PokerStars hand-history folders already on this
    machine. Returns [{"path", "username", "client", "files"}] sorted with
    the most-used first, or [] if nothing turns up - setup always allows
    typing a path by hand."""
    found = {}
    for root in _HH_SEARCH_ROOTS:
        if not root or not os.path.isdir(root):
            continue
        for pattern in ("PokerStars*/HandHistory/*", ".PokerStars*/HandHistory/*"):
            for path in glob.glob(os.path.join(root, pattern)):
                if not os.path.isdir(path) or path in found:
                    continue
                try:
                    n_files = len(glob.glob(os.path.join(path, "**", "*.txt"), recursive=True))
                except OSError:
                    n_files = 0
                client = os.path.basename(os.path.dirname(os.path.dirname(path)))
                found[path] = {
                    "path": path,
                    "username": os.path.basename(path),
                    "client": client.lstrip("."),
                    "files": n_files,
                }
    return sorted(found.values(), key=lambda c: -c["files"])


def scan_folder(folder, hero_username=None, on_new_hand=None):
    """Scan every .txt file in folder (recursively). Skips files whose
    mtime/size haven't changed since the last scan. Returns count of newly
    imported hands."""
    if not folder or not os.path.isdir(folder):
        return 0

    if not _scan_lock.acquire(blocking=False):
        return 0  # a scan's already running and will cover this folder too

    files = glob.glob(os.path.join(folder, "**", "*.txt"), recursive=True)
    SCAN_PROGRESS["active"] = True
    SCAN_PROGRESS["total"] = len(files)
    SCAN_PROGRESS["done"] = 0

    try:
        new_count = 0
        for filepath in files:
            try:
                stat = os.stat(filepath)
            except OSError:
                SCAN_PROGRESS["done"] += 1
                continue

            prev = db.get_file_state(filepath)
            if prev and prev["mtime"] == stat.st_mtime and prev["size"] == stat.st_size:
                SCAN_PROGRESS["done"] += 1
                continue  # unchanged, skip re-parsing

            try:
                hands = hh_parser.parse_file(filepath, hero_username=hero_username)
            except Exception as e:
                print(f"[watcher] failed to parse {filepath}: {e}")
                SCAN_PROGRESS["done"] += 1
                continue

            imported_here = 0
            for hand in hands:
                if db.upsert_hand(hand):
                    imported_here += 1
                    new_count += 1
                    if on_new_hand:
                        on_new_hand(hand)
                if hand.get("is_tournament") and hand.get("tournament_id"):
                    db.upsert_tournament_shell(
                        hand["tournament_id"], hand.get("tourney_game_desc"),
                        hand.get("tourney_buyin"), hand.get("date_played"),
                    )
                    if hand.get("tourney_finished"):
                        db.set_tournament_result(
                            hand["tournament_id"], hand.get("tourney_finish_place"), hand.get("tourney_payout"),
                        )

            db.set_file_state(filepath, stat.st_mtime, stat.st_size, len(hands))
            if imported_here:
                print(f"[watcher] {filepath}: +{imported_here} new hands")
            SCAN_PROGRESS["done"] += 1

        return new_count
    finally:
        SCAN_PROGRESS["active"] = False
        _scan_lock.release()


class FolderWatcher:
    """Runs scan_folder on a background thread every SCAN_INTERVAL_SECONDS."""

    def __init__(self, get_folder_fn, get_username_fn=None, get_tourn_summary_dir_fn=None,
                 on_scan_complete=None):
        self.get_folder_fn = get_folder_fn
        self.get_username_fn = get_username_fn
        self.get_tourn_summary_dir_fn = get_tourn_summary_dir_fn
        # Called with the number of newly imported hands after each scan.
        # Used to refresh the static export, which is only worth rebuilding
        # when a scan actually found something.
        self.on_scan_complete = on_scan_complete
        self._stop = threading.Event()
        self._thread = None
        self.last_scan_new = 0
        self.last_scan_time = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            folder = self.get_folder_fn()
            if folder:
                try:
                    hero_username = self.get_username_fn() if self.get_username_fn else None
                    self.last_scan_new = scan_folder(folder, hero_username=hero_username)
                    self.last_scan_time = time.time()
                    if self.get_tourn_summary_dir_fn:
                        tourn_summary.scan_folder(self.get_tourn_summary_dir_fn(), hero_username=hero_username)
                    if self.on_scan_complete:
                        # Deliberately inside the try: a failing hook must not
                        # take the scan loop down with it.
                        self.on_scan_complete(self.last_scan_new)
                except Exception as e:
                    print(f"[watcher] scan error: {e}")
            self._stop.wait(SCAN_INTERVAL_SECONDS)
