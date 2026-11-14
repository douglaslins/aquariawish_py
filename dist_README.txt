Pulls Tracker
=============

Start:      AquariaWish.exe
First time: 1. Extract the whole zip into a folder (do not start the program from inside the zip).
            2. If Windows shows "Windows protected your PC": the program has no publisher signature, which is what triggers this.
               Click "More info", then "Run anyway". To avoid the message, right click the zip before extracting, choose Properties and
               tick "Unblock" (if that box is there).
            3. An antivirus program may check the first start. The program does not need administrator rights.

License:    LICENSE.txt (MIT) covers the program's own code, icon and logo banner. THIRD_PARTY_NOTICES.md lists the other software, fonts,
            icons and data in this folder, with their licenses.
Game content: Game content and materials are trademarks and copyrights of their respective publisher and its licensors. Genshin Impact,
            Honkai: Star Rail and Zenless Zone Zero: (c) All rights reserved by miHoYo. (c) COGNOSPHERE. All rights reserved. "HoYoverse" is a
            trademark of COGNOSPHERE PTE. LTD. Other properties and any right, title, and interest thereof and therein (intellectual property
            rights included) not derived from these games belong to their respective owners. As fan wikis do, this program claims no license
            for the game images. It asserts that showing them to identify banners and items is fair use, or the equivalent exception under
            applicable law. HoYoverse has not licensed or approved this.
Fan project: Unofficial, not affiliated with, endorsed by or sponsored by HoYoverse or COGNOSPHERE. It gives no advantage in the games: it
            does not start, automate or change them, and it only reads the files they save and your own pull history. The author's
            understanding is that this is within HoYoverse's rules on third-party tools, which are about fairness and account security;
            HoYoverse has not confirmed it. Use it at your own risk and check the terms of service of each game.
Privacy:    Pulls, UID and settings stay on this computer, in the "data" folder. The program has no analytics. The temporary key of the
            history link is sent only to the game's history service and is never saved. More: https://github.com/douglaslins/aquariawish_py
            (README, "Network access and APIs", and docs/privacy-and-data.md).
Keep together: AquariaWish.exe, the "_internal" folder and the "data" folder. The program reads its interface from "_internal" and keeps
            everything it writes in "data" (settings, pulls, downloaded banner art, backups, the log pulls_tracker.log, browser storage).
            Moving or copying the whole folder moves the program with its data.
Where to put it: a folder you can write to, for example D:\PullsTracker or C:\Users\<you>\PullsTracker.
            - Not inside OneDrive, Dropbox, Google Drive or similar. A sync program can lock or rewrite the database while the program
              runs. On many computers Desktop and Documents are inside OneDrive: check the folder path. Settings > Privacy and storage
              shows the data folder and warns when it is inside one of these.
            - In C:\Program Files the program cannot write "data" there and uses %LOCALAPPDATA%\PullsTracker\data instead.
One copy at a time: a second start on the same folder shows "already running" and closes. Two copies of the program in two different
            folders each have their own data.

Updating:   Close the program. In the new version's folder, take AquariaWish.exe and the "_internal" folder and copy them over the old
            ones. Do not replace or delete "data": it holds your pulls. A pull database from an older version is brought up to date on the
            first start and a copy of the old file is kept next to it (pulls.sqlite.before-format-N). An older program that finds a database
            from a newer version shows a message and does not change it.
Backups:    Settings > Privacy and storage > Back up now (the program also backs up before a sync, before an account is removed and before an
            import that replaces pulls). To keep your pulls when moving to another computer, copy the "data" folder or use Export.
Removing:   Close the program and delete the folder. If it used %LOCALAPPDATA%\PullsTracker, delete that folder too. With the WebView2
            engine the program only reads the registry (it writes nothing there). The only item kept elsewhere is the optional HoYoLAB
            cookie in Windows Credential Manager, if you save one in Settings (it needs the "keyring" package in the build; without it
            the cookie is not stored at all).
The game:   The program only reads the game's saved web cache to find the history link. It does not change game files.

Your loading banner (573 x 230 px): data\assets\loading\banner.png (banner.webp, banner.jpg and banner.jpeg also work).
Needs:      Windows 10 or 11 and the Microsoft Edge WebView2 runtime (already there on Windows 11 and on updated Windows 10;
            without it the program says so at the start and offers the download page:
            https://developer.microsoft.com/microsoft-edge/webview2/ ).
If it does not start or shows a blank window:
            1. Run  AquariaWish.exe --selfcheck   It shows what is missing and writes data\selfcheck.txt
            2. Read data\pulls_tracker.log
            When it cannot start, a message says why. Send selfcheck.txt and the log when asking for help. The log does not contain the
            temporary key of the history link.
Options:    --debug      developer tools (F12)
            --perf       measure the window's responsiveness into the log
            --selfcheck  check the installation
