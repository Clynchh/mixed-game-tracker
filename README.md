# MixTrack

A local hand tracker for mixed games (8-Game rotation + NL 2-7 Single Draw,
razz, stud, stud hi/lo, omaha hi/lo, triple draw, hold'em) that PokerTracker
and Holdem Manager don't support.

**What it does:** watches a folder for `.txt` hand history files PokerStars
already saves, parses them, and gives you a local web dashboard to browse,
filter, and manually tag hands so you can find your own leaks.

**What it doesn't do:** anything live. It never touches the PokerStars
process, reads memory, or overlays the table. It's a plain file-watcher +
SQLite database + local Flask server, reading files after you've already
saved them — same category of tool as opening a saved hand history in a
text editor. This sidesteps PokerStars' HUD/tracking-tool restrictions
entirely since there's no interaction with the client while you play.

## Setup

```bash
pip install -r requirements.txt
python3 app.py
```

Then open **http://127.0.0.1:5151** in your browser.

On first load, go to **Settings** and set your hand history folder — this is
whatever path PokerStars is configured to save hands to
(Client Settings → Hand History → Hand History Location). It's usually
something like:

- Linux: `~/.PokerStars/HandHistory/<YourScreenName>/`
- Windows: `C:\Users\<you>\AppData\Local\PokerStars\HandHistory\<YourScreenName>\`

Once set, MixTrack scans the folder every 15 seconds for new/changed `.txt`
files. It's safe to point it at a folder you're actively playing in — it
only reads, never writes there.

## How it works

- `parser.py` splits each file into individual hands and parses metadata
  (game type, stakes, date, your hole cards) plus your net result per hand,
  using a per-street running-total money tracker that's generic enough to
  handle stud bring-ins/completes, draw discards, and flop-game raises with
  the same logic.
- `watcher.py` polls the configured folder, skips files that haven't
  changed size/mtime since last scan, and imports any new hands (hand IDs
  are the dedup key, so re-scanning is always safe).
- `db.py` / `tracker.db` — everything lives in one SQLite file next to the
  app. Back it up like any other file if you want to keep history.
- `app.py` — the dashboard: per-game-type stat cards, a filterable hand
  list, and a hand detail view where you tag hands with free-text labels
  (e.g. `bad-fold`, `missed-value`, `ante-steal-spot`) plus an optional
  note. Filter the dashboard by tag later to review a specific leak across
  sessions.

## Extending it

The money-tracking logic in `parser.py` (`_compute_money`) is deliberately
generic rather than a full action-by-action model — it's built to get your
net result and the raw hand text right, which is what you need for manual
review. If you later want structured per-street action data (e.g. to
compute your own fold-to-3bet% in razz), that's a natural next layer on
top of the existing street-header detection.

## Sample data

`sample_hands/session1.txt` has one hand of each game type in the rotation
(synthetic, not real PokerStars data) — useful for confirming the app works
before pointing it at your real folder. Delete it, or just repoint Settings
at your real hand history folder, whenever you're ready.
