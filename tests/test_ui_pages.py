"""The pages (pulls_tracker_ui) in a real browser: Chromium through Playwright, sample mode (no Python backend), no network.

Skipped when Playwright or its Chromium is not installed (`pip install playwright` and `playwright install chromium`): the program needs
neither. They cover what the Python tests cannot see: an exception in a page script, and text such as "NaN" on a screen.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "pulls_tracker_ui"
NOW = "now=2026-10-25T12:00:00Z"           # inside the banner windows of the snapshot js/banners.js
BAD = re.compile(r"(?<![A-Za-z])NaN|undefined|\[object|\bnull\b|Infinity")      # NaN also inside "NaNm" (a duration of NaN minutes)

try:
    from playwright.sync_api import sync_playwright
except ImportError:      # pragma: no cover
    sync_playwright = None


def snapshot() -> dict:
    text = (UI / "js" / "banners.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"window\.PT_BANNERS\s*=\s*(\{.*\})\s*;?\s*$", text, re.S).group(1))


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:      # noqa: BLE001 - Chromium not installed for this Playwright
            cls.pw.stop()
            raise unittest.SkipTest(f"Chromium is not available: {type(e).__name__}") from e

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, query, banners=None, clock=False, settings=None):
        """A page of index.html; `banners`: the data of js/banners.js to use instead of the snapshot. Errors of the page collect in self.errors."""
        page = self.browser.new_page(viewport={"width": 1280, "height": 860})
        self.addCleanup(page.close)
        self.errors = []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and self.errors.append(m.text))
        if banners is not None:
            body = "window.PT_BANNERS = " + json.dumps(banners) + ";"
            page.route("**/js/banners.js", lambda r: r.fulfill(status=200, content_type="application/javascript", body=body))
        page.route("http*://**", lambda r: r.abort())                  # the pages never need the network; a picture that is missing is fine
        if settings is not None:                                       # what the sample provider keeps in localStorage
            page.add_init_script("localStorage.setItem('pt.v1.settings', " + json.dumps(json.dumps(settings)) + ")")
        if clock:
            page.clock.install()
        page.goto((UI / "index.html").as_uri() + "?" + query)
        page.wait_for_function("window.PT_APP && window.PT_UI")
        page.wait_for_timeout(400)
        return page

    @staticmethod
    def settle(page):
        page.wait_for_function("document.getElementById('view').getAttribute('aria-busy') !== 'true'")
        page.wait_for_timeout(100)

    def test_every_screen_draws_without_an_error_or_a_NaN(self):
        page = self.open(f"state=synced&{NOW}")
        for game in ("genshin", "hsr", "zzz"):
            page.click(f'.tab[data-game="{game}"]')
            page.wait_for_timeout(300)
            for screen in ("Dashboard", "Banners", "History", "Stats"):
                page.click(f'.nav-btn[data-screen="{screen}"]')
                self.settle(page)
                if screen == "Banners":
                    for tab in ("upcoming", "past", "current"):
                        page.click(f"#bt-{tab}")
                        self.settle(page)
                text = page.evaluate("document.body.innerText")
                self.assertIsNone(BAD.search(text), f"{game} {screen}: {BAD.search(text) and text[max(0, BAD.search(text).start() - 40):BAD.search(text).end() + 40]!r}")
            page.click('.nav-btn[data-screen="Dashboard"]')
            self.settle(page)
        self.assertEqual(self.errors, [])

    def test_a_live_banner_without_an_end_date_says_so_in_its_detail(self):
        data = snapshot()
        for s in data["slides"]["genshin"]:
            s["end"] = None                                           # "End not announced" is a state the pages are written for
        page = self.open(NOW, banners=data)
        page.click('.nav-btn[data-screen="Banners"]')
        page.wait_for_selector(".bcard-open")
        page.locator(".bcard-open").first.click()
        page.wait_for_timeout(500)
        detail = page.locator(".bdet-dl").first.inner_text()
        self.assertIsNone(BAD.search(detail), detail)
        self.assertIn("end not announced", page.locator(".bdet-dl dd").first.inner_text().lower())         # the Status row
        self.assertEqual(self.errors, [])

    def test_a_game_without_any_banner_raises_no_error_when_the_timers_fire(self):
        data = snapshot()
        data["slides"]["genshin"] = []                                # the first game shown has no banner: state.slide stays empty
        page = self.open(NOW, banners=data, clock=True)
        self.assertTrue(page.locator("#empty").is_visible())
        page.clock.run_for(65_000)                                    # the Main screen redraws its time and pager every 30 s
        self.assertEqual(self.errors, [])

    def test_reset_of_a_loss_pool_longer_than_the_default_leaves_no_undefined_name(self):
        pool = [f"Name {i}" for i in range(9)]                          # the settings file keeps up to 30 names; the default list has 7
        page = self.open(NOW, settings={"lossPool": pool})
        page.click('.nav-btn[data-screen="Settings"]')
        page.get_by_role("button", name="Edit the 7 characters in your pool").click()
        page.get_by_role("button", name="Reset to default").click()
        values = page.locator(".pool input").evaluate_all("els => els.filter(e => e.isConnected).map(e => e.value)")
        self.assertEqual(values[:2], ["Himeko", "Welt"])
        self.assertNotIn("undefined", values)
        self.assertEqual(len(values), 7)
        page.get_by_role("button", name="Apply").click()
        page.wait_for_timeout(200)
        self.assertIn("7 characters", page.locator("#mp-settings").inner_text())
        self.assertEqual(self.errors, [])

    def test_the_whole_top_bar_moves_the_window_except_its_buttons(self):
        # pywebview starts a drag for every element inside a drag region (class pywebview-drag-region), so the region is a layer under the
        # bar and the buttons are not inside it. What is under the pointer decides: this checks that for the empty parts and the buttons.
        page = self.open("frameless=1")
        page.wait_for_selector("body[data-frameless]")
        hits = page.evaluate("""() => {
          const g = (s) => document.querySelector(s).getBoundingClientRect();
          const bar = g('.topbar'), y = bar.top + bar.height / 2;
          const tabs = [...document.querySelectorAll('.tab')].map((t) => t.getBoundingClientRect()).filter((r) => r.width > 0);
          const at = (x) => { const el = document.elementFromPoint(x, y); return !!(el && el.closest('.pywebview-drag-region')); };
          return { gap: at(g('.tabs').left + 3), afterTabs: at(tabs[tabs.length - 1].right + 40), beforeButtons: at(g('.winctl').left - 30),
                   brand: at(g('.brand').left + g('.brand').width / 2), tab: at(tabs[0].left + tabs[0].width / 2),
                   minimize: at(g('.wc-min').left + 20), close: at(g('.wc-close').left + 20),
                   buttonsAtTheRightEdge: Math.round(bar.right - g('.wc-close').right) === 0 };
        }""")
        self.assertEqual(hits, {"gap": True, "afterTabs": True, "beforeButtons": True, "brand": False, "tab": False, "minimize": False, "close": False,
                                "buttonsAtTheRightEdge": True})
        page.click("#tab-hsr")                                          # a click on a tab still selects it
        self.assertEqual(page.get_attribute("#tab-hsr", "aria-selected"), "true")
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
