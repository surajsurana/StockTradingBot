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


ALLOWED_CONTROL = {chr(9), chr(10), chr(13)}      # tab, newline, carriage return


def _control_chars(text: str) -> list:
    return sorted({c for c in text if ord(c) < 32 and c not in ALLOWED_CONTROL})


class TestTheLiveSummaryCard(unittest.TestCase):
    """One card with the combined figures, each line opening to the account-by-account split. The
    Classic cards were retired on 2026-10-09 once New had earned it; their markup stays, hidden,
    because the same render pass produces the Today's P&L chip."""

    def test_both_layouts_exist_with_a_switch(self):
        """New is not finalised, so Classic stays until it is."""
        page = _page()
        self.assertIn('id="livecards"', page)
        self.assertIn('id="liveboard"', page)
        self.assertIn('data-view="old"', page)
        self.assertIn('data-view="new"', page)

    def test_exactly_one_layout_is_on_screen(self):
        body = _script(_page())
        body = body[body.index("function applyLiveView()"):]
        body = body[:body.index(chr(10) + "}")]
        self.assertIn('cards.hidden = which === "new"', body)
        self.assertIn('board.hidden = which !== "new"', body)

    def test_classic_is_still_the_default(self):
        self.assertIn('localStorage.getItem(LIVE_VIEW_KEY) || "old"', _script(_page()))

    def test_every_line_can_be_opened(self):
        body = _script(_page())
        body = body[body.index("function renderLiveBoard"):]
        for metric in ("invested", "pnl", "held", "cash", "open", "closed"):
            self.assertIn(f'metric("{metric}"', body, metric)
        self.assertIn('.metric[data-metric]', body)    # the row that opens
        self.assertIn('.sub[data-metric=', body)       # the rows it opens

    def test_the_strategy_counts_lead_to_the_strategies_themselves(self):
        """Classic's counts were clickable and these have to be too -- the NAMES live in the
        Strategies tab, where they can be sorted and filtered. A count you cannot open is a dead
        end, and listing names on a summary card is a table in disguise."""
        body = _script(_page())
        body = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        for counted in ("sactive", "sresearched"):
            self.assertIn(f'navrow("{counted}"', body, counted)
        self.assertIn("showStrategies(JSON.parse(el.dataset.filters))", body)

    def test_the_split_is_never_louder_than_the_line_it_opens(self):
        """The drill-down is subordinate: if its figures are bolder or darker than the line they
        came out of, the eye lands on a part before the whole."""
        page = _page()
        self.assertIn(".potlist .metric > b{", page)
        self.assertIn("font-weight:600;color:var(--ink)", page)
        sub = page[page.index(".potlist .sub em{"):]
        sub = sub[:sub.index("}")]
        self.assertIn("font-weight:400", sub)
        self.assertIn("color:var(--muted)", sub)

    def test_today_is_not_repeated_on_the_card(self):
        """It already has a chip of its own in the top right; two copies of one number invite the
        reader to check whether they agree."""
        body = _script(_page())
        body = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        self.assertNotIn('metric("today"', body)

    def test_the_counts_are_not_in_the_money_card(self):
        """Money and counts read differently. A trade count sitting under Total P&L in the same
        column of the same table gets read as money for a moment, every time."""
        body = _script(_page())
        body = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        money_card = body[body.index("All platforms"):body.index(">Positions<")]
        for counted in ('metric("open"', 'metric("closed"', 'navrow("sactive"'):
            self.assertNotIn(counted, money_card, counted)

    def test_a_us_paper_book_is_not_counted_as_kite_money(self):
        """Pool I has no broker. With no desk of its own it fell to poolVenue's default and its
        dollars were added to the Kite account's rupees."""
        body = _script(_page())
        body = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        self.assertIn('us:', body)
        self.assertIn('ALL_VENUES = ["kite", "coindcx", "groww", "us"]', body)

    def test_the_card_does_not_invent_its_own_numbers(self):
        """It reads the same ledger rows the Positions table does. A summary built from its own
        source is a second opinion, not a summary."""
        body = _script(_page())
        body = body[body.index("function renderLiveBoard"):]
        body = body[:body.index("function applyLiveView")]
        self.assertIn("s.ledger", body)
        # value is holdings PLUS cash -- holdings alone made a flat account look empty
        self.assertIn("(v.held || 0) + (v.cash == null ? 0 : v.cash)", body)


class TestHidingActuallyHides(unittest.TestCase):
    """CSS `display:` on an element BEATS the [hidden] attribute, so setting hidden does nothing at
    all. It has bitten twice: .profilemenu's display:flex kept the Settings menu open however many
    places called close(), and .cards' display:flex kept the Classic summary on screen in the New
    view. Any selector that sets display needs a [hidden] partner."""

    def test_every_display_rule_has_a_hidden_partner(self):
        page = _page()
        for selector in (".profilemenu", ".cards", ".tickerrow", ".todaybar"):
            if f"{selector}{{display:" in page or f"{selector}{{display:" in page.replace(" ", ""):
                self.assertIn(f"{selector}[hidden]{{display:none}}", page, selector)

    def test_a_table_inside_a_card_overrides_the_global_min_width(self):
        """table{min-width:640px} exists so the big data tables scroll rather than crush. A table
        inside a card inherits it and runs straight past the card's edge -- the numbers get drawn
        outside the box."""
        self.assertIn(".pottbl{width:100%;min-width:0;", _page())

    def test_the_two_that_already_broke_are_covered(self):
        page = _page()
        self.assertIn(".profilemenu[hidden]{display:none}", page)
        self.assertIn(".cards[hidden]{display:none}", page)


class TestNoStrayControlCharacters(unittest.TestCase):
    r"""Twice in one session an escape meant for JavaScript was eaten by the tool writing the file:
    a newline escape became a real line break, which is a syntax error, and a backslash-b became a
    literal backspace -- 0x08, sitting inside a regular expression.

    The second one PARSED FINE and simply never matched, so every ledger row fell through to the
    default venue and one card reported another card's money. A character you cannot see is the
    worst kind of wrong answer, and node --check does not catch it.
    """

    def test_the_page_holds_no_control_characters(self):
        bad = _control_chars(_page())
        self.assertEqual(bad, [], f"control characters in the page: {[hex(ord(c)) for c in bad]}")

    def test_the_check_actually_fires(self):
        # a guard nobody has seen fail is a guard nobody knows is working
        self.assertEqual(_control_chars("const venue" + chr(8) + "Of"), [chr(8)])


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
