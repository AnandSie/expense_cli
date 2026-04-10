# Expense CLI

Small Python CLI for tracking and categorizing personal expenses. Uses CSV for storage, TOML for configuration, and Typer + Rich for the CLI.

## Installation

Requires Python >= 3.10 and [pipx](https://pipx.pypa.io/stable/installation/).

**Install from source:**

```bash
git clone https://github.com/AnandSie/expense_cli.git
cd expense_cli
pipx install .
```

`expense` is now available globally in any terminal without activating a virtual environment.

**Upgrading after a `git pull`:**

```bash
pipx reinstall expense-cli
```

---

### Contributing / development install

```bash
git clone https://github.com/AnandSie/expense_cli.git
cd expense_cli
pipx install -e .                          # editable: source changes take effect immediately
pip install -e ".[dev]"                    # install pytest into your local venv for running tests
```

## Usage

```bash
expense add <amount> <description> [--category CAT] [--date DATE] [--time TIME] [--iban IBAN] [--counterparty NAME]
expense list [--category CAT] [--counterparty NAME] [--from DATE] [--to DATE] [--month YYYY-MM] [--year N]
            [--unreviewed] [--reviewed] [--wide] [--direction in|out]
            [--min N] [--max N] [--without-category CAT,...] [--without-counterparty NAME,...] [--id ID,...]
expense import <file> --bank <bank_name> [--force]
expense review [--unidentified] [--uncategorized]
expense edit <id> [--iban IBAN] [--counterparty NAME] [--category CAT]
expense delete <id> [--yes]
expense delete --all
expense insights [--by category|counterparty] [--from DATE] [--to DATE] [--month YYYY-MM]
                 [--direction in|out] [--chart] [--trend] [--rollup]
                 [--without VALUE,...] [--min N] [--max N]
expense reapply
expense config list
expense config bank-list
expense config bank <bank_name>
expense config bank new <bank_name>
expense config bank-set <bank_name> --field FIELD [--column COL] [--from-column COL --pattern REGEX] [--extract-iban-from COL]
expense config counterparties [list]
expense config counterparties add --name NAME [--iban IBAN] [--contains TEXT] [--category CAT]
expense config counterparties edit <name> [--iban IBAN] [--contains TEXT] [--category CAT]
expense config counterparties remove <name>
expense config categories [list]
expense config categories add --counterparty NAME --category CAT
expense config categories edit <counterparty> --category CAT
expense config categories remove <counterparty>
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

Each mapping field can be:
- A plain string — direct column name
- A dict with `column` (direct read) and/or `from_column` + `pattern` (regex; first capture group wins, else full match)
- A dict with `extract_iban_from` — scans the given column for a valid IBAN; used only if exactly one match is found

```toml
iban = "IBANColumn"                                              # direct column
iban = { column = "IBAN", extract_iban_from = "Description" }   # column first, auto-detect as fallback
iban = { extract_iban_from = "Description" }                    # auto-detect IBAN from text
```

**Supported `[bank]` keys:**

| Key | Default | Notes |
|-----|---------|-------|
| `encoding` | `"utf-8"` | CSV/TAB only |
| `date_format` | `"%Y-%m-%d"` | Python strptime format |
| `decimal_separator` | `"."` | use `","` for European formats |
| `delimiter` | `","` | CSV only; `.tab` files use tab automatically |
| `time_format` | *(none)* | optional; Python strptime format (e.g. `"%H:%M:%S"`) |

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
