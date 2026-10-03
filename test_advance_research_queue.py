"""advance_research_queue.py -- the weekly, deterministic cron that advances the research queue."""
import tempfile
import unittest
from unittest.mock import MagicMock, patch

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
        msg = message({}, "Coffee Can Portfolio", "swing", "A decade-consistency quality-growth screen.", "india")
        self.assertIn("Coffee Can Portfolio", msg)
        self.assertIn("swing", msg)
        self.assertIn("Research queue", msg)

    def test_message_explains_paper_direct_mode(self):
        msg = message({"mode": "paper_direct"}, "Value (Earnings Yield)", "swing", "Buys cheap stocks.", "india")
        self.assertIn("can't get a real historical backtest", msg)
        self.assertIn("paper-trading proposal", msg)

    def test_message_labels_non_india_lanes(self):
        msg_india = message({}, "X", "swing", "Y.", "india")
        msg_crypto = message({}, "X", "crypto", "Y.", "crypto")
        msg_us = message({}, "X", "swing", "Y.", "us")
        self.assertNotIn("(india)", msg_india)   # india stays unlabeled -- the original, unprefixed queue
        self.assertIn("(crypto)", msg_crypto)
        self.assertIn("(us)", msg_us)

    def test_run_lane_against_the_real_roadmap_for_each_of_the_three_lanes(self):
        # All three lanes have a real researchable candidate as of 2026-10-03 (crypto and us both via
        # merged Discovery Scout PRs #2 and #3) -- confirms run_lane() advances each lane against the
        # REAL roadmap without crashing, and that each lane writes to its own queue file rather than
        # treading on another's. No real Telegram send -- send=False throughout.
        #
        # IMPORTANT: run_lane() also calls sync_from_github()/publish_snapshot(), which make REAL
        # GitHub API calls and (worse) a REAL `git commit`+`push` against advance_research_queue.py's
        # own REPO_DIR -- this bit a real run once already (three genuine "automated update" commits
        # landed on origin/main the first time this test existed, unmocked). Both must stay mocked
        # here, permanently, so a test run can never again write to the real repo or hit the real API.
        import advance_research_queue
        from advance_research_queue import run_lane
        with patch("requests.get") as mock_get, \
             patch("subprocess.run") as mock_run, \
             patch.object(advance_research_queue, "REPO_DIR", tempfile.mkdtemp()):
            mock_get.return_value = MagicMock(status_code=404, json=lambda: [])   # no branch/PR found, anywhere
            mock_run.return_value = MagicMock(returncode=0)   # `git diff --cached --quiet`: nothing staged
            picked = {}
            for lane in ("india", "crypto", "us"):
                d = tempfile.mkdtemp()
                run_lane(lane, d, send=False, token="", chat_id="")   # must not raise
                data = load(d, lane=lane)
                self.assertIsNotNone(data["current"], lane)
                picked[lane] = data["current"]["key"]
            self.assertEqual(len(set(picked.values())), 3)   # three lanes, three different candidates
        # each lane picks ITS OWN top-ranked candidate -- derived from the real roadmap rather than
        # hardcoded, so adding a candidate changes the expectation automatically instead of failing here
        for lane, key in picked.items():
            self.assertEqual(key, build_roadmap(lane=lane)["researchable_now"][0].candidate.key, lane)
        mock_run.assert_called()   # confirms the git path was actually exercised, just safely mocked
        self.assertFalse(any(call.args[0][:2] == ["git", "push"] for call in mock_run.call_args_list))   # returncode=0 on the diff-check means no commit was ever attempted -- confirms that, not just that push is mocked

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
