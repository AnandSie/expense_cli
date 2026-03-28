# Expense CLI

Small Python CLI for tracking and categorizing personal expenses. Uses CSV for storage, TOML for configuration, and Typer + Rich for the CLI.

## Installation

```bash
python -m venv .venv
.venv/Scripts/pip install -e .
```

Requires Python >= 3.10.

## Usage

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

## The Three-Step Pipeline

Every imported transaction goes through three steps:

1. **Import mapping** — raw bank columns → internal model (`date`, `amount`, `description`, `iban`, `counterparty`)
2. **Identification** — resolve a normalized counterparty name via `counterparties.toml` (by IBAN or description substring)
3. **Categorization** — assign a category via `categories.toml` (by exact counterparty name)

A transaction is considered **reviewed** when it has both a `counterparty` and a `category`. IBAN is a helper for identification — not a goal in itself.

Use `expense review` or `expense list --unreviewed` to find transactions still needing attention. Use `expense edit` to fix them manually.

## Configuration

All config lives under `~/.expense_cli/`:

| File | Purpose |
|------|---------|
| `expenses.csv` | Main data store (auto-created) |
| `banks/<name>.toml` | Bank-specific import mapping |
| `counterparties.toml` | Step 2: IBAN/description → counterparty name |
| `categories.toml` | Step 3: counterparty name → category |

Use `expense config bank <name>`, `expense config counterparties`, and `expense config categories` to print and validate any config file.

---

### Bank config (`~/.expense_cli/banks/<name>.toml`)

Tells the importer how to parse your bank's export file. See `examples/banks/` for ready-to-copy templates.

**Simple case — dedicated columns for everything:**

```toml
[bank]
encoding          = "utf-8"
date_format       = "%Y-%m-%d"
decimal_separator = "."
delimiter         = ","

[mapping]
date         = "Date"
amount       = "Amount"
description  = "Description"
iban         = "CounterpartyIBAN"
counterparty = "CounterpartyName"
```

**Flexible mapping — extract IBAN/counterparty via regex from a description field:**

```toml
[bank]
date_format       = "%d.%m.%Y"
decimal_separator = ","

[mapping]
date        = "Buchungsdatum"
amount      = "Betrag"
description = "Verwendungszweck"

[mapping.iban]
column      = "Gegenkontonummer"
from_column = "Verwendungszweck"
pattern     = '[A-Z]{2}\d{2}[A-Z0-9]+'

[mapping.counterparty]
from_column = "Verwendungszweck"
pattern     = 'Auftraggeber:\s*(.+?)(?:\s{2,}|$)'
```

Each mapping field can be a plain string (column name) or a dict with `column` (direct read) and/or `from_column` + `pattern` (regex fallback; first capture group wins, else full match).

**Supported `[bank]` keys:**

| Key | Default | Notes |
|-----|---------|-------|
| `encoding` | `"utf-8"` | CSV/TAB only |
| `date_format` | `"%Y-%m-%d"` | Python strptime format |
| `decimal_separator` | `"."` | use `","` for European formats |
| `delimiter` | `","` | CSV only; `.tab` files use tab automatically |

**Supported date format examples:**

| Bank format | `date_format` value |
|-------------|---------------------|
| `2026-01-03` | `"%Y-%m-%d"` |
| `20260103` | `"%Y%m%d"` |
| `03.01.2026` | `"%d.%m.%Y"` |

---

### Counterparties config (`~/.expense_cli/counterparties.toml`)

Maps raw IBAN or description substrings to a normalized counterparty name. Rules are evaluated in order; first match wins.

```toml
[[counterparty]]
iban = "NL91ABNA0417164300"
name = "Albert Heijn"

[[counterparty]]
description_contains = "netflix"
name                 = "Netflix"

[[counterparty]]
description_contains = "spotify"
name                 = "Spotify"
```

Each entry needs exactly one of `iban` (exact match) or `description_contains` (case-insensitive substring), plus a `name`.

---

### Categories config (`~/.expense_cli/categories.toml`)

Maps normalized counterparty names to categories. Rules are evaluated in order; first match wins.

```toml
[[rules]]
counterparty = "Albert Heijn"
category     = "groceries"

[[rules]]
counterparty = "Netflix"
category     = "subscriptions"

[[rules]]
counterparty = "Spotify"
category     = "subscriptions"
```

Each rule needs `counterparty` (exact, case-insensitive match) and `category`.

---

## Running Tests

```bash
.venv/Scripts/pytest
.venv/Scripts/pytest -v   # verbose
```
