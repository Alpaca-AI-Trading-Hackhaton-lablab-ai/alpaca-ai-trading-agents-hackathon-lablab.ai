"""Scraped-article condensing (no network)."""

import unittest

from services import news_service

# Shape of a real Tavily result: nav chrome, then one paragraph of signal,
# then footer chrome.
SCRAPED = """LoadingImage 11

 History record

 Latest News

 Quotes  More

On Tuesday, Apple rose nearly 2%, closing at $322.59, after John Ternus
officially succeeded Tim Cook as chief executive officer of the company.

 Back to the Top

 Claim Rewards
"""


class CondenseTest(unittest.TestCase):
    def test_drops_nav_chrome(self):
        out = news_service._condense(SCRAPED)
        self.assertIn("Apple rose nearly 2%", out)
        for junk in ("Latest News", "Back to the Top", "Claim Rewards"):
            self.assertNotIn(junk, out)

    def test_caps_length(self):
        long_prose = ("Markets moved sharply on the session as traders "
                      "repriced rate expectations. ") * 40
        out = news_service._condense(long_prose)
        # +1 for the ellipsis appended after the word-boundary cut.
        self.assertLessEqual(len(out), news_service._CONTENT_CHARS + 1)
        self.assertTrue(out.endswith("…"))

    def test_short_article_survives_whole(self):
        """Every line is below the prose floor, but it is a stub, not chrome."""
        out = news_service._condense("SPY flat.\nVolume light.")
        self.assertEqual(out, "SPY flat. Volume light.")

    def test_empty(self):
        self.assertEqual(news_service._condense(""), "")
        self.assertEqual(news_service._condense(None), "")

    def test_clean_condenses_content(self):
        items = news_service._clean([{"title": "T", "content": SCRAPED}])
        self.assertNotIn("Claim Rewards", items[0]["content"])
        self.assertIn("Apple rose nearly 2%", items[0]["content"])


if __name__ == "__main__":
    unittest.main()
