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

DQ = chr(34)
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
        inside a string from an unterminated one, and a check that cries wolf gets deleted.)

        The research merge gate sets STB_RESEARCH_MERGE_GATE: it only ever merges branches that do
        not touch dashboard/ (it refuses them), so the page cannot change there, and the VPS it runs
        on has no node -- this would refuse every research branch for a reason none could cause."""
        if os.environ.get("STB_RESEARCH_MERGE_GATE"):
            self.skipTest("research merge gate: dashboard/ cannot change in a branch it would merge")
        self.assertIsNotNone(shutil.which("node"),
                             "node is needed to syntax-check dashboard/index.html")


ALLOWED_CONTROL = {chr(9), chr(10), chr(13)}      # tab, newline, carriage return


def _control_chars(text: str) -> list:
    return sorted({c for c in text if ord(c) < 32 and c not in ALLOWED_CONTROL})


class TestTheLiveSummaryCard(unittest.TestCase):
    """One row of cards, each line opening to the account-by-account split. Classic was retired on
    2026-10-13 once the new layout had earned it."""

    def test_there_is_one_layout_and_nothing_to_switch(self):
        page = _page()
        self.assertIn('id="liveboard"', page)
        for gone in ('id="livecards"', 'data-view="old"', 'data-view="new"', "LIVE_VIEW_KEY",
                     "viewswitch"):
            self.assertNotIn(gone, page, gone)

    def test_the_board_is_always_the_one_on_screen(self):
        body = _script(_page())
        body = body[body.index("function applyLiveView()"):]
        body = body[:body.index(chr(10) + "}")]
        self.assertIn("board.hidden = false", body)
        self.assertIn("renderLiveBoard(STATE)", body)

    def test_the_chip_elements_survive_even_though_nothing_shows_them(self):
        """renderLiveCards still WRITES these -- the same pass computes the day the card displays.
        Deleting the elements under a renderer that writes to them is how this page has blanked
        itself before, so they stay, hidden."""
        page = _page()
        for kept in ('id="todaybar"', 'id="ticker"', 'id="todayhome"'):
            self.assertIn(kept, page, kept)
        self.assertIn('<div class="livetop" id="todayhome" hidden>', page)

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
        # "Written", not "Queued": it is not waiting in the queue, it is merged and waiting for the
        # next nightly run -- see TestTheBacktestIsDatedByItsNextRun.
        self.assertIn("Written · backtest", body)

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
        self.assertIn("todayByKind[kd]", live)     # the chip's by-type line is still computed
        card = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        card = card[card.index("Today's P&L"):]
        self.assertIn("order.map(k => flatrow(vs[k].name", card)


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

    def test_the_headline_figure_wears_the_same_chip_as_the_rows(self):
        """A teal badge over a stack of grey pills reads as two different things in one card."""
        page = _page()
        pick = lambda sel: page[page.index(sel) + len(sel):page.index("}", page.index(sel))]
        head = pick(".bigpot .potsum{")
        row = pick(".potlist .metric{")
        for prop in ("background:var(--band)", "border:1px solid var(--rule)", "border-radius:4px"):
            self.assertIn(prop, head, prop)
            self.assertIn(prop, row, prop)

    def test_the_cards_share_one_top_and_bottom(self):
        """Five cards of four different heights read as a pile, not a row."""
        page = _page()
        self.assertIn("align-items:stretch", page)
        self.assertIn(".boardrow .bigpot{max-width:none;flex:none;height:100%}", page)


class TestTheStrategiesPeriodFilter(unittest.TestCase):
    """A date range on the Strategies tab, All time by default. What a window can honestly answer
    comes off the ledger -- the trades that CLOSED inside it -- so it rewrites exactly the realised
    P&L, the closed count and the win rate. Capital and age are as-of-today figures and a position
    still open has no "P&L during March", so those are left alone."""

    def test_the_control_exists_and_defaults_to_all_time(self):
        page = _page()
        self.assertIn('id="reg-cal"', page)
        self.assertIn('>All time</button>', page)
        self.assertIn('id="reg-pop"', page)

    def test_it_has_a_range_of_its_own(self):
        """Filtering the trade list and asking how a strategy did in March are different questions
        and must not move together."""
        body = _script(_page())
        self.assertIn("const REG_CAL = {y: null, m: null, from: \"\", to: \"\"}", body)
        self.assertIn("cal: CAL,", body)        # the Live-day tape keeps its own
        self.assertIn("cal: REG_CAL,", body)

    def test_only_closed_trades_inside_the_window_are_counted(self):
        body = _script(_page())
        fn = body[body.index("function periodFigures"):body.index("function renderRegistry")]
        self.assertIn('String(r.status || "").toLowerCase() === "open"', fn)   # skipped
        self.assertIn("within(r.date)", fn)
        self.assertIn("v.wins += 1", fn)

    def test_a_window_leaves_the_as_of_today_columns_alone(self):
        """Capital and the costed Net P&L are whole-life figures; recomputing them per window would
        be inventing a number, so the window hands back null and the row shows a dash."""
        body = _script(_page())
        body = body[body.index("const period = periodFigures"):]
        body = body[:body.index("const winOf")]
        self.assertIn("pnl: p ? p.pnl : null", body)
        self.assertIn("net: undefined", body)        # gross only -- marked with the * convention
        self.assertNotIn("capital:", body)

    def test_the_page_number_resets_when_the_window_changes(self):
        body = _script(_page())
        self.assertIn("REG_CAL.from, REG_CAL.to]", body)


class TestPaperLooksLikeLive(unittest.TestCase):
    """Standing rule: the two modes are the same screen, with each one's own money in it. The
    Strategies cards used to be Live-only, and the Live-day board read the BROKER's cash even in
    Paper -- a real Kite balance beside simulated positions describes an account that does not
    exist."""

    def test_the_strategies_cards_are_not_live_only(self):
        body = _script(_page())
        fn = body[body.index("function renderAllocations"):]
        fn = fn[:fn.index("panel.innerHTML")]
        self.assertNotIn('if(MODE !== "live"){ panel.hidden = true; return; }', fn)
        self.assertIn("panel.hidden = false", fn)

    def test_one_cash_source_feeds_both_the_cards_and_the_board(self):
        """Two answers to "how much cash" on one screen is the bug this whole row exists to avoid."""
        body = _script(_page())
        self.assertIn("function cashByVenue(s)", body)
        board = body[body.index("function renderLiveBoard"):body.index("function applyLiveView")]
        self.assertIn("cashByVenue(s)", board)
        cards = body[body.index("function renderAllocations"):body.index("const regCols")]
        self.assertIn("cashByVenue(s)", cards)

    def test_paper_cash_comes_from_the_paper_books(self):
        body = _script(_page())
        fn = body[body.index("function cashByVenue(s)"):]
        fn = fn[:fn.index(chr(10) + "}")]
        self.assertIn("lc.balance", fn)                 # live: the broker's own
        self.assertIn("(s.pool_d || {}).cash", fn)      # paper: the books' own
        self.assertIn("(s.pool_g || {}).cash", fn)
        self.assertIn("(s.pool_i || {}).books", fn)


class TestTheCreditWarningBar(unittest.TestCase):
    """The one thing on this page that interrupts you: a LIVE book that cannot manage its own open
    positions. On 2026-10-13 Pool G had been down since the 11th and nothing on screen said so."""

    def test_the_bar_is_stuck_to_the_top_and_can_be_dismissed(self):
        page = _page()
        self.assertIn('id="creditbar"', page)
        self.assertIn('id="creditbar-x"', page)
        css = page[page.index(".alertbar{"):]
        self.assertIn("position:sticky", css[:css.index("}")])
        self.assertIn(".alertbar[hidden]{display:none}", page)   # or sticky would beat hidden

    def test_dismissing_it_remembers_which_outage(self):
        """Hiding today's must not hide tomorrow's, and it has to come back if the books stop again
        after a recovery."""
        body = _script(_page())
        fn = body[body.index("function renderCreditBar"):body.index("document.getElementById(\"creditbar-x\")")]
        self.assertIn('(h.since || "") + "|"', fn)       # the stamp is the outage, not a flag
        self.assertIn("seen === stamp", fn)

    def test_it_is_drawn_on_every_load(self):
        body = _script(_page())
        self.assertIn("renderCreditBar(s); renderLive(s);", body)


class TestHidingActuallyHides(unittest.TestCase):
    """CSS `display:` on an element BEATS the [hidden] attribute, so setting hidden does nothing at
    all. It has bitten twice: .profilemenu's display:flex kept the Settings menu open however many
    places called close(), and .cards' display:flex kept the Classic summary on screen in the New
    view. Any selector that sets display needs a [hidden] partner."""

    def test_every_display_rule_has_a_hidden_partner(self):
        """GENERAL, not a list of the ones that already bit. Every element the markup marks `hidden`
        is checked against every class it carries: if that class sets `display:` anywhere in the
        stylesheet, it needs a `[hidden]` partner or the attribute does nothing.

        The hand-written list this replaced named four selectors and missed the fifth -- .livetop,
        which kept the retired Today's P&L chip on screen above the cards after Classic was removed.
        """
        page = _page()
        css = page[page.index("<style>"):page.index("</style>")]
        sets_display = {m.group(1) for m in re.finditer(r"\.([A-Za-z0-9_-]+)\{display:", css)}
        has_partner = {m.group(1) for m in re.finditer(r"\.([A-Za-z0-9_-]+)\[hidden\]\{display:none\}", css)}
        missing = []
        for tag in re.finditer(r"<\w+[^>]*\shidden[\s>]", page):
            classes = re.search(r'class="([^"]+)"', tag.group(0))
            for cls_name in (classes.group(1).split() if classes else []):
                if cls_name in sets_display and cls_name not in has_partner:
                    missing.append(cls_name)
        self.assertEqual(sorted(set(missing)), [],
                         "hidden does nothing on these -- a display: rule beats it")

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


class TestTheCapitalBoxIsInBothModes(unittest.TestCase):
    """The standing rule: paper and live must not differ. Live mode had an editable capital box on
    each row and paper had none, so a paper book's size could not be changed at all."""

    def setUp(self):
        self.page = _page()
        self.script = _script(self.page)

    def test_the_column_is_not_live_only(self):
        self.assertNotIn("MODE === " + DQ + "live" + DQ + " ? [...REG_COLS", self.script)
        self.assertIn("? " + DQ + "Live capital" + DQ + " : " + DQ + "Paper capital" + DQ, self.script)

    def test_the_cell_is_drawn_in_both_modes(self):
        self.assertIn("? allocCell(r) : paperCapCell(r)", self.script)

    def test_the_header_and_the_row_agree_on_how_many_columns_there_are(self):
        """A conditional <td> against an unconditional <th> shifts every value one column left --
        the sub-rows carried exactly that bug the first time this column was added."""
        self.assertNotIn(DQ + "<td></td>" + DQ + " : " + DQ + DQ, self.script)
        self.assertNotIn("${allocCell(r)}</td>` : " + DQ + DQ, self.script)

    def test_each_mode_writes_to_its_own_endpoint(self):
        self.assertIn("/api/live/allocate" + DQ + " : " + DQ + "/api/paper/capital", self.script)

    def test_a_paper_box_starts_at_the_figure_in_the_capital_column(self):
        """It is the number the change is measured from, so showing anything else would mean typing
        a figure against one base and having it applied to another."""
        fn = self.script[self.script.index("function paperCapCell"):self.script.index("function capBox")]
        self.assertIn("Math.round(r.capital)", fn)

    def test_only_a_paper_book_gets_a_box(self):
        """A live strategy's money is real; a retired book is the record that retired it."""
        fn = self.script[self.script.index("function paperCapCell"):self.script.index("function capBox")]
        self.assertIn("liveState(r.status) === " + DQ + "Live" + DQ, fn)
        self.assertIn("liveState(r.status) !== " + DQ + "Paper" + DQ, fn)


class TestTheStrategiesRail(unittest.TestCase):
    """Research, Paper, Live and Retired are four readings of one list, so they sit together under
    Strategies instead of Research having a top-level entry of its own."""

    def setUp(self):
        self.page = _page()
        self.script = _script(self.page)

    def test_research_moved_into_the_group(self):
        nav = self.page[self.page.index("<nav role=" + DQ + "tablist" + DQ):self.page.index("</nav>")]
        self.assertIn("nav-strat-kids", nav)
        self.assertGreater(nav.index(">Research<"), nav.index("nav-strat-kids"))

    def test_all_four_views_are_there(self):
        kids = self.page[self.page.index("id=" + DQ + "nav-strat-kids" + DQ):]
        kids = kids[:kids.index("</div>")]
        for view in ("Research", "Paper", "Live", "Retired"):
            self.assertIn(">" + view + "<", kids, view)

    def test_the_group_can_actually_be_collapsed(self):
        """CSS `display:` beats the [hidden] attribute, so a flex container needs a [hidden]
        partner or folding the group does nothing at all."""
        css = self.page[self.page.index("<style>"):self.page.index("</style>")]
        self.assertIn(".navkids{display:flex", css)
        self.assertIn(".navkids[hidden]{display:none}", css)

    def test_a_sub_view_sets_the_same_filter_the_column_header_has(self):
        """So the rail and the header dropdown can never disagree about what is on screen. The rail
        records the view and the renderer writes it to the select -- see
        TestTheFirstClickHappensBeforeTheCodeExists for why it cannot write it itself."""
        self.assertIn("STRAT_VIEW = b.dataset.strat;", self.script)
        self.assertIn("reg-status" + DQ + ").value = STRAT_VIEW", self.script)

    def test_retired_is_its_own_state_and_not_lumped_in_with_off(self):
        """A strategy that was retired is not the same thing as one that was never deployed."""
        self.assertIn("ARCHIVED" + DQ + " ? " + DQ + "Retired" + DQ + " : liveState(status)", self.script)
        self.assertIn("regClass(r.status) === statusF", self.script)
        self.assertIn("<option value=" + DQ + "Retired" + DQ + ">Retired</option>", self.script)

    def test_the_mode_no_longer_hides_rows_behind_the_rails_back(self):
        """Two filters for one question: the mode dropped every non-live row AND the rail filters by
        status, so Paper and Retired came up empty in Live mode for no stated reason."""
        self.assertNotIn("filter(r => MODE !== " + DQ + "live" + DQ + " || liveState(r.status)", self.script)

    def test_which_view_you_land_on_is_remembered_per_mode(self):
        """Live mode opening on the paper books would be the old pre-filter's bug with extra steps."""
        self.assertIn(DQ + "desk.strat." + DQ + " + MODE", self.script)
        self.assertIn("MODE === " + DQ + "live" + DQ + " ? " + DQ + "Live" + DQ + " : " + DQ + "Paper" + DQ,
                      self.script)

    def test_switching_mode_moves_the_view_with_it(self):
        self.assertIn("stratButton(lastStratView()).click()", self.script)

    def test_which_page_is_open_has_one_source(self):
        """aria-selected cannot answer it any more: three rail buttons open the same section, and
        the highlight among them is allowed to be none when a card opens the list unfiltered."""
        self.assertIn("const currentTab = () => CUR_TAB;", self.script)
        self.assertIn("CUR_TAB = b.dataset.tab;", self.script)

    def test_a_cards_own_filters_still_win(self):
        """The sub-view is clicked FIRST, so its own filter cannot overwrite the card's."""
        fn = self.script[self.script.index("function showStrategies"):]
        fn = fn[:fn.index("function goLive")]
        self.assertLess(fn.index("stratButton("), fn.index("reg-pool"))


class TestTheFirstClickHappensBeforeTheCodeExists(unittest.TestCase):
    """THE BUG. Restoring the tab you left open clicks a rail button while the page's script is
    still being parsed, so at that moment half the script's own consts are in their temporal dead
    zone. The Strategies sub-views made that click do real work -- build the table header, write to
    the status filter -- and buildRegHead stamped the header "built" on its FIRST line before
    reaching regCols() and throwing. The stamp made every later call skip the rebuild, so the header
    stayed empty for the life of the page, and the first sel("reg-status") read .value off null:

        Could not load state: Cannot read properties of null (reading 'value')

    Every tab came up blank. The restore is wrapped in try/catch, so nothing was logged."""

    def setUp(self):
        self.script = _script(_page())

    def _fn(self, name):
        start = self.script.index("function " + name + "(")
        end = self.script.index(chr(10) + "}", start)
        return self.script[start:end]

    def test_the_header_is_marked_built_only_once_it_is(self):
        """A header that threw half way through is not a built header, and must be rebuilt."""
        fn = self._fn("buildRegHead")
        self.assertIn("head.dataset.built = MODE;", fn)
        self.assertGreater(fn.index("head.dataset.built = MODE;"), fn.index("head.innerHTML ="),
                           "the built stamp must come after the header is actually written")

    def test_the_rail_click_does_no_dom_work_of_its_own(self):
        """It records which view was asked for; the renderer applies it, by which time everything
        the renderer needs exists. Calling buildRegHead from the click is what detonated."""
        start = self.script.index("let CUR_TAB")
        handler = self.script[start:self.script.index("document.getElementById(" + DQ + "bell" + DQ + ")", start)]
        self.assertIn("STRAT_VIEW = b.dataset.strat;", handler)
        self.assertNotIn("buildRegHead()", handler)
        self.assertNotIn("reg-status", handler)

    def test_the_renderer_applies_the_pending_view_and_clears_it(self):
        """Left pending it would re-apply itself over the header dropdown on the next poll, so
        changing the filter by hand would silently snap back every fifteen seconds."""
        fn = self.script[self.script.index("function renderRegistry(s){"):]
        fn = fn[:fn.index("markSort(")]
        self.assertIn("STRAT_VIEW !== null", fn)
        self.assertIn("STRAT_VIEW = null;", fn)


class TestTheStrategiesPanelCountsStrategies(unittest.TestCase):
    """The panel above the table carried cash, invested and holdings -- the same three figures the
    Live day board already shows, off the same ledger. On a page whose every row is a strategy the
    question is not how much money there is, it is how many strategies there are and what stage each
    has reached. So in Paper it counts strategies, one card per view in the rail beside it."""

    def setUp(self):
        self.page = _page()
        self.script = _script(self.page)

    def _fn(self):
        start = self.script.index("function renderStrategyCounts(")
        return self.script[start:self.script.index("function renderAllocations(s){")]

    def test_paper_gets_the_counts_instead_of_the_money(self):
        self.assertIn("if(MODE !== " + DQ + "live" + DQ + ") return renderStrategyCounts(s);", self.script)

    def test_live_keeps_the_money_cards(self):
        """They are the pool the Live capital column on each row is assigned out of, so they belong
        beside it. Paper's notional cash answers no such question."""
        fn = self.script[self.script.index("function renderAllocations(s){"):]
        fn = fn[:fn.index("// THE SAME COLUMN IN BOTH MODES")]
        self.assertIn("Cash available", fn)
        self.assertIn("renderStrategyCounts(s);", fn)

    def test_there_is_one_card_per_view_in_the_rail(self):
        """Four cards, four rail entries, same four words -- or the page is telling two stories
        about the same list."""
        fn = self._fn()
        for view in ("Research", "Paper", "Live", "Retired"):
            self.assertIn("card(" + DQ + view + DQ, fn, view)

    def test_each_card_opens_its_own_view(self):
        fn = self._fn()
        self.assertIn("stratButton(el.dataset.view).click()", fn)

    def test_a_card_is_reachable_from_the_keyboard(self):
        """It is a button that happens to look like a card."""
        fn = self._fn()
        self.assertIn('tabindex="0"', fn)
        self.assertIn('role="button"', fn)
        self.assertIn('e.key === "Enter"'.replace('"', DQ), fn)

    def test_research_counts_candidates_and_says_so(self):
        """Nothing in that lane is a strategy yet, so counting it among the strategies would be
        wrong in a way no label could fix."""
        fn = self._fn()
        self.assertIn("l.ready", fn)
        self.assertIn("not strategies yet", fn)

    def test_retired_splits_on_whether_it_ever_traded(self):
        """That is the difference between the two retirement rules: rejected in research and never
        deployed, or archived after a paper book had 120 days to make its case."""
        fn = self._fn()
        self.assertIn("Rejected in research", fn)
        self.assertIn("Retired from paper", fn)
        self.assertIn("r.started", fn)

    def test_four_cards_do_not_stay_four_wide_on_a_phone(self):
        """.four has its own rule, so it outranks the stacking one unless that names it too."""
        css = self.page[self.page.index("<style>"):self.page.index("</style>")]
        self.assertIn(".cashcards.four{grid-template-columns:repeat(4", css)
        self.assertIn(".cashcards.four{grid-template-columns:repeat(2", css)
        self.assertIn(".cashcards.four{grid-template-columns:minmax(0,1fr)}", css)


class TestTheBacktestIsDatedByItsNextRun(unittest.TestCase):
    """A card read "Queued - backtest 02:30, claimed Oct 13 06:00 AM" and was reported as a backtest
    that had been due on the 13th and never happened. Both halves were true statements about
    different things sitting next to each other: 02:30 is when the nightly backtest fires, the 13th
    is when the writing started. The date that matters is the next one."""

    def setUp(self):
        self.script = _script(_page())

    def test_the_label_names_the_night_it_will_run(self):
        self.assertIn("backtest runs ${esc(nextBacktest())}", self.script)
        self.assertNotIn("backtest ${BACKTEST_AT}, claimed", self.script)

    def test_it_rolls_to_tomorrow_once_todays_run_has_gone(self):
        """Otherwise it points at a time this morning, which is exactly the reading that caused the
        confusion."""
        fn = self.script[self.script.index("const nextBacktest = () =>"):]
        fn = fn[:fn.index("};")]
        self.assertIn("at.setHours(2, 30, 0, 0)", fn)
        self.assertIn("if(at <= now) at.setDate(at.getDate() + 1)", fn)

    def test_tonight_is_called_tonight(self):
        fn = self.script[self.script.index("const nextBacktest = () =>"):]
        fn = fn[:fn.index("};")]
        self.assertIn("tonight", fn)

    def test_written_not_queued(self):
        """"Queued" was the other half of the confusion: it is not waiting in the queue, it is
        written, merged and waiting for the next nightly run."""
        self.assertIn("Written · backtest", self.script)


class TestNotificationsAreOneTimeline(unittest.TestCase):
    """Two tables meant deciding which of them a thing belonged in before you could read either, and
    the answer to "what happened recently?" was split across both. A strategy going live and an order
    the book could not place are both things that happened at a time."""

    def setUp(self):
        self.page = _page()
        self.script = _script(self.page)

    def _section(self):
        start = self.page.index("id=" + DQ + "tab-notifications" + DQ)
        return self.page[start:self.page.index("</section>", start)]

    def test_there_is_exactly_one_table(self):
        self.assertEqual(self._section().count("<table>"), 1)

    def test_the_blocked_orders_still_appear_in_it(self):
        """Merging them must not quietly drop them: an order the guard stopped is the original
        reason this page exists."""
        fn = self.script[self.script.index("function timelineRows(n){"):]
        fn = fn[:fn.index("const ACTIVITY_PILL")]
        self.assertIn("n.rows", fn)
        self.assertIn("Blocked by the guard", fn)
        self.assertIn("Could not be taken", fn)

    def test_both_sources_are_sorted_together(self):
        """Interleaved by time, or it is two tables again with one border removed."""
        fn = self.script[self.script.index("function timelineRows(n){"):]
        fn = fn[:fn.index("const ACTIVITY_PILL")]
        self.assertIn("n.activity", fn)
        self.assertIn(".sort(", fn)

    def test_the_reason_survives_the_merge(self):
        """The rate and value columns went; the reason is the whole point of the row."""
        fn = self.script[self.script.index("function timelineRows(n){"):]
        fn = fn[:fn.index("const ACTIVITY_PILL")]
        self.assertIn("r.reasons", fn)

    def test_there_is_no_second_table_body_left_behind(self):
        """An orphan id that nothing fills is how a renderer ends up writing to nothing."""
        self.assertNotIn("notif-rows", self.page)


class TestTheTimelineIsPaged(unittest.TestCase):
    def setUp(self):
        self.page = _page()
        self.script = _script(self.page)

    def test_fifty_rows_a_page_by_default(self):
        self.assertIn("NOTIF_PAGE_SIZE = 50", self.script)
        sel = self.page[self.page.index("id=" + DQ + "notif-pagesize" + DQ):]
        self.assertIn("<option selected>50</option>", sel[:sel.index("</select>")])

    def test_the_controls_exist_and_are_wired(self):
        for el in ("notif-pagesize", "notif-prev", "notif-next", "notif-pageinfo"):
            self.assertIn(el, self.page, el)
        self.assertIn("function wireNotifPager()", self.script)

    def test_changing_the_size_goes_back_to_the_first_page(self):
        """Otherwise asking for 200 a page can land you on page 4 of 1, which renders nothing."""
        fn = self.script[self.script.index("function wireNotifPager()"):]
        self.assertIn("NOTIF_PAGE = 1", fn[:fn.index("}")+400])

    def test_the_page_is_clamped_to_what_exists(self):
        fn = self.script[self.script.index("function renderActivity(rows){"):]
        fn = fn[:fn.index("function wireNotifPager")]
        self.assertIn("Math.min(Math.max(1, NOTIF_PAGE), pages)", fn)

    def test_the_pager_is_hidden_when_it_would_do_nothing(self):
        fn = self.script[self.script.index("function renderActivity(rows){"):]
        fn = fn[:fn.index("function wireNotifPager")]
        self.assertIn("notif-pager" + DQ + ").hidden", fn)


class TestTheTimelineDateIsReadable(unittest.TestCase):
    """10-10-26 can be read as the 10th of October or the 10th of someone else's month. A month in
    words cannot."""

    def setUp(self):
        self.script = _script(_page())

    def test_the_timeline_uses_day_month_in_words_year(self):
        fn = self.script[self.script.index("function renderActivity(rows){"):]
        fn = fn[:fn.index("function wireNotifPager")]
        self.assertIn("dmy(r.date)", fn)
        self.assertNotIn("ddmmyy(", fn)

    def test_the_formatter_produces_that_shape(self):
        fn = self.script[self.script.index("const dmy = iso =>"):]
        fn = fn[:fn.index(";", fn.index("return"))]
        self.assertIn("MONTHS[+m[2] - 1]", fn)
        self.assertIn("+m[3]", fn)          # 1 Oct, not 01 Oct
        self.assertIn("m[1]", fn)           # the full year, not two digits

    def test_the_format_it_replaced_is_gone_rather_than_left_lying_about(self):
        """ddmmyy had exactly two call sites, both on this timeline. With them rewritten it had
        none, and a date formatter nothing calls is the kind of thing a later edit reaches for by
        mistake."""
        self.assertNotIn("ddmmyy", self.script)


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
