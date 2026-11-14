# Privacy and data

## What the program stores

Everything the program writes is in the `data` folder next to `AquariaWish.exe`. When that folder cannot be written (for example in `C:\Program Files`), the program uses `%LOCALAPPDATA%\PullsTracker\data`. Settings > Privacy and storage shows the folder.

| In `data` | Holds |
|---|---|
| `pulls.sqlite` | Accounts (game, UID, region, UTC offset, nickname, last sync time, language), pulls (ID, banner type, item, name, rarity, time and the raw record the history service returned) and the banner archive. After an update, a copy named `pulls.sqlite.before-format-N` |
| `backups/` | Copies of the database. The newest 10 are kept |
| `settings.json`, `window.json`, `rules.json` | Settings, window size and position, optional rules |
| `pulls_tracker.log`, `selfcheck.txt` | The log and the result of `--selfcheck`. They hold folder paths, host names and, for each sync, how many pulls the service sent per banner type and how long ago the newest was made. The link key is never written to the log |
| `assets/art/`, `js/banners.js` | Banner data and art downloaded from the hosts listed in the README ("Network access and APIs") |
| `webview/` | Browser storage of the WebView2 engine |

The program does not ask for a game or HoYoverse password. The optional HoYoLAB cookie (Settings > Data sources) is the only item stored outside this folder. It goes to Windows Credential Manager through the `keyring` package, and never to the settings file or the log. Without the package the program does not store it. No part of the program reads the cookie yet.

The program changes nothing in the registry, and it does not change game files.

## What leaves the computer

- **Banner and image requests** go to the third-party hosts in the README. Those hosts see your IP address and the User-Agent `banner-tracker-test/0.4`. The requests carry no account data.
- **History requests** go to the game's history service, the `public-operation-*` hosts in the README. They run when you click Sync now, and at start for a game whose full history is stored and whose saved link is younger than 24 hours. Each request carries the temporary link key (`authkey`) that the game saved in its web cache, the banner type, the page size and the page, and `lang=en` when the link uses another language. The User-Agent is `Mozilla/5.0 (Windows NT 10.0; Win64; x64) PullsTracker`. Only hosts under `hoyoverse.com`, `mihoyo.com` and `hoyolab.com` that match the game are accepted, and redirects are not followed. The program reads the key from the cache file, keeps it in memory for the sync, and does not save it in the database, in the settings or in the log. The key stops working after a while. A link older than about a day is probably expired.
- **Downloaded art** is cached in `data/assets/art/<game>/`. When a host does not answer, the program keeps using the last-good banner file (`data/js/banners.js`).
- **The WebView2 runtime** is Microsoft software with its own network behavior. The program opens the WebView2 download page only when you choose Yes in the start-up message.
- **Nothing else.** The program has no analytics or telemetry and uploads no pulls, UIDs or database files. Pulls and UID go to HoYoverse only as part of the history requests, because the service returns them to you.

## What you control

- **UID on screen.** Settings > Privacy and storage has "Mask the UID on screens and in exports to images".
- **Files you create.** A UIGF or CSV export contains your UID and pulls. It is written only where you choose. Do not post it in public if you do not want your UID to be public.
- **Logs in a bug report.** Read `pulls_tracker.log` before you post it. It holds folder paths, host names and counts of the pulls the service sent.
- **The link key.** Anyone who has a valid history link key can read that account's pull history until the link expires. Do not post the saved link or the cache file.
- **Image cache.** Settings > Privacy and storage > Clear image cache removes the downloaded pictures.
- **Removal.** Close the program and delete its folder (and `%LOCALAPPDATA%\PullsTracker` if the program used it). Delete the HoYoLAB cookie in Windows Credential Manager if you saved one.

This page describes version `3335db7` of the program. It is not a legal privacy policy, and the services the program contacts have their own.
