# Releasing

How to ship the app to friends, first time and every time after.

## First release (v1.0.0)

1. **Save what you've got.** Commit and push any pending changes.
2. **Make sure the repo is public** (or add your friends as collaborators) —
   they need to be able to see the Releases page to download the file. It's
   currently at github.com/Clynchh/mixed-game-tracker; check its visibility
   in GitHub's repo Settings if you're not sure.
3. **Build the .exe:**
   ```
   pip install pyinstaller
   pyinstaller MixedGamesTracker.spec
   ```
   This creates `dist/MixedGamesTracker.exe`.
4. **Test it for real** — copy that .exe somewhere outside the project
   folder and double-click it, like a friend would. Confirm it opens a
   browser, walks you through setup, and works.
5. **Create the GitHub Release:** on your repo page → Releases → "Draft a
   new release" → tag it `v1.0.0` → upload `MixedGamesTracker.exe` as the
   file → publish.
6. **Send friends the Releases page link.** They download the .exe, run it,
   done.

## Every release after that

1. Make your code changes as normal.
2. In `version.py`, bump `VERSION` (e.g. `"1.0.1"`). If the change affects
   how hands are *parsed* — money math, hero detection, blind sizes — also
   bump `PARSER_VERSION`. That second one is what makes existing users get
   the "re-read your hands" banner automatically.
3. Commit and push.
4. Rebuild: `pyinstaller MixedGamesTracker.spec`, test the fresh .exe the
   same way as step 4 above.
5. New GitHub Release, new tag matching the version (e.g. `v1.0.1`), upload
   the new .exe.
6. Tell your friends there's an update — they download the new .exe and
   replace the old one. Their hand database lives in
   `%LOCALAPPDATA%\MixedGamesTracker`, completely separate from the .exe
   file, so nothing is lost when they swap it.

That's the whole loop — no installers, no store, no auto-update system,
just "download the new file, replace the old one."
