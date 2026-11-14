# Files

| Path | What it is |
|---|---|
| `pulls_tracker.py` | entry point |
| `pt/window.py` | the window, the switch from loading panel to main screen, `WindowApi` (the methods the page can call) |
| `pt/loader.py` | the order of the load tasks and the runner that reports progress to the panel |
| `pt/tasks.py` | the load task functions |
| `pt/banners.py` | banner and asset engine of the tasks (uses `banner_tracker_test.py`) |
| `pt/log.py` | log file `data/pulls_tracker.log` |
| `pt/paths.py` | where the program finds the interface and keeps its data (source or PyInstaller build) |
| `pt/selfcheck.py` | `--selfcheck`: checks folders, interface files, modules and WebView2, writes `data/selfcheck.txt` |
| `pt/winsize.py`, `pt/winpatch.py` | edge resizing on Windows; the pywebview/WebView2 handler change |
| `pt/api.py` | `PullsApi`: the data methods the screens call (state, dashboard, history, stats, settings, import, export); `WindowApi` adds the window methods |
| `pt/tracker.py` | the service behind them: derives pity, guarantee and results from the stored pulls, answers the screens' queries, import and export |
| `pt/store.py` | the SQLite pull database `data/pulls.sqlite` (accounts, pulls, banner history); its format number and the migration steps |
| `pt/engine.py` | pity, guarantee state machine, luck grade, statistics (pure functions) |
| `pt/games.py` | the rules of the three games as data (banner groups, gacha codes, pity caps, servers) |
| `pt/pulls.py` | reading pulls from API items, UIGF v4.0 and CSV files, writing UIGF v4.0 and CSV |
| `pt/settings.py` | `data/settings.json`: defaults, validation, atomic saving |
| `pt/locate.py` | finds the game folder, its web cache file and the saved links in it (no network) |
| `pt/client.py` | requests to the game's history service: pages, waiting after "too frequently", cancel (the network call is a parameter) |
| `pt/sync.py` | the sync: the four steps, the UID check, merging into the database, the first-run check, the sync at start |
| `pt/vault.py` | the HoYoLAB cookie in the system keyring (optional `keyring` package) |
| `pt/single.py`, `pt/preflight.py` | one copy per data folder; the checks and messages at the start (WebView2, database format, a start that fails) |
| `pulls_tracker.spec`, `build_exe.bat`, `build_exe.sh`, `requirements-build.txt`, `dist_README.txt` | the PyInstaller build (see Packaging) |
| `.gitignore` | keeps `data/` (your pulls and UID), `dist/`, `build/`, `.venv-build/`, caches and logs out of the repository |
| `LICENSE`, `THIRD_PARTY_NOTICES.md` | the MIT License of the program's own code, and the notices and license texts of the third-party software, fonts, icons and data. The build scripts copy both next to `AquariaWish.exe` |
| `pulls_tracker_ui/fonts/`, `pulls_tracker_ui/licenses/` | the fonts (SIL Open Font License 1.1) with their license texts (the Philosopher text is in `licenses/`), and the icon license texts |
| `pulls_tracker_ui/` | the interface (HTML, CSS, JS); see its README |
| `banner_tracker_test.py` | banner fetcher that writes `pulls_tracker_ui/js/banners.js` and the art |
| `tests/` | tests of the window, loader, database, sync, accounts and backups, no screen needed: `python -m unittest discover -s tests` |
| `data/` | created at the first run; everything the program writes goes here: `js/banners.js`, `assets/art/`, `assets/loading/` (your loading banner), `webview/`, `window.json`, `settings.json`, `pulls.sqlite`, `backups/`, `rules.json` (optional), `pulls_tracker.log`, `selfcheck.txt`, `.instance.lock` (empty; the one-copy guard on Linux and macOS) |

