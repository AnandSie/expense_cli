import typer
from datetime import date
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
    unreviewed: bool = typer.Option(False, "--unreviewed", help="Show only expenses missing counterparty or category"),
    reviewed: bool = typer.Option(False, "--reviewed", help="Show only expenses with counterparty and category set"),
) -> None:
    """List expenses, optionally filtered by category or date range."""
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
        table.add_row(e["id"], e["date"], e.get("weekday", ""), e.get("time", ""), e["amount"], e["description"], e.get("iban", ""), e.get("counterparty", ""), e["category"])

    console.print(table)


@app.command(name="import")
def import_expenses(
    filepath: str = typer.Argument(..., help="Path to the bank CSV file"),
    bank: str = typer.Option(..., "--bank", "-b", help="Bank name matching a config in ~/.expense_cli/banks/<name>.toml"),
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
    dedup_keys = {
        (e["date"], e["amount"], e["iban"], e["description"])
        for e in existing
    }

    counterparties = load_counterparties()
    rules = load_rules()
    start_id = next_id(existing)

    to_import = []
    skipped = 0
    for i, row in enumerate(incoming):
        key = (row["date"], row["amount"], row["iban"], row["description"])
        if key in dedup_keys:
            skipped += 1
            continue
        row["id"] = start_id + len(to_import)
        row["weekday"] = _weekday_from_date(row["date"])
        if not row["counterparty"]:
            row["counterparty"] = identify(row["iban"], row["description"], counterparties)
        row["category"] = categorize(row["counterparty"], rules)
        to_import.append(row)
        dedup_keys.add(key)

    if to_import:
        write_expenses_batch(to_import)

    typer.echo(f"Imported {len(to_import)}, skipped {skipped} duplicates.")


_SKIP = object()  # sentinel: skip this transaction, move to next

import sys as _sys

if _sys.platform == "win32":
    import msvcrt as _msvcrt

    def _getch() -> str | None:
        ch = _msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # special key prefix (arrows, F-keys) — consume and ignore
            _msvcrt.getwch()
            return None
        return ch
else:
    import tty as _tty, termios as _termios

    def _getch() -> str | None:
        fd = _sys.stdin.fileno()
        old = _termios.tcgetattr(fd)
        try:
            _tty.setraw(fd)
            ch = _sys.stdin.read(1)
        finally:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old)
        return ch


_HINT = "  \033[2mtype to filter  ·  1-9 pick  ·  ^S skip tx  ·  ^Q quit\033[0m"
_SEP  = "  " + "─" * 52


def _pick(prompt_text: str, options: list[str], header: str = "") -> str | object | None:
    """Prompt with raw key input, live filtering, and a persistent hint bar below.

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
    if options:
        if header:
            print(f"\n  \033[2m{header}\033[0m")
        opts = "   ".join(f"\033[2m[{i + 1}]\033[0m {o}" for i, o in enumerate(options[:9]))
        print(f"  {opts}")

    prompt_prefix = f"  \033[1m{prompt_text}:\033[0m "

    # Print prompt line, then separator + hint below, then move cursor back up to prompt
    _sys.stdout.write(f"\n{prompt_prefix}\n{_SEP}\n{_HINT}")
    _sys.stdout.write(f"\033[2A\r{prompt_prefix}")  # up 2 lines, col 1, rewrite prefix → cursor right after it
    _sys.stdout.flush()

    buffer: list[str] = []

    def get_filtered() -> list[str]:
        if not buffer:
            return options[:9]
        query = "".join(buffer).lower()
        return [o for o in options if query in o.lower()][:9]

    def redraw_all() -> None:
        filt = get_filtered()
        if filt:
            opts_str = "   ".join(f"\033[2m[{i + 1}]\033[0m {o}" for i, o in enumerate(filt))
            opts_line = f"  {opts_str}"
        else:
            opts_line = "  \033[2m(no matches — will save as new)\033[0m"
        # Up 2 to options line, clear + rewrite; down 2 to prompt line, clear + rewrite
        _sys.stdout.write(
            f"\033[2A\r\033[K{opts_line}"
            f"\033[2B\r\033[K{prompt_prefix}{''.join(buffer)}"
        )
        _sys.stdout.flush()

    def exit_pick() -> None:
        """Move cursor past the hint bar before returning."""
        _sys.stdout.write("\033[2B\n")
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

        if ch in ("\r", "\n"):  # Enter — submit
            exit_pick()
            return "".join(buffer).strip()

        if ch in ("\x08", "\x7f"):  # Backspace
            if buffer:
                buffer.pop()
                redraw_all()
            continue

        if ch.isdigit():  # pick from current filtered list
            filt = get_filtered()
            idx = int(ch) - 1
            if 0 <= idx < len(filt):
                _sys.stdout.write(filt[idx])
                exit_pick()
                return filt[idx]
            # digit out of range — fall through and treat as regular character

        buffer.append(ch)
        redraw_all()


@app.command()
def review(
    unidentified: bool = typer.Option(False, "--unidentified", help="Show only expenses with no counterparty"),
    uncategorized: bool = typer.Option(False, "--uncategorized", help="Show only uncategorized expenses"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Step through expenses interactively"),
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

    # Build shortcut lists
    all_expenses = read_expenses()
    known_counterparties = sorted({e["counterparty"] for e in all_expenses if e.get("counterparty")})
    for cp in load_counterparties():
        if cp["name"] not in known_counterparties:
            known_counterparties.append(cp["name"])

    rules = load_rules()
    known_categories = sorted({e["category"] for e in all_expenses if e.get("category")})
    for rule in rules:
        if rule.get("category") and rule["category"] not in known_categories:
            known_categories.append(rule["category"])

    console.print(f"[dim]Reviewing {len(expenses)} expense(s).[/dim]")

    saved = 0
    for i, expense in enumerate(expenses):
        console.print("")
        console.rule(f"[bold cyan][{i + 1}/{len(expenses)}][/bold cyan]  [dim]#{expense['id']}[/dim]")
        def row(key: str, value: str, key_color: str = "dim") -> str:
            return f"  [{key_color}]{key:<12}[/{key_color}]  {value}"

        console.print(row("date",         f"[cyan]{expense['date']}[/cyan]",       "dim cyan"))
        console.print(row("amount",       f"[yellow]{expense['amount']}[/yellow]", "dim yellow"))
        if expense.get("iban"):
            console.print(row("iban",     f"[dim]{expense['iban']}[/dim]"),         highlight=False)
        if expense.get("counterparty"):
            console.print(row("counterparty", expense["counterparty"]))
        if expense.get("category"):
            console.print(row("category", expense["category"]))
        console.print(row("description",  expense["description"]))

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
def version() -> None:
    """Print the current CLI version."""
    typer.echo(__version__)


def main() -> None:
    app()
