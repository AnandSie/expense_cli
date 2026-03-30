# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Code Style & Design Principles

### SOLID + Separation of Concerns
- **Single Responsibility** — each module does one thing: `importer` parses files, `identifier` resolves counterparties, `categorizer` assigns categories, `storage` persists data, `cli` handles I/O. Keep it that way; do not bleed logic across layers.
- **Separation of Concerns** — the pipeline stages (import → identify → categorize) are intentionally decoupled. Business logic (matching, categorizing) must never live in `cli.py` or `storage.py`.
- **Open/Closed** — new banks, counterparties, and categories are added via TOML config, not code changes.
- **Dependency Inversion** — `cli.py` depends on abstractions (`read_expenses`, `categorize`, `identify`), not on CSV or TOML internals directly.

### Test-Driven Development
- Write the test first, then the implementation. No new logic without a failing test first.
- Tests live in `tests/`. Run with `.venv/Scripts/pytest`.
- Use `tmp_storage` fixture for any test that touches storage or config files — never touch `~/.expense_cli/`.
- Test behaviour, not implementation — assert on outcomes (CSV contents, CLI output, return values), not on internal calls.

### Python Style
- Type hints on all function signatures.
- No classes unless there is a genuine need — plain functions and dicts are preferred.
- No global mutable state.
- Keep functions small and focused; if a function needs a comment to explain what it does, consider splitting it.
- Prefer explicit over implicit — avoid magic, clever one-liners that obscure intent.
- No print statements; use `typer.echo` or `console.print` in `cli.py`, return values everywhere else.

### What belongs where
- `cli.py` — argument parsing, output formatting, orchestration only. No business logic.
- `storage.py` — CSV read/write/migrate only. No parsing or matching logic.
- `importer.py` — file parsing and field extraction only. No categorization or identification.
- `identifier.py` — counterparty resolution only.
- `categorizer.py` — category assignment only.

## Ideas & Backlog

See [`IDEAS.md`](IDEAS.md) for a prioritized list of future features — especially the **Insights** section, which covers summary/aggregation commands, subscription detection, fixed vs variable spend, and month-over-month trends. Consult it when working on anything analytics-related.

## Project Overview

A small Python CLI for tracking and categorizing personal expenses. Uses CSV for storage, TOML for configuration, and Typer + Rich for the CLI.

## Setup & Running

```bash
# Install in editable mode (use venv at .venv)
pip install -e .

# Run via module (matches .claude/launch.json)
python -m expense_cli <command>

# Run via installed script (after pip install)
expense <command>
```

Requires Python >=3.10.

## Running Tests

```bash
.venv/Scripts/pytest        # all tests
.venv/Scripts/pytest -v     # verbose
```

Tests live in `tests/` and use `tmp_path` + `monkeypatch` to redirect `~/.expense_cli/` to a temp dir. Never touch the real user data.

## Key Commands

```bash
expense add <amount> <description> [--category CAT] [--date DATE] [--time TIME] [--iban IBAN] [--counterparty NAME]
expense list [--category CAT] [--from DATE] [--to DATE] [--unreviewed] [--reviewed]
expense import <file> --bank <bank_name> [--force]
expense review [--unidentified] [--uncategorized] [--interactive|-i]
expense edit <id> [--iban IBAN] [--counterparty NAME] [--category CAT]
expense delete <id> [--yes]
expense delete --all
expense config bank-list
expense config bank <bank_name>
expense config counterparties
expense config categories
expense version
```

## Architecture

The app is structured in five layers:

1. **`cli.py`** — Typer commands; the only entry point. Calls into storage, importer, identifier, and categorizer. Also contains `config` subcommands for inspecting and validating all TOML configs.
2. **`storage.py`** — CSV persistence at `~/.expense_cli/expenses.csv`. Handles auto-migration of missing columns, the legacy `name→counterparty` column rename, and the `"uncategorized"` sentinel → empty string. Exposes `update_expense()`, `delete_expense()`, and `reset_expenses()` for in-place edits and deletion.
3. **`importer.py`** — Parses bank statement files (CSV, TAB, XLS) using bank-specific TOML configs (`~/.expense_cli/banks/<name>.toml`). Handles encoding, delimiters, date formats, and decimal separators per bank. XLS (Excel 97-2003) is read via `xlrd`; date/amount cells are handled by type (numeric date serial → `xlrd.xldate_as_datetime`, number cells used directly). Field mapping supports a flexible config: either a plain string (column name) or a dict with `column` and/or `from_column`+`pattern` (regex with optional capture group).
4. **`identifier.py`** — Resolves raw import data to a normalized counterparty name using `~/.expense_cli/counterparties.toml`. Matches by exact IBAN first, then `description_contains` substring. Only runs if no counterparty was set during import mapping.
5. **`categorizer.py`** — Assigns a category from `~/.expense_cli/categories.toml` by matching the normalized counterparty name (exact, case-insensitive).

## Pipeline (import flow)

```
bank CSV → importer (raw fields) → identifier (normalized counterparty) → categorizer (category)
```

## User Configuration (runtime, not in repo)

All user data and config lives under `~/.expense_cli/`:
- `expenses.csv` — main data store (auto-created on first write)
- `banks/<name>.toml` — bank-specific column mapping and parsing settings
- `counterparties.toml` — identification rules (iban or description_contains → normalized name)
- `categories.toml` — categorization rules (counterparty name → category)

## Reviewed vs Unreviewed

A transaction is **reviewed** when it has both `counterparty` and `category` set (non-empty). IBAN is a helper for identification only — not part of the reviewed definition.

- `expense review` — show all unreviewed (missing counterparty OR category)
- `expense review --unidentified` — only missing counterparty
- `expense review --uncategorized` — only missing category
- `expense list --unreviewed` / `--reviewed` — filter the full list

## Interactive Review (`review -i`)

Steps through unreviewed expenses one at a time. For each expense:
1. Shows date, amount, IBAN, and description (description last — it can be long)
2. If counterparty is missing: prompts for it
3. If category is missing: prompts for it; auto-suggests based on `categories.toml` if counterparty matched

The prompt UI (`_pick` in `cli.py`) uses raw terminal input with three lines:
- **prompt line** — where the user types
- **options line** — live-filtered shortcuts (updates on every keystroke)
- **hint line** — always visible, erased after each field is submitted

Controls:
- **type** → filters the options list live (case-insensitive substring)
- **digit** → instantly picks from the current filtered list (works even mid-word)
- **Enter** → submits typed value, or skips field if empty
- **Ctrl+S** → skips the whole transaction (move to next)
- **Ctrl+Q** → quits the session

Known counterparties come from existing `expenses.csv` values + `counterparties.toml` names. Known categories come from existing values + `categories.toml` rules. New values typed during review are added to the shortcut list for the rest of the session.

`Console(highlight=False)` is set globally — Rich auto-highlighting is disabled to prevent bank data (dates, numbers, IBANs) from being colored unexpectedly.

## Delete

- `expense delete <id> [--yes]` — delete a single expense; prompts for confirmation unless `--yes`
- `expense delete --all` — wipe all expenses; requires typing `DELETE` to confirm

## Data Model

`FIELDNAMES` in `storage.py`:
```
id, date, weekday, time, amount, description, iban, counterparty, category, source_hash
```

- `weekday` — derived from `date` by the caller (cli.py or migration), never by storage write functions
- `time` — stored as `HH:MM:SS`; empty string if not available
- `source_hash` — 16-char SHA-256 of all raw bank file columns; used for deduplication; never displayed

## Import Deduplication

`expense import` deduplicates using `source_hash` (SHA-256 of all raw bank columns, including columns not stored in the data model like `BeginSaldo`/`EindSaldo`). This handles cases where two legitimate transactions share the same date, amount, IBAN, and description but differ in other bank-provided fields.

- If a row has no `source_hash` (rows imported before this feature), falls back to `(date, amount, iban, description)` tuple comparison.
- Duplicate rows are shown in a table so the user can inspect them.
- `--force` bypasses deduplication entirely and imports all rows.
- Deduplication logic lives in `cli.py`, not `storage.py`.

## Bank Config Mapping

Each field in `[mapping]` can be:
- A string: direct column name
- A dict with `column` (read directly) and/or `from_column` + `pattern` (regex fallback; first capture group wins, else full match)
- A dict with `extract_iban_from` (scans the given column for a valid IBAN; used only if exactly one match is found — empty if zero or multiple)

Examples:
```toml
iban = "IBANColumn"                                           # direct column
iban = { column = "IBANColumn" }                             # same, dict form
iban = { from_column = "Description", pattern = "..." }      # custom regex
iban = { extract_iban_from = "Description" }                 # auto-detect IBAN from text
iban = { column = "IBAN", extract_iban_from = "Description" } # column first, auto-detect as fallback
```

The `config bank <name>` command prints and validates a bank TOML. `config counterparties` and `config categories` do the same for their respective files. Validation catches missing required fields, unknown keys, and deprecated field names.

## TOML Parsing

Uses `tomllib` (stdlib, Python 3.11+) or `tomli` (backport for 3.10). Import pattern in `importer.py` and `categorizer.py`:
```python
try:
    import tomllib
except ImportError:
    import tomli as tomllib
```
