"""Securo code report for possible recurring expenses; no external dependencies."""
from collections import defaultdict
import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

# Monthly and annual series need two occurrences; longer intermediate intervals need three.
INTERVALS = (("monthly", 30.44, 24, 38), ("quarterly", 91.31, 75, 105),
             ("half-yearly", 182.62, 160, 205), ("yearly", 365.25, 330, 400))
TEXT = {"monthly": "Monthly", "quarterly": "Quarterly", "half-yearly": "Half-yearly", "yearly": "Yearly",
        "total": "Estimated monthly cost", "count": "Possible recurring payments", "table": "Possible recurring payments",
        "annualized": "Annualized estimate", "annualized_hint": "12 × the current monthly estimate",
        "largest": "Largest monthly equivalent", "delta_hint": "Change in estimate vs previous month",
        "payee": "Payee", "interval": "Interval", "amount": "Latest amount", "monthly_amount": "Monthly equivalent",
        "last": "Last payment", "category": "Category", "uncategorized": "Uncategorized",
        "review": "Review", "amount_changed": "Latest amount changed",
        "trend": "Estimated monthly costs over time", "trend_series": "Estimated monthly costs",
        "trend_hint": "Each month is estimated using only payments available by then. Earlier months can be incomplete.",
        "category_table": "Category breakdown",
        "series_limit": "Only the 400 most recent transactions per payee were checked.",
        "truncated": "The transaction history was truncated. Results are incomplete.",
        "empty": "No active recurring payments detected. Monthly payments need entries in two consecutive months and a recent payment. Select a longer period if necessary."}



def money(tx, currency):
    value = tx.get("amount_primary")
    if value is None and (tx.get("currency") or "").upper() == currency.upper():
        value = tx.get("amount")
    try:
        result = abs(Decimal(str(value)))
        return result if result.is_finite() and result > 0 else None
    except (InvalidOperation, ValueError):
        return None


def active_series(entries, interval, as_of):
    last = entries[-1][0]
    key = interval[0]
    if last > as_of:
        return False
    if key == "monthly":
        current_month = as_of.year * 12 + as_of.month
        last_month = last.year * 12 + last.month
        previous = entries[-2][0]
        previous_month = previous.year * 12 + previous.month
        return current_month - last_month in (0, 1) and last_month - previous_month == 1
    months = {"quarterly": 3, "half-yearly": 6, "yearly": 12}[key]
    target = last.year * 12 + last.month - 1 + months
    year, zero_month = divmod(target, 12)
    month = zero_month + 1
    due = date(year, month, min(last.day, calendar.monthrange(year, month)[1]))
    # Bank booking dates can move slightly around weekends and holidays.
    return as_of <= due + timedelta(days=7)


def detect(rows, categories, currency, as_of):
    excluded = {c["id"] for c in categories if c.get("is_ignored") or c.get("treat_as_transfer")}
    groups = defaultdict(list)
    for tx in rows:
        if tx.get("type") != "debit" or tx.get("status") != "posted" or tx.get("is_ignored"):
            continue
        if tx.get("category_lines") or tx.get("category_id") in excluded:
            continue
        payee = (tx.get("payee") or "").strip()
        description = (tx.get("description") or "").casefold()
        # Amazon's subscription and one-off orders can share the same payee.
        prime = "amazon" in payee.casefold() and "prime" in description
        if prime:
            payee = "Amazon Prime"
        amount = money(tx, currency)
        try:
            day = date.fromisoformat(tx.get("date") or "")
        except ValueError:
            continue
        if payee and amount is not None and day <= as_of:
            groups[(tx.get("account_id"), " ".join(payee.casefold().split()))].append((day, amount, payee, tx.get("category_id")))
    found = []
    for entries in groups.values():
        entries.sort(key=lambda item: item[0])
        # Bound work for very high-volume merchants. The report warns if this occurs.
        entries = entries[-400:]
        if len(entries) < 2:
            continue
        best = None
        for interval in INTERVALS:
            key, _, low, high = interval
            paths = []
            for index, current in enumerate(entries):
                # A path contains indices and the count of ordinary (unskipped) cycles.
                best_path, direct_count = [index], 0
                for previous in range(index - 1, -1, -1):
                    gap = (current[0] - entries[previous][0]).days
                    if gap > high * 2:
                        break
                    direct = low <= gap <= high
                    skipped = key != "yearly" and low * 2 <= gap <= high * 2
                    if not (direct or skipped):
                        continue
                    old_amount = entries[previous][1]
                    if abs(current[1] - old_amount) > max(Decimal("1"), old_amount * Decimal("0.30")):
                        continue
                    old_path, old_direct = paths[previous]
                    candidate = old_path + [index]
                    candidate_direct = old_direct + int(direct)
                    if (len(candidate), candidate_direct) > (len(best_path), direct_count):
                        best_path, direct_count = candidate, candidate_direct
                paths.append((best_path, direct_count))
                minimum = 2 if key in ("monthly", "yearly") else 3
                if len(best_path) >= minimum and direct_count:
                    # Prefer the latest eligible sequence, then stronger and more regular evidence.
                    score = (current[0].toordinal(), len(best_path), direct_count)
                    if best is None or score > best[0]:
                        best = (score, interval, best_path)
        if best is None:
            continue
        _, interval, indices = best
        selected = [entries[index] for index in indices]
        # A recent price change or an early payment may follow an established series.
        # Ignore intervening transactions only when their amounts clearly differ.
        remaining = [entry for entry in entries if entry[0] > selected[-1][0]]
        if len(selected) >= 3 and remaining:
            latest = remaining[-1]
            gap = (latest[0] - selected[-1][0]).days
            key, _, low, high = interval
            tolerance = max(Decimal("1"), selected[-1][1] * Decimal("0.30"))
            similar = abs(latest[1] - selected[-1][1]) <= tolerance
            unrelated = all(abs(entry[1] - selected[-1][1]) > tolerance for entry in remaining[:-1])
            if unrelated and ((low <= gap <= high) or (key == "monthly" and 20 <= gap <= high and similar)):
                selected.append(latest)
        if interval[0] == "monthly":
            # A payee with frequent unrelated purchases can accidentally have
            # a monthly-looking subsequence. Look at recent activity only, so
            # old unrelated activity cannot hide a newly established series.
            cutoff = as_of - timedelta(days=183)
            recent = [entry for entry in entries if entry[0] >= cutoff]
            matched = [entry for entry in selected if entry[0] >= cutoff]
            if len(matched) < len(recent):
                recent_months = {(entry[0].year, entry[0].month) for entry in recent}
                matched_months = {(entry[0].year, entry[0].month) for entry in matched}
                if (len(matched) < 3 or 2 * len(matched) <= len(recent)
                        or 3 * len(matched_months) < 2 * len(recent_months)):
                    continue
        if active_series(selected, interval, as_of):
            found.append((selected, interval))
    return found


class Plugin:
    def widget(self, params, context):
        key = params.get("widget")
        if key not in ("recurring-payments", "monthly-fixed-costs", "annual-fixed-costs", "recurring-payments-compact"):
            raise ValueError("Unknown widget")
        blocks = self.report(params, context)["blocks"]
        notices = [block for block in blocks if block.get("type") == "text"
                   and block.get("text") in (TEXT["truncated"], TEXT["series_limit"])]
        if key in ("monthly-fixed-costs", "annual-fixed-costs"):
            value = blocks[0 if key == "monthly-fixed-costs" else 2]
            return {"blocks": [{**value, "width": "full"}, *notices]}
        summary = [{**blocks[0], "width": "third"}, {**blocks[2], "width": "third"},
                   {**blocks[1], "width": "third"}]
        if key == "recurring-payments-compact":
            summary = []
        table = next((block for block in blocks if block.get("title") == TEXT["table"]), None)
        if table is None:
            return {"blocks": [*summary, *notices, {"type": "text", "text": TEXT["empty"]}]}
        compact = {**table, "columns": [column for column in table["columns"]
                                         if column["key"] in ("payee", "monthly")],
                   "rows": [{"payee": row["payee"], "monthly": row["monthly"]}
                            for row in table["rows"]], "page_size": 5 if key == "recurring-payments-compact" else 10}
        if key == "recurring-payments-compact":
            compact.pop("title", None)
        return {"blocks": [*summary, *notices, compact]}

    def report(self, params, context):
        t = TEXT
        data = params["data"]
        txs = data["transactions"]
        end = date.fromisoformat(params["end_date"])
        today = date.fromisoformat(params.get("today", params["end_date"]))
        as_of = min(today, end)
        found = detect(txs["rows"], data.get("categories", []), params["currency"], as_of)
        categories = {c["id"]: c for c in data.get("categories", [])}
        items = []
        for entries, interval in found:
            day, amount, payee, category_id = entries[-1]
            monthly = amount / {"monthly": 1, "quarterly": 3, "half-yearly": 6, "yearly": 12}[interval[0]]
            category = categories.get(category_id, {})
            previous = entries[-2][1]
            changed = abs(amount - previous) > max(Decimal("1"), previous * Decimal("0.30"))
            items.append({"row": {"payee": payee[:500], "category": category.get("name", t["uncategorized"])[:500],
                                  "interval": t[interval[0]], "amount": float(amount),
                                  "monthly": float(monthly), "last": day.isoformat(),
                                  "review": t["amount_changed"] if changed else ""},
                          "entries": entries, "monthly": monthly})
        items.sort(key=lambda x: -x["monthly"])
        monthly_total = sum((x["monthly"] for x in items), Decimal(0))
        blocks = [
            {"type": "kpi", "label": t["total"], "value": float(round(monthly_total, 2))},
            {"type": "kpi", "label": t["count"], "value": len(items), "format": "number"},
            {"type": "kpi", "label": t["annualized"],
             "value": float(round(monthly_total * 12, 2)) if items else None, "hint": t["annualized_hint"]},
            {"type": "kpi", "label": t["largest"],
             "value": float(round(items[0]["monthly"], 2)) if items else None,
             "hint": items[0]["row"]["payee"][:120] if items else None},
        ]
        if txs.get("truncated"):
            blocks.append({"type": "text", "text": t["truncated"]})
        payee_counts = defaultdict(int)
        for tx in txs["rows"]:
            if tx.get("payee"):
                payee_counts[(tx.get("account_id"), " ".join(tx["payee"].casefold().split()))] += 1
        if any(count > 400 for count in payee_counts.values()):
            blocks.append({"type": "text", "text": t["series_limit"]})
        if items:
            blocks.append({"type": "table", "title": t["table"], "sort_by": "monthly", "columns": [
                {"key": "payee", "label": t["payee"]}, {"key": "category", "label": t["category"]},
                {"key": "interval", "label": t["interval"]},
                {"key": "amount", "label": t["amount"], "format": "currency"},
                {"key": "monthly", "label": t["monthly_amount"], "format": "currency"},
                {"key": "last", "label": t["last"], "format": "date"},
                {"key": "review", "label": t["review"]}], "rows": [x["row"] for x in items[:1000]]})
            category_totals = defaultdict(Decimal)
            for item in items:
                category_totals[item["row"]["category"]] += item["monthly"]
            top = sorted(category_totals.items(), key=lambda x: -x[1])[:1000]
            blocks.append({"type": "table", "title": t["category_table"], "width": "half",
                           "columns": [{"key": "category", "label": t["category"]},
                                       {"key": "monthly", "label": t["monthly_amount"], "format": "currency"}],
                           "sort_by": "monthly", "rows": [{"category": name[:500], "monthly": float(value)} for name, value in top]})
        else:
            blocks.append({"type": "text", "text": t["empty"]})
        # Re-run detection at each month end without using later transactions.
        # Blank months mean insufficient evidence, not zero costs.
        start = date.fromisoformat(params["start_date"])
        end_index = as_of.year * 12 + as_of.month - 1
        start_index = start.year * 12 + start.month - 1
        labels, amounts = [], []
        divisors = {"monthly": 1, "quarterly": 3, "half-yearly": 6, "yearly": 12}
        for index in range(max(start_index, end_index - 5), end_index + 1):
            year, zero_month = divmod(index, 12)
            month = zero_month + 1
            snapshot = min(as_of, date(year, month, calendar.monthrange(year, month)[1]))
            labels.append(f"{year:04d}-{month:02d}")
            if index == start_index:
                amounts.append(None)
                continue
            historical = found if snapshot == as_of else detect(txs["rows"], data.get("categories", []), params["currency"], snapshot)
            total = sum((entries[-1][1] / divisors[interval[0]] for entries, interval in historical), Decimal(0))
            amounts.append(float(round(total, 2)) if historical else None)
        if (not txs.get("truncated") and len(amounts) >= 2
                and amounts[-2] is not None and amounts[-1] is not None):
            blocks[0]["delta"] = float(round(Decimal(str(amounts[-1])) - Decimal(str(amounts[-2])), 2))
            blocks[0]["delta_good"] = "down"
            blocks[0]["hint"] = t["delta_hint"]
        if any(value is not None for value in amounts):
            blocks.append({"type": "chart", "title": t["trend"], "chart": "bar", "width": "half" if items else "full",
                           "x": labels, "x_format": "month",
                           "series": [{"name": t["trend_series"], "values": amounts}]})
            blocks.append({"type": "text", "text": t["trend_hint"]})
        return {"blocks": blocks}


plugin = Plugin()
