Fonts used by the start screen (one per game tab)

All three families are under the SIL Open Font License 1.1. The license text and copyright line of each is in this folder.
None of them is a game font.

Genshin Impact     genshin-ysabeau-office-800.woff2   Ysabeau Office ExtraBold, Latin subset. Copyright 2023 The Ysabeau Project Authors
                                                      (github.com/CatharsisFonts/Ysabeau). License: OFL-Ysabeau.txt. Chosen as the closest
                                                      OFL match to the flared humanist sans of the game; the one weight is used for all text.
Honkai: Star Rail  hsr-barlow-400, -500, -700, -800   Barlow, Latin subsets. Copyright 2017 The Barlow Project Authors (github.com/jpt/barlow).
                   .woff2                             License: OFL-Barlow.txt. A DIN-style sans.
Zenless Zone Zero  zzz-mona-sans.woff2                Mona Sans variable. Copyright 2022 The Mona Sans Project Authors (github.com/github/mona-sans),
                                                      Reserved Font Name "Mona". License: OFL-Mona-Sans.txt. Substitute for the game's Inpin
                                                      Hongmeng (commercial).

The Ysabeau Office and Barlow files are the unmodified Latin subsets published by the Fontsource project
(npm packages @fontsource/ysabeau-office and @fontsource/barlow, version numbers in THIRD_PARTY_NOTICES.md at the repository root).

To use a real game font later: add an @font-face for it in css/app.css and put its family name first in --f-genshin, --f-hsr or --f-zzz.
Do not publish or redistribute a game font file; the game's publisher owns it.
