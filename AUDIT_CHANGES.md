# Audit changes (v36 -> v36 audited)

Scope: every Python file, the build scripts, the tests and the page code (`pulls_tracker_ui/js`, `index.html`). No file was removed; this one is the only file added.
Result: 250 tests pass (212 before, 38 added; each new test was checked to fail on the old code). ruff shows no new finding in the program code (the new tests add a few unused-lambda-argument notes, the same pattern as the old tests), bandit shows two fewer than before, and ESLint is clean.
Review depth: every Python file was read; the page code was linted (ESLint) and read where it handles API results, errors, the window and the start-up, and tested in headless Chromium, but the drawing code of `app.js`, `views.js` and `panels.js` was not read line by line.
Not run: the Windows-only code (Win32 calls, WebView2 patch, registry, mutex, `start /wait` in `build_exe.bat`). It is covered by tests with fakes only.

## Safety of the user's files
- Nothing deletes or writes outside the data folder, except what you choose: the export file (temporary file in the same folder, removed on failure) and a backup folder named in Settings.
- `clearImageCache` only deletes `<data>/assets/art` and refuses when that is a link to anywhere else, including the data folder itself.
- Backup rotation only deletes files named exactly `pulls-YYYYMMDD-HHMMSS.sqlite` (before: any `pulls-*.sqlite` in the backup folder).
- Redirects of the history service are not followed, and only https addresses are fetched (the fetcher also accepts http for localhost).
- The build scripts delete nothing themselves.

## Fixes by area
- Keychain (`vault.py`): the "no backend" check recognised neither keyring fail backend; a cookie with a missing piece was returned cut.
- Network (`client.py`): no redirects, https only, repeated or non-list pages stop the sync instead of looping, cancel wakes the waits.
- Database (`store.py`): a busy or unreadable database is no longer moved aside as "damaged"; a failed backup leaves no half file; removing an account also removes its "complete history" marks.
- Data (`pulls.py`, `engine.py`, `sync.py`, `tracker.py`): ids and ranks the database cannot hold are skipped, UIGF files with bad accounts or time zones are refused, non-numeric UIDs are refused, CSV cells starting with `= + - @` are written as text (export and history export), the import mismatch check is made on the server, imports are read with a size limit and as UTF-8 only, old or malformed banner data no longer breaks the refresh.
- Window (`window.py`, `winsize.py`, `sync.py`): closing the window during start-up left an invisible process running for up to 60 s (the single-instance lock was already released); a failed resize thread or cursor read no longer blocks or moves the window; `pywebview>=6.0,<7` because the patch uses its internals.
- Page (`js/`): a `#data=` value is checked before it is written into a script tag; dropped files and links are ignored; a failed Python call is shown instead of looking like an empty history or a "works in the desktop application" message; a refused import is no longer reported as "Imported NaN pulls"; a failed settings save keeps the draft; dead code removed.
- Builds: the build uses `.venv-build`, waits for the self check and stops on its failure; `build_exe.bat` has CRLF line endings.
- Tests: every test module clears `PULLS_TRACKER_DATA`, so no test can touch a real data folder.

## Left as it was (your decision)
- `vault.load` (read the stored HoYoLAB cookie) is never called: nothing uses the cookie after it is stored.
- `APP_VERSION` is `0.33` in `pt/pulls.py` (written into exports); the `vNN` numbers of the README are a separate counter.
- `banner_tracker_test.py` is the fetcher and is imported by name; its name matches pytest's `*_test.py` pattern, so a `pytest` run at the project root imports it.
- The UID-to-region table in `games.py` could not be verified here (the sync learns the real offset from the server).
- `pastBanners`, `windowStats` and the true/false methods cannot carry the failure mark, so a failure there still shows as an empty list or a plain false (it is logged).

## Second audit (file by file, six chunks)
Result: 272 tests pass (250 before, 22 added; each new test was checked to fail on the code before the fix). `pyflakes` is clean, `bandit` reports no high-severity item (its medium items are SQL text built from `?` placeholders and a `"0.0.0.0"` version string, not a socket), `node --check` passes on every script, and the number of places that delete, move or run something is the same as before (no new one).
Not run: the Windows-only code, as above. `tests/test_ui_pages.py` (new) needs Playwright with Chromium and is skipped without them; the program needs neither.

Fixed:
- Fetcher (`banner_tracker_test.py`): a redirect to an `http:` or `ftp:` address was followed (only the first address was checked); every redirect is now checked.
- Data folder (`paths.py`): the write probe had one shared name, so a second process could delete it and make the folder look read-only; each process has its own name.
- Game folder (`locate.py`): a folder chosen in Settings that had no web cache was still returned as the game folder when the game logs named a cache.
- Start-up (`loader.py`, `window.py`, `winpatch.py`): an error in the condition of a load task ended the runner before the window opened; a failed open of the main screen closed the window without a message; an unsupported pywebview internal stopped the start over a performance patch.
- Network (`client.py`): `IncompleteRead` and `BadStatusLine` escaped as "internal" errors, and a body shorter than its `Content-Length` was taken as whole.
- Keychain (`vault.py`): a failed delete was reported as done, so a locked keychain left old pieces behind.
- Sync (`sync.py`): a game file removed between the search and the `stat` call raised an "internal" error.
- Import (`pulls.py`): pull times that `strptime` accepts but the screens cannot show (year 1, year 9999) are skipped; JSON nested thousands of levels deep and a CSV field over 128 KB are refused with the import message instead of a Python error.
- History (`tracker.py`): on a 23 or 25 hour day (clock change, Local mode) the "to" date lost its last hour or took the next day's first hour.
- Settings (`api.py`, `tracker.py`): the storage and backup figures failed as a whole when one file was removed while they were measured, which left the Settings screen empty.
- Banner data (`banners.py`): a stored `banners.js` that is valid but of the wrong shape (`"slides": 5`, `"games": []`) made the load tasks fail at every start and was never replaced; it now counts as "nothing stored" and is written again.
- Page: a live banner without an end date showed "ends in NaNm"; a game without banners raised an error every 30 seconds; "Reset to default" in the loss pool wrote `undefined` names when the pool was longer than the default; the time-zone label table did not match the labels of `games.py`.
- Tests: one test of `winsize` could only hang, not fail; it now asserts.

Checked and left as it was (your decision):
- `requirements.txt` has no upper bound for `keyring` and `pyinstaller`, and there is no lock file.
- `api.importPreview` reads any path the page sends (it needs a compromised page; only parse errors come back). *Fixed in the second audit, below.*
- There is no Content-Security-Policy in `index.html` (its effect under WebView2 and `file://` could not be tested here).
- *(Fixed in the second audit, below: an unparsable file is now kept as `settings.json.bad`.)* `settings.load` treats any read error as "use defaults", and the settings and preference writes do not call `fsync`.
- The fetcher keeps its request log and image cache for the life of the process; they are used by one start-up pass, so this is a memory cost only.
- Small items: a stale `.before-format-N` copy is kept after a database format change; the step numbers in the sync log skip after step 3; `selfcheck` prints "Saved in" even when the write failed (fixed in the second audit); `log.setup` is not safe to call twice (fixed in the second audit); `WindowApi.windowGetRect` and `windowSetRect` do not check for a missing window; `tracker.read_banners_js` repeats the fetcher's `load_previous`; the HoYoLAB cookie is stored but `vault.load` is never called (as listed above); a CSV text cell that starts with `'` followed by `=`, `+`, `-` or `@` does not round-trip exactly; `buildSlides` drops a slide whose start is not a date; `loadHist` marks a game as loaded before the request ends, so a failed request is not repeated when you switch games (the main screen keeps showing no history for that game).

# Second audit (v36 audited -> v36 audited 2)

Scope: every file again, by running the code in a temporary data folder. Result: 310 tests pass, 1 skipped (`keyring` not installed); 38 tests added in `tests/test_audit_fixes.py` and 2 adapted in `tests/test_backend.py`. Of the 38 new tests, 32 fail on the code before the fixes (the other 6 check that existing behavior still holds). pyflakes, `node --check` and ESLint show no finding; the headless walk of the pages shows no page error and no request to another host.
Not run: the Windows-only code (the mutex with the real `kernel32`, `build_exe.bat`), only against fakes and by text check.
Safety of the user's files: unchanged finding. Nothing deletes or writes outside the data folder, except what you choose (export file, backup folder). The fixes below make two more guards stricter (build scripts, image-cache clearing).

## Fixes
- `store.py`: only a file SQLite cannot read as a database counts as damaged (other `sqlite3` errors no longer move a healthy file aside); `remove_account` and `update_account` commit all their statements together or none; two damaged copies made in the same second are both kept; a database of a newer format is checked before any write and stays byte for byte as it was.
- `tracker.py`, `paths.py`: `refresh_banners` survives a `banners.js` that cannot be examined and tries the same files again after a busy database; `parse_iso` returns nothing outside the range the program keeps; a backup that disappears after listing no longer stops the next backup; `paths.database_path()` names `pulls.sqlite` in one place.
- `api.py`: `importPreview` reads only the file chosen in the dialog; `clearImageCache` deletes the folder it checked.
- `pulls.py`, `banner_tracker_test.py`: lone surrogates in remote or imported text are replaced (before, one made a whole sync, import, export or `banners.js` write fail); `file_safe` limits names to 100 characters with a hash and avoids Windows device names; a failed write of `banners.js` leaves no `.tmp` file.
- `locate.py`: the authkey and its pieces are not in `repr(Link)`.
- `settings.py`: an unparsable `settings.json` is kept as `settings.json.bad`; a UTF-8 BOM is accepted; a relative `backupFolder` is reset to the default (silently, like the other values `clean` corrects).
- `log.py`, `single.py`, `selfcheck.py`, `window.py`: `log.setup` twice writes each line once; the mutex is closed with the library object that has its argument types; the self check message says when the report could not be saved; a missing pywebview is a `RuntimeError` the start-up handler shows (it was a `SystemExit` that ended the program with no message).
- `index.html`, `js/loading.js`: `data=` is accepted only as `file://...` or a relative path.
- `build_exe.bat`, `build_exe.sh`: stop with exit code 3 when `dist/PullsTracker/data/pulls.sqlite` exists (the build deletes that folder).

## Trade-offs
- `file://server/share/` is accepted for `data=` as well as `file:///`: the program builds that address for a data folder on a network share.
- A relative `backupFolder` typed in Settings is reset to the default without a message (the same way `clean` handles other bad values).

## Left as it was (your decision)
- The HoYoLAB cookie feature (`vault.py`, `client.py`): stored but never read back (`vault.load`). Remove it or keep it.
- `APP_VERSION` is still `0.33` (written into exports).
- The waits in `http_get` cannot be cancelled (a cancel takes effect after the current request).
