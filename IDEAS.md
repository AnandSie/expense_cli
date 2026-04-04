# Ideas & Future Direction

A living list of features and directions worth exploring. Not a roadmap — just a place to capture thinking so it can resurface later.

---

## Insights

The goal: surface spending patterns that are hard to see by looking at individual transactions. After import + categorization, the data is structured enough to answer real questions.

### The amount × frequency quadrant

```
               low amount          high amount
              ┌───────────────────┬───────────────────┐
high freq     │  invisible drain  │  high impact      │
              │  ← THE RISK       │  (you notice)     │
              ├───────────────────┼───────────────────┤
low freq      │  negligible       │  one-off, noticed │
              │                   │                   │
              └───────────────────┴───────────────────┘
```

The dangerous quadrant is top-left: small, frequent transactions you individually dismiss but which add up.
The actionable view: sort counterparties by **count descending** and check whether the total surprises you.

### Ideas, roughly by value vs effort

**Easy, high value**
- `expense insights` — total + % + count per category, sorted by amount. One table, immediately useful.
- `expense insights --by counterparty` — same but per vendor; the count column is what makes patterns visible.
- Optional `--from`/`--to` filters on both, to scope to a month or quarter.

**Medium effort, high value**
- **Month-over-month per category** — `(year-month, category)` grouping. Shows drift before it becomes a problem. E.g. "groceries: €280 last month → €340 this month (+21%)". Needs a few months of history to be useful.
- **New counterparties** — transactions where the counterparty appeared for the first time in the period. Catches new subscriptions or new habits early.

**Medium effort, genuinely non-obvious**
- **Subscription / recurrence detection** — group by `(counterparty, amount)`, find rows appearing 2+ times at ~30-day intervals. No new data needed. Subscriptions are easy to forget individually ("it's only €9.99") but compound.
- **Fixed vs variable split** — fixed = same counterparty + same amount recurring; variable = everything else. "Your controllable spend this month was X" is a more actionable number than total spend.

**Lower priority / needs more thought**
- Weekday and time-of-day patterns (`weekday` and `time` already stored). "60% of restaurant spend on Fri/Sat" etc.
- Anomaly flagging: "this month you spent 40% more on X than your 3-month average."

### Display
- Tables beat charts for a CLI — more precise, works at any terminal width, no dependencies.
- A `%` column and a `count` column in the summary table carry most of the insight.
- Rich horizontal bar charts (unicode blocks) could work for top-N categories as a visual aid, but only as a bonus — the table is primary.

---

## Other directions

- **Export** — CSV or JSON export of filtered/aggregated data for use in spreadsheets or other tools.
- **Budget targets** — set a monthly limit per category, warn when approaching it.
- **Tags** — a free-form tag field alongside category, for cross-cutting labels (e.g. "holiday", "one-off") that don't fit a category hierarchy.
