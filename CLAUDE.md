# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

Requires Python >=3.10. No test suite exists yet.

## Key Commands

```bash
expense add <amount> <description> [--category CAT] [--date DATE] [--iban IBAN] [--counterparty NAME]
expense list [--category CAT] [--from DATE] [--to DATE]
expense import <file> --bank <bank_name>
expense review [--unidentified] [--uncategorized]
expense edit <id> [--iban IBAN] [--counterparty NAME] [--category CAT]
expense config bank-list
expense config bank <bank_name>
expense config categories
expense version
```

## Architecture

The app is structured in four layers:

1. **`cli.py`** — Typer commands; the only entry point. Calls into storage, importer, and categorizer. Also contains `config` subcommands for inspecting and validating bank and category TOML configs.
2. **`storage.py`** — CSV persistence at `~/.expense_cli/expenses.csv`. Handles auto-migration of missing columns, the legacy `name→counterparty` column rename, and the `"uncategorized"` sentinel → empty string. Exposes `update_expense()` for in-place edits.
3. **`importer.py`** — Parses bank statement files (CSV, TAB, XLS) using bank-specific TOML configs (`~/.expense_cli/banks/<name>.toml`). Handles encoding, delimiters, date formats, and decimal separators per bank. XLS (Excel 97-2003) is read via `xlrd`; date/amount cells are handled by type (numeric date serial → `xlrd.xldate_as_datetime`, number cells used directly). Field mapping supports a flexible config: either a plain string (column name) or a dict with `column` and/or `from_column`+`pattern` (regex with optional capture group).
4. **`categorizer.py`** — Applies rules from `~/.expense_cli/categories.toml` to assign categories. Matches by exact `iban` or substring via `name_contains` (counterparty name).

## User Configuration (runtime, not in repo)

All user data and config lives under `~/.expense_cli/`:
- `expenses.csv` — main data store (auto-created on first write)
- `banks/<name>.toml` — bank-specific column mapping and parsing settings
- `categories.toml` — categorization rules (`iban` or `name_contains` → category)

## Import Deduplication

`expense import` deduplicates by checking (date, amount, iban, description) against existing rows before writing — done in `cli.py`, not `storage.py`.

## Bank Config Mapping

Each field in `[mapping]` can be:
- A string: direct column name
- A dict with `column` (read directly) and/or `from_column` + `pattern` (regex fallback; first capture group wins, else full match)

The `config bank <name>` command prints and validates a bank TOML. The `config categories` command does the same for `categories.toml`. Validation catches missing required fields, unknown keys, and deprecated field names (e.g. `mapping.name` → use `mapping.counterparty`).

## TOML Parsing

Uses `tomllib` (stdlib, Python 3.11+) or `tomli` (backport for 3.10). Import pattern in `importer.py` and `categorizer.py`:
```python
try:
    import tomllib
except ImportError:
    import tomli as tomllib
```
