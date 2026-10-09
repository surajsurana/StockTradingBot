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

    def test_value_by_platform_is_holdings_plus_cash_not_invested_plus_total_pnl(self):
        """The arithmetic that is easy to get wrong. Invested + cash + TOTAL P&L double-counts the
        realised part: when a trade closes, that money is already back in the cash balance. Value is
        the open positions at today's price plus the cash -- which is what worth() computes."""
        body = _script(_page())
        body = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        card = body[body.index(">Total Value<"):]
        card = card[:card.index("</div></div>")]
        self.assertIn("worth(vs[k])", card)          # holdings + that account's own cash
        self.assertNotIn("tot.pnl", card)            # never total P&L

    def test_the_total_is_printed_once(self):
        """It is the headline of Total Value. Printing it again on All platforms invites the reader
        to check whether the two agree, which is the one thing a summary must never make them do."""
        body = _script(_page())
        body = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        self.assertEqual(body.count("worth({held: tot.held, cash: tot.cash})"), 1)

    def test_the_cards_are_in_the_order_they_are_read_in(self):
        body = _script(_page())
        body = body[body.index('box.innerHTML = `<div class="boardrow">'):]
        names = re.findall(r'class="nm">([^<]+)<', body[:body.index("</div>`;")])
        self.assertEqual(names, ["Total Value", "All platforms", "Positions", "Strategies",
                                 "Today's P&L"])

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
        # the figure matches its own label's size, so the line reads as one thing
        fig = page[page.index(".potlist .metric > b{"):]
        self.assertIn("font-size:inherit", fig[:fig.index("}")])
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
        money_card = body[body.index(">All platforms<"):body.index(">Positions<")]
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


class TestTheResearchStageIsTheRealOne(unittest.TestCase):
    """"Researching now" was shown for a claim, and a claim is not a run. The page must tell apart
    being written, queued for tonight's backtest, and a backtest actually running."""

    def test_the_three_stages_are_distinct_on_the_page(self):
        body = _script(_page())
        body = body[body.index("const stageChip"):body.index("const queueAction")]
        self.assertIn("c.queue.backtesting", body)     # a live process, not a claim
        self.assertIn("c.queue.implemented", body)     # merged, so tonight's run can have it
        self.assertIn("Being written", body)
        self.assertIn("Queued · backtest", body)

    def test_only_a_running_backtest_is_called_research(self):
        body = _script(_page())
        body = body[body.index("const stageChip"):]
        said = body[:body.index("Researching now")]
        self.assertIn("c.queue.backtesting", said,
                      "'Researching now' must be reached only via backtesting, never via in_progress")


class TestTodaysPnlAddsUpToTheChip(unittest.TestCase):
    """The column and the chip must be the same arithmetic over the same rows, or the reader is
    invited to check whether they agree -- and before this they did not: the chip took its booked
    part from the pool summaries, which have no entry for Pool G at all and read zero for Pool D
    whenever its state file is not stamped today."""

    def test_the_table_has_a_todays_pnl_column(self):
        self.assertIn(chr(34) + "pnl_today" + chr(34) + ",", _page())
        self.assertIn("Today's P&L", _page())

    def test_the_chip_reads_the_same_ledger_rows_the_column_does(self):
        body = _script(_page())
        body = body[body.index("const inToday = a =>"):]
        body = body[:body.index("const openRows")]
        self.assertIn('a.status !== "Open" && inToday(a)', body)   # booked today, off the ledger
        self.assertIn('a.status === "Open" && inToday(a)', body)   # today's move, off the ledger
        self.assertNotIn("realised_today", body)

    def test_nothing_still_takes_the_day_from_the_pool_summaries(self):
        """Both sources at once would count the day twice."""
        body = _script(_page())
        body = body[body.index("function renderLive("):body.index("const openRows")]
        self.assertNotIn("today += +p.realised_today", body)
        self.assertNotIn("todayByKind[kindOfPool[keys[0]]", body)

    def test_the_pnl_toggle_is_gone(self):
        """With a column of its own, flipping the P&L column to today would put two columns headed
        "Today's P&L" side by side."""
        page = _page()
        self.assertNotIn("TAPE_PNL_MODE", page)
        self.assertNotIn("tape-pnl-toggle", page)


class TestTheDayIsACardLikeTheRest(unittest.TestCase):
    """The day used to be the raw chip parked in a column of its own: it read as debris beside four
    cards, and its by-type line -- built for a full-width band -- ran off the side of the page."""

    def test_the_day_card_does_not_recompute_the_day(self):
        """renderLive knows the pool/book filters; the board does not. Two calculations of one
        figure, side by side on the same screen, is the bug this whole row exists to avoid."""
        body = _script(_page())
        card = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        self.assertIn("const day = TODAY;", card)
        self.assertIn("day.byVenue[k]", card)      # split by platform, like every other card
        self.assertNotIn("day.byKind", card)       # the by-TYPE line belongs to Classic's chip
        live = body[body.index("const todayTotal = today + openMove"):]
        self.assertIn("TODAY = {total: todayTotal", live[:600])

    def test_the_day_splits_by_platform_over_the_same_desks_as_every_other_card(self):
        """Four cards split by desk and one split by asset type is five cards you cannot read
        across. Both splits come off the same ledger pass -- the same rows added up two ways."""
        body = _script(_page())
        live = body[body.index("const todayByVenue = {}"):]
        live = live[:live.index(chr(10) + "  }")]
        self.assertIn("poolVenue(a.pool)", live)
        self.assertIn("todayByKind[kd]", live)     # Classic's running line still needs by-type
        card = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        card = card[card.index("Today's P&L"):]
        self.assertIn("order.map(k => flatrow(vs[k].name", card)

    def test_the_chip_is_hidden_when_the_card_is_showing(self):
        page = _page()
        self.assertIn("#todayhome.carded .todaybar,#todayhome.carded .tickerrow{display:none}", page)
        body = _script(page)
        body = body[body.index("function applyLiveView"):]
        self.assertIn('home.classList.toggle("carded", which === "new")', body)

    def test_five_cards_in_five_columns(self):
        page = _page()
        self.assertIn(".boardrow{display:grid;grid-template-columns:repeat(5,minmax(0,1fr))", page)
        body = _script(page)
        body = body[body.index('box.innerHTML = `<div class="boardrow">'):]
        names = re.findall(r'class="nm">([^<]+)<', body[:body.index("</div>`;")])
        self.assertEqual(names, ["Total Value", "All platforms", "Positions", "Strategies",
                                 "Today's P&L"])

    def test_the_five_titles_sit_on_one_line(self):
        """All platforms carries no headline figure, so without a floor on the header height its
        title rode higher than the other four -- five cards in a row and one of them off by 3px."""
        page = _page()
        head = page[page.index(".bigpot .head{"):]
        head = head[:head.index("}")]
        self.assertIn("min-height:30px", head)
        self.assertIn("align-items:center", head)   # a chip has a box; baselines no longer line up

    def test_the_headline_figure_is_chipped(self):
        page = _page()
        sum_ = page[page.index(".bigpot .potsum{"):]
        sum_ = sum_[:sum_.index("}")]
        self.assertIn("background:var(--chip)", sum_)
        self.assertIn("border-radius", sum_)

    def test_the_cards_share_one_top_and_bottom(self):
        """Five cards of four different heights read as a pile, not a row."""
        page = _page()
        self.assertIn("align-items:stretch", page)
        self.assertIn(".boardrow .bigpot{max-width:none;flex:none;height:100%}", page)


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

    def test_the_per_card_width_caps_are_overridden_after_they_are_set(self):
        """Equal specificity with .bigpot.onecard, so the later rule is the only thing that wins --
        otherwise the 520px cap holds and the card never fills its column."""
        page = _page()
        self.assertLess(page.index(".bigpot.onecard{"), page.index(".boardrow .bigpot{max-width:none"))

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
