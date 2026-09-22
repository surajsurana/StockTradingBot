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


if __name__ == "__main__":
    unittest.main()
