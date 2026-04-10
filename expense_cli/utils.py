def _sparkline(values: list[float]) -> str:
    """Convert a list of floats to a Unicode sparkline (oldest → newest), scaled by absolute value."""
    if not values:
        return ""
    abs_vals = [abs(v) for v in values]
    max_val = max(abs_vals)
    if max_val == 0:
        return " " * len(values)
    blocks = " ▁▂▃▄▅▆▇█"
    return "".join(blocks[min(8, round(v / max_val * 8))] for v in abs_vals)


def _category_parent(category: str) -> str:
    """Return the parent portion of a slash-notation category (e.g. 'food' from 'food/groceries')."""
    return category.split("/", 1)[0] if "/" in category else category


def _category_matches(stored: str, filter_val: str) -> bool:
    """True if stored equals filter_val, or stored starts with filter_val + '/' (prefix match)."""
    s, f = stored.lower(), filter_val.lower()
    return s == f or s.startswith(f + "/")


def _month_range(start: str, end: str) -> list[str]:
    """Return sorted list of 'YYYY-MM' strings from start to end inclusive."""
    if not start or not end:
        return []
    months = []
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def compute_ratio(
    expenses: list[dict],
    num_cats: list[str],
    den_cats: list[str],
    exclude: list[str] | None = None,
) -> float | None:
    """Return abs(sum_numerator) / abs(sum_denominator), or None if denominator is 0.

    Both sides use prefix category matching so e.g. 'investeren' matches 'investeren/etf'.
    Absolute values are used so direction (in/out) doesn't matter.
    Expenses whose category matches any entry in *exclude* are dropped before both sums.
    """
    exclude = exclude or []
    eligible = [
        e for e in expenses
        if not any(_category_matches(e.get("category", ""), x) for x in exclude)
    ]
    num = sum(
        abs(float(e["amount"]))
        for e in eligible
        if any(_category_matches(e.get("category", ""), c) for c in num_cats)
    )
    den = sum(
        abs(float(e["amount"]))
        for e in eligible
        if any(_category_matches(e.get("category", ""), c) for c in den_cats)
    )
    return num / den if den else None
