# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec of Pulls Tracker: ONE FOLDER build (not one file).

    python -m PyInstaller --noconfirm --clean pulls_tracker.spec        (or run build_exe.bat)

Result:  dist/AquariaWish - Pulls Tracker/AquariaWish.exe     the program (no console window)
         dist/AquariaWish - Pulls Tracker/_internal/           Python, libraries and the interface (pulls_tracker_ui), read only
         dist/AquariaWish - Pulls Tracker/data/                made by the program at the first start: settings, pulls, downloaded art, log, browser storage

Why a folder and not one file: a one-file build unpacks itself into a temporary folder at every start (slow, and antivirus programs
distrust it), and its "folder of the .exe" is not where the user keeps things. With the folder build everything the program writes goes in
data/ next to the .exe, so the whole folder can be moved, copied or backed up.

Set PT_CONSOLE=1 to build with a console window (to see errors while testing).
Put pulls_tracker.ico (your own icon: python make_icon.py my_design.png makes it) next to this file. The build then
  1. embeds it in AquariaWish.exe, which is the icon Explorer, the desktop and the Start menu show for the file, and
  2. copies it into the bundle, where the program reads it to give the running window and its taskbar button the same icon.
Without the file the build works as before, with the default icons.
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

HERE = Path(SPECPATH)
WINDOWS = sys.platform == "win32"
CONSOLE = os.environ.get("PT_CONSOLE") == "1"
ICON = HERE / "pulls_tracker.ico"

datas = [(str(HERE / "pulls_tracker_ui"), "pulls_tracker_ui")]          # the interface, read only (paths.ui_dir)
if ICON.is_file():
    datas.append((str(ICON), "."))          # the same icon file inside the bundle: the window and its taskbar button use it (pt/appicon.py)
binaries = []

# Loaded by name or from inside functions, which the import scan can miss.
hiddenimports = ["banner_tracker_test", "pt", "pt.window", "pt.loader", "pt.tasks", "pt.banners", "pt.log", "pt.paths", "pt.winsize", "pt.winpatch", "pt.appicon", "pt.selfcheck", "pt.api", "pt.tracker", "pt.store", "pt.engine", "pt.games", "pt.pulls", "pt.settings", "pt.locate", "pt.client", "pt.sync", "pt.vault", "pt.single", "pt.preflight", "sqlite3"]

# pywebview: its scripts, and on Windows the WebView2 files (WebView2Loader.dll, Microsoft.Web.WebView2.*.dll) of webview/lib.
datas += collect_data_files("webview", subdir="js")
if WINDOWS:
    datas += collect_data_files("webview", subdir="lib")
    binaries += collect_dynamic_libs("webview")
    hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr", "clr_loader", "pythonnet", "proxy_tools", "bottle"]
    for pkg in ("clr_loader", "pythonnet"):          # the .NET bridge: its DLLs and data files
        try:
            datas += collect_data_files(pkg)
            binaries += collect_dynamic_libs(pkg)
            hiddenimports += collect_submodules(pkg)
        except Exception as e:      # noqa: BLE001
            print(f"warning: could not collect {pkg}: {e}")

# keyring (the HoYoLAB cookie, pt/vault.py) is optional: when it is installed its backends are collected; without it the program runs and
# does not store the cookie.
try:
    import keyring      # noqa: F401
    hiddenimports += collect_submodules("keyring.backends")
    datas += copy_metadata("keyring")
except Exception as e:      # noqa: BLE001
    print(f"note: keyring is not in this build ({type(e).__name__}); the HoYoLAB cookie cannot be stored")

# Not used on Windows: the other pywebview backends and everything they would pull in.
excludes = ["PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi", "tkinter", "cefpython3", "wx", "numpy", "PIL", "matplotlib", "pytest"]
# Build tools that the release of 3335db7 embedded in the .exe (setuptools, PyInstaller's own modules, pefile, altgraph, packaging). The program does not import
# them. Leaving them out also keeps their licenses out of THIRD_PARTY_NOTICES.md. After a build, --selfcheck (run by build_exe.bat) must still say ALL OK.
excludes += ["setuptools", "pkg_resources", "PyInstaller", "pefile", "altgraph", "packaging"]

a = Analysis(
    [str(HERE / "pulls_tracker.py")],
    pathex=[str(HERE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # the folder build: libraries stay beside the .exe (in _internal)
    name="AquariaWish",
    console=CONSOLE,
    icon=str(ICON) if ICON.is_file() else None,
    upx=False,                      # UPX makes antivirus false alarms more likely
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AquariaWish - Pulls Tracker")
