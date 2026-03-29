import typer
from datetime import date
import calendar
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from expense_cli import __version__
from expense_cli.storage import read_expenses, write_expense, write_expenses_batch, next_id, update_expense, delete_expense, reset_expenses, _weekday_from_date

app = typer.Typer(
    help="A command-line tool for organizing and categorizing expenses."
)
config_app = typer.Typer(help="Inspect and validate configuration files.")
app.add_typer(config_app, name="config")
console = Console(highlight=False)

BANKS_DIR = Path.home() / ".expense_cli" / "banks"
CATEGORIES_PATH = Path.home() / ".expense_cli" / "categories.toml"
COUNTERPARTIES_PATH = Path.home() / ".expense_cli" / "counterparties.toml"


def _parse_month(month: str, year: Optional[int] = None) -> tuple[str, str]:
    """Parse a --month value into (from_date, to_date) strings (YYYY-MM-DD).

    Accepts:
        "2026-03"  — explicit year-month
        "3"        — month number; requires year parameter
    """
    if "-" in month:
        try:
            y, m = month.split("-")
            year_val, month_val = int(y), int(m)
        except ValueError:
            raise ValueError(f"Invalid --month format '{month}'. Use YYYY-MM or a number 1-12.")
    else:
        try:
            month_val = int(month)
        except ValueError:
            raise ValueError(f"Invalid --month value '{month}'. Use YYYY-MM or a number 1-12.")
        year_val = year if year is not None else date.today().year

    if not 1 <= month_val <= 12:
        raise ValueError(f"Month must be between 1 and 12, got {month_val}.")

    last_day = calendar.monthrange(year_val, month_val)[1]
    from_date = f"{year_val:04d}-{month_val:02d}-01"
    to_date = f"{year_val:04d}-{month_val:02d}-{last_day:02d}"
    return from_date, to_date


def _validate_field_config(value, field_name: str) -> list[str]:
    """Validate a single mapping field config (string or dict). Returns error messages."""
    errors = []
    if isinstance(value, str):
        if not value.strip():
            errors.append(f"mapping.{field_name}: column name must not be empty")
    elif isinstance(value, dict):
        has_column = "column" in value
        has_from_column = "from_column" in value
        has_pattern = "pattern" in value
        if not has_column and not (has_from_column and has_pattern):
            errors.append(
                f"mapping.{field_name}: dict config must have 'column' or both 'from_column' and 'pattern'"
            )
    else:
        errors.append(f"mapping.{field_name}: must be a string or table, got {type(value).__name__}")
    return errors


def _validate_bank_config(config: dict) -> list[str]:
    errors = []
    if "mapping" not in config:
        errors.append("Missing required [mapping] section")
        return errors
    mapping = config["mapping"]
    for required in ("date", "amount"):
        if required not in mapping:
            errors.append(f"mapping.{required}: required field is missing")
        else:
            errors.extend(_validate_field_config(mapping[required], required))
    for optional in ("description", "time", "iban", "counterparty"):
        if optional in mapping:
            errors.extend(_validate_field_config(mapping[optional], optional))
    if "name" in mapping:
        errors.append("mapping.name: rename to mapping.counterparty")
    known_bank_keys = {"encoding", "date_format", "time_format", "decimal_separator", "delimiter"}
    for key in config.get("bank", {}):
        if key not in known_bank_keys:
            errors.append(f"[bank].{key}: unknown key")
    return errors


def _validate_categories_config(config: dict) -> list[str]:
    errors = []
    rules = config.get("rules", [])
    if not isinstance(rules, list):
        errors.append("'rules' must be an array ([[rules]] entries)")
        return errors
    for i, rule in enumerate(rules):
        prefix = f"rules[{i}]"
        if "category" not in rule or not rule["category"]:
            errors.append(f"{prefix}: missing or empty 'category'")
        if "counterparty" not in rule or not rule["counterparty"]:
            errors.append(f"{prefix}: missing or empty 'counterparty'")
        for legacy in ("iban", "name_contains"):
            if legacy in rule:
                errors.append(f"{prefix}: '{legacy}' is no longer valid — use counterparties.toml for matching, categories.toml maps counterparty → category")
    return errors


def _validate_counterparties_config(config: dict) -> list[str]:
    errors = []
    entries = config.get("counterparty", [])
    if not isinstance(entries, list):
        errors.append("'counterparty' must be an array ([[counterparty]] entries)")
        return errors
    for i, entry in enumerate(entries):
        prefix = f"counterparty[{i}]"
        if "name" not in entry or not entry["name"]:
            errors.append(f"{prefix}: missing or empty 'name'")
        matchers = [k for k in entry if k in ("iban", "description_contains")]
        if len(matchers) == 0:
            errors.append(f"{prefix}: must have 'iban' or 'description_contains'")
        if len(matchers) > 1:
            errors.append(f"{prefix}: has both 'iban' and 'description_contains' — only one matcher per entry")
    return errors


@config_app.command(name="bank-list")
def config_bank_list() -> None:
    """List all available bank configs."""
    if not BANKS_DIR.exists() or not list(BANKS_DIR.glob("*.toml")):
        typer.echo("No bank configs found in ~/.expense_cli/banks/")
        return
    for path in sorted(BANKS_DIR.glob("*.toml")):
        typer.echo(path.stem)


@config_app.command(name="bank")
def config_bank(
    bank: str = typer.Argument(..., help="Bank name (matches ~/.expense_cli/banks/<name>.toml)"),
) -> None:
    """Print and validate a bank config file."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    path = BANKS_DIR / f"{bank}.toml"
    if not path.exists():
        typer.echo(f"No bank config found at {path}", err=True)
        raise typer.Exit(1)

    content = path.read_text(encoding="utf-8")
    console.print(Syntax(content, "toml", theme="ansi_dark", line_numbers=True))

    config = tomllib.loads(content)
    errors = _validate_bank_config(config)
    if errors:
        console.print("\n[red]Validation errors:[/red]")
        for e in errors:
            console.print(f"  [red]x[/red] {e}", highlight=False)
    else:
        console.print("\n[green]OK Config is valid[/green]")


@config_app.command(name="categories")
def config_categories() -> None:
    """Print and validate the categories config file."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    if not CATEGORIES_PATH.exists():
        typer.echo(f"No categories config found at {CATEGORIES_PATH}", err=True)
        raise typer.Exit(1)

    content = CATEGORIES_PATH.read_text(encoding="utf-8")
    console.print(Syntax(content, "toml", theme="ansi_dark", line_numbers=True))

    config = tomllib.loads(content)
    errors = _validate_categories_config(config)
    if errors:
        console.print("\n[red]Validation errors:[/red]")
        for e in errors:
            console.print(f"  [red]x[/red] {e}", highlight=False)
    else:
        console.print(f"\n[green]OK Config is valid ({len(config.get('rules', []))} rules)[/green]")


@config_app.command(name="counterparties")
def config_counterparties() -> None:
    """Print and validate the counterparties config file."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    if not COUNTERPARTIES_PATH.exists():
        typer.echo(f"No counterparties config found at {COUNTERPARTIES_PATH}", err=True)
        raise typer.Exit(1)

    content = COUNTERPARTIES_PATH.read_text(encoding="utf-8")
    console.print(Syntax(content, "toml", theme="ansi_dark", line_numbers=True))

    config = tomllib.loads(content)
    errors = _validate_counterparties_config(config)
    if errors:
        console.print("\n[red]Validation errors:[/red]")
        for e in errors:
            console.print(f"  [red]x[/red] {e}", highlight=False)
    else:
        console.print(f"\n[green]OK Config is valid ({len(config.get('counterparty', []))} entries)[/green]")


@app.command()
def add(
    amount: float = typer.Argument(..., help="Expense amount (e.g. 12.50)"),
    description: str = typer.Argument(..., help="Short description of the expense"),
    category: str = typer.Option("", "--category", "-c", help="Expense category"),
    expense_date: Optional[str] = typer.Option(None, "--date", "-d", help="Date in YYYY-MM-DD format (default: today)"),
    expense_time: Optional[str] = typer.Option(None, "--time", help="Time in HH:MM:SS format"),
    iban: str = typer.Option("", "--iban", help="Counterparty IBAN"),
    counterparty: str = typer.Option("", "--counterparty", "-n", help="Counterparty name"),
) -> None:
    """Add a new expense."""
    if expense_date is None:
        expense_date = date.today().isoformat()

    expenses = read_expenses()
    row = {
        "id": next_id(expenses),
        "date": expense_date,
        "weekday": _weekday_from_date(expense_date),
        "time": expense_time or "",
        "amount": f"{amount:.2f}",
        "description": description,
        "category": category,
        "iban": iban,
        "counterparty": counterparty,
    }
    write_expense(row)
    typer.echo(f"Added expense #{row['id']}")


@app.command(name="list")
def list_expenses(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category"),
    from_date: Optional[str] = typer.Option(None, "--from", help="Start date YYYY-MM-DD (inclusive)"),
    to_date: Optional[str] = typer.Option(None, "--to", help="End date YYYY-MM-DD (inclusive)"),
    month: Optional[str] = typer.Option(None, "--month", help="Filter by month: YYYY-MM or 1-12"),
    year: Optional[int] = typer.Option(None, "--year", help="Year to use with --month number (default: current year)"),
    unreviewed: bool = typer.Option(False, "--unreviewed", help="Show only expenses missing counterparty or category"),
    reviewed: bool = typer.Option(False, "--reviewed", help="Show only expenses with counterparty and category set"),
    wide: bool = typer.Option(False, "--wide", "-w", help="Show all columns (description, IBAN, weekday, time)"),
) -> None:
    """List expenses, optionally filtered by category or date range."""
    if month and (from_date or to_date):
        typer.echo("--month cannot be combined with --from or --to.", err=True)
        raise typer.Exit(1)

    if month:
        try:
            from_date, to_date = _parse_month(month, year)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)

    expenses = read_expenses()

    if category:
        expenses = [e for e in expenses if e["category"].lower() == category.lower()]
    if from_date:
        expenses = [e for e in expenses if e["date"] >= from_date]
    if to_date:
        expenses = [e for e in expenses if e["date"] <= to_date]
    if unreviewed:
        expenses = [e for e in expenses if not e.get("counterparty") or not e["category"]]
    elif reviewed:
        expenses = [e for e in expenses if e.get("counterparty") and e["category"]]

    if not expenses:
        typer.echo("No expenses found.")
        return

    total = f"{sum(float(e['amount']) for e in expenses):.2f}"
    table = Table(show_footer=True)
    table.add_column("ID", style="dim")
    table.add_column("Date")
    table.add_column("Weekday", style="dim")
    if wide:
        table.add_column("Time", style="dim")
    table.add_column("Amount", justify="right", footer=total)
    if wide:
        table.add_column("Description")
        table.add_column("IBAN", style="dim")
    table.add_column("Counterparty")
    table.add_column("Category")

    for e in expenses:
        row = [e["id"], e["date"], e.get("weekday", "")]
        if wide:
            row.append(e.get("time", ""))
        row.append(e["amount"])
        if wide:
            row += [e.get("description", ""), e.get("iban", "")]
        row += [e.get("counterparty", ""), e["category"]]
        table.add_row(*row)

    console.print(table)


@app.command(name="import")
def import_expenses(
    filepath: str = typer.Argument(..., help="Path to the bank CSV file"),
    bank: str = typer.Option(..., "--bank", "-b", help="Bank name matching a config in ~/.expense_cli/banks/<name>.toml"),
    force: bool = typer.Option(False, "--force", help="Import all rows, skipping duplicate detection"),
) -> None:
    """Import expenses from a bank CSV file."""
    from expense_cli.importer import load_bank_config, read_bank_file
    from expense_cli.identifier import load_counterparties, identify
    from expense_cli.categorizer import load_rules, categorize

    try:
        config = load_bank_config(bank)
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    try:
        incoming = read_bank_file(filepath, config)
    except Exception as e:
        typer.echo(f"Failed to parse file: {e}", err=True)
        raise typer.Exit(1)

    existing = read_expenses()

    # Hash-based dedup (new rows) with legacy 4-tuple fallback (rows imported before source_hash existed)
    hashed_existing = {e["source_hash"] for e in existing if e.get("source_hash")}
    legacy_keys = {
        (e["date"], e["amount"], e["iban"], e["description"]): e
        for e in existing if not e.get("source_hash")
    }

    def _is_duplicate(row: dict):
        """Return the matching existing row if duplicate, else None."""
        h = row.get("source_hash", "")
        if h:
            return h in hashed_existing
        return (row["date"], row["amount"], row["iban"], row["description"]) in legacy_keys

    def _existing_for(row: dict):
        """Return the stored row that matches, for display purposes."""
        h = row.get("source_hash", "")
        if h:
            return next((e for e in existing if e.get("source_hash") == h), None)
        return legacy_keys.get((row["date"], row["amount"], row["iban"], row["description"]))

    counterparties = load_counterparties()
    rules = load_rules()
    start_id = next_id(existing)

    to_import = []
    skipped_pairs = []  # list of (incoming_row, existing_row)
    for i, row in enumerate(incoming):
        if not force and _is_duplicate(row):
            skipped_pairs.append((row, _existing_for(row)))
            continue
        row["id"] = start_id + len(to_import)
        row["weekday"] = _weekday_from_date(row["date"])
        if not row["counterparty"]:
            row["counterparty"] = identify(row["iban"], row["description"], counterparties)
        row["category"] = categorize(row["counterparty"], rules)
        to_import.append(row)
        h = row.get("source_hash", "")
        if h:
            hashed_existing.add(h)
        else:
            legacy_keys[(row["date"], row["amount"], row["iban"], row["description"])] = row

    if to_import:
        write_expenses_batch(to_import)

    if skipped_pairs:
        table = Table(title=f"Skipped {len(skipped_pairs)} duplicate(s)", show_lines=True)
        table.add_column("", style="dim")
        table.add_column("ID", style="dim")
        table.add_column("Date")
        table.add_column("Amount", justify="right")
        table.add_column("Description")
        table.add_column("Counterparty")
        table.add_column("IBAN", style="dim")
        table.add_column("Category")
        table.add_column("Weekday", style="dim")
        for incoming_row, existing_row in skipped_pairs:
            table.add_row(
                "existing",
                str(existing_row.get("id", "")),
                str(existing_row.get("date", "")),
                str(existing_row.get("amount", "")),
                str(existing_row.get("description", "")),
                str(existing_row.get("counterparty", "")),
                str(existing_row.get("iban", "")),
                str(existing_row.get("category", "")),
                str(existing_row.get("weekday", "")),
            )
            table.add_row(
                "incoming",
                "",
                str(incoming_row.get("date", "")),
                str(incoming_row.get("amount", "")),
                str(incoming_row.get("description", "")),
                str(incoming_row.get("counterparty", "")),
                str(incoming_row.get("iban", "")),
                "",
                "",
            )
        console.print(table)

    typer.echo(f"Imported {len(to_import)}, skipped {len(skipped_pairs)} duplicates.")


_SKIP = object()  # sentinel: skip this transaction, move to next

import sys as _sys
import os as _os

if _sys.platform == "win32":
    import msvcrt as _msvcrt

    def _getch() -> str | None:
        ch = _msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # special key prefix
            ch2 = _msvcrt.getwch()
            if ch2 == "\x48":
                return "UP"
            if ch2 == "\x50":
                return "DOWN"
            return None  # other special keys ignored
        return ch
else:
    import tty as _tty, termios as _termios

    def _getch() -> str | None:
        fd = _sys.stdin.fileno()
        old = _termios.tcgetattr(fd)
        try:
            _tty.setraw(fd)
            ch = _sys.stdin.read(1)
            if ch == "\x1b":
                ch2 = _sys.stdin.read(1)
                if ch2 == "[":
                    ch3 = _sys.stdin.read(1)
                    if ch3 == "A":
                        return "UP"
                    if ch3 == "B":
                        return "DOWN"
                return None
        finally:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old)
        return ch


_HINT = "  \033[2mtype to filter  ·  ↑↓ more options  ·  1-9 pick  ·  ^S skip tx  ·  ^Q quit\033[0m"


def _pick(prompt_text: str, options: list[str], header: str = "") -> str | object | None:
    """Prompt with raw key input, live filtering, and a persistent hint bar below.

    Layout (all below the cursor — no options printed above):
        prompt line   ← cursor here
        options line  ← filtered matches or "(no matches)" — updates live
        hint line     ← always visible

    - type characters  → filters the options list live
    - digit            → instantly pick from the current filtered list
    - Ctrl+S (\x13)   → skip this transaction
    - Ctrl+Q (\x11)   → quit the review session
    - Enter            → submit typed text (or skip field if empty)
    - Backspace        → delete last character

    Returns:
        str   — chosen or typed value (empty string = skip this field)
        _SKIP — skip this whole transaction
        None  — quit the review session
    """
    prompt_prefix = f"  \033[1m{prompt_text}:\033[0m "
    buffer: list[str] = []
    _page_offset: int = 0  # index of first visible option in filtered list

    def get_filtered() -> list[str]:
        if not buffer:
            return options
        query = "".join(buffer).lower()
        return [o for o in options if query in o.lower()]

    def get_visible(filt: list[str]) -> list[str]:
        return filt[_page_offset:_page_offset + 9]

    import shutil as _shutil
    try:
        _term_width = _shutil.get_terminal_size().columns
    except Exception:
        _term_width = 120

    def make_options_line(filt: list[str]) -> str:
        if not options:
            return ""
        visible = get_visible(filt)
        if not visible:
            return "  \033[2m(no matches — will save as new)\033[0m"
        # Build items left to right, stop before wrapping; always show at least 1
        items: list[str] = []
        used = 2  # leading "  "
        for i, o in enumerate(visible):
            sep = 3 if items else 0
            visible_len = len(f"[{i + 1}] {o}")
            if items and used + sep + visible_len > _term_width - 8:
                break
            items.append(f"\033[2m[{i + 1}]\033[0m {o}")
            used += sep + visible_len
        line = "  " + "   ".join(items)
        # Page indicator
        has_prev = _page_offset > 0
        has_next = _page_offset + 9 < len(filt)
        if has_prev or has_next:
            nav = ("▲ " if has_prev else "  ") + ("▼" if has_next else " ")
            line += f"   \033[2m{nav}\033[0m"
        return line

    initial_opts = make_options_line(options)

    # Print: blank line, prompt, options (as separator), hint — then move cursor back up to prompt
    _sys.stdout.write(f"\n{prompt_prefix}\n{initial_opts}\n{_HINT}")
    _sys.stdout.write(f"\033[2A\r{prompt_prefix}")  # up 2, col 1, rewrite prefix → cursor right after it
    _sys.stdout.flush()

    def redraw_all() -> None:
        opts_line = make_options_line(get_filtered())
        typed = "".join(buffer)
        _sys.stdout.write(
            f"\r\033[K{prompt_prefix}{typed}"
            f"\n\r\033[K{opts_line}"
            f"\033[1A\r\033[K{prompt_prefix}{typed}"  # back to prompt line, reposition cursor at end of typed text
        )
        _sys.stdout.flush()

    def exit_pick() -> None:
        """Clear options and hint lines, leave only the prompt line with its value."""
        _sys.stdout.write(
            "\n\r\033[K"   # move to options line, clear it
            "\n\r\033[K"   # move to hint line, clear it
            "\033[2A\n"    # back to prompt line, then newline to advance
        )
        _sys.stdout.flush()

    while True:
        ch = _getch()
        if ch is None:
            continue

        if ch == "\x11":  # Ctrl+Q — quit
            exit_pick()
            return None

        if ch == "\x13":  # Ctrl+S — skip transaction
            exit_pick()
            return _SKIP

        if ch in ("\r", "\n"):  # Enter — submit typed text
            value = "".join(buffer).strip()
            exit_pick()
            return value

        if ch in ("UP", "DOWN"):  # scroll the visible window of options
            filt = get_filtered()
            if filt:
                if ch == "DOWN":
                    _page_offset = min(_page_offset + 9, max(0, len(filt) - 1) // 9 * 9)
                else:
                    _page_offset = max(0, _page_offset - 9)
                redraw_all()
            continue

        if ch in ("\x08", "\x7f"):  # Backspace
            if buffer:
                buffer.pop()
                _page_offset = 0
                redraw_all()
            continue

        if ch.isdigit():  # pick from current visible window
            filt = get_filtered()
            visible = get_visible(filt)
            idx = int(ch) - 1
            if 0 <= idx < len(visible):
                _sys.stdout.write(f"\r\033[K{prompt_prefix}{visible[idx]}")
                exit_pick()
                return visible[idx]
            # digit out of range — fall through and treat as regular character

        _page_offset = 0
        buffer.append(ch)
        redraw_all()


@app.command()
def review(
    unidentified: bool = typer.Option(False, "--unidentified", help="Show only expenses with no counterparty"),
    uncategorized: bool = typer.Option(False, "--uncategorized", help="Show only uncategorized expenses"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Step through expenses interactively"),
    month: Optional[int] = typer.Option(None, "--month", "-m", min=1, max=12, help="Filter by month (1-12)"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Filter by year (e.g. 2024)"),
) -> None:
    """Show expenses that need manual attention (missing counterparty or category)."""
    from expense_cli.identifier import load_counterparties
    from expense_cli.categorizer import load_rules, categorize

    expenses = read_expenses()

    if unidentified:
        expenses = [e for e in expenses if not e.get("counterparty")]
    elif uncategorized:
        expenses = [e for e in expenses if not e["category"]]
    else:
        expenses = [e for e in expenses if not e.get("counterparty") or not e["category"]]

    if year is not None:
        expenses = [e for e in expenses if e["date"].startswith(str(year))]
    if month is not None:
        target = f"-{month:02d}-"
        expenses = [e for e in expenses if target in e["date"]]

    expenses.sort(key=lambda e: (e["date"], e.get("time", "")), reverse=True)

    if not expenses:
        typer.echo("Nothing to review.")
        return

    if not interactive:
        table = Table(show_footer=True)
        table.add_column("ID", style="dim")
        table.add_column("Date")
        table.add_column("Weekday")
        table.add_column("Time")
        table.add_column("Amount", justify="right", footer=f"{sum(float(e['amount']) for e in expenses):.2f}")
        table.add_column("Description")
        table.add_column("IBAN", style="dim")
        table.add_column("Counterparty")
        table.add_column("Category")

        for e in expenses:
            category_val = e["category"] if e["category"] else "[dim]—[/dim]"
            counterparty_val = e.get("counterparty") or "[dim]—[/dim]"
            iban_val = e.get("iban") or "[dim]—[/dim]"
            table.add_row(e["id"], e["date"], e.get("weekday", ""), e.get("time", ""), e["amount"], e["description"], iban_val, counterparty_val, category_val)

        console.print(table)
        console.print(f"[dim]{len(expenses)} expense(s) need review[/dim]")
        return

    # Build shortcut lists sorted by frequency (most used first), then append config-only entries
    all_expenses = read_expenses()

    from collections import Counter
    cp_counts = Counter(e["counterparty"] for e in all_expenses if e.get("counterparty"))
    known_counterparties = [name for name, _ in cp_counts.most_common()]
    for cp in load_counterparties():
        if cp["name"] not in known_counterparties:
            known_counterparties.append(cp["name"])

    rules = load_rules()
    cat_counts = Counter(e["category"] for e in all_expenses if e.get("category"))
    known_categories = [cat for cat, _ in cat_counts.most_common()]
    for rule in rules:
        if rule.get("category") and rule["category"] not in known_categories:
            known_categories.append(rule["category"])

    console.print(f"[dim]Reviewing {len(expenses)} expense(s).[/dim]")

    saved = 0
    for i, expense in enumerate(expenses):
        console.print("")
        console.rule(f"[bold cyan][{i + 1}/{len(expenses)}][/bold cyan]  [dim]#{expense['id']}[/dim]")
        import shutil as _shutil_review, textwrap as _textwrap
        _key_col = 16  # 2 leading + 12 key + 2 separator
        _val_width = max(20, _shutil_review.get_terminal_size().columns - _key_col)

        def row(key: str, value: str, key_color: str = "dim") -> str:
            return f"  [{key_color}]{key:<12}[/{key_color}]  {value}"

        def wrap_row(key: str, value: str, key_color: str = "dim") -> str:
            lines = _textwrap.wrap(value, width=_val_width) or [""]
            joined = ("\n" + " " * _key_col).join(lines)
            return f"  [{key_color}]{key:<12}[/{key_color}]  {joined}"

        console.print(row("date",         f"[cyan]{expense['date']}[/cyan]",       "dim cyan"))
        amount_color = "green" if float(expense["amount"]) >= 0 else "red"
        console.print(row("amount", f"[{amount_color}]{expense['amount']}[/{amount_color}]", f"dim {amount_color}"))
        if expense.get("iban"):
            console.print(row("iban",     f"[dim]{expense['iban']}[/dim]"),         highlight=False)
        if expense.get("counterparty"):
            console.print(row("counterparty", expense["counterparty"]))
        if expense.get("category"):
            console.print(row("category", expense["category"]))
        console.print(wrap_row("description", expense["description"]))

        console.print("")

        fields: dict = {}
        skip_transaction = False

        # Counterparty step
        if not expense.get("counterparty"):
            result = _pick("Counterparty", known_counterparties, "Known counterparties")
            if result is None:
                break
            if result is _SKIP:
                skip_transaction = True
            elif result:
                fields["counterparty"] = result
                if result not in known_counterparties:
                    known_counterparties.append(result)

        # Category step
        if not skip_transaction and not expense["category"]:
            current_counterparty = fields.get("counterparty") or expense.get("counterparty", "")
            suggested = categorize(current_counterparty, rules) if current_counterparty else ""
            if suggested:
                console.print(row("suggested", f"[green]{suggested}[/green] [dim](Enter to accept)[/dim]", "dim green"))
            result = _pick("Category", known_categories, "Known categories")
            if result is None:
                if fields:
                    update_expense(int(expense["id"]), fields)
                    saved += 1
                break
            if result is _SKIP:
                skip_transaction = True
            elif result:
                fields["category"] = result
                if result not in known_categories:
                    known_categories.append(result)
            elif suggested:
                fields["category"] = suggested

        if skip_transaction:
            console.print(f"  [dim]skipped transaction[/dim]")
        elif fields:
            update_expense(int(expense["id"]), fields)
            saved += 1
            console.print(f"  [green]✓ saved[/green]")
        else:
            console.print(f"  [dim]skipped[/dim]")

    console.print("")
    console.rule()
    console.print(f"[dim]Done. {saved} expense(s) updated.[/dim]")


@app.command()
def edit(
    expense_id: int = typer.Argument(..., help="ID of the expense to update"),
    iban: Optional[str] = typer.Option(None, "--iban", help="Set counterparty IBAN"),
    counterparty: Optional[str] = typer.Option(None, "--counterparty", "-n", help="Set counterparty name"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Set category"),
) -> None:
    """Update identity or category on an existing expense."""
    fields = {}
    if iban is not None:
        fields["iban"] = iban
    if counterparty is not None:
        fields["counterparty"] = counterparty
    if category is not None:
        fields["category"] = category

    if not fields:
        typer.echo("Provide at least one of --iban, --counterparty, --category.", err=True)
        raise typer.Exit(1)

    if not update_expense(expense_id, fields):
        typer.echo(f"No expense with ID {expense_id}.", err=True)
        raise typer.Exit(1)

    updated = next(e for e in read_expenses() if int(e["id"]) == expense_id)
    console.print(f"Updated expense #{expense_id}:")
    for key in ("date", "amount", "description", "iban", "counterparty", "category"):
        value = updated[key]
        if key in fields:
            console.print(f"  {key}: [green]{value}[/green]", highlight=False)
        else:
            console.print(f"  {key}: [dim]{value}[/dim]", highlight=False)


@app.command()
def delete(
    expense_id: Optional[int] = typer.Argument(None, help="ID of the expense to delete"),
    all_: bool = typer.Option(False, "--all", help="Delete all expenses (requires confirmation)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt for single delete"),
) -> None:
    """Delete a single expense by ID, or all expenses with --all."""
    import expense_cli.storage as _storage

    if all_ and expense_id is not None:
        typer.echo("Cannot use --all with an ID.", err=True)
        raise typer.Exit(1)

    if not all_ and expense_id is None:
        typer.echo("Provide an expense ID or use --all to delete everything.", err=True)
        raise typer.Exit(1)

    if all_:
        count = len(read_expenses())
        path = _storage.CSV_PATH
        if count == 0:
            typer.echo("No expenses stored. Nothing to delete.")
        else:
            typer.echo(f"This will permanently delete {count} expense(s) from:")
            typer.echo(f"  {path}")
            typer.echo("Configuration files are NOT affected.")
        confirmation = typer.prompt("\nType DELETE to confirm (anything else aborts)")
        if confirmation != "DELETE":
            typer.echo("Aborted.")
            raise typer.Exit(1)
        deleted = reset_expenses()
        typer.echo(f"Deleted {deleted} expense(s).")
        return

    expenses = read_expenses()
    match = next((e for e in expenses if int(e["id"]) == expense_id), None)
    if match is None:
        typer.echo(f"No expense with ID {expense_id}.", err=True)
        raise typer.Exit(1)

    console.print(f"  [dim]id:[/dim]          {match['id']}")
    console.print(f"  [dim]date:[/dim]        {match['date']}")
    console.print(f"  [dim]amount:[/dim]      {match['amount']}")
    console.print(f"  [dim]description:[/dim] {match['description']}")
    console.print(f"  [dim]category:[/dim]    {match['category'] or '-'}")
    console.print(f"  [dim]counterparty:[/dim]{match.get('counterparty') or '-'}")
    console.print(f"  [dim]iban:[/dim]        {match.get('iban') or '-'}")

    if not yes:
        typer.confirm("\nPermanently delete this expense?", abort=True)

    delete_expense(expense_id)
    typer.echo(f"Deleted expense #{expense_id}.")


@app.command()
def summary(
    by: str = typer.Option("category", "--by", help="Group by: category or counterparty"),
    from_date: Optional[str] = typer.Option(None, "--from", help="Start date (YYYY-MM-DD, inclusive)"),
    to_date: Optional[str] = typer.Option(None, "--to", help="End date (YYYY-MM-DD, inclusive)"),
    month: Optional[str] = typer.Option(None, "--month", help="Filter by month: YYYY-MM or 1-12"),
    year: Optional[int] = typer.Option(None, "--year", help="Year to use with --month number (default: current year)"),
) -> None:
    """Summarize spending grouped by category or counterparty."""
    if month and (from_date or to_date):
        typer.echo("--month cannot be combined with --from or --to.", err=True)
        raise typer.Exit(1)

    if month:
        try:
            from_date, to_date = _parse_month(month, year)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)

    if by not in ("category", "counterparty"):
        typer.echo("--by must be 'category' or 'counterparty'.", err=True)
        raise typer.Exit(1)

    expenses = read_expenses()

    if from_date:
        expenses = [e for e in expenses if e["date"] >= from_date]
    if to_date:
        expenses = [e for e in expenses if e["date"] <= to_date]

    if not expenses:
        typer.echo("No expenses found.")
        return

    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for e in expenses:
        key = e.get(by) or "(none)"
        amount = float(e["amount"])
        totals[key] = totals.get(key, 0.0) + amount
        counts[key] = counts.get(key, 0) + 1

    grand_total = sum(totals.values())
    sorted_keys = sorted(totals, key=lambda k: totals[k], reverse=True)

    table = Table(show_lines=False, box=None, padding=(0, 1))
    table.add_column(by.capitalize(), min_width=20)
    table.add_column("Total", justify="right")
    table.add_column("%", justify="right", style="dim")
    table.add_column("Count", justify="right", style="dim")

    for key in sorted_keys:
        pct = totals[key] / grand_total * 100
        table.add_row(
            key,
            f"{totals[key]:.2f}",
            f"{pct:.1f}%",
            str(counts[key]),
        )

    console.print(table)
    console.print(f"  [dim]Total: {grand_total:.2f}[/dim]")


@app.command()
def version() -> None:
    """Print the current CLI version."""
    typer.echo(__version__)


def main() -> None:
    app()
