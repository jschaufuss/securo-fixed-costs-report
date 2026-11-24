import unittest
from fixed_costs import plugin


def tx(day, amount="10", payee="Streaming", **overrides):
    row = {"date": day, "amount": amount, "amount_primary": amount, "currency": "EUR", "payee": payee,
           "account_id": "a", "type": "debit", "status": "posted", "is_ignored": False, "category_lines": []}
    row.update(overrides)
    return row


def report(rows, **kwargs):
    latest = max((row["date"] for row in rows if row.get("date")), default="2025-12-31")
    params = {"start_date": "2024-01-01", "end_date": "2025-12-31", "today": latest, "currency": "EUR", "lang": "de",
              "data": {"transactions": {"rows": rows, "truncated": kwargs.get("truncated", False)}, "categories": []}}
    return plugin.report(params, None)["blocks"]


class ReportTest(unittest.TestCase):
    def test_widget_shows_summary_and_all_payments_with_truncation_warning(self):
        rows = [tx(f"2025-{month:02d}-01", str(cost), f"Service {cost}")
                for cost in range(1, 8) for month in (1, 2, 3)]
        params = {"widget": "recurring-payments", "start_date": "2024-04-01", "end_date": "2025-03-31",
                  "today": "2025-03-31", "currency": "EUR",
                  "data": {"transactions": {"rows": rows, "truncated": True}, "categories": []}}
        blocks = plugin.widget(params, None)["blocks"]
        self.assertEqual(blocks[0]["value"], 28)
        self.assertEqual(blocks[1]["value"], 336)
        self.assertEqual(blocks[2]["value"], 7)
        self.assertEqual([b["width"] for b in blocks[:3]], ["third"] * 3)
        self.assertTrue(any(b.get("type") == "text" and "truncated" in b["text"] for b in blocks))
        table = next(b for b in blocks if b["type"] == "table")
        self.assertEqual([r["monthly"] for r in table["rows"]], [7, 6, 5, 4, 3, 2, 1])
        self.assertEqual(table["page_size"], 10)
        self.assertEqual([c["key"] for c in table["columns"]], ["payee", "monthly"])
        params["widget"] = "monthly-fixed-costs"
        self.assertEqual(plugin.widget(params, None)["blocks"][0]["value"], 28)
        params["widget"] = "annual-fixed-costs"
        self.assertEqual(plugin.widget(params, None)["blocks"][0]["value"], 336)
        params["widget"] = "recurring-payments-compact"
        compact = next(b for b in plugin.widget(params, None)["blocks"] if b["type"] == "table")
        self.assertEqual(compact["page_size"], 5)
        self.assertNotIn("title", compact)
        self.assertEqual(len(compact["rows"]), 7)

    def test_widget_empty_and_historical_month(self):
        params = {"widget": "recurring-payments", "start_date": "2024-04-01", "end_date": "2025-03-31",
                  "today": "2025-06-01", "currency": "EUR", "data": {"transactions": {"rows": [
                      tx("2025-02-01"), tx("2025-03-01"), tx("2025-04-01", "100")], "truncated": False}, "categories": []}}
        table = next(b for b in plugin.widget(params, None)["blocks"] if b["type"] == "table")
        self.assertEqual(table["rows"][0]["monthly"], 10)
        params["data"]["transactions"]["rows"] = []
        self.assertTrue(any(b["type"] == "text" for b in plugin.widget(params, None)["blocks"]))
        params["widget"] = "unknown"
        with self.assertRaises(ValueError):
            plugin.widget(params, None)

    def test_recognizes_monthly_and_yearly_and_uses_latest_amount(self):
        rows = [tx("2025-01-01"), tx("2025-02-01"), tx("2025-03-01", "11"),
                tx("2024-04-01", "120", "Insurance"), tx("2025-04-01", "120", "Insurance")]
        blocks = report(rows)
        self.assertEqual(blocks[1]["value"], 2)
        self.assertAlmostEqual(blocks[0]["value"], 21, places=1)
        table = next(b for b in blocks if b.get("title") == "Possible recurring payments")["rows"]
        self.assertEqual({r["interval"] for r in table}, {"Monthly", "Yearly"})

    def test_excludes_unreliable_or_ineligible_data_and_warns_on_truncation(self):
        rows = [tx("2025-01-01"), tx("2025-02-01"), tx("2025-04-01", "50"),
                tx("2025-01-01", payee="Foreign", currency="USD", amount_primary=None),
                tx("2025-02-01", payee="Foreign", currency="USD", amount_primary=None),
                tx("2025-03-01", payee="Foreign", currency="USD", amount_primary=None)]
        blocks = report(rows, truncated=True)
        self.assertEqual(blocks[1]["value"], 0)
        self.assertTrue(any(b.get("type") == "text" and "truncated" in b["text"] for b in blocks))

    def test_summary_cards_show_annual_estimate_largest_and_change(self):
        rows = [tx("2025-01-01", "10", "Service"), tx("2025-02-01", "10", "Service"),
                tx("2025-03-01", "11", "Service")]
        params = {"start_date": "2025-01-01", "end_date": "2025-03-31", "today": "2025-03-31", "currency": "EUR",
                  "data": {"transactions": {"rows": rows, "truncated": False}, "categories": []}}
        blocks = plugin.report(params, None)["blocks"]
        self.assertEqual([b["type"] for b in blocks[:4]], ["kpi"] * 4)
        self.assertEqual(blocks[0]["value"], 11)
        self.assertEqual(blocks[0]["delta"], 1)
        self.assertEqual(blocks[0]["delta_good"], "down")
        self.assertEqual(blocks[2]["value"], 132)
        self.assertEqual(blocks[3]["value"], 11)
        self.assertEqual(blocks[3]["hint"], "Service")
        params["data"]["transactions"]["truncated"] = True
        self.assertNotIn("delta", plugin.report(params, None)["blocks"][0])

    def test_summary_does_not_invent_largest_or_annual_estimate_without_matches(self):
        blocks = report([tx("2025-01-01")])
        self.assertIsNone(blocks[2]["value"])
        self.assertIsNone(blocks[3]["value"])
        self.assertNotIn("delta", blocks[0])

    def test_chart_uses_latest_category(self):
        rows = [tx("2025-01-01", category_id="old"), tx("2025-02-01", category_id="new"),
                tx("2025-03-01", "11", category_id="new")]
        params = {"start_date": "2025-01-01", "end_date": "2025-04-30", "currency": "EUR", "lang": "de",
                  "data": {"transactions": {"rows": rows, "truncated": False},
                           "categories": [{"id": "old", "name": "Alt"}, {"id": "new", "name": "Streaming"}]}}
        blocks = plugin.report(params, None)["blocks"]
        self.assertEqual(next(b for b in blocks if b.get("title") == "Possible recurring payments")["rows"][0]["category"], "Streaming")
        summary = next(b for b in blocks if b.get("title") == "Category breakdown")
        self.assertEqual(summary["rows"], [{"category": "Streaming", "monthly": 11.0}])
        chart = next(b for b in blocks if b.get("title") == "Estimated monthly costs over time")
        self.assertEqual(chart["chart"], "bar")
        self.assertEqual(chart["x"], ["2025-01", "2025-02", "2025-03", "2025-04"])
        self.assertEqual(chart["series"][0]["values"], [None, 10.0, 11.0, 11.0])
        self.assertFalse(any(b.get("chart") == "pie" for b in blocks))

    def test_history_is_shown_after_current_series_ends_without_future_leakage(self):
        rows = [tx("2025-01-01"), tx("2025-02-01")]
        params = {"start_date": "2025-01-01", "end_date": "2025-06-30", "today": "2025-06-30", "currency": "EUR",
                  "data": {"transactions": {"rows": rows, "truncated": False}, "categories": []}}
        blocks = plugin.report(params, None)["blocks"]
        self.assertEqual(blocks[1]["value"], 0)
        chart = next(b for b in blocks if b.get("title") == "Estimated monthly costs over time")
        self.assertEqual(chart["width"], "full")
        self.assertEqual(chart["series"][0]["values"], [None, 10.0, 10.0, None, None, None])


    def test_quarterly_half_yearly_and_yearly_monthly_equivalents(self):
        rows = [tx(d, "120", "Quarterly") for d in ("2024-07-15", "2024-10-15", "2025-01-15")]
        rows += [tx(d, "180", "Half-yearly") for d in ("2024-01-15", "2024-07-15", "2025-01-15")]
        rows += [tx(d, "240", "Yearly") for d in ("2024-01-15", "2025-01-15")]
        blocks = report(rows)
        table = next(b for b in blocks if b.get("title") == "Possible recurring payments")["rows"]
        found = {r["payee"]: (r["interval"], r["monthly"]) for r in table}
        self.assertEqual(found, {"Quarterly": ("Quarterly", 40.0), "Half-yearly": ("Half-yearly", 30.0), "Yearly": ("Yearly", 20.0)})
        self.assertEqual(blocks[0]["value"], 90)
        self.assertNotIn("heatmap", [b["type"] for b in blocks])

    def test_quarterly_and_half_yearly_require_three_occurrences(self):
        for dates in (("2024-01-15", "2024-04-15"), ("2024-01-15", "2024-07-15")):
            self.assertEqual(report([tx(day) for day in dates])[1]["value"], 0)

    def test_regular_series_with_intervening_purchase_and_early_last_payment(self):
        rows = [tx("2025-01-01", "100"), tx("2025-02-01", "100"), tx("2025-03-05", "100"),
                tx("2025-03-06", "6"), tx("2025-03-25", "100")]
        table = next(b for b in report(rows) if b.get("title") == "Possible recurring payments")
        self.assertEqual(table["rows"][0]["last"], "2025-03-25")
        self.assertEqual(table["rows"][0]["monthly"], 100)

    def test_recent_price_change_on_an_established_series_is_shown(self):
        rows = [tx("2025-01-01", "121"), tx("2025-01-31", "100"), tx("2025-03-05", "100"),
                tx("2025-04-02", "100"), tx("2025-05-03", "212")]
        table = next(b for b in report(rows) if b.get("title") == "Possible recurring payments")
        self.assertEqual(table["rows"][0]["monthly"], 212)
        self.assertEqual(table["rows"][0]["review"], "Latest amount changed")
        self.assertEqual(table["rows"][0]["last"], "2025-05-03")

    def test_amazon_prime_is_separated_from_other_amazon_purchases(self):
        rows = [tx(day, "8.99", "Amazon", description="Amazon Prime") for day in
                ("2025-01-01", "2025-02-03", "2025-03-04", "2025-04-03")]
        rows += [tx("2025-01-02", "65", "Amazon", description="Order"),
                 tx("2025-02-15", "43", "Amazon", description="Order")]
        table = next(b for b in report(rows) if b.get("title") == "Possible recurring payments")
        self.assertEqual([row["payee"] for row in table["rows"]], ["Amazon Prime"])
        self.assertEqual(table["rows"][0]["monthly"], 8.99)

    def test_unrelated_purchase_between_regular_payments_does_not_replace_latest(self):
        rows = [tx("2025-01-01", "100"), tx("2025-02-01", "100"), tx("2025-03-01", "100"),
                tx("2025-03-03", "4")]
        table = next(b for b in report(rows) if b.get("title") == "Possible recurring payments")
        self.assertEqual(table["rows"][0]["last"], "2025-03-01")

    def test_supermarket_purchases_are_not_cherry_picked_as_a_subscription(self):
        rows = [tx("2025-07-01", "14", "Supermarket"), tx("2025-08-12", "22", "Supermarket"),
                tx("2025-09-05", "38", "Supermarket"), tx("2025-10-01", "35", "Supermarket"),
                tx("2025-11-01", "36", "Supermarket")]
        self.assertEqual(report(rows)[1]["value"], 0)

    def test_dense_merchant_is_not_classified_from_a_monthly_subsequence(self):
        rows = []
        for month in (7, 8, 9, 10, 11):
            for day, amount in ((1, "20"), (8, "50"), (15, "75"), (22, "90")):
                rows.append(tx(f"2025-{month:02d}-{day:02d}", amount, "Supermarket"))
        self.assertEqual(report(rows)[1]["value"], 0)

    def test_isolated_two_month_subscription_still_qualifies(self):
        self.assertEqual(report([tx("2025-10-02", "15", "Service"), tx("2025-11-02", "15", "Service")])[1]["value"], 1)

    def test_established_service_with_single_extra_charge_still_qualifies(self):
        rows = [tx("2025-07-02", "100", "Utility"), tx("2025-08-02", "100", "Utility"),
                tx("2025-09-02", "100", "Utility"), tx("2025-10-02", "100", "Utility"),
                tx("2025-10-05", "6", "Utility"), tx("2025-11-02", "100", "Utility")]
        table = next(b for b in report(rows) if b.get("title") == "Possible recurring payments")
        self.assertEqual(table["rows"][0]["payee"], "Utility")
        self.assertEqual(table["rows"][0]["last"], "2025-11-02")

    def test_accounts_are_distinct_and_inactive_category_is_excluded(self):
        rows = [tx("2025-01-01"), tx("2025-02-01"), tx("2025-03-01"),
                tx("2025-01-01", account_id="b"), tx("2025-02-01", account_id="b"), tx("2025-03-01", account_id="b")]
        self.assertEqual(report(rows)[1]["value"], 2)
        from fixed_costs import detect
        for row in rows:
            row["category_id"] = "transfer"
        self.assertEqual(detect(rows, [{"id": "transfer", "treat_as_transfer": True}], "EUR", __import__("datetime").date(2025, 3, 31)), [])

    def test_missing_payment_and_two_monthly_payments(self):
        self.assertEqual(report([tx("2025-01-01"), tx("2025-02-01")])[1]["value"], 1)
        self.assertEqual(report([tx("2025-01-01"), tx("2025-02-01"), tx("2025-04-01")])[1]["value"], 0)

    def test_monthly_must_be_recent_and_in_two_consecutive_calendar_months(self):
        def result(days, today):
            rows = [tx(day) for day in days]
            params = {"start_date": "2025-01-01", "end_date": today, "today": today, "currency": "EUR",
                      "data": {"transactions": {"rows": rows, "truncated": False}, "categories": []}}
            return plugin.report(params, None)["blocks"][1]["value"]
        self.assertEqual(result(["2025-08-01", "2025-09-01"], "2025-11-22"), 0)
        self.assertEqual(result(["2025-09-01", "2025-10-01"], "2025-11-22"), 1)
        self.assertEqual(result(["2025-10-01", "2025-11-01"], "2025-11-22"), 1)
        self.assertEqual(result(["2025-08-01", "2025-10-01"], "2025-11-22"), 0)
        self.assertEqual(result(["2024-12-31", "2025-01-31"], "2025-02-01"), 1)

    def test_recent_pair_is_not_hidden_by_longer_old_series(self):
        rows = [tx(day) for day in ("2025-01-01", "2025-02-01", "2025-03-01", "2025-09-01", "2025-10-01")]
        params = {"start_date": "2025-01-01", "end_date": "2025-11-22", "today": "2025-11-22", "currency": "EUR",
                  "data": {"transactions": {"rows": rows, "truncated": False}, "categories": []}}
        table = next(b for b in plugin.report(params, None)["blocks"] if b.get("title") == "Possible recurring payments")
        self.assertEqual(table["rows"][0]["last"], "2025-10-01")

    def test_future_transactions_do_not_hide_current_series(self):
        rows = [tx(day) for day in ("2025-09-01", "2025-10-01", "2026-02-01", "2026-03-01")]
        params = {"start_date": "2025-01-01", "end_date": "2026-04-01", "today": "2025-11-22", "currency": "EUR",
                  "data": {"transactions": {"rows": rows, "truncated": False}, "categories": []}}
        table = next(b for b in plugin.report(params, None)["blocks"] if b.get("title") == "Possible recurring payments")
        self.assertEqual(table["rows"][0]["last"], "2025-10-01")

    def test_longer_intervals_expire_after_due_date_with_seven_day_grace(self):
        from datetime import date
        from fixed_costs import active_series
        for kind, days, before, after in (
            ("quarterly", ["2024-07-31", "2024-10-31", "2025-01-31"], date(2025, 5, 7), date(2025, 5, 8)),
            ("half-yearly", ["2024-01-31", "2024-07-31", "2025-01-31"], date(2025, 8, 7), date(2025, 8, 8)),
            ("yearly", ["2024-02-29", "2025-02-28"], date(2026, 3, 7), date(2026, 3, 8)),
        ):
            entries = [(date.fromisoformat(day), None, None, None) for day in days]
            self.assertTrue(active_series(entries, (kind,), before))
            self.assertFalse(active_series(entries, (kind,), after))

    def test_historical_selection_uses_period_end_as_reference(self):
        rows = [tx("2025-01-01"), tx("2025-02-01")]
        params = {"start_date": "2025-01-01", "end_date": "2025-02-28", "today": "2026-11-22", "currency": "EUR",
                  "data": {"transactions": {"rows": rows, "truncated": False}, "categories": []}}
        self.assertEqual(plugin.report(params, None)["blocks"][1]["value"], 1)

    def test_split_ignored_pending_and_transfers_are_excluded(self):
        for change in ({"category_lines": [{"amount": "10"}]}, {"is_ignored": True},
                       {"status": "pending"}, {"type": "transfer"}):
            rows = [tx("2025-01-01", **change), tx("2025-02-01", **change), tx("2025-03-01", **change)]
            self.assertEqual(report(rows)[1]["value"], 0)


if __name__ == "__main__":
    unittest.main()
