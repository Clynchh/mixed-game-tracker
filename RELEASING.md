# Releasing

How to ship the app to friends, first time and every time after.

A GitHub Actions workflow (`.github/workflows/release.yml`) builds both the
Windows `.exe` and a macOS binary automatically whenever a `v*` tag is
pushed, and drafts a GitHub Release with both attached — PyInstaller can't
cross-compile, so this is what actually makes a Mac build possible without
owning a Mac. You still review, test, and publish by hand.

## First release (v1.0.0)

1. **Save what you've got.** Commit and push any pending changes.
2. **Make sure the repo is public** (or add your friends as collaborators) —
   they need to be able to see the Releases page to download the file. It's
   currently at github.com/Clynchh/mixed-game-tracker; check its visibility
   in GitHub's repo Settings if you're not sure.
3. **Tag it and push the tag:**
   ```
   git tag -a v1.0.0 -m "v1.0.0"
   git push origin v1.0.0
   ```
4. **Wait for the workflow to finish** (repo page → Actions tab) — it
   builds both platforms and opens a **draft** release with
   `MixedGamesTracker-windows.exe` and `MixedGamesTracker-macos` attached.
5. **Test both for real** before publishing:
   - Windows: download the `.exe` somewhere outside the project folder and
     double-click it, like a friend would. Confirm it opens a browser,
     walks you through setup, and works.
   - macOS: it's unsigned and unnotarized (no Apple Developer account is
     wired up), so Gatekeeper will refuse to open it with a plain
     double-click. Whoever tests it needs to right-click → Open the first
     time (or `xattr -d com.apple.quarantine MixedGamesTracker` in
     Terminal), then approve it once in System Settings → Privacy &
     Security. Worth spelling this out to Mac-using friends too, since
     they'll hit the same prompt.
6. **Publish the draft** once both check out, and edit in release notes.
7. **Send friends the Releases page link.** They download the file for
   their OS, run it, done.

## Every release after that

1. Make your code changes as normal.
2. In `version.py`, bump `VERSION` (e.g. `"1.0.1"`). If the change affects
   how hands are *parsed* — money math, hero detection, blind sizes — also
   bump `PARSER_VERSION`. That second one is what makes existing users get
   the "re-read your hands" banner automatically.
3. Commit and push.
4. Tag and push the tag (e.g. `v1.0.1`), same as step 3 above.
5. Wait for the workflow's draft release, test both binaries, publish.
6. Tell your friends there's an update — they download the new file and
   replace the old one. Their hand database lives in
   `%LOCALAPPDATA%\MixedGamesTracker` (Windows) or
   `~/Library/Application Support/MixedGamesTracker` (macOS), completely
   separate from the app file, so nothing is lost when they swap it.

That's the whole loop — no installers, no store, no auto-update system,
just "download the new file, replace the old one."

## If the workflow fails, or you need a one-off local build

The manual path still works exactly as before:
```
pip install pyinstaller
pyinstaller MixedGamesTracker.spec
```
This creates `dist/MixedGamesTracker.exe` (Windows) or `dist/MixedGamesTracker`
(macOS/Linux) — but only for whatever platform you're building *on*, since
PyInstaller doesn't cross-compile.
