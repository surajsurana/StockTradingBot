"""
Mock-based unit tests for research_lab/quant_researcher.py -- same
mocked-call_fn convention already used for test_macro_strategist.py/
test_research_analyst.py. No real Claude calls. Run with:

    python test_quant_researcher.py
"""

import unittest

from research_lab.quant_researcher import (
    build_hypothesis_prompt, parse_hypotheses_response, propose_hypotheses,
)

_FAKE_RESPONSE = """### HYPOTHESIS 1
NAME: Gap Continuation
MECHANISM: Stocks gapping up 1%+ with 2x relative volume in first 15 min continue.
RATIONALE: Overnight information asymmetry resolving via institutional order flow.
RULES: Enter on break of first-15-min high, stop at gap-fill level, target 2x risk.
DISTINCTIVENESS: Unlike ORB, requires an overnight gap as the trigger, not an intraday range.

### HYPOTHESIS 2
NAME: VWAP Reversion
MECHANISM: Price stretched 1.5+ ATR from VWAP by 11am reverts toward VWAP.
RATIONALE: VWAP acts as a fair-value anchor institutional algos trade against.
RULES: Enter when price crosses back toward VWAP after an extreme, stop beyond the extreme, target VWAP.
DISTINCTIVENESS: Mean-reversion around a volume-weighted anchor, not a breakout of a fixed range.
"""


class TestParseHypothesesResponse(unittest.TestCase):
    def test_parses_multiple_hypotheses(self):
        hyps = parse_hypotheses_response(_FAKE_RESPONSE)
        self.assertEqual(len(hyps), 2)
        self.assertEqual(hyps[0].name, "Gap Continuation")
        self.assertEqual(hyps[1].name, "VWAP Reversion")

    def test_extracts_all_fields(self):
        hyps = parse_hypotheses_response(_FAKE_RESPONSE)
        self.assertIn("Overnight information asymmetry", hyps[0].rationale)
        self.assertIn("gap-fill", hyps[0].rules)
        self.assertIn("Unlike ORB", hyps[0].distinctiveness)

    def test_skips_malformed_block_but_keeps_well_formed_ones(self):
        malformed = _FAKE_RESPONSE + "\n### HYPOTHESIS 3\nNAME: Incomplete\n"
        hyps = parse_hypotheses_response(malformed)
        self.assertEqual(len(hyps), 2)  # block 3 missing required fields, skipped

    def test_raises_if_nothing_parses(self):
        with self.assertRaises(RuntimeError):
            parse_hypotheses_response("not a valid response at all")


class TestBuildHypothesisPrompt(unittest.TestCase):
    def test_includes_knowledge_base_history_when_present(self):
        prompt = build_hypothesis_prompt("Prior research history:\n- [SEED-ORB-1] ORB -- REJECT", n=5)
        self.assertIn("SEED-ORB-1", prompt)

    def test_notes_first_batch_when_no_history(self):
        prompt = build_hypothesis_prompt("", n=5)
        self.assertIn("first batch", prompt.lower())

    def test_requests_exactly_n_hypotheses(self):
        prompt = build_hypothesis_prompt("", n=7)
        self.assertIn("exactly 7", prompt.lower())

    def test_includes_cash_equity_only_constraint(self):
        prompt = build_hypothesis_prompt("", n=5)
        self.assertIn("cash", prompt.lower())
        self.assertIn("no futures, no options", prompt.lower())

    def test_research_conclusions_included_as_binding_when_present(self):
        prompt = build_hypothesis_prompt("", n=5, research_conclusions="Single-regime dependency keeps failing.")
        self.assertIn("Single-regime dependency", prompt)
        self.assertIn("binding constraints", prompt.lower())

    def test_no_conclusions_section_when_none_given(self):
        prompt = build_hypothesis_prompt("", n=5, research_conclusions="")
        self.assertNotIn("binding constraints", prompt.lower())


class TestProposeHypotheses(unittest.TestCase):
    def test_uses_provided_call_fn_not_real_claude(self):
        hyps = propose_hypotheses(api_key="fake", n=2, call_fn=lambda p: _FAKE_RESPONSE)
        self.assertEqual(len(hyps), 2)

    def test_knowledge_base_summary_reaches_the_prompt(self):
        captured = {}

        def fake_call(prompt):
            captured["prompt"] = prompt
            return _FAKE_RESPONSE

        propose_hypotheses(api_key="fake", n=2, call_fn=fake_call,
                            knowledge_base_path="/nonexistent/path/for/test.jsonl")
        self.assertIn("Research areas to draw from", captured["prompt"])

    def test_conclusions_reach_the_prompt_when_a_review_exists(self):
        import os
        import tempfile
        from research_lab.knowledge_base import record_conclusion

        fd, conclusions_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.remove(conclusions_path)
        try:
            record_conclusion("Single-regime dependency keeps failing.", ["EXP-001"], path=conclusions_path)
            captured = {}

            def fake_call(prompt):
                captured["prompt"] = prompt
                return _FAKE_RESPONSE

            propose_hypotheses(api_key="fake", n=2, call_fn=fake_call,
                                knowledge_base_path="/nonexistent/path/for/test.jsonl",
                                conclusions_path=conclusions_path)
            self.assertIn("Single-regime dependency", captured["prompt"])
        finally:
            if os.path.exists(conclusions_path):
                os.remove(conclusions_path)


if __name__ == "__main__":
    unittest.main()
