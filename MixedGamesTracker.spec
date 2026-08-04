# PyInstaller build recipe - produces a single double-clickable file that
# needs no Python installed on the other end.
#
#   pip install pyinstaller
#   pyinstaller MixedGamesTracker.spec
#
# The result lands in dist/. Build on the platform you're shipping to -
# PyInstaller doesn't cross-compile, so a Windows .exe has to be built on
# Windows.
#
# A .spec is used rather than a plain command line because the templates and
# static folders have to be bundled, and the --add-data separator differs
# between Windows and everywhere else.

block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    # Flask reads these from disk at runtime, so they have to travel with the
    # executable rather than being compiled in.
    datas=[
        ("templates", "templates"),
        ("static", "static"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MixedGamesTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Keep the console window: it shows the local URL and any startup error,
    # and closing it is how you quit the app.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
