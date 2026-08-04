# Mixed Games Tracker

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

You need Python 3 installed. Then:

```bash
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5151** and it walks you through a two-step setup:

1. **Your hand history folder.** It looks for PokerStars folders already on
   your machine and offers them — usually one click. If it doesn't find
   yours, paste the path in by hand. In PokerStars it's under
   Settings → Hand History → Hand History Location, and hand history saving
   needs to be switched on there.
2. **Your screen name**, spelled exactly as you play under.

That second one matters more than it looks. In Hold'em and Omaha the hand
history only ever shows your own hole cards, so the app could guess which
player is you. Stud and razz show every player's up-cards, so without your
exact name it can mistake somebody else for you and report their results as
yours.

After that it scans the folder every 15 seconds for new hands. It's safe to
point at a folder you're actively playing in — it only ever reads, never
writes there.

Your data stays on your own machine. There's no account, no upload, and the
app makes no network requests at all.

## Distributing it

Everything is per-user and local, so sharing it is just sharing the code —
`tracker.db` (your hands, your settings) is gitignored and never travels
with it. Whoever installs it gets the setup screen and their own database.

The app is free. If you want to offer people a way to chip in, see
`support_config.py` — paste in payment links from a provider like Stripe,
Ko-fi or GitHub Sponsors and a Support page appears. Leave it empty (the
default) and there's no Support page and no mention of money anywhere. The
app never handles payments itself: it only links out to the provider's own
checkout page, so no keys or card details ever go near it.

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
- `app.py` — the dashboard: separate Cash / Tournament tabs (results are not
  comparable — tournament chips aren't cash until you finish), per-game-type
  stat cards, a $-or-BB display toggle, and a filterable hand list. Each hand
  has a detail view where you tag it with free-text labels (e.g. `bad-fold`,
  `missed-value`, `ante-steal-spot`) plus an optional note. Filter the
  dashboard by tag later to review a specific leak across sessions.
- Tournament results (buy-in vs. actual cash payout) are tracked separately
  from hand-level chip swings, since chip net within a tournament isn't real
  money — only what you cash out for is. This shows as its own banner on the
  Tournament tab.

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
