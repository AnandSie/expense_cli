---
name: Expense CLI Architecture Decisions
description: Key architectural decisions, module boundaries, and technical debt observed in the codebase
type: project
---

Current modules as of 2026-04-05:
- cli.py — Typer commands, output only. Has _parse_month, _render_bar, _sparkline, _category_matches, _month_range helper functions.
- storage.py — CSV CRUD. FIELDNAMES list drives schema. _migrate() adds columns and fixes sentinels on read.
- importer.py — bank CSV/XLS parsing. Flexible field_config (string, dict with column/from_column/pattern/extract_iban_from).
- identifier.py — counterparty resolution from counterparties.toml. Also owns all CRUD on that TOML.
- categorizer.py — thin wrapper re-exporting identifier functions; categorize() does name match against counterparty entries with a category field.
- toml_store.py — shared TOML read/write utility (write_toml_array).

Key decisions:
- categories.toml was merged into counterparties.toml — category is now a field on a counterparty entry, not a separate file. Migration handled in identifier.py.
- direction field exists on expenses (in/out). Analytics should filter to direction == "out" for spend analysis.
- source_hash is internal deduplication only — must be stripped in any user-facing export.
- amount is stored as string in CSV — all computation must float() it.
- Reviewed = counterparty AND category both non-empty (IBAN not required).

Technical debt noted:
- _category_matches and _month_range in cli.py need to move to a shared utility if analytics.py needs them.
- FIELDNAMES includes "direction" (added recently per storage.py line 9 TODO comment area) — roadmap analytics must account for this.

New modules anticipated by roadmap:
- analytics.py (v0.2) — pure functions, no I/O
- exporter.py (v0.4) — pure functions, no I/O
- budget.py (v0.5) — pure functions + load/validate

conftest.py tmp_storage fixture must be extended when new path constants are added (e.g., BUDGETS_PATH in v0.5).
