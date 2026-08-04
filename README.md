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

## Getting started

**The easy way — no Python needed.** Download `MixedGamesTracker.exe` from
the [Releases page](../../releases), put it anywhere you like, and
double-click it. Your browser opens on the app automatically. Closing the
black console window quits it.

Windows will likely warn you the file is from an unknown publisher, because
the download isn't code-signed (signing certificates cost money). "More
info" → "Run anyway" gets past it.

**From source**, if you'd rather:

```bash
pip install -r requirements.txt
python app.py
```

Either way it walks you through a two-step setup:

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

## Updating

Download the new `MixedGamesTracker.exe` and replace the old one. That's it —
your hands aren't in the app, they're in a database in your own user folder
(Settings shows you exactly where), so replacing the app never touches them.

Two things happen by themselves when you open a newer version:

- **New features that need new data** are added to your existing database in
  place, keeping every hand. A copy of the database is saved alongside it as
  `tracker.db.bak` first, just in case.
- **If an update improves how hands are read**, a banner appears offering to
  re-read your hand history files, so hands you imported before the update
  pick up the fix too. Without it they'd quietly keep the old figures,
  because the scanner normally skips files it has already seen. Your tags
  and notes are kept — only the hand details get refreshed. You can also
  trigger this any time from Settings.

Worth copying that database file somewhere safe now and then. It's the only
thing that can't be re-downloaded — though since it's rebuilt from your
PokerStars hand history files, even losing it only costs you your tags.

## Running the tests (maintainer notes)

```bash
pip install -r requirements-dev.txt
pytest
```

Runs entirely against a throwaway temp database (never `tracker.db`) and the
bundled `sample_hands/session1.txt`. Covers hand parsing, the replayer's
chip-conservation invariant, the hand evaluator, migrations, and the Flask
routes — including the same-origin check on every state-changing endpoint.

## Building a release (maintainer notes)

```bash
pip install pyinstaller
pyinstaller MixedGamesTracker.spec
```

That produces a single self-contained `dist/MixedGamesTracker.exe` (~13 MB)
with Python and every dependency inside, so the person you send it to needs
nothing installed. Upload it to a GitHub Release — it's deliberately
gitignored rather than committed, since binaries don't belong in the repo.

PyInstaller can't cross-compile: build the Windows executable on Windows,
the macOS one on macOS.

Bump `VERSION` in `version.py` for each release. If a release changes how
hands are *parsed* — a fix to the money maths, hero detection, blind sizes —
also bump `PARSER_VERSION`. That's what makes existing users get the
"re-read your hands" prompt; without it the fix would only apply to hands
imported after they updated, and their older hands would silently keep the
wrong numbers.

A packaged build stores its database under the user's own data folder
(`%LOCALAPPDATA%\MixedGamesTracker` on Windows) rather than beside the
executable, so it survives replacing the .exe with a newer one and works
even if it's been dropped somewhere read-only. Running from source keeps
`tracker.db` next to the code as before.

Nothing personal travels with a build — `tracker.db` holds all hands and
settings and is gitignored, so everyone starts on the setup screen with
their own empty database.

### Taking donations

Off by default. Open `support_config.py` and set `KOFI_USERNAME` to your
Ko-fi handle, and a small "Support me" button appears at the bottom-left of
every page, linking out to your Ko-fi page. Leave it empty and no button
shows and no request to ko-fi.com is ever made.

The app never processes payments itself, and deliberately so: it runs on
each player's own machine, so any secret key shipped inside it would be
readable by everyone holding a copy. The button only ever links out to your
Ko-fi page, which handles the actual checkout — no card details, keys or
accounts go anywhere near this code.

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
