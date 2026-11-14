Fonts used by the start screen (one per game tab)

All three families are under the SIL Open Font License 1.1. None of them is a game font.

Genshin Impact     genshin-philosopher-700-latin.woff2      Philosopher Bold, Latin and Latin Extended subsets. Copyright 2011 The Philosopher Project
                   genshin-philosopher-700-latin-ext.woff2  Authors (github.com/alexeiva/philosopher). License: ../licenses/PHILOSOPHER-OFL.txt.
                                                            A lookalike of the game's font; css/app.css thickens it with a text stroke.
Honkai: Star Rail  hsr-barlow-400, -500, -700, -800.woff2   Barlow, Latin subsets. Copyright 2017 The Barlow Project Authors (github.com/jpt/barlow).
                                                            License: OFL-Barlow.txt. A DIN-style sans.
Zenless Zone Zero  zzz-mona-sans.woff2                      Mona Sans variable. Copyright 2022 The Mona Sans Project Authors
                                                            (github.com/github/mona-sans), Reserved Font Name "Mona". License: OFL-Mona-Sans.txt.
                                                            Substitute for the game's Inpin Hongmeng (commercial).

The Philosopher and Barlow files are the unmodified subsets published by the Fontsource project (npm packages @fontsource/philosopher
and @fontsource/barlow, version numbers in THIRD_PARTY_NOTICES.md at the repository root).

Do not commit or ship a font file whose license you cannot show. D-DIN PRO files with a "CyberFei" or "Datto ... All rights reserved"
copyright line are not covered by the Datto OFL text. To use a real game font on your own computer, add an @font-face for it in
css/app.css and put its family name first in --f-genshin, --f-hsr or --f-zzz, and keep the file out of the repository and out of releases.
