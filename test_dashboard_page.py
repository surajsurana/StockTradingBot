"""
The dashboard page itself must PARSE, and must not reference elements it no longer has.

Why this exists. On 2026-10-08 two separate edits shipped a page that loaded and then did nothing:
first a renderer still wrote to a ticker row the layout no longer had, so load() threw "Cannot set
properties of null" and every tab came up blank; then a stray newline inside a string literal broke
the whole script, so not one function was defined. The Python suite was fully green through both.

A dashboard is code. These are the two cheapest checks that would have caught each of them, and they
need nothing but the file.

    python test_dashboard_page.py
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "index.html")


def _page() -> str:
    with open(PAGE, encoding="utf-8") as f:
        return f.read()


def _script(page: str) -> str:
    return page[page.index("<script>") + len("<script>"):page.rindex("</script>")]


class TestThePageScriptParses(unittest.TestCase):
    def test_node_can_parse_it(self):
        """A syntax error anywhere in the page's script leaves EVERY function undefined, so the page
        renders its empty shell and nothing else -- which looks like a data problem, not a code one.
        Skipped where node is unavailable rather than silently passing."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH")
        path = os.path.join(tempfile.mkdtemp(), "page.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_script(_page()))
        done = subprocess.run([node, "--check", path], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[:800])

    def test_node_is_available_to_run_that_check(self):
        """The parse check above is the only thing standing between a typo and a blank dashboard, and
        it skips silently without node. This fails loudly instead, so the guard cannot quietly stop
        guarding. (A quote-balance heuristic was tried and dropped -- it cannot tell an apostrophe
        inside a string from an unterminated one, and a check that cries wolf gets deleted.)"""
        self.assertIsNotNone(shutil.which("node"),
                             "node is needed to syntax-check dashboard/index.html")


class TestEveryElementItTouchesExists(unittest.TestCase):
    def test_no_renderer_writes_to_an_element_that_was_removed(self):
        """getElementById(...) returning null is a TypeError the moment anything is assigned to it,
        and it aborts the whole of load() -- so one stale id blanks every tab, not just its own."""
        page = _page()
        ids = set(re.findall(r'id="([^"]+)"', page))
        # created at runtime by the research tab's own filter builders, never present in the source
        built_at_runtime = {"queue-lane", "res-lane"}
        referenced = set(re.findall(r'getElementById\("([^"]+)"\)', page))
        missing = sorted(referenced - ids - built_at_runtime)
        self.assertEqual(missing, [], f"referenced but not in the page: {missing}")

    def test_every_destination_has_a_section_to_show(self):
        page = _page()
        # the JS builds some selectors by interpolation; only literal destinations are checked
        tabs = {t for t in re.findall(r'data-tab="([^"]+)"', page) if '$' not in t}
        sections = set(re.findall(r'<section id="tab-([^"]+)"', page))
        self.assertEqual(sorted(tabs - sections), [], "destinations with no section")

    def test_the_markets_tab_is_gone_and_the_board_replaced_it(self):
        page = _page()
        self.assertNotIn('data-tab="markets"', page)
        self.assertNotIn('<section id="tab-markets"', page)
        self.assertIn('id="boardtrack"', page)

    def test_the_profile_menu_can_actually_be_hidden(self):
        """.profilemenu sets display:flex, which beats the [hidden] attribute's display:none -- so
        hiding it did nothing at all in a real browser, every time, and the menu never closed."""
        self.assertIn(".profilemenu[hidden]{display:none}", _page())


if __name__ == "__main__":
    unittest.main()
