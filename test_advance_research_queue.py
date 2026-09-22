"""advance_research_queue.py -- the weekly, deterministic cron that advances the research queue."""
import tempfile
import unittest

from advance_research_queue import message
from research_queue import load
from swing_research.research_roadmap import build_roadmap


class TestAdvanceResearchQueue(unittest.TestCase):
    def test_advancing_against_the_real_roadmap_picks_the_top_candidate_and_writes_it(self):
        from research_queue import advance
        d = tempfile.mkdtemp()
        roadmap = build_roadmap()
        entry = advance(d, roadmap)
        self.assertIsNotNone(entry)                                   # the real roadmap always has candidates
        self.assertEqual(entry["started_by"], "auto")
        top = roadmap["researchable_now"][0].candidate
        self.assertEqual(entry["key"], top.key)                       # top-ranked, not just any eligible one
        self.assertEqual(load(d)["current"]["key"], top.key)
        self.assertIsNone(advance(d, roadmap))                        # one at a time: a second call does nothing

    def test_message_names_the_candidate_and_its_horizon_lane(self):
        msg = message({}, "Coffee Can Portfolio", "swing", "A decade-consistency quality-growth screen.")
        self.assertIn("Coffee Can Portfolio", msg)
        self.assertIn("swing", msg)
        self.assertIn("Research queue", msg)

    def test_message_explains_paper_direct_mode(self):
        msg = message({"mode": "paper_direct"}, "Value (Earnings Yield)", "swing", "Buys cheap stocks.")
        self.assertIn("can't get a real historical backtest", msg)
        self.assertIn("paper-trading proposal", msg)

    def test_real_roadmap_advance_reaches_every_paper_direct_eligible_candidate_in_its_ranked_turn(self):
        # end-to-end against the real candidate list: paper_direct_eligible candidates interleave into
        # the SAME ranked pool by score (not "all backtest first, then paper_direct") -- work through
        # the whole combined pool and confirm every eligible one is reached, each with the right mode.
        from research_queue import advance, resolve
        d = tempfile.mkdtemp()
        roadmap = build_roadmap()
        want = {s.candidate.key for s in roadmap["paper_direct_eligible"]}
        total = len(roadmap["researchable_now"]) + len(want)
        seen_paper_direct = {}
        for _ in range(total):
            entry = advance(d, roadmap)
            self.assertIsNotNone(entry)
            if entry["mode"] == "paper_direct":
                seen_paper_direct[entry["key"]] = entry["mode"]
            resolve(d, entry["key"], "paper_trading_proposed" if entry["mode"] == "paper_direct" else "researched")
        self.assertEqual(set(seen_paper_direct), want)   # every eligible candidate reached, none skipped
        self.assertIsNone(advance(d, roadmap))            # and now genuinely nothing left


if __name__ == "__main__":
    unittest.main()
