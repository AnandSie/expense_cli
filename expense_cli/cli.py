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
config_app = typer.Typer(help="Inspect and validate configuration files.", invoke_without_command=True)
app.add_typer(config_app, name="config")
counterparties_app = typer.Typer(help="Manage counterparty identification rules.", invoke_without_command=True)
config_app.add_typer(counterparties_app, name="counterparties")
categories_app = typer.Typer(help="Manage category rules.", invoke_without_command=True)
config_app.add_typer(categories_app, name="categories")
console = Console(highlight=False)

BANKS_DIR = Path.home() / ".expense_cli" / "banks"
CATEGORIES_PATH = Path.home() / ".expense_cli" / "categories.toml"
COUNTERPARTIES_PATH = Path.home() / ".expense_cli" / "counterparties.toml"

_COUNTERPARTIES_TEMPLATE = """\
# Counterparty identification rules.
# Each entry maps an IBAN or a description substring to a normalized name.
# Matched in order: IBAN first, then description_contains.

# [[counterparty]]
# iban = "NL91ABNA0417164300"
# name = "albert heijn"

# [[counterparty]]
# description_contains = "spotify"
# name = "spotify"
"""

_CATEGORIES_TEMPLATE = """\
# Category assignment rules.
# Each rule maps a counterparty name (exact, case-insensitive) to a category.

# [[rules]]
# counterparty = "albert heijn"
# category = "groceries"

# [[rules]]
# counterparty = "spotify"
# category = "subscriptions"
"""

_BANK_TEMPLATE = """\
[bank]
encoding = "utf-8"
date_format = "%Y-%m-%d"
decimal_separator = "."
delimiter = ","
# time_format = "%H:%M:%S"  # optional

[mapping]
date = "Date"
amount = "Amount"
# description = "Description"   # optional
# iban = "IBAN"                  # optional
# counterparty = "Counterparty"  # optional
# time = "Time"                  # optional
"""


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


def _render_bar(value: float, max_value: float, width: int = 30) -> str:
    """Return a Unicode block bar string scaled to `width` characters."""
    if max_value == 0:
        return ""
    filled = round(abs(value) / max_value * width)
    return "█" * filled


def _sparkline(values: list[float]) -> str:
    """Convert a list of non-negative floats to a Unicode sparkline (oldest → newest)."""
    if not values:
        return ""
    max_val = max(values)
    if max_val == 0:
        return " " * len(values)
    blocks = " ▁▂▃▄▅▆▇█"
    return "".join(blocks[min(8, round(v / max_val * 8))] for v in values)


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
        has_extract_iban_from = "extract_iban_from" in value
        if not has_column and not (has_from_column and has_pattern) and not has_extract_iban_from:
            errors.append(
                f"mapping.{field_name}: dict config must have 'column', both 'from_column'+'pattern', or 'extract_iban_from'"
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


def _validate_counterparties_config(config: dict) -> list[str]:
    errors = []
    entries = config.get("counterparty", [])
    if not isinstance(entries, list):
        errors.append("'counterparty' must be an array ([[counterparty]] entries)")
        return errors
    known_keys = {"name", "iban", "description_contains", "category"}
    for i, entry in enumerate(entries):
        prefix = f"counterparty[{i}]"
        if "name" not in entry or not entry["name"]:
            errors.append(f"{prefix}: missing or empty 'name'")
        matchers = [k for k in entry if k in ("iban", "description_contains")]
        if len(matchers) > 1:
            errors.append(f"{prefix}: has both 'iban' and 'description_contains' — only one matcher per entry")
        for key in entry:
            if key not in known_keys:
                errors.append(f"{prefix}: unknown field '{key}'")
    return errors


@config_app.callback(invoke_without_command=True)
def _config_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _config_list()


@config_app.command(name="list")
def _config_list() -> None:
    """Show all counterparty rules (matchers + categories) in one table."""
    from expense_cli.identifier import load_counterparties

    entries = load_counterparties()
    if not entries:
        typer.echo("No counterparty rules found.")
        typer.echo("Add one with: expense config counterparties add --name <name> [--iban IBAN] [--contains TEXT] [--category CAT]")
        return

    table = Table(show_header=True)
    table.add_column("Name")
    table.add_column("IBAN", style="dim")
    table.add_column("Description contains", style="dim")
    table.add_column("Category")
    for entry in entries:
        table.add_row(
            entry.get("name", ""),
            entry.get("iban", ""),
            entry.get("description_contains", ""),
            entry.get("category", ""),
        )
    console.print(table)


@config_app.command(name="bank-list")
def _config_bank_list_compat() -> None:
    """List all available bank configs (kept for backwards compatibility)."""
    if not BANKS_DIR.exists() or not list(BANKS_DIR.glob("*.toml")):
        typer.echo("No bank configs found in ~/.expense_cli/banks/")
        return
    for path in sorted(BANKS_DIR.glob("*.toml")):
        typer.echo(path.stem)


@config_app.command(name="bank")
def config_bank(
    bank: str = typer.Argument(..., help="Bank name, or 'new <name>' to create a template"),
    sub_name: Optional[str] = typer.Argument(None, hidden=True),
) -> None:
    """Print and validate a bank config, or create a new template.

    Examples:\n
      expense config bank mybank\n
      expense config bank new mybank
    """
    if bank == "new":
        if sub_name is None:
            typer.echo("Usage: expense config bank new <name>", err=True)
            raise typer.Exit(1)
        _config_bank_new_impl(sub_name)
        return

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    path = BANKS_DIR / f"{bank}.toml"
    if not path.exists():
        typer.echo(f"No bank config found at {path}", err=True)
        typer.echo(f"Run: expense config bank new {bank}", err=True)
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


def _config_bank_new_impl(bank: str) -> None:
    """Create a template bank config at ~/.expense_cli/banks/<name>.toml."""
    path = BANKS_DIR / f"{bank}.toml"
    if path.exists():
        if not typer.confirm(f"{path} already exists. Overwrite?", default=False):
            typer.echo("Aborted.")
            return
    BANKS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(_BANK_TEMPLATE, encoding="utf-8")
    typer.echo(f"Created {path}")
    typer.echo("Edit it to match your bank's column names, then run: expense config bank " + bank)


@config_app.command(name="bank-set")
def config_bank_set(
    bank: str = typer.Argument(..., help="Bank name to configure"),
    field: str = typer.Option(..., "--field", "-f", help="Mapping field to set (e.g. iban, date, amount)"),
    column: Optional[str] = typer.Option(None, "--column", help="Column name to read directly"),
    from_column: Optional[str] = typer.Option(None, "--from-column", help="Column to apply a regex pattern to"),
    pattern: Optional[str] = typer.Option(None, "--pattern", help="Regex pattern; first capture group used, else full match"),
    extract_iban_from: Optional[str] = typer.Option(None, "--extract-iban-from", help="Column to auto-detect a single IBAN from"),
) -> None:
    """Set a mapping field in a bank config.

    Examples:\n
      expense config bank-set mybank --field iban --column IBAN\n
      expense config bank-set mybank --field iban --extract-iban-from Description\n
      expense config bank-set mybank --field iban --column IBAN --extract-iban-from Description\n
      expense config bank-set mybank --field iban --from-column Description --pattern "[A-Z]{2}[0-9]{2}[A-Z0-9]+"
    """
    from expense_cli.toml_store import read_toml, write_bank_config

    path = BANKS_DIR / f"{bank}.toml"
    if not path.exists():
        typer.echo(f"No bank config found at {path}", err=True)
        typer.echo(f"Run: expense config bank new {bank}", err=True)
        raise typer.Exit(1)

    if not column and not extract_iban_from and not (from_column and pattern):
        typer.echo(
            "Provide at least one of: --column, --extract-iban-from, or --from-column + --pattern",
            err=True,
        )
        raise typer.Exit(1)

    if from_column and not pattern:
        typer.echo("--from-column requires --pattern", err=True)
        raise typer.Exit(1)

    if pattern and not from_column:
        typer.echo("--pattern requires --from-column", err=True)
        raise typer.Exit(1)

    config = read_toml(path)
    config.setdefault("mapping", {})

    # Build the field value: string if only --column, dict otherwise
    parts: dict[str, str] = {}
    if column:
        parts["column"] = column
    if from_column:
        parts["from_column"] = from_column
    if pattern:
        parts["pattern"] = pattern
    if extract_iban_from:
        parts["extract_iban_from"] = extract_iban_from

    if list(parts.keys()) == ["column"]:
        config["mapping"][field] = column
    else:
        config["mapping"][field] = parts

    write_bank_config(path, config)
    typer.echo(f"Updated {bank}: mapping.{field}")
    typer.echo(f"Run: expense config bank {bank}  to verify")


@counterparties_app.callback(invoke_without_command=True)
def _counterparties_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        config_counterparties()


@counterparties_app.command(name="list")
def config_counterparties() -> None:
    """Print and validate the counterparties config file."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    if not COUNTERPARTIES_PATH.exists():
        typer.echo(f"No counterparties config at {COUNTERPARTIES_PATH}")
        typer.echo("\nExample template:\n")
        typer.echo(_COUNTERPARTIES_TEMPLATE)
        if typer.confirm(f"Create this template at {COUNTERPARTIES_PATH}?", default=False):
            COUNTERPARTIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            COUNTERPARTIES_PATH.write_text(_COUNTERPARTIES_TEMPLATE, encoding="utf-8")
            typer.echo(f"Created {COUNTERPARTIES_PATH} — edit it to add your rules.")
        return

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


@counterparties_app.command(name="add")
def config_counterparties_add(
    name: str = typer.Option(..., "--name", "-n", help="Normalized counterparty name"),
    contains: Optional[str] = typer.Option(None, "--contains", help="Description substring to match (case-insensitive)"),
    iban: Optional[str] = typer.Option(None, "--iban", help="Exact IBAN to match (case-insensitive)"),
    category: Optional[str] = typer.Option(None, "--category", "-k", help="Category to assign to this counterparty"),
) -> None:
    """Add a matcher and/or category to a counterparty entry (creates it if it doesn't exist yet).

    If an entry for NAME already exists, the fields are added to it.
    Both --iban and --contains can be set on the same counterparty by running add twice.
    To change an existing value use: expense config counterparties edit\n

    Examples:\n
      expense config counterparties add --name "albert heijn" --contains "albert heijn" --category groceries\n
      expense config counterparties add --name "albert heijn" --iban NL91ABNA0417164300\n
      expense config counterparties add --name "mom" --category transfers
    """
    from expense_cli.identifier import save_counterparty_rule, matcher_exists

    if contains and iban:
        typer.echo("Provide --contains or --iban, not both.", err=True)
        raise typer.Exit(1)
    if not contains and not iban and not category:
        typer.echo("Provide at least one of --contains, --iban, or --category.", err=True)
        raise typer.Exit(1)

    name = name.lower()
    category = category.lower() if category else None

    matcher_type: str | None = None
    matcher_value: str | None = None
    if contains or iban:
        matcher_type = "iban" if iban else "description_contains"
        matcher_value = iban if iban else contains.lower()  # type: ignore[union-attr]
        if matcher_exists(name, matcher_type):
            typer.echo(f"'{name}' already has a {matcher_type} matcher. Use: expense config counterparties edit --name '{name}' --{'iban' if iban else 'contains'} '<new value>'", err=True)
            raise typer.Exit(1)

    save_counterparty_rule(name, matcher_type, matcher_value, category=category)
    parts = []
    if matcher_type:
        parts.append(f"{matcher_type} [dim]{matcher_value}[/dim]")
    if category:
        parts.append(f"category [dim]{category}[/dim]")
    console.print(f"[green]✓[/green] [bold]{name}[/bold] ← {', '.join(parts)}", highlight=False)


@counterparties_app.command(name="edit")
def config_counterparties_edit(
    name: str = typer.Option(..., "--name", "-n", help="Name of the counterparty entry to edit"),
    new_name: Optional[str] = typer.Option(None, "--new-name", help="Rename the counterparty"),
    contains: Optional[str] = typer.Option(None, "--contains", help="Replace the description_contains matcher"),
    iban: Optional[str] = typer.Option(None, "--iban", help="Replace the iban matcher"),
    category: Optional[str] = typer.Option(None, "--category", "-k", help="Set or replace the category"),
) -> None:
    """Change fields on an existing counterparty entry.

    With no value flags, opens an interactive prompt pre-filled with the current values.\n

    Examples:\n
      expense config counterparties edit --name "albert heijn"\n
      expense config counterparties edit --name "albert heijn" --contains "ah.nl"\n
      expense config counterparties edit --name "albert heijn" --new-name "ah"\n
      expense config counterparties edit --name "albert heijn" --category groceries
    """
    from expense_cli.identifier import edit_counterparty_rule, load_counterparties

    # Flag-based (non-interactive) path
    if any([new_name, contains, iban, category]):
        ok = edit_counterparty_rule(
            name=name.lower(),
            new_name=new_name.lower() if new_name else None,
            iban=iban,
            description_contains=contains.lower() if contains else None,
            category=category.lower() if category else None,
        )
        if not ok:
            typer.echo(f"No counterparty entry named '{name}'.", err=True)
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] Updated '{name}'.")
        return

    # Interactive path
    entry = next((e for e in load_counterparties() if e.get("name", "").lower() == name.lower()), None)
    if entry is None:
        typer.echo(f"No counterparty entry named '{name}'.", err=True)
        raise typer.Exit(1)

    console.print(f"  [dim]Editing [/dim][cyan]{entry['name']}[/cyan][dim] — empty Enter keeps the current value[/dim]\n")

    r_name = _input_prefilled("name", entry["name"], color="\033[36m")
    if r_name is None or r_name is _SKIP:
        return
    r_iban = _input_prefilled("iban", entry.get("iban", ""), color="\033[36m")
    if r_iban is None or r_iban is _SKIP:
        return
    r_contains = _input_prefilled("contains", entry.get("description_contains", ""), color="\033[36m")
    if r_contains is None or r_contains is _SKIP:
        return
    r_category = _input_prefilled("category", entry.get("category", ""), color="\033[33m")
    if r_category is None or r_category is _SKIP:
        return

    changes: dict = {}
    if r_name and r_name != entry["name"]:
        changes["new_name"] = r_name.lower()
    if r_iban and r_iban != entry.get("iban", ""):
        changes["iban"] = r_iban
    if r_contains and r_contains != entry.get("description_contains", ""):
        changes["description_contains"] = r_contains.lower()
    if r_category and r_category != entry.get("category", ""):
        changes["category"] = r_category.lower()

    if not changes:
        console.print("  [dim]No changes.[/dim]")
        return

    edit_counterparty_rule(name=name.lower(), **changes)
    console.print(f"[green]✓[/green] Updated '{entry['name']}'.")


@counterparties_app.command(name="remove")
def config_counterparties_remove(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Name of the counterparty entry to remove"),
    all_: bool = typer.Option(False, "--all", help="Remove all counterparty entries (requires confirmation)"),
) -> None:
    """Remove a counterparty entry, or all entries with --all.

    Examples:\n
      expense config counterparties remove --name "albert heijn"\n
      expense config counterparties remove --all
    """
    from expense_cli.identifier import delete_counterparty_rule, clear_counterparty_rules

    if all_ and name is not None:
        typer.echo("Cannot use --all with --name.", err=True)
        raise typer.Exit(1)

    if not all_ and name is None:
        typer.echo("Provide --name or use --all to remove everything.", err=True)
        raise typer.Exit(1)

    if all_:
        confirm = typer.prompt("Type DELETE to confirm removing all counterparty entries")
        if confirm != "DELETE":
            typer.echo("Aborted.")
            raise typer.Exit(1)
        removed = clear_counterparty_rules()
        typer.echo(f"Removed {removed} counterparty entry/entries.")
        return

    ok = delete_counterparty_rule(name.lower())  # type: ignore[union-attr]
    if not ok:
        typer.echo(f"No counterparty entry named '{name}'.", err=True)
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Removed '{name}'.")


@categories_app.callback(invoke_without_command=True)
def _categories_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        config_categories()


@categories_app.command(name="list")
def config_categories() -> None:
    """List all counterparties that have a category assigned."""
    from expense_cli.categorizer import load_rules

    rules = load_rules()
    if not rules:
        typer.echo("No category rules found.")
        typer.echo("Add one with: expense config counterparties add --name <name> --category <cat>")
        typer.echo("  or:         expense config categories add --counterparty <name> --category <cat>")
        return

    table = Table(show_header=True)
    table.add_column("Counterparty")
    table.add_column("Category")
    for rule in sorted(rules, key=lambda r: r.get("name", "").lower()):
        table.add_row(rule.get("name", ""), rule.get("category", ""))
    console.print(table)
    console.print(f"\n[green]OK {len(rules)} category rule(s)[/green]")


@categories_app.command(name="add")
def config_categories_add(
    counterparty: str = typer.Option(..., "--counterparty", "-c", help="Counterparty name (must match counterparties.toml)"),
    category: str = typer.Option(..., "--category", "-k", help="Category to assign"),
) -> None:
    """Add a category rule (creates it if it doesn't exist yet).

    To change an existing rule use: expense config categories edit\n

    Examples:\n
      expense config categories add --counterparty "albert heijn" --category groceries\n
      expense config categories add -c spotify -k subscriptions
    """
    from expense_cli.categorizer import save_category_rule, category_rule_exists

    counterparty = counterparty.lower()
    category = category.lower()

    if category_rule_exists(counterparty):
        typer.echo(f"A rule for '{counterparty}' already exists. Use: expense config categories edit --counterparty '{counterparty}' --category '<new>'", err=True)
        raise typer.Exit(1)

    save_category_rule(counterparty, category)
    console.print(f"[green]✓[/green] [bold]{counterparty}[/bold] → [bold]{category}[/bold]")


@categories_app.command(name="edit")
def config_categories_edit(
    counterparty: str = typer.Option(..., "--counterparty", "-c", help="Counterparty name of the rule to edit"),
    new_counterparty: Optional[str] = typer.Option(None, "--new-counterparty", help="Rename the counterparty key"),
    category: Optional[str] = typer.Option(None, "--category", "-k", help="Replace the category"),
) -> None:
    """Change an existing category rule.

    With no value flags, opens an interactive prompt pre-filled with the current values.\n

    Examples:\n
      expense config categories edit --counterparty "albert heijn"\n
      expense config categories edit --counterparty "albert heijn" --category food\n
      expense config categories edit -c spotify --new-counterparty "spotify premium"
    """
    from expense_cli.categorizer import edit_category_rule, load_rules

    # Flag-based (non-interactive) path
    if any([new_counterparty, category]):
        ok = edit_category_rule(
            counterparty=counterparty.lower(),
            new_counterparty=new_counterparty.lower() if new_counterparty else None,
            category=category.lower() if category else None,
        )
        if not ok:
            typer.echo(f"No category rule for counterparty '{counterparty}'.", err=True)
            raise typer.Exit(1)
        console.print(f"[green]✓[/green] Updated rule for '{counterparty}'.")
        return

    # Interactive path
    rule = next((r for r in load_rules() if r.get("name", "").lower() == counterparty.lower()), None)
    if rule is None:
        typer.echo(f"No category rule for counterparty '{counterparty}'.", err=True)
        raise typer.Exit(1)

    console.print(f"  [dim]Editing rule for [/dim][cyan]{rule['name']}[/cyan][dim] — empty Enter keeps the current value[/dim]\n")

    r_counterparty = _input_prefilled("counterparty", rule["name"], color="\033[36m")
    if r_counterparty is None or r_counterparty is _SKIP:
        return
    r_category = _input_prefilled("category", rule["category"], color="\033[33m")
    if r_category is None or r_category is _SKIP:
        return

    changes: dict = {}
    if r_counterparty and r_counterparty != rule["name"]:
        changes["new_counterparty"] = r_counterparty.lower()
    if r_category and r_category != rule["category"]:
        changes["category"] = r_category.lower()

    if not changes:
        console.print("  [dim]No changes.[/dim]")
        return

    edit_category_rule(counterparty=counterparty.lower(), **changes)
    console.print(f"[green]✓[/green] Updated rule for '{rule['name']}'.")


@categories_app.command(name="remove")
def config_categories_remove(
    counterparty: Optional[str] = typer.Option(None, "--counterparty", "-c", help="Counterparty name of the rule to remove"),
    all_: bool = typer.Option(False, "--all", help="Remove all category rules (requires confirmation)"),
) -> None:
    """Remove a category rule, or all rules with --all.

    Examples:\n
      expense config categories remove --counterparty "albert heijn"\n
      expense config categories remove --all
    """
    from expense_cli.categorizer import delete_category_rule, clear_category_rules

    if all_ and counterparty is not None:
        typer.echo("Cannot use --all with --counterparty.", err=True)
        raise typer.Exit(1)

    if not all_ and counterparty is None:
        typer.echo("Provide --counterparty or use --all to remove everything.", err=True)
        raise typer.Exit(1)

    if all_:
        confirm = typer.prompt("Type DELETE to confirm removing all category rules")
        if confirm != "DELETE":
            typer.echo("Aborted.")
            raise typer.Exit(1)
        removed = clear_category_rules()
        typer.echo(f"Removed {removed} category rule(s).")
        return

    ok = delete_category_rule(counterparty.lower())  # type: ignore[union-attr]
    if not ok:
        typer.echo(f"No category rule for counterparty '{counterparty}'.", err=True)
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Removed rule for '{counterparty}'.")


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
    """Add a new expense.

    Examples:\n
      expense add 12.50 "Coffee"\n
      expense add -42.00 "Supermarket" --category groceries --counterparty "albert heijn"\n
      expense add 9.99 "Spotify" --date 2026-03-01 --iban NL91ABNA0417164300
    """
    if expense_date is None:
        expense_date = date.today().isoformat()

    expenses = read_expenses()
    row = {
        "id": next_id(expenses),
        "date": expense_date,
        "weekday": _weekday_from_date(expense_date),
        "time": expense_time or "",
        "amount": f"{amount:.2f}",
        "direction": "out" if amount < 0 else "in",
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
    counterparty: Optional[str] = typer.Option(None, "--counterparty", "-cp", help="Filter by counterparty"),
    from_date: Optional[str] = typer.Option(None, "--from", help="Start date YYYY-MM-DD (inclusive)"),
    to_date: Optional[str] = typer.Option(None, "--to", help="End date YYYY-MM-DD (inclusive)"),
    month: Optional[str] = typer.Option(None, "--month", help="Filter by month: YYYY-MM or 1-12"),
    year: Optional[int] = typer.Option(None, "--year", help="Year to use with --month number (default: current year)"),
    unreviewed: bool = typer.Option(False, "--unreviewed", help="Show only expenses missing counterparty or category"),
    reviewed: bool = typer.Option(False, "--reviewed", help="Show only expenses with counterparty and category set"),
    wide: bool = typer.Option(False, "--wide", "-w", help="Show all columns (description, IBAN, time)"),
    min_amount: Optional[float] = typer.Option(None, "--min", help="Hide transactions with abs(amount) below this value"),
    max_amount: Optional[float] = typer.Option(None, "--max", help="Hide transactions with abs(amount) above this value"),
    direction: Optional[str] = typer.Option(None, "--direction", help="Filter by direction: 'in' or 'out'"),
    without_category: Optional[str] = typer.Option(None, "--without-category", help="Exclude categories (comma-separated)"),
    without_counterparty: Optional[str] = typer.Option(None, "--without-counterparty", help="Exclude counterparties (comma-separated)"),
    expense_id: Optional[str] = typer.Option(None, "--id", help="Show expense(s) by ID (comma-separated)"),
) -> None:
    """List expenses, optionally filtered by category, date range, amount, or direction.

    Examples:\n
      expense list\n
      expense list --id 42\n
      expense list --category groceries\n
      expense list --month 2026-03\n
      expense list --from 2026-01-01 --to 2026-03-31\n
      expense list --unreviewed --wide\n
      expense list --direction out --min 50\n
      expense list --without-category food,transport\n
      expense list --without-counterparty Albert,Shell
    """
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

    if expense_id is not None:
        try:
            ids = {int(i.strip()) for i in expense_id.split(",")}
        except ValueError:
            typer.echo("--id must be a comma-separated list of integers.", err=True)
            raise typer.Exit(1)
        missing = ids - {int(e["id"]) for e in expenses}
        if missing:
            typer.echo(f"No expense(s) with ID: {', '.join(str(i) for i in sorted(missing))}.", err=True)
            raise typer.Exit(1)
        expenses = [e for e in expenses if int(e["id"]) in ids]

    if category:
        expenses = [e for e in expenses if e["category"].lower() == category.lower()]
    if counterparty:
        expenses = [e for e in expenses if e.get("counterparty", "").lower() == counterparty.lower()]
    if from_date:
        expenses = [e for e in expenses if e["date"] >= from_date]
    if to_date:
        expenses = [e for e in expenses if e["date"] <= to_date]
    if unreviewed:
        expenses = [e for e in expenses if not e.get("counterparty") or not e["category"]]
    elif reviewed:
        expenses = [e for e in expenses if e.get("counterparty") and e["category"]]
    if min_amount is not None:
        expenses = [e for e in expenses if abs(float(e["amount"])) >= min_amount]
    if max_amount is not None:
        expenses = [e for e in expenses if abs(float(e["amount"])) <= max_amount]
    if direction:
        expenses = [e for e in expenses if e.get("direction") == direction]
    if without_category:
        excluded = {c.strip().lower() for c in without_category.split(",")}
        expenses = [e for e in expenses if e["category"].lower() not in excluded]
    if without_counterparty:
        excluded = {c.strip().lower() for c in without_counterparty.split(",")}
        expenses = [e for e in expenses if e.get("counterparty", "").lower() not in excluded]

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
        amt = float(e["amount"])
        amt_str = f"[red]{e['amount']}[/red]" if amt < 0 else f"[green]{e['amount']}[/green]"
        row = [e["id"], e["date"], e.get("weekday", "")]
        if wide:
            row.append(e.get("time", ""))
        row.append(amt_str)
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
    """Import expenses from a bank statement file (CSV, TAB, or XLS).

    Examples:\n
      expense import statement.csv --bank ing\n
      expense import january.xls --bank rabobank\n
      expense import export.csv --bank mybank --force
    """
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
_BACK = object()  # sentinel: go back to the previous transaction


def _is_valid_iban(value: str) -> bool:
    """Return True if value matches the basic IBAN format: 2 letters + 2 digits + alphanumeric."""
    import re
    return bool(re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", value.strip().upper()))

import sys as _sys
import os as _os

if _sys.platform == "win32":
    import msvcrt as _msvcrt

    def _getch() -> str | None:
        ch = _msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # special key prefix
            ch2 = _msvcrt.getwch()
            if ch2 == "\x48": return "UP"
            if ch2 == "\x50": return "DOWN"
            if ch2 == "\x4b": return "LEFT"
            if ch2 == "\x4d": return "RIGHT"
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
                    if ch3 == "A": return "UP"
                    if ch3 == "B": return "DOWN"
                    if ch3 == "C": return "RIGHT"
                    if ch3 == "D": return "LEFT"
                return None
        finally:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old)
        return ch


_HINT = "  \033[2mtype to filter  ·  ↑↓ more options  ·  1-9 pick  ·  ^Z back  ·  ^S skip tx  ·  ^Q quit\033[0m"
_EDIT_HINT = "  \033[2m← → ↑ ↓ move  ·  backspace  ·  ^U clear  ·  ^Z back  ·  Enter save  ·  empty skip  ·  ^S skip tx  ·  ^Q quit\033[0m"


def _input_prefilled(prompt_text: str, default: str, color: str = "") -> str | object | None:
    """Show prompt with default text pre-filled and fully editable.

    Supports cursor movement (←→), backspace, Ctrl+U (clear), Ctrl+Z (back),
    Ctrl+S (skip tx), Ctrl+Q (quit), Enter (submit), empty submit = skip field.

    Falls back to typer.prompt when stdin is not a TTY (e.g. in tests).

    Returns:
        str   — edited value (empty string = skip saving)
        _SKIP — skip this transaction
        None  — quit the review session
    """
    if not _sys.stdin.isatty():
        return typer.prompt(prompt_text, default=default)

    import shutil as _shutil_input
    _term_width = _shutil_input.get_terminal_size().columns
    prefix = f"  \033[1m{color}{prompt_text}:\033[0m "
    _prefix_visible = 2 + len(prompt_text) + 2  # "  " + text + ": " (no ANSI)
    _cw1 = max(10, _term_width - _prefix_visible)  # content chars on first (prefix) line
    _cw = _term_width                              # content chars on continuation lines

    buffer: list[str] = list(default)
    cursor = len(buffer)
    _lines_drawn = [0]

    def get_lines() -> list[str]:
        if not buffer:
            return [""]
        result = ["".join(buffer[:_cw1])]
        rest = buffer[_cw1:]
        while rest:
            result.append("".join(rest[:_cw]))
            rest = rest[_cw:]
        return result

    def cursor_2d() -> tuple[int, int]:
        """(row, content_col) — content_col is 0-indexed from start of content on that row."""
        if cursor <= _cw1:
            return (0, cursor)
        pos = cursor - _cw1
        return (1 + pos // _cw, pos % _cw)

    def idx_from_2d(row: int, col: int) -> int:
        if row == 0:
            return col
        return _cw1 + (row - 1) * _cw + col

    def redraw() -> None:
        lines = get_lines()
        crow, ccol = cursor_2d()
        total = len(lines)
        prev = _lines_drawn[0]
        if prev > 1:
            _sys.stdout.write(f"\033[{prev - 1}A")
        _sys.stdout.write("\r")
        for i, line in enumerate(lines):
            content = (prefix + line) if i == 0 else line
            _sys.stdout.write(f"\033[K{content}")
            if i < total - 1:
                _sys.stdout.write("\n\r")
        for _ in range(prev - total):
            _sys.stdout.write("\n\r\033[K")
        if prev > total:
            _sys.stdout.write(f"\033[{prev - total}A")
        _lines_drawn[0] = total
        lines_up = total - 1 - crow
        if lines_up > 0:
            _sys.stdout.write(f"\033[{lines_up}A")
        _sys.stdout.write("\r")
        term_col = (_prefix_visible + ccol) if crow == 0 else ccol
        if term_col > 0:
            _sys.stdout.write(f"\033[{term_col}C")
        _sys.stdout.flush()

    def finish() -> None:
        """Scroll terminal cursor to last content line and write a newline."""
        lines = get_lines()
        crow, _ = cursor_2d()
        lines_below = len(lines) - 1 - crow
        if lines_below > 0:
            _sys.stdout.write(f"\033[{lines_below}B")
        _sys.stdout.write("\n")
        _sys.stdout.flush()

    # Initial draw
    lines = get_lines()
    _lines_drawn[0] = len(lines)
    crow, ccol = cursor_2d()
    out = f"\n{_EDIT_HINT}\n"
    for i, line in enumerate(lines):
        out += (prefix + line) if i == 0 else line
        if i < len(lines) - 1:
            out += "\n"
    lines_up = len(lines) - 1 - crow
    if lines_up > 0:
        out += f"\033[{lines_up}A"
    out += "\r"
    term_col = (_prefix_visible + ccol) if crow == 0 else ccol
    if term_col > 0:
        out += f"\033[{term_col}C"
    _sys.stdout.write(out)
    _sys.stdout.flush()

    while True:
        ch = _getch()
        if ch is None:
            continue

        if ch == "\x11":  # Ctrl+Q — quit review session
            finish()
            return None

        if ch == "\x1a":  # Ctrl+Z — go back to previous transaction
            finish()
            return _BACK

        if ch == "\x13":  # Ctrl+S — skip transaction
            finish()
            return _SKIP

        if ch in ("\r", "\n"):  # Enter — submit
            finish()
            return "".join(buffer)

        if ch in ("\x08", "\x7f"):  # Backspace — delete char before cursor
            if cursor > 0:
                del buffer[cursor - 1]
                cursor -= 1
                redraw()

        elif ch == "\x15":  # Ctrl+U — clear whole line
            buffer.clear()
            cursor = 0
            redraw()

        elif ch == "LEFT":
            if cursor > 0:
                cursor -= 1
                redraw()

        elif ch == "RIGHT":
            if cursor < len(buffer):
                cursor += 1
                redraw()

        elif ch == "UP":
            crow, ccol = cursor_2d()
            if crow > 0:
                lines = get_lines()
                row_len = len(lines[crow - 1])
                cursor = min(idx_from_2d(crow - 1, ccol), idx_from_2d(crow - 1, row_len))
                redraw()

        elif ch == "DOWN":
            crow, ccol = cursor_2d()
            lines = get_lines()
            if crow < len(lines) - 1:
                row_len = len(lines[crow + 1])
                cursor = min(idx_from_2d(crow + 1, ccol), idx_from_2d(crow + 1, row_len))
                redraw()

        elif len(ch) == 1 and ch.isprintable():
            buffer.insert(cursor, ch)
            cursor += 1
            redraw()


def _pick(prompt_text: str, options: list[str], header: str = "", color: str = "", initial: str = "") -> str | object | None:
    """Prompt with raw key input, live filtering, and a persistent hint bar below.

    Layout (all below the cursor — no options printed above):
        prompt line   ← cursor here
        options line  ← filtered matches or "(no matches)" — updates live
        hint line     ← always visible

    - type characters  → filters the options list live
    - digit            → instantly pick from the current filtered list
    - Ctrl+Z (\x1a)   → go back to the previous transaction
    - Ctrl+S (\x13)   → skip this transaction
    - Ctrl+Q (\x11)   → quit the review session
    - Enter            → submit typed text (or skip field if empty)
    - Backspace        → delete last character

    Returns:
        str   — chosen or typed value (empty string = skip this field)
        _BACK — go back to the previous transaction
        _SKIP — skip this whole transaction
        None  — quit the review session
    """
    prompt_prefix = f"  \033[1m{color}{prompt_text}:\033[0m "
    buffer: list[str] = list(initial)
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
            items.append(f"\033[2m[{i + 1}]\033[0m {color}{o}\033[0m")
            used += sep + visible_len
        line = "  " + "   ".join(items)
        # Page indicator
        has_prev = _page_offset > 0
        has_next = _page_offset + 9 < len(filt)
        if has_prev or has_next:
            nav = ("▲ " if has_prev else "  ") + ("▼" if has_next else " ")
            line += f"   \033[2m{nav}\033[0m"
        return line

    initial_opts = make_options_line(get_filtered())

    # Print: blank line, prompt, options (as separator), hint — then move cursor back up to prompt
    _sys.stdout.write(f"\n{prompt_prefix}{''.join(buffer)}\n{initial_opts}\n{_HINT}")
    _sys.stdout.write(f"\033[2A\r{prompt_prefix}{''.join(buffer)}")  # up 2, col 1, rewrite prefix → cursor right after it
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

        if ch == "\x1a":  # Ctrl+Z — go back to previous transaction
            exit_pick()
            return _BACK

        if ch == "\x13":  # Ctrl+S — skip transaction
            exit_pick()
            return _SKIP

        if ch in ("\r", "\n"):  # Enter — submit typed text
            value = "".join(buffer).strip()
            exit_pick()
            return value

        if ch in ("LEFT", "RIGHT"):  # ignored in pick
            continue

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
    month: Optional[int] = typer.Option(None, "--month", "-m", min=1, max=12, help="Filter by month (1-12)"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Filter by year (e.g. 2024)"),
    info: bool = typer.Option(False, "--info", "-i", help="Show unreviewed count per month and exit"),
) -> None:
    """Interactively step through expenses that need attention (missing counterparty or category).

    Examples:\n
      expense review\n
      expense review --info\n
      expense review --unidentified\n
      expense review --uncategorized --month 3
    """
    from expense_cli.identifier import load_counterparties, rule_exists, save_counterparty_rule
    from expense_cli.categorizer import load_rules, categorize, category_rule_exists, save_category_rule

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

    if info:
        # Per-month breakdown of all unreviewed expenses (before year/month filter)
        all_unreviewed = read_expenses()
        if unidentified:
            all_unreviewed = [e for e in all_unreviewed if not e.get("counterparty")]
        elif uncategorized:
            all_unreviewed = [e for e in all_unreviewed if not e["category"]]
        else:
            all_unreviewed = [e for e in all_unreviewed if not e.get("counterparty") or not e["category"]]

        month_counts: dict[str, int] = {}
        for e in all_unreviewed:
            ym = e["date"][:7]  # "YYYY-MM"
            month_counts[ym] = month_counts.get(ym, 0) + 1

        if not month_counts:
            typer.echo("Nothing to review.")
        else:
            from rich.table import Table as _Table
            summary = _Table(show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0))
            summary.add_column("Month")
            summary.add_column("Unreviewed", justify="right")
            for ym in sorted(month_counts):
                summary.add_row(ym, str(month_counts[ym]))
            console.print(summary)
        return

    if not expenses:
        typer.echo("Nothing to review.")
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

    import shutil as _shutil_review, textwrap as _textwrap

    def row(key: str, value: str, key_color: str = "dim") -> str:
        return f"  [{key_color}]{key:<12}[/{key_color}]  {value}"

    saved = 0
    i = 0
    force_reprompt: set[str] = set()

    while i < len(expenses):
        expense = expenses[i]
        console.print("")
        console.rule(f"[bold cyan][{i + 1}/{len(expenses)}][/bold cyan]  [dim]#{expense['id']}[/dim]")
        _key_col = 16  # 2 leading + 12 key + 2 separator
        _val_width = max(20, _shutil_review.get_terminal_size().columns - _key_col)

        def wrap_row(key: str, value: str, key_color: str = "dim") -> str:
            lines = _textwrap.wrap(value, width=_val_width) or [""]
            joined = ("\n" + " " * _key_col).join(lines)
            return f"  [{key_color}]{key:<12}[/{key_color}]  {joined}"

        console.print(row("date",         f"[cyan]{expense['date']}[/cyan]",       "dim cyan"))
        if expense.get("weekday"):
            console.print(row("weekday",   f"[dim]{expense['weekday']}[/dim]"))
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
        revisiting = expense["id"] in force_reprompt

        # Counterparty step
        if not expense.get("counterparty") or revisiting:
            initial_cp = expense.get("counterparty", "") if revisiting else ""
            result = _pick("Counterparty", known_counterparties, "Known counterparties", color="\033[36m", initial=initial_cp)
            if result is None:
                break
            if result is _BACK:
                if i > 0:
                    i -= 1
                    force_reprompt.add(expenses[i]["id"])
                else:
                    console.print("  [dim]Already at the first expense — restarting.[/dim]")
                    force_reprompt.add(expense["id"])
                continue
            if result is _SKIP:
                skip_transaction = True
            elif result:
                result = result.lower()
                fields["counterparty"] = result
                if result not in known_counterparties:
                    known_counterparties.append(result)

        # Category step
        if not skip_transaction and (not expense["category"] or revisiting):
            current_counterparty = fields.get("counterparty") or expense.get("counterparty", "")
            suggested = categorize(current_counterparty, rules) if current_counterparty else ""
            if suggested:
                console.print(row("suggested", f"[yellow]{suggested}[/yellow] [dim](Enter to accept)[/dim]", "dim yellow"))
            initial_cat = expense.get("category", "") if revisiting else ""
            result = _pick("Category", known_categories, "Known categories", color="\033[33m", initial=initial_cat)
            if result is None:
                if fields:
                    update_expense(int(expense["id"]), fields)
                    expense.update(fields)
                    saved += 1
                break
            if result is _BACK:
                if i > 0:
                    i -= 1
                    force_reprompt.add(expenses[i]["id"])
                else:
                    console.print("  [dim]Already at the first expense — restarting.[/dim]")
                    force_reprompt.add(expense["id"])
                continue
            if result is _SKIP:
                skip_transaction = True
            elif result:
                result = result.lower()
                fields["category"] = result
                if result not in known_categories:
                    known_categories.append(result)
            elif suggested:
                fields["category"] = suggested

        if skip_transaction:
            console.print(f"  [dim]skipped transaction[/dim]")
        elif fields:
            update_expense(int(expense["id"]), fields)
            expense.update(fields)
            saved += 1
            console.print(f"  [green]✓ saved[/green]")

            # Offer to save counterparty as an identification rule
            cp = fields.get("counterparty")
            if cp and not rule_exists(cp):
                iban = expense.get("iban", "")
                desc = expense.get("description", "")
                iban_saved = False
                if iban and _is_valid_iban(iban):
                    if typer.confirm(f"  \033[36mSave rule: iban '{iban}' → '{cp}'?\033[0m", default=True):
                        save_counterparty_rule(cp, "iban", iban)
                        console.print(f"  [cyan]✓ rule saved (iban)[/cyan]")
                        iban_saved = True
                if not iban_saved and not rule_exists(cp):
                    keyword = _input_prefilled(f"Keyword for '{cp}'", desc, color="\033[36m")
                    if keyword is None:
                        break  # quit review session
                    if keyword is _BACK:
                        if i > 0:
                            i -= 1
                            force_reprompt.add(expenses[i]["id"])
                        else:
                            console.print("  [dim]Already at the first expense — restarting.[/dim]")
                            force_reprompt.add(expense["id"])
                        continue
                    if keyword is not _SKIP and keyword.strip():
                        save_counterparty_rule(cp, "description_contains", keyword.strip().lower())
                        console.print(f"  [cyan]✓ rule saved (description_contains)[/cyan]")

            # Offer to save category as a rule
            cat = fields.get("category")
            final_cp = fields.get("counterparty") or expense.get("counterparty", "")
            if cat and final_cp and not category_rule_exists(final_cp):
                if typer.confirm(f"  \033[33mSave category rule '{final_cp}' → '{cat}'?\033[0m", default=False):
                    save_category_rule(final_cp, cat)
                    console.print(f"  [yellow]✓ category rule saved[/yellow]")
        else:
            console.print(f"  [dim]skipped[/dim]")

        force_reprompt.discard(expense["id"])
        i += 1

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
    """Update identity or category on an existing expense.

    Examples:\n
      expense edit 42 --counterparty "albert heijn"\n
      expense edit 42 --category groceries\n
      expense edit 42 --iban NL91ABNA0417164300 --counterparty "albert heijn"
    """
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
    """Delete a single expense by ID, or all expenses with --all.

    Examples:\n
      expense delete 42\n
      expense delete 42 --yes\n
      expense delete --all
    """
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
def insights(
    by: str = typer.Option("category", "--by", help="Group by: category or counterparty"),
    from_date: Optional[str] = typer.Option(None, "--from", help="Start date (YYYY-MM-DD, inclusive)"),
    to_date: Optional[str] = typer.Option(None, "--to", help="End date (YYYY-MM-DD, inclusive)"),
    month: Optional[str] = typer.Option(None, "--month", help="Filter by month: YYYY-MM or 1-12"),
    year: Optional[int] = typer.Option(None, "--year", help="Year to use with --month number (default: current year)"),
    direction: Optional[str] = typer.Option(None, "--direction", help="Filter rows: 'in' or 'out' (default: show all)"),
    without: Optional[str] = typer.Option(None, "--without", help="Exclude values for the active --by dimension (comma-separated)"),
    without_category: Optional[str] = typer.Option(None, "--without-category", help="Exclude categories (comma-separated)"),
    without_counterparty: Optional[str] = typer.Option(None, "--without-counterparty", help="Exclude counterparties (comma-separated)"),
    chart: bool = typer.Option(False, "--chart", help="Show a bar chart below the table"),
    trend: bool = typer.Option(False, "--trend", help="Show monthly pivot table (months × groups)"),
    months: Optional[int] = typer.Option(None, "--months", help="Number of recent months for --trend (default: 6)"),
) -> None:
    """Summarize transactions grouped by category or counterparty with a bar chart.

    Shows Out, In, and Net columns so you can see the net effect per group
    (e.g. gifts: what you spent minus what you received from friends).
    A Trend sparkline column is shown automatically when data spans multiple months.

    Examples:\n
      expense insights\n
      expense insights --direction out\n
      expense insights --by counterparty\n
      expense insights --month 2026-03\n
      expense insights --by category --from 2026-01-01 --to 2026-03-31\n
      expense insights --without food,transport\n
      expense insights --by counterparty --without Albert,Shell\n
      expense insights --trend\n
      expense insights --trend --months 3\n
      expense insights --trend --by counterparty --from 2026-01-01 --to 2026-06-30
    """
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

    if direction and direction not in ("in", "out"):
        typer.echo("--direction must be 'in' or 'out'.", err=True)
        raise typer.Exit(1)

    expenses = read_expenses()

    if from_date:
        expenses = [e for e in expenses if e["date"] >= from_date]
    if to_date:
        expenses = [e for e in expenses if e["date"] <= to_date]

    if direction:
        expenses = [e for e in expenses if e.get("direction") == direction]
    if without:
        excluded = {c.strip().lower() for c in without.split(",")}
        if by == "category":
            expenses = [e for e in expenses if e["category"].lower() not in excluded]
        else:
            expenses = [e for e in expenses if e.get("counterparty", "").lower() not in excluded]
    if without_category:
        excluded = {c.strip().lower() for c in without_category.split(",")}
        expenses = [e for e in expenses if e["category"].lower() not in excluded]
    if without_counterparty:
        excluded = {c.strip().lower() for c in without_counterparty.split(",")}
        expenses = [e for e in expenses if e.get("counterparty", "").lower() not in excluded]

    if not expenses:
        label = "expenses" if direction == "out" else "income" if direction == "in" else "transactions"
        typer.echo(f"No {label} found.")
        return

    # --- Monthly bucketing (shared by sparkline and --trend) ---
    monthly_out: dict[str, dict[str, float]] = {}
    monthly_in: dict[str, dict[str, float]] = {}
    for e in expenses:
        mo = e["date"][:7]
        key = e.get(by) or "(none)"
        amount = float(e["amount"])
        if e.get("direction") == "out":
            if mo not in monthly_out:
                monthly_out[mo] = {}
            monthly_out[mo][key] = monthly_out[mo].get(key, 0.0) + amount
        else:
            if mo not in monthly_in:
                monthly_in[mo] = {}
            monthly_in[mo][key] = monthly_in[mo].get(key, 0.0) + amount

    # Primary monthly dict for trend/sparkline: out-focused (spending) unless --direction in
    monthly_primary = monthly_in if direction == "in" else monthly_out
    all_months = sorted(set(monthly_out) | set(monthly_in))

    # --- Option A: --trend pivot table ---
    if trend:
        from datetime import date as _date_cls

        if from_date or to_date:
            start_mo = from_date[:7] if from_date else (all_months[0] if all_months else "")
            end_mo = to_date[:7] if to_date else (all_months[-1] if all_months else "")
        else:
            n = months or 6
            today = _date_cls.today()
            ey, em = today.year, today.month
            sy, sm = ey, em
            for _ in range(n - 1):
                sm -= 1
                if sm == 0:
                    sm = 12
                    sy -= 1
            start_mo = f"{sy:04d}-{sm:02d}"
            end_mo = f"{ey:04d}-{em:02d}"

        show_months = _month_range(start_mo, end_mo)
        if not show_months:
            typer.echo("No data to show.")
            return

        all_groups = {e.get(by) or "(none)" for e in expenses}
        col_totals = {mo: sum(monthly_primary.get(mo, {}).get(g, 0.0) for g in all_groups) for mo in show_months}
        grand_total = sum(col_totals.values())
        avg_total = grand_total / len(show_months)
        sorted_groups = sorted(all_groups, key=lambda g: sum(monthly_primary.get(mo, {}).get(g, 0.0) for mo in show_months), reverse=True)

        pivot = Table(show_lines=False, box=None, padding=(0, 1), show_footer=True)
        pivot.add_column(by.capitalize(), min_width=16, footer="[dim]Total[/dim]")
        for mo in show_months:
            pivot.add_column(mo, justify="right", footer=f"[dim]{col_totals[mo]:.2f}[/dim]")
        pivot.add_column("Avg/mo", justify="right", style="dim", footer=f"[dim]{avg_total:.2f}[/dim]")

        for group in sorted_groups:
            row_vals = [monthly_primary.get(mo, {}).get(group, 0.0) for mo in show_months]
            avg = sum(row_vals) / len(show_months)
            pivot.add_row(
                group,
                *[f"{v:.2f}" if v else "[dim]—[/dim]" for v in row_vals],
                f"{avg:.2f}",
            )

        console.print(pivot)
        return

    # --- Regular grouped summary ---
    out_totals: dict[str, float] = {}
    in_totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for e in expenses:
        key = e.get(by) or "(none)"
        amount = float(e["amount"])
        counts[key] = counts.get(key, 0) + 1
        if e.get("direction") == "out":
            out_totals[key] = out_totals.get(key, 0.0) + amount
        else:
            in_totals[key] = in_totals.get(key, 0.0) + amount

    all_keys = set(out_totals) | set(in_totals)
    nets = {k: out_totals.get(k, 0.0) + in_totals.get(k, 0.0) for k in all_keys}
    sorted_keys = sorted(all_keys, key=lambda k: abs(nets[k]), reverse=True)

    total_out = sum(out_totals.values())
    total_in = sum(in_totals.values())
    total_net = total_out + total_in
    total_count = sum(counts.values())

    # Option B: sparkline — auto-shown when data spans >= 2 months
    sparkline_months = all_months[-6:]
    show_sparkline = len(sparkline_months) >= 2

    table = Table(show_lines=False, box=None, padding=(0, 1), show_footer=True)
    table.add_column(by.capitalize(), min_width=20, footer="[dim]Total[/dim]")
    table.add_column("Out", justify="right", style="red", footer=f"[dim]{total_out:.2f}[/dim]" if out_totals else "")
    table.add_column("In", justify="right", style="green", footer=f"[dim]{total_in:.2f}[/dim]" if in_totals else "")
    table.add_column("Net", justify="right", footer=f"[dim]{total_net:.2f}[/dim]")
    table.add_column("%", justify="right", style="dim", footer="[dim]100.0%[/dim]")
    table.add_column("Count", justify="right", style="dim", footer=f"[dim]{total_count}[/dim]")
    if show_sparkline:
        table.add_column("Trend", style="dim", no_wrap=True)

    total_abs_net = sum(abs(v) for v in nets.values())
    for key in sorted_keys:
        out_val = out_totals.get(key, 0.0)
        in_val = in_totals.get(key, 0.0)
        pct = abs(nets[key]) / total_abs_net * 100 if total_abs_net else 0.0
        row: list[str] = [
            key,
            f"{out_val:.2f}" if key in out_totals else "",
            f"{in_val:.2f}" if key in in_totals else "",
            f"{nets[key]:.2f}",
            f"{pct:.1f}%",
            str(counts[key]),
        ]
        if show_sparkline:
            spark_vals = [monthly_primary.get(mo, {}).get(key, 0.0) for mo in sparkline_months]
            row.append(_sparkline(spark_vals))
        table.add_row(*row)

    if chart:
        chart_data = [(key, nets[key]) for key in sorted_keys]
        max_val = max(abs(v) for _, v in chart_data) if chart_data else 0.0
        fixed_color = "green" if direction == "in" else "red" if direction == "out" else None

        chart_table = Table(show_lines=False, box=None, padding=(0, 1), show_header=False)
        chart_table.add_column("Label", min_width=20, style="dim")
        chart_table.add_column("Bar", no_wrap=True)
        chart_table.add_column("Amount", justify="right")

        for i, (key, val) in enumerate(chart_data):
            base_color = fixed_color or ("green" if val >= 0 else "red")
            bar_color = f"bright_{base_color}" if i % 2 else base_color
            bar = _render_bar(val, max_val)
            amt_str = f"[{bar_color}]{val:.2f}[/{bar_color}]"
            chart_table.add_row(key, f"[{bar_color}]{bar}[/{bar_color}]", amt_str)

        console.print(chart_table)
    else:
        console.print(table)


@app.command()
def reapply() -> None:
    """Re-apply identification and categorization rules to all unreviewed expenses.

    Unreviewed = missing counterparty OR category. Rules are re-run from
    counterparties.toml (identify) and categories.toml (categorize).
    Already-set counterparties are never overwritten.

    Examples:\n
      expense reapply
    """
    from expense_cli.identifier import load_counterparties, identify
    from expense_cli.categorizer import load_rules, categorize

    counterparties = load_counterparties()
    rules = load_rules()

    expenses = read_expenses()
    unreviewed = [e for e in expenses if not e.get("counterparty") or not e.get("category")]

    if not unreviewed:
        typer.echo("Nothing to reapply — all expenses are reviewed.")
        return

    identified = 0
    categorized = 0
    updated_rows: list[tuple[dict, dict]] = []  # (expense, updates)

    for expense in unreviewed:
        updates: dict = {}

        if not expense.get("counterparty"):
            resolved = identify(expense.get("iban", ""), expense.get("description", ""), counterparties)
            if resolved:
                updates["counterparty"] = resolved
                identified += 1

        effective_counterparty = updates.get("counterparty") or expense.get("counterparty", "")
        if not expense.get("category"):
            cat = categorize(effective_counterparty, rules)
            if cat:
                updates["category"] = cat
                categorized += 1

        if updates:
            update_expense(int(expense["id"]), updates)
            updated_rows.append((expense, updates))

    if updated_rows:
        table = Table(title=f"Reapplied to {len(updated_rows)} expense(s)", show_lines=True)
        table.add_column("ID", style="dim")
        table.add_column("Date")
        table.add_column("Amount", justify="right")
        table.add_column("Description")
        table.add_column("Counterparty")
        table.add_column("Category")
        for expense, updates in updated_rows:
            cp = updates.get("counterparty") or expense.get("counterparty", "")
            cat = updates.get("category") or expense.get("category", "")
            cp_str = f"[green]{cp}[/green]" if "counterparty" in updates else cp
            cat_str = f"[green]{cat}[/green]" if "category" in updates else cat
            amt = float(expense.get("amount", 0))
            amt_str = f"[red]{expense['amount']}[/red]" if amt < 0 else f"[green]{expense['amount']}[/green]"
            table.add_row(expense["id"], expense["date"], amt_str, expense.get("description", ""), cp_str, cat_str)
        console.print(table)

    typer.echo(f"Reapplied rules: {identified} identified, {categorized} categorized ({len(updated_rows)} expenses updated).")


@app.command()
def version() -> None:
    """Print the current CLI version."""
    typer.echo(__version__)


def main() -> None:
    app()
