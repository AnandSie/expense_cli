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
expense add <amount> <description> [--category CAT] [--date DATE] [--iban IBAN] [--counterparty NAME]
expense list [--category CAT] [--from DATE] [--to DATE] [--unreviewed] [--reviewed]
expense import <file> --bank <bank_name>
expense review [--unidentified] [--uncategorized]
expense edit <id> [--iban IBAN] [--counterparty NAME] [--category CAT]
expense remove <id> [--yes]
expense config bank-list
expense config bank <bank_name>
expense config counterparties
expense config categories
expense version
```

## Architecture

The app is structured in five layers:

1. **`cli.py`** — Typer commands; the only entry point. Calls into storage, importer, identifier, and categorizer. Also contains `config` subcommands for inspecting and validating all TOML configs.
2. **`storage.py`** — CSV persistence at `~/.expense_cli/expenses.csv`. Handles auto-migration of missing columns, the legacy `name→counterparty` column rename, and the `"uncategorized"` sentinel → empty string. Exposes `update_expense()` and `remove_expense()` for in-place edits and deletion.
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

- `expense review` / `expense list --unreviewed` — show transactions needing attention
- `expense list --reviewed` — show fully resolved transactions

## Import Deduplication

`expense import` deduplicates by checking (date, amount, iban, description) against existing rows before writing — done in `cli.py`, not `storage.py`.

## Bank Config Mapping

Each field in `[mapping]` can be:
- A string: direct column name
- A dict with `column` (read directly) and/or `from_column` + `pattern` (regex fallback; first capture group wins, else full match)

The `config bank <name>` command prints and validates a bank TOML. `config counterparties` and `config categories` do the same for their respective files. Validation catches missing required fields, unknown keys, and deprecated field names.

## TOML Parsing

Uses `tomllib` (stdlib, Python 3.11+) or `tomli` (backport for 3.10). Import pattern in `importer.py` and `categorizer.py`:
```python
try:
    import tomllib
except ImportError:
    import tomli as tomllib
```
