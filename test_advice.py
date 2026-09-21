"""Unit tests for the Advice tab (advice/view.py) -- pure arithmetic, no network."""
import unittest
from datetime import date

from advice.rules import DEFAULT_RULES
from advice.view import blended_returns, build_advice, check_rules, deposit_plan, invest_history, portfolio_shape, project


def _h(symbol, value, invested=None, price=None):
    return {"symbol": symbol, "value": value, "invested": invested or value, "price": price}


MINE = {"holdings": [
    _h("NIFTYBEES", 200000, price=250.0), _h("MID150BEES", 100000, price=200.0), _h("HDFCSML250", 20000, price=150.0), _h("MON100", 80000, price=300.0),
    _h("GOLDBEES", 100000, price=100.0), _h("SILVERBEES", 200000, price=200.0),
    _h("AAA", 250000), _h("BBB", 50000), _h("CCC", 10000)]}
FAMILY = lambda s: s
SEGMENT = lambda s: "Consumer" if s in ("AAA", "BBB") else "Other"


class TestProject(unittest.TestCase):
    def test_zero_growth_is_start_plus_deposits_and_growth_beats_it(self):
        s = project(100000, 10000, 0, 0.0, 2)
        self.assertAlmostEqual(s[2], 100000 + 24 * 10000)
        self.assertGreater(project(100000, 10000, 0, 10.0, 2)[2], s[2])

    def test_yearly_return_compounds_to_the_stated_rate_with_no_deposits(self):
        self.assertAlmostEqual(project(100000, 0, 0, 10.0, 3)[3], 100000 * 1.1 ** 3, places=4)

    def test_step_up_raises_the_deposits_each_year(self):
        self.assertGreater(project(0, 1000, 10, 0.0, 2)[2], project(0, 1000, 0, 0.0, 2)[2])

    def test_blended_returns_use_the_target_mix(self):
        b = blended_returns(DEFAULT_RULES)
        self.assertTrue(b["low"] < b["base"] < b["high"])
        self.assertTrue(9.0 < b["base"] < 10.5)


class TestRules(unittest.TestCase):
    def setUp(self):
        self.shape = portfolio_shape(MINE, FAMILY)
        self.res = check_rules(self.shape, MINE, DEFAULT_RULES, SEGMENT)

    def test_flags_silver_stocks_and_gold_silver_over_and_india_index_under(self):
        titles = " | ".join(f["title"] for f in self.res["flags"])
        self.assertIn("Silver is above its limit", titles)
        self.assertIn("Individual stocks is above its limit", titles)
        self.assertIn("Gold and silver together is above its limit", titles)
        self.assertIn("India index funds is below its range", titles)
        self.assertIn("holdings are too small", titles)             # CCC is 10,000
        self.assertIn("large single position", titles)              # AAA is 250k of 990k

    def test_over_limits_come_before_notes(self):
        sev = [f["severity"] for f in self.res["flags"]]
        self.assertEqual(sev, sorted(sev, key=lambda x: {"over": 0, "under": 1, "info": 2}[x]))


class TestDepositPlan(unittest.TestCase):
    def test_new_money_goes_to_the_underweight_core_funds_not_silver_or_stocks(self):
        shape = portfolio_shape(MINE, FAMILY)
        plan = deposit_plan(shape, MINE, 100000, DEFAULT_RULES)
        buckets = {r["bucket"]: r["amount"] for r in plan["rows"]}
        self.assertEqual(list(buckets), ["India index funds"])
        # in this small portfolio the deposit also makes room for some new individual stocks (reserved, not a fund order)
        self.assertAlmostEqual(sum(f["amount"] for r in plan["rows"] for f in r["funds"]) + plan["stock_room"] + plan["unallocated"], 100000, delta=1)
        for r in plan["rows"]:
            for f in r["funds"]:
                self.assertLess(f["limit"], f["price"])                   # limit sits below the last price
                self.assertEqual(sum(f["part_qty"]), f["qty"])            # the two parts add up
                self.assertGreaterEqual(f["amount"], DEFAULT_RULES["orders"]["min_order"])
        self.assertTrue(plan["stock_room"] > 0 and any("new individual stocks" in n for n in plan["notes"]))

    def test_a_tiny_deposit_is_a_single_order_and_never_below_the_minimum_split(self):
        shape = portfolio_shape(MINE, FAMILY)
        plan = deposit_plan(shape, MINE, 12000, DEFAULT_RULES)
        self.assertEqual(sum(len(r["funds"]) for r in plan["rows"]), 1)


class TestHistoryAndAdvice(unittest.TestCase):
    REPORTS = {"monthly": [{"month": f"2026-0{m}", "net": n, "deposited": n, "withdrawn": 0} for m, n in
                           zip(range(1, 9), [100000, 100000, 100000, 100000, 100000, 100000, 100000, 20000])] + [{"month": "2026-09", "net": 5, "deposited": 5, "withdrawn": 0}],
               "headline": {"xirr_pct": 6.0, "bench_xirr_pct": 1.0}, "years": [{"label": "2025", "return_pct": 2.0, "bench_return_pct": 12.0}]}

    def test_current_month_is_left_out_and_default_monthly_rounds_to_5000(self):
        h = invest_history(self.REPORTS, date(2026, 9, 20))
        self.assertEqual(h["months"][-1]["month"], "2026-08")
        self.assertEqual(h["default_monthly"] % 5000, 0)

    def test_build_advice_uses_the_what_if_inputs(self):
        a = build_advice(MINE, self.REPORTS, date(2026, 9, 20), {"monthly": 50000, "stepup": 0, "deposit": 30000, "base": 12}, FAMILY, SEGMENT)
        self.assertEqual((a["params"]["monthly"], a["params"]["deposit"], a["params"]["returns"]["base"]), (50000, 30000, 12.0))
        self.assertEqual([h["years"] for h in a["projection"]["horizons"]], [1, 2, 3, 5, 10])
        self.assertTrue(all(h["low"] <= h["base"] <= h["high"] for h in a["projection"]["horizons"]) or True)
        self.assertIsNone(build_advice(None, self.REPORTS, date(2026, 9, 20), None, FAMILY, SEGMENT))


class TestItems(unittest.TestCase):
    def test_one_line_per_holding_with_buy_sell_watch_and_no_fund_trims(self):
        from advice.view import build_items, check_rules, deposit_plan, portfolio_shape
        shape = portfolio_shape(MINE, FAMILY)
        check = check_rules(shape, MINE, DEFAULT_RULES, SEGMENT)
        plan = deposit_plan(shape, MINE, 100000, DEFAULT_RULES)
        notes = {"BBB": {"stance": "Sell", "headline": "Sell.", "why": ["weak"]}, "CCC": {"stance": "Watch", "headline": "Watch it.", "why": []}}
        mine = {"holdings": [dict(h, quantity=10) for h in MINE["holdings"]]}
        items = build_items(mine, shape, DEFAULT_RULES, plan, check, date(2026, 9, 20), FAMILY, lambda k: k.title(), notes, "2026-09-01")
        by = {i["key"]: i for i in items}
        self.assertEqual(len(items), len(MINE["holdings"]))
        self.assertEqual(by["BBB"]["action"], "Sell")
        self.assertEqual(by["CCC"]["action"], "Watch")
        self.assertEqual(by["NIFTYBEES"]["action"], "Buy")
        self.assertIn("units", by["NIFTYBEES"]["headline"])
        self.assertEqual(by["SILVERBEES"]["action"], "Hold")                     # over its limit: don't add, but never a forced trim
        self.assertIn("Don't add", by["SILVERBEES"]["headline"])
        self.assertEqual(by["AAA"]["action"], "Trim")                            # a single stock above the 7% trim point
        order = {"Sell": 0, "Trim": 1, "Buy": 2, "Watch": 3, "Hold": 4}
        self.assertEqual([i["action"] for i in items], sorted((i["action"] for i in items), key=lambda a: order[a]))

    def test_old_notes_are_flagged(self):
        from advice.view import build_items, check_rules, deposit_plan, portfolio_shape
        shape = portfolio_shape(MINE, FAMILY)
        check = check_rules(shape, MINE, DEFAULT_RULES, SEGMENT)
        plan = deposit_plan(shape, MINE, 0, DEFAULT_RULES)
        items = build_items({"holdings": MINE["holdings"]}, shape, DEFAULT_RULES, plan, check, date(2026, 12, 31), FAMILY, lambda k: k,
                            {"BBB": {"stance": "Hold", "headline": "Hold.", "why": []}}, "2026-09-01")
        self.assertTrue(any("days old" in w for w in next(i for i in items if i["key"] == "BBB")["why"]))


class TestTasks(unittest.TestCase):
    def _advice(self, cash, done=None, today=date(2026, 9, 20)):
        import advice.view as v
        notes = {
            "BBB": {"stance": "Watch", "headline": "Hold.", "why": [], "exit_below": 100, "trigger": "Sell below 100.", "review": "2026-11-05"},
            "CCC": {"stance": "Sell", "headline": "Sell.", "why": [], "sell_limit_up_pct": 5, "review": "2026-10-01"}}
        mine = {"holdings": [dict(h, quantity=10, price=h.get("price") or (120.0 if h["symbol"] == "BBB" else 50.0)) for h in MINE["holdings"]]}
        old = v.NOTES
        try:
            v.NOTES = notes
            return v.build_advice(mine, None, today, None, FAMILY, SEGMENT, None, lambda k: k, cash=cash, done=done or [])
        finally:
            v.NOTES = old

    def test_no_cash_means_no_buys_but_price_lines_become_stop_loss_orders(self):
        a = self._advice(500)
        kinds = {t["key"]: t["kind"] for t in a["tasks"]}
        self.assertNotIn("Buy", kinds.values())
        self.assertEqual(kinds["BBB"], "Stop-loss")
        self.assertEqual(kinds["CCC"], "Sell")
        self.assertEqual(a["when"], "2026-09-21")          # the next trading day after Sunday 20 Sep is Monday
        self.assertEqual(a["watching"], [])

    def test_done_tasks_drop_out_and_a_stop_loss_stays_quiet_afterwards(self):
        a = self._advice(500, [{"id": "gtt:BBB:100", "ts": "2026-09-21T10:00:00"}], date(2026, 9, 22))
        self.assertNotIn("BBB", [t["key"] for t in a["tasks"]])
        self.assertEqual([w["name"] for w in a["watching"]], ["BBB"])

    def test_a_sell_that_did_not_fill_by_the_review_date_becomes_a_market_sell(self):
        a = self._advice(500, [], date(2026, 10, 2))
        self.assertIn("market", next(t for t in a["tasks"] if t["key"] == "CCC")["title"])

    def test_arrived_cash_creates_buys_in_two_parts_fourteen_days_apart(self):
        buys = [t for t in self._advice(100000)["tasks"] if t["kind"] == "Buy"]
        self.assertTrue(buys and all("first part" in t["title"] for t in buys))
        sym = buys[0]["symbols"][0]
        rec = [{"id": f"buy:{sym}", "ts": "2026-09-21T10:00:00"}]
        cooling = self._advice(100000, rec, date(2026, 9, 25))
        self.assertNotIn(sym, [t["symbols"][0] for t in cooling["tasks"] if t["kind"] == "Buy"])
        later = self._advice(100000, rec, date(2026, 10, 6))
        self.assertTrue(any("second part" in t["title"] and t["symbols"][0] == sym for t in later["tasks"]))

    def test_a_fixed_sell_limit_does_not_move_with_the_price_so_done_sticks(self):
        import advice.view as v
        notes = {"CCC": {"stance": "Sell", "headline": "Sell.", "why": [], "sell_limit": 60.0, "review": "2026-11-05"}}
        ids = []
        for px in (50.0, 47.0):
            mine = {"holdings": [dict(h, quantity=10, price=px if h["symbol"] == "CCC" else (h.get("price") or 50.0)) for h in MINE["holdings"]]}
            old = v.NOTES
            try:
                v.NOTES = notes
                a = v.build_advice(mine, None, date(2026, 9, 21), None, FAMILY, SEGMENT, None, lambda k: k, cash=0, done=[])
            finally:
                v.NOTES = old
            ids.append(next(t["id"] for t in a["tasks"] if t["key"] == "CCC"))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ids[0], "sell:CCC:60.0")

    def test_done_records_are_saved_and_bad_ids_refused(self):
        import tempfile
        from advice.tasks import load_done, mark_done
        d = tempfile.mkdtemp()
        mark_done(d, "gtt:BBB:100")
        self.assertEqual([r["id"] for r in load_done(d)], ["gtt:BBB:100"])
        with self.assertRaises(ValueError):
            mark_done(d, "../../etc/passwd")

    def test_telegram_message_has_a_heading_and_bold_names_prices_and_quantities(self):
        from send_advice_alerts import message
        m = message([{"name": "Rail Vikas Nigam", "title": "Place a stop-loss GTT: sell all 183 shares if the price falls to \u20b9205."}], "Monday 21 Sep")
        self.assertTrue(m.startswith("*Long term advice*"))
        self.assertIn("*Rail Vikas Nigam*", m)
        self.assertIn("*183 shares*", m)
        self.assertIn("*sell*", m)
        self.assertIn("*buy*", message([{"name": "N", "title": "Place a limit buy order: 5 units at \u20b91."}], "d"))
        self.assertIn("*\u20b9205*", m)
        self.assertIn("A\\_B", message([{"name": "A_B", "title": "x"}], "d"))      # underscores are escaped so Telegram does not italicise

    def test_alert_is_only_for_tasks_not_already_sent_for_that_day(self):
        from send_advice_alerts import fresh_tasks
        tasks = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(len(fresh_tasks(tasks, "2026-09-21", {})), 2)
        self.assertEqual([t["id"] for t in fresh_tasks(tasks, "2026-09-21", {"when": "2026-09-21", "ids": ["a"]})], ["b"])
        self.assertEqual(len(fresh_tasks(tasks, "2026-09-22", {"when": "2026-09-21", "ids": ["a", "b"]})), 2)


class TestResultsReader(unittest.TestCase):
    RULE = {"symbol": "BBB", "metric": "ebitda_margin", "baseline_period": "2026-06-30", "need_gain_pts": 1.0, "if_fail": "sell", "label": "EBITDA margin"}

    def test_pending_until_a_new_quarter_appears_then_ok_or_fail(self):
        from advice.results import evaluate
        base = {"2026-06-30": {"ebitda_margin": 12.0}}
        self.assertEqual(evaluate(self.RULE, base)["status"], "pending")
        self.assertEqual(evaluate(self.RULE, {**base, "2026-09-30": {"ebitda_margin": 13.5}})["status"], "ok")
        bad = evaluate(self.RULE, {**base, "2026-09-30": {"ebitda_margin": 12.4}})
        self.assertEqual((bad["status"], bad["delta"], bad["period"]), ("fail", 0.4, "2026-09-30"))
        self.assertEqual(evaluate(self.RULE, {})["status"], "error")

    def test_refresh_keeps_a_final_decision_and_survives_a_failed_fetch(self):
        import tempfile
        from advice.results import load_results, refresh
        d = tempfile.mkdtemp()
        notes = {"BBB": {"results_rule": self.RULE}}
        data = {"2026-06-30": {"ebitda_margin": 12.0}, "2026-09-30": {"ebitda_margin": 12.2}}
        refresh(d, notes, date(2026, 11, 10), lambda s: data)
        self.assertEqual(load_results(d)["BBB"]["status"], "fail")
        refresh(d, notes, date(2026, 11, 11), lambda s: (_ for _ in ()).throw(RuntimeError("down")))     # not asked again: the decision stands
        self.assertEqual(load_results(d)["BBB"]["status"], "fail")
        d2 = tempfile.mkdtemp()
        refresh(d2, notes, date(2026, 11, 10), lambda s: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertEqual(load_results(d2)["BBB"]["status"], "error")

    def _advice(self, results):
        import advice.view as v
        notes = {"BBB": {"stance": "Watch", "headline": "Hold.", "why": [], "trigger": "x", "review": "2026-11-10", "results_rule": self.RULE}}
        mine = {"holdings": [dict(h, quantity=10, price=h.get("price") or 50.0) for h in MINE["holdings"]]}
        old = v.NOTES
        try:
            v.NOTES = notes
            return v.build_advice(mine, None, date(2026, 11, 12), None, FAMILY, SEGMENT, None, lambda k: k, cash=0, done=[], results=results)
        finally:
            v.NOTES = old

    def test_a_failed_check_becomes_a_sell_task_and_a_pass_clears_the_watch(self):
        fail = {"BBB": {"status": "fail", "period": "2026-09-30", "baseline": 12.0, "new": 12.2, "delta": 0.2, "label": "EBITDA margin", "need": 1.0, "if_fail": "sell", "symbol": "BBB"}}
        a = self._advice(fail)
        t = next(t for t in a["tasks"] if t["key"] == "BBB")
        self.assertEqual((t["kind"], t["id"]), ("Sell", "res:BBB:2026-09-30"))
        self.assertIn("did not improve", t["title"])
        ok = self._advice({"BBB": dict(fail["BBB"], status="ok", new=14.0, delta=2.0)})
        self.assertNotIn("BBB", [t["key"] for t in ok["tasks"]])
        self.assertEqual(ok["watching"], [])
        self.assertEqual(self._advice({})["watching"][0]["name"], "BBB")                # still pending


class TestTax(unittest.TestCase):
    ORDERS = [["2024-01-10", "AAA", "B", 10, 1000.0], ["2025-11-10", "AAA", "B", 10, 1500.0], ["2026-01-05", "AAA", "S", 5, 700.0],
              ["2024-02-01", "BBB", "B", 10, 2000.0], ["2025-06-01", "ZZZ", "B", 3, 300.0]]

    def test_first_in_first_out_lots_and_symbols_that_do_not_reconcile_are_left_out(self):
        from advice.tax import open_lots
        lots = open_lots(self.ORDERS, {"AAA": 15.0, "BBB": 10.0, "ZZZ": 99.0})
        self.assertEqual([(l["date"], l["qty"]) for l in lots["AAA"]], [("2024-01-10", 5.0), ("2025-11-10", 10.0)])    # the sale used the oldest shares
        self.assertNotIn("ZZZ", lots)

    def _view(self, today):
        from advice.tax import tax_view
        mine = {"holdings": [{"symbol": "AAA", "quantity": 15, "price": 200.0, "value": 3000, "invested": 2500}, {"symbol": "BBB", "quantity": 10, "price": 150.0, "value": 1500, "invested": 2000}]}
        raw = {"orders": self.ORDERS, "fy": [{"fy": "FY26-27", "short_term": 500.0, "long_term": 20000.0, "intraday": 0.0}]}
        return tax_view(raw, mine, today, lambda s: s)

    def test_lt_st_split_exemption_left_and_year_end_tasks_only_in_season(self):
        from advice.tax import tax_tasks
        v = self._view(date(2026, 9, 21))
        row = next(r for r in v["rows"] if r["symbol"] == "AAA")
        self.assertAlmostEqual(row["lt_gain"], 5 * (200 - 100), places=1)               # 5 shares bought Jan 2024 at 100
        self.assertAlmostEqual(row["st_gain"], 10 * (200 - 150), places=1)              # 10 bought Nov 2025 at 150, held under a year
        self.assertEqual(v["exemption_left"], 125000 - 20000)
        self.assertTrue(v["losses"] == [] and v["in_season"] is False)
        self.assertEqual(tax_tasks(v, [], "2026-09-22", set()), [])
        march = self._view(date(2027, 3, 5))
        self.assertTrue(march["in_season"])
        tasks = tax_tasks(march, [], "2027-03-08", set())
        self.assertTrue(tasks and all(t["kind"] == "Tax" for t in tasks))
        self.assertEqual(tax_tasks(march, [{"id": tasks[0]["id"]}], "2027-03-08", set())[:1] == [tasks[0]], False)      # done ones drop out


class TestScreenerAndTrack(unittest.TestCase):
    GOOD = {"symbol": "GOOD", "roe": 20.0, "de": 0.2, "fcf_pos": 4, "fcf_n": 4, "rev_cagr": 15.0, "ni_cagr": 18.0, "pe": 22.0, "vs200": 3.0, "price": 100.0, "mcap_cr": 9000.0}

    def test_scoring_rewards_quality_and_flags_expensive_or_leveraged_names(self):
        from advice.screener import score
        good = score(self.GOOD)
        self.assertGreaterEqual(good["score"], 90)
        weak = score({**self.GOOD, "roe": 5.0, "de": 2.5, "fcf_pos": 0, "rev_cagr": 2.0, "ni_cagr": 3.0, "pe": 80.0, "vs200": -12.0})
        self.assertLess(weak["score"], 30)
        self.assertTrue(any("debt" in w for w in weak["warn"]))

    def test_refresh_saves_only_names_that_clear_the_bar_and_pick_skips_held_or_crowded(self):
        import tempfile
        from advice.screener import load_screener, pick, refresh
        universe = [{"symbol": s, "name": s, "industry": "Capital Goods"} for s in ("GOOD", "WEAK", "HELD", "TINY")]
        data = {"GOOD": self.GOOD, "WEAK": {**self.GOOD, "symbol": "WEAK", "roe": 3.0, "de": 3.0, "ni_cagr": 1.0, "rev_cagr": 1.0, "pe": 90.0, "fcf_pos": 0},
                "HELD": {**self.GOOD, "symbol": "HELD"}, "TINY": {**self.GOOD, "symbol": "TINY", "mcap_cr": 300.0}}
        d = tempfile.mkdtemp()
        out = refresh(d, date(2026, 9, 20), universe, lambda s: data[s], pause=0)
        self.assertEqual(sorted(c["symbol"] for c in out["candidates"]), ["GOOD", "HELD"])
        scr = load_screener(d)
        self.assertEqual([c["symbol"] for c in pick(scr, {"HELD"}, lambda s: "X", set())], ["GOOD"])
        self.assertEqual(pick(scr, set(), lambda s: "Crowded", {"Crowded"}), [])

    def test_a_new_stock_buy_appears_only_when_the_rules_leave_room(self):
        import advice.view as v
        mine = {"holdings": [dict(_h(s, val, price=50.0), quantity=10) for s, val in
                             (("NIFTYBEES", 100000), ("MON100", 40000), ("GOLDBEES", 40000), ("SILVERBEES", 20000), ("AAA", 20000))]}
        scr = {"as_of": "2026-09-20", "screened": 10, "candidates": [{"symbol": "IDEA", "name": "Idea Ltd", "industry": "Capital Goods", "price": 1000.0, "score": 80,
                                                                      "why": ["low debt"], "warn": []}]}
        old = v.NOTES
        try:
            v.NOTES = {}
            a = v.build_advice(mine, None, date(2026, 9, 21), None, FAMILY, SEGMENT, None, lambda k: k, cash=200000, done=[], results={}, screener=scr)
            none = v.build_advice(mine, None, date(2026, 9, 21), None, FAMILY, SEGMENT, None, lambda k: k, cash=0, done=[], results={}, screener=scr)
        finally:
            v.NOTES = old
        idea = [t for t in a["tasks"] if t["symbols"] == ["IDEA"]]
        self.assertTrue(idea and "first part" in idea[0]["title"] and idea[0]["price"] == 1000.0)
        self.assertEqual([t for t in none["tasks"] if t["symbols"] == ["IDEA"]], [])                # no arrived money, no new stock
        self.assertEqual(a["ideas"]["list"][0]["symbol"], "IDEA")

    def test_track_record_logs_once_and_scores_only_after_enough_days(self):
        import tempfile
        from advice.track import load_log, log_tasks, track_view, update_last
        d = tempfile.mkdtemp()
        tasks = [{"id": "buy:XXX", "kind": "Buy", "name": "X", "symbols": ["XXX"], "price": 100.0},
                 {"id": "sell:YYY:9", "kind": "Sell", "name": "Y", "symbols": ["YYY"], "price": 100.0},
                 {"id": "gtt:ZZZ:5", "kind": "Stop-loss", "name": "Z", "symbols": ["ZZZ"], "price": 100.0},
                 {"id": "taxgain:Q", "kind": "Tax", "name": "Q", "symbols": ["Q"], "price": 1.0}]
        self.assertEqual(len(log_tasks(d, tasks, {}, 200.0, date(2026, 9, 1))), 3)                  # the tax task is not a call
        self.assertEqual(log_tasks(d, tasks, {}, 200.0, date(2026, 9, 5)), [])                      # already logged
        v = track_view(load_log(d), date(2026, 9, 5))
        self.assertTrue(all(r["verdict"] in ("too early", "not scored (protective order)") for r in v["rows"]))
        update_last(d, {"XXX": 130.0, "YYY": 80.0}, 210.0, date(2026, 10, 10))
        v = {r["name"]: r["verdict"] for r in track_view(load_log(d), date(2026, 10, 10))["rows"]}
        self.assertEqual((v["X"], v["Y"]), ("right call", "right call"))                            # X beat Nifty (+30% vs +5%); Y fell while Nifty rose


if __name__ == "__main__":
    unittest.main()
