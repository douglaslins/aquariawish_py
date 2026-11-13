# Product

## Register

product

## Platform

web (a local HTML front end for a Python application; opened in a desktop browser or an embedded web view)

## Users

One primary user: a player of Genshin Impact, Honkai: Star Rail and Zenless Zone Zero who tracks pulls (wishes, warps, signal searches) from the game cache and the MiHoYo servers. Context: at a desk, often in the evening, deciding whether the pulls and currency owned will cover a goal on a banner that has a deadline. The task on the Main screen: pick a goal (constellation/eidolon/mindscape tier or weapon rank), enter owned currency and tickets, read how many pulls are still needed.

## Product Purpose

A pulls tracker that answers one question first: "On this banner, how many pulls do I need to reach my goal, and do I have them?" It reads pity and guarantee from the synced history, banners from a calendar source, and shows the result as a worst-case count. Success: the user reads the answer in a few seconds, trusts the numbers, and can reach every other screen from the sidebar.

## Positioning

The only pulls tracker that opens on the current banner and the answer, not on a dashboard of statistics.

## Brand Personality

Calm, exact, game-aware. Each game keeps its own typeface and banner treatment; the shell stays neutral and dark so the banner art is the brightest thing on screen.

## Anti-references

- AI-generated images or icons (never).
- Dashboard-template looks: hero metrics with gradient accents, identical card grids, decorative glass.
- Display fonts on labels and data. One family per game screen.
- Copying a game's UI frames wholesale; the three games share one shell.

## Design Principles

1. The banner is the first read, the goal is the second, the result is the third (IA section 2.6).
2. Neutral dark shell; accent color comes from the active banner's element.
3. One family per game: Genshin, Star Rail and Zenless Zone Zero each use their own typeface on their own tab.
4. Numbers are worst case and say so; estimated or assumed values are labeled.
5. Every state is designed: no history, stale data, missing art, no current banners, ended banner, upcoming banner.

## Accessibility & Inclusion

- WCAG AA contrast (4.5:1 body text, 3:1 large text and UI components).
- Full keyboard use; visible focus; 44 px touch targets where practical.
- Reduced motion respected; motion only shows a state change.
- Color is never the only carrier of meaning: elements have icons and names, results have words.

## Provenance

Written without an interview from: the information architecture and mockups (Pulls_Tracker_IA_and_Mockups.md, revision 3), the banner tracker test runs of 2026-10-23 and 2026-10-24, and the user's messages of 2026-10-24. Confirm or correct.
