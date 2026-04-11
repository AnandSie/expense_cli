import typer
from datetime import date
import calendar
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from expense_cli import __version__
from expense_cli.duplicates import format_match_key, group_possible_duplicates, resolve_match_fields
from expense_cli.storage import read_expenses, write_expense, write_expenses_batch, next_id, update_expense, delete_expense, reset_expenses, _weekday_from_date
from expense_cli.utils import _sparkline, _category_parent, _category_matches, _month_range, compute_ratio

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

NOTE_MAX_LEN = 120

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
        dc = entry.get("description_contains")
        if dc is not None and not isinstance(dc, str):
            if not isinstance(dc, list) or not all(isinstance(p, str) for p in dc):
                errors.append(f"{prefix}: 'description_contains' must be a string or list of strings")
        iban_val = entry.get("iban")
        if iban_val is not None and not isinstance(iban_val, str):
            if not isinstance(iban_val, list) or not all(isinstance(i, str) for i in iban_val):
                errors.append(f"{prefix}: 'iban' must be a string or list of strings")
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
    for entry in sorted(entries, key=lambda e: (
        _category_parent(e.get("category", "")),
        e.get("category", ""),
        e.get("name", "").lower(),
    )):
        dc = entry.get("description_contains", "")
        if isinstance(dc, list):
            dc = ", ".join(dc)
        iban_val = entry.get("iban", "")
        if isinstance(iban_val, list):
            iban_val = ", ".join(iban_val)
        table.add_row(
            entry.get("name", ""),
            iban_val,
            dc,
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
    column: Optional[str] = typer.Option(None, "--column", "-c", help="Column name to read directly"),
    from_column: Optional[str] = typer.Option(None, "--from-column", "-F", help="Column to apply a regex pattern to"),
    pattern: Optional[str] = typer.Option(None, "--pattern", "-p", help="Regex pattern; first capture group used, else full match"),
    extract_iban_from: Optional[str] = typer.Option(None, "--extract-iban-from", "-e", help="Column to auto-detect a single IBAN from"),
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
    contains: Optional[str] = typer.Option(None, "--contains", "-t", help="Description substring to match (case-insensitive)"),
    iban: Optional[str] = typer.Option(None, "--iban", "-i", help="Exact IBAN to match (case-insensitive)"),
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
        if matcher_exists(name, matcher_type, matcher_value):
            typer.echo(f"'{name}' already has '{matcher_value}' as a {matcher_type} value.", err=True)
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
    new_name: Optional[str] = typer.Option(None, "--new-name", "-N", help="Rename the counterparty"),
    contains: Optional[str] = typer.Option(None, "--contains", "-t", help="Replace the description_contains matcher"),
    iban: Optional[str] = typer.Option(None, "--iban", "-i", help="Replace the iban matcher"),
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
    _iban_current = entry.get("iban", "")
    if isinstance(_iban_current, list):
        _iban_current = ", ".join(_iban_current)
    r_iban = _input_prefilled("iban", _iban_current, color="\033[36m")
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
    if r_iban and r_iban != _iban_current:
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
    all_: bool = typer.Option(False, "--all", "-a", help="Remove all counterparty entries (requires confirmation)"),
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
    for rule in sorted(rules, key=lambda r: (_category_parent(r.get("category", "")), r.get("category", ""), r.get("name", "").lower())):
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
    new_counterparty: Optional[str] = typer.Option(None, "--new-counterparty", "-C", help="Rename the counterparty key"),
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
    from collections import Counter as _Counter
    _known_cats = [cat for cat, _ in _Counter(
        e["category"] for e in read_expenses() if e.get("category")
    ).most_common()]
    for _r in load_rules():
        if _r.get("category") and _r["category"] not in _known_cats:
            _known_cats.append(_r["category"])
    _known_cats.sort(key=lambda c: (_category_parent(c), c))
    r_category = _pick("Category", _known_cats, initial=rule.get("category", ""))
    if r_category is None or r_category is _SKIP or r_category is _BACK:
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
    all_: bool = typer.Option(False, "--all", "-a", help="Remove all category rules (requires confirmation)"),
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
    expense_time: Optional[str] = typer.Option(None, "--time", "-t", help="Time in HH:MM:SS format"),
    iban: str = typer.Option("", "--iban", "-i", help="Counterparty IBAN"),
    counterparty: str = typer.Option("", "--counterparty", "-n", help="Counterparty name"),
    note: str = typer.Option("", "--note", "-N", help=f"Optional note (max {NOTE_MAX_LEN} chars)"),
) -> None:
    """Add a new expense.

    Examples:\n
      expense add 12.50 "Coffee"\n
      expense add -42.00 "Supermarket" --category groceries --counterparty "albert heijn"\n
      expense add 9.99 "Spotify" --date 2026-03-01 --iban NL91ABNA0417164300
    """
    if expense_date is None:
        expense_date = date.today().isoformat()

    if len(note) > NOTE_MAX_LEN:
        typer.echo(f"Note truncated to {NOTE_MAX_LEN} characters.", err=True)
        note = note[:NOTE_MAX_LEN]

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
        "note": note,
    }
    write_expense(row)
    typer.echo(f"Added expense #{row['id']}")


@app.command(name="list")
def list_expenses(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category"),
    counterparty: Optional[str] = typer.Option(None, "--counterparty", "-p", help="Filter by counterparty"),
    from_date: Optional[str] = typer.Option(None, "--from", "-f", help="Start date YYYY-MM-DD (inclusive)"),
    to_date: Optional[str] = typer.Option(None, "--to", "-t", help="End date YYYY-MM-DD (inclusive)"),
    month: Optional[str] = typer.Option(None, "--month", "-m", help="Filter by month: YYYY-MM or 1-12"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Year to use with --month number (default: current year)"),
    unreviewed: bool = typer.Option(False, "--unreviewed", "-u", help="Show only expenses missing counterparty or category"),
    reviewed: bool = typer.Option(False, "--reviewed", "-r", help="Show only expenses with counterparty and category set"),
    wide: bool = typer.Option(False, "--wide", "-w", help="Show all columns (description, IBAN, time)"),
    min_amount: Optional[float] = typer.Option(None, "--min", "-l", help="Hide transactions with abs(amount) below this value"),
    max_amount: Optional[float] = typer.Option(None, "--max", "-x", help="Hide transactions with abs(amount) above this value"),
    direction: Optional[str] = typer.Option(None, "--direction", "-d", help="Filter by direction: 'in' or 'out'"),
    exclude_category: Optional[str] = typer.Option(None, "--exclude-category", "-C", help="Exclude categories (comma-separated, prefix match)"),
    exclude_counterparty: Optional[str] = typer.Option(None, "--exclude-counterparty", "-P", help="Exclude counterparties (comma-separated)"),
    include_category: Optional[str] = typer.Option(None, "--include-category", help="Show only these categories (comma-separated, prefix match)"),
    include_counterparty: Optional[str] = typer.Option(None, "--include-counterparty", help="Show only these counterparties (comma-separated)"),
    expense_id: Optional[str] = typer.Option(None, "--id", "-i", help="Show expense(s) by ID (comma-separated)"),
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
      expense list --exclude-category food,transport\n
      expense list --exclude-counterparty Albert,Shell\n
      expense list --include-category food,health\n
      expense list --include-counterparty Albert,Shell
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
        # Include children of any split parent in the result
        str_ids = {str(i) for i in ids}
        expenses = [e for e in expenses if int(e["id"]) in ids or e.get("split_id") in str_ids]

    if category:
        expenses = [e for e in expenses if _category_matches(e["category"], category)]
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
    if exclude_category:
        excl_list = [c.strip() for c in exclude_category.split(",")]
        expenses = [e for e in expenses if not any(_category_matches(e["category"], ex) for ex in excl_list)]
    if exclude_counterparty:
        excluded = {c.strip().lower() for c in exclude_counterparty.split(",")}
        expenses = [e for e in expenses if e.get("counterparty", "").lower() not in excluded]
    if include_category:
        incl_list = [c.strip() for c in include_category.split(",")]
        expenses = [e for e in expenses if any(_category_matches(e["category"], inc) for inc in incl_list)]
    if include_counterparty:
        incl_set = {c.strip().lower() for c in include_counterparty.split(",")}
        expenses = [e for e in expenses if e.get("counterparty", "").lower() in incl_set]

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
        table.add_column("Description", overflow="fold")
        table.add_column("IBAN", style="dim", overflow="fold")
    table.add_column("Counterparty")
    table.add_column("Category")
    if wide:
        table.add_column("Note", overflow="fold", style="dim")

    _all_expenses = read_expenses()
    _split_parent_ids_list = {e["split_id"] for e in _all_expenses if e.get("split_id")}

    for e in expenses:
        amt = float(e["amount"])
        amt_str = f"[red]{e['amount']}[/red]" if amt < 0 else f"[green]{e['amount']}[/green]"
        if str(e["id"]) in _split_parent_ids_list:
            id_cell = f"{e['id']} [dim](split)[/dim]"
        elif e.get("split_id"):
            id_cell = f"[dim]↳[/dim] {e['id']}"
        else:
            id_cell = e["id"]
        row = [id_cell, e["date"], e.get("weekday", "")]
        if wide:
            row.append(e.get("time", ""))
        row.append(amt_str)
        if wide:
            row += [e.get("description", ""), e.get("iban", "")]
        row += [e.get("counterparty", ""), e["category"]]
        if wide:
            row.append(e.get("note", ""))
        table.add_row(*row)

    console.print(table)


@app.command(name="import")
def import_expenses(
    filepath: str = typer.Argument(..., help="Path to the bank CSV file"),
    bank: str = typer.Option(..., "--bank", "-b", help="Bank name matching a config in ~/.expense_cli/banks/<name>.toml"),
    force: bool = typer.Option(False, "--force", "-F", help="Import all rows, skipping duplicate detection"),
    match_field: list[str] = typer.Option([], "--match-field", help="Fields to use for possible duplicate detection (comma-separated)"),
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

    try:
        match_fields = resolve_match_fields(match_field)
    except ValueError as e:
        typer.echo(str(e), err=True)
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

    possible_duplicate_groups = [
        (key, members)
        for key, members in group_possible_duplicates(existing + to_import, match_fields)
        if any(row in to_import for row in members)
    ]

    # --- Imported rows table ---
    if to_import:
        n_identified = sum(1 for r in to_import if r.get("counterparty"))
        n_categorized = sum(1 for r in to_import if r.get("category"))
        n_need_review = sum(1 for r in to_import if not r.get("counterparty") or not r.get("category"))

        imp_table = Table(
            title=f"Imported {len(to_import)} row(s) from [bold]{filepath}[/bold] ([dim]{bank}[/dim])",
            show_lines=False, box=None, padding=(0, 1),
        )
        imp_table.add_column("Date")
        imp_table.add_column("Amount", justify="right")
        imp_table.add_column("Description", max_width=40)
        imp_table.add_column("Counterparty")
        imp_table.add_column("Category")
        for r in to_import:
            amt = float(r.get("amount", 0))
            amt_str = f"[red]{amt:.2f}[/red]" if amt < 0 else f"[green]{amt:.2f}[/green]"
            cp = r.get("counterparty") or "[dim]—[/dim]"
            cat = r.get("category") or "[dim]—[/dim]"
            imp_table.add_row(r.get("date", ""), amt_str, r.get("description", ""), cp, cat)
        console.print(imp_table)

        parts = [f"[green]{len(to_import)} imported[/green]"]
        parts.append(f"{n_identified} identified" if n_identified else "[dim]0 identified[/dim]")
        parts.append(f"{n_categorized} categorized" if n_categorized else "[dim]0 categorized[/dim]")
        if n_need_review:
            parts.append(f"[yellow]{n_need_review} need review[/yellow]")
        console.print("  " + " · ".join(parts), highlight=False)
        if n_need_review:
            console.print("  [dim]Run [bold]expense review[/bold] to assign missing counterparties/categories.[/dim]")
    else:
        console.print("[dim]Nothing new to import.[/dim]")

    # --- Skipped duplicates table ---
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

    if possible_duplicate_groups:
        fields_str = ", ".join(match_fields)
        table = Table(
            title=f"Possible duplicates ({fields_str})",
            show_lines=True,
        )
        table.add_column("Match", style="dim")
        table.add_column("Source", style="dim")
        table.add_column("ID", style="dim")
        table.add_column("Date")
        table.add_column("Amount", justify="right")
        table.add_column("Description")
        table.add_column("Counterparty")
        table.add_column("IBAN", style="dim")
        for key, members in possible_duplicate_groups:
            label = format_match_key(match_fields, key)
            imported_ids = {row.get("id") for row in to_import}
            for idx, member in enumerate(members):
                source = "imported" if member.get("id") in imported_ids else "existing"
                table.add_row(
                    label if idx == 0 else "",
                    source,
                    str(member.get("id", "")),
                    str(member.get("date", "")),
                    str(member.get("amount", "")),
                    str(member.get("description", "")),
                    str(member.get("counterparty", "")),
                    str(member.get("iban", "")),
                )
        console.print(table)


@app.command()
def duplicates(
    from_date: Optional[str] = typer.Option(None, "--from", "-f", help="Start date YYYY-MM-DD"),
    to_date: Optional[str] = typer.Option(None, "--to", "-t", help="End date YYYY-MM-DD"),
    match_field: list[str] = typer.Option([], "--match-field", help="Fields to use for duplicate detection (comma-separated)"),
) -> None:
    """Show possible duplicate transactions based on selected fields."""
    try:
        match_fields = resolve_match_fields(match_field)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    expenses = read_expenses()
    if from_date:
        expenses = [e for e in expenses if e["date"] >= from_date]
    if to_date:
        expenses = [e for e in expenses if e["date"] <= to_date]

    duplicate_groups = group_possible_duplicates(expenses, match_fields)
    if not duplicate_groups:
        typer.echo("No possible duplicates found.")
        return

    for idx, (key, members) in enumerate(duplicate_groups, start=1):
        table = Table(
            title=f"Possible duplicate group {idx} ({format_match_key(match_fields, key)})",
            show_lines=True,
        )
        table.add_column("ID", style="dim")
        table.add_column("Date")
        table.add_column("Amount", justify="right")
        table.add_column("Description")
        table.add_column("Counterparty")
        table.add_column("IBAN", style="dim")
        for member in members:
            table.add_row(
                str(member.get("id", "")),
                str(member.get("date", "")),
                str(member.get("amount", "")),
                str(member.get("description", "")),
                str(member.get("counterparty", "")),
                str(member.get("iban", "")),
            )
        console.print(table)


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

    _divider = "\033[2m" + "─" * _term_width + "\033[0m"

    def finish() -> None:
        """Scroll to last content line, clear divider and hint lines, then advance."""
        lines = get_lines()
        crow, _ = cursor_2d()
        lines_below = len(lines) - 1 - crow
        if lines_below > 0:
            _sys.stdout.write(f"\033[{lines_below}B")
        _sys.stdout.write(
            "\n\r\033[K"   # move to divider line, clear it
            "\n\r\033[K"   # move to hint line, clear it
            "\033[2A\n"    # back to last content line, then newline to advance
        )
        _sys.stdout.flush()

    # Initial draw: blank line, content, divider, hint — then move cursor back up to content
    lines = get_lines()
    _lines_drawn[0] = len(lines)
    crow, ccol = cursor_2d()
    out = "\n"
    for i, line in enumerate(lines):
        out += (prefix + line) if i == 0 else line
        if i < len(lines) - 1:
            out += "\n"
    out += f"\n{_divider}\n{_EDIT_HINT}"
    # Cursor is at hint. Move up to the right content row.
    lines_up = len(lines) + 1 - crow
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


def _pick_note(existing: str) -> "str | object | None":
    """Prompt for an optional free-text note using _input_prefilled.

    In non-TTY mode (tests, pipes) returns "" immediately so the note step
    is silently skipped without consuming stdin.
    """
    import sys as _sys_note
    if not _sys_note.stdin.isatty():
        return ""
    return _input_prefilled("Note", existing, color="\033[35m")


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
    _divider = "\033[2m" + "─" * _term_width + "\033[0m"

    # Print: blank line, prompt, options, divider, hint — then move cursor back up to prompt
    _sys.stdout.write(f"\n{prompt_prefix}{''.join(buffer)}\n{initial_opts}\n{_divider}\n{_HINT}")
    _sys.stdout.write(f"\033[3A\r{prompt_prefix}{''.join(buffer)}")  # up 3, col 1, rewrite prefix → cursor right after it
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
        """Clear options, divider, and hint lines, leave only the prompt line with its value."""
        _sys.stdout.write(
            "\n\r\033[K"   # move to options line, clear it
            "\n\r\033[K"   # move to divider line, clear it
            "\n\r\033[K"   # move to hint line, clear it
            "\033[3A\n"    # back to prompt line, then newline to advance
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
    unidentified: bool = typer.Option(False, "--unidentified", "-u", help="Show only expenses with no counterparty"),
    uncategorized: bool = typer.Option(False, "--uncategorized", "-U", help="Show only uncategorized expenses"),
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
    known_categories.sort(key=lambda c: (_category_parent(c), c))

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
        if expense.get("note"):
            console.print(wrap_row("note", expense["note"], "dim magenta"))
        console.print(wrap_row("description", expense["description"]))

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

        # Note step — always offered unless transaction is being skipped.
        # In non-TTY mode _pick_note returns "" immediately (no stdin consumed).
        if not skip_transaction:
            existing_note = expense.get("note", "")
            note_result = _pick_note(existing_note)
            if note_result is None:
                if fields:
                    update_expense(int(expense["id"]), fields)
                    expense.update(fields)
                    saved += 1
                break
            if note_result is _BACK:
                if i > 0:
                    i -= 1
                    force_reprompt.add(expenses[i]["id"])
                else:
                    console.print("  [dim]Already at the first expense — restarting.[/dim]")
                    force_reprompt.add(expense["id"])
                continue
            if note_result is _SKIP:
                skip_transaction = True
            elif note_result != existing_note:
                fields["note"] = note_result[:NOTE_MAX_LEN]

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
    expense_id: str = typer.Argument(..., help="ID(s) of the expense(s) to update (comma-separated for multi-edit)"),
    iban: Optional[str] = typer.Option(None, "--iban", "-i", help="Set counterparty IBAN"),
    counterparty: Optional[str] = typer.Option(None, "--counterparty", "-n", help="Set counterparty name"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Set category"),
    note: Optional[str] = typer.Option(None, "--note", "-N", help=f"Set note (max {NOTE_MAX_LEN} chars; empty string to clear)"),
) -> None:
    """Update identity or category on an existing expense.

    Examples:\n
      expense edit 42 --counterparty "albert heijn"\n
      expense edit 42 --category groceries\n
      expense edit 42 --iban NL91ABNA0417164300 --counterparty "albert heijn"\n
      expense edit 218,219,220 --category food/lunch\n
      expense edit 42 --note "reimbursed by work"
    """
    try:
        ids = [int(x.strip()) for x in expense_id.split(",")]
    except ValueError:
        typer.echo("IDs must be integers (comma-separated for multi-edit).", err=True)
        raise typer.Exit(1)

    fields = {}
    if iban is not None:
        fields["iban"] = iban
    if counterparty is not None:
        fields["counterparty"] = counterparty
    if category is not None:
        fields["category"] = category
    if note is not None:
        fields["note"] = note[:NOTE_MAX_LEN]

    if not fields:
        typer.echo("Provide at least one of --iban, --counterparty, --category, --note.", err=True)
        raise typer.Exit(1)

    not_found = [eid for eid in ids if not update_expense(eid, fields)]
    if not_found:
        typer.echo(f"No expense(s) with ID: {', '.join(str(x) for x in not_found)}.", err=True)
        if len(not_found) == len(ids):
            raise typer.Exit(1)

    updated_rows = {int(e["id"]): e for e in read_expenses() if int(e["id"]) in ids}
    for eid in ids:
        if eid not in updated_rows:
            continue
        updated = updated_rows[eid]
        console.print(f"Updated expense #{eid}:")
        for key in ("date", "amount", "description", "iban", "counterparty", "category", "note"):
            value = updated[key]
            if key in fields:
                console.print(f"  {key}: [green]{value}[/green]", highlight=False)
            else:
                console.print(f"  {key}: [dim]{value}[/dim]", highlight=False)


@app.command()
def delete(
    expense_id: Optional[int] = typer.Argument(None, help="ID of the expense to delete"),
    all_: bool = typer.Option(False, "--all", "-a", help="Delete all expenses (requires confirmation)"),
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


def _parse_part_spec(spec: str, parent_abs: float) -> tuple[str, float | None]:
    """Parse a --part spec string into (category, amount | None).

    Formats accepted:
        "food/groceries:45.00"   → ("food/groceries", 45.00)
        "food/groceries:50%"     → ("food/groceries", 25.00)  if parent_abs=50
        "food/groceries"         → ("food/groceries", None)   remainder
    """
    if ":" in spec:
        idx = spec.rfind(":")
        category = spec[:idx].strip()
        raw = spec[idx + 1:].strip()
        if not category:
            raise ValueError(f"Empty category in part spec: {spec!r}")
        if raw.endswith("%"):
            pct = float(raw[:-1])
            if not (0 < pct <= 100):
                raise ValueError(f"Percentage must be between 0 and 100, got {pct}")
            return category, round(pct / 100.0 * parent_abs, 2)
        else:
            amount = float(raw)
            if amount <= 0:
                raise ValueError(f"Part amount must be > 0, got {amount}")
            return category, amount
    else:
        category = spec.strip()
        if not category:
            raise ValueError("Empty category in part spec")
        return category, None


@app.command()
def split(
    expense_id: int = typer.Argument(..., help="ID of the expense to split"),
    part: Optional[list[str]] = typer.Option(
        None, "--part", "-p",
        help='"category[:amount_or_pct]" — repeatable. Omit amount on last part for remainder.',
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Split one expense into multiple sub-expenses with different categories.

    The parent expense is kept unchanged (preserving deduplication).
    Children inherit date, counterparty and IBAN from the parent.
    The parent is automatically excluded from insights since the children cover it.

    Examples:\n
      expense split 42\n
      expense split 42 --part food/groceries:45.00 --part transport:50% --part overig\n
      expense split 42 --part food:60 --part transport --yes
    """
    from expense_cli.categorizer import load_rules, categorize as _categorize

    all_expenses = read_expenses()
    parent = next((e for e in all_expenses if int(e["id"]) == expense_id), None)
    if parent is None:
        typer.echo(f"No expense with ID {expense_id}.", err=True)
        raise typer.Exit(1)

    # Guard: already a parent?
    existing_split_parents = {e["split_id"] for e in all_expenses if e.get("split_id")}
    if str(expense_id) in existing_split_parents:
        typer.echo(f"Expense #{expense_id} is already split. Delete the existing children first.", err=True)
        raise typer.Exit(1)

    # Guard: already a child?
    if parent.get("split_id"):
        typer.echo(
            f"Expense #{expense_id} is itself a child (split from #{parent['split_id']}). "
            "Split the original parent instead.",
            err=True,
        )
        raise typer.Exit(1)

    parent_abs = abs(float(parent["amount"]))
    sign = -1.0 if float(parent["amount"]) < 0 else 1.0

    parent_cp = parent.get("counterparty", "")

    # ---------- Non-interactive path ----------
    if part:
        raw_parts: list[tuple[str, float | None]] = []
        try:
            for spec in part:
                raw_parts.append(_parse_part_spec(spec, parent_abs))
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)

        remainder_count = sum(1 for _, a in raw_parts if a is None)
        if remainder_count > 1:
            typer.echo("Only one remainder part (no amount) is allowed.", err=True)
            raise typer.Exit(1)

        explicit_sum = sum(a for _, a in raw_parts if a is not None)
        if explicit_sum > parent_abs + 0.01:
            typer.echo(
                f"Parts sum to {explicit_sum:.2f} which exceeds the parent amount of {parent_abs:.2f}.",
                err=True,
            )
            raise typer.Exit(1)

        if remainder_count == 0:
            if abs(explicit_sum - parent_abs) > 0.01:
                typer.echo(
                    f"Parts sum to {explicit_sum:.2f} but parent is {parent_abs:.2f}. "
                    "Add a remainder part (no amount) or adjust amounts.",
                    err=True,
                )
                raise typer.Exit(1)
        else:
            remainder_amt = round(parent_abs - explicit_sum, 2)
            if remainder_amt <= 0:
                typer.echo(
                    f"Remainder would be {remainder_amt:.2f} (≤ 0). Adjust your explicit amounts.",
                    err=True,
                )
                raise typer.Exit(1)
            raw_parts = [(cat, remainder_amt if amt is None else amt) for cat, amt in raw_parts]

        # Non-interactive: inherit parent counterparty for all parts
        parts: list[tuple[str, str, float]] = [(cat, parent_cp, amt) for cat, amt in raw_parts]  # type: ignore[assignment]

    # ---------- Interactive path ----------
    else:
        from expense_cli.categorizer import load_rules as _load_rules
        from expense_cli.identifier import load_counterparties as _load_counterparties
        from collections import Counter as _Counter
        rules = _load_rules()
        known_categories: list[str] = []
        for e in all_expenses:
            if e.get("category") and e["category"] not in known_categories:
                known_categories.append(e["category"])
        for rule in rules:
            cat = rule.get("category", "")
            if cat and cat not in known_categories:
                known_categories.append(cat)
        known_categories.sort(key=lambda c: (_category_parent(c), c))

        cp_counts = _Counter(e["counterparty"] for e in all_expenses if e.get("counterparty"))
        known_counterparties: list[str] = [name for name, _ in cp_counts.most_common()]
        for cp in _load_counterparties():
            if cp["name"] not in known_counterparties:
                known_counterparties.append(cp["name"])

        console.print("")
        console.print(f"  [dim]id[/dim]            {parent['id']}")
        console.print(f"  [dim]date[/dim]           {parent['date']}  [dim]({parent.get('weekday', '')})[/dim]")
        amt_color = "red" if float(parent["amount"]) < 0 else "green"
        console.print(f"  [dim]amount[/dim]         [{amt_color}]{parent['amount']}[/{amt_color}]")
        if parent_cp:
            console.print(f"  [dim]counterparty[/dim]  {parent_cp}")
        console.print(f"  [dim]category[/dim]       {parent['category'] or '-'}")
        console.print(f"  [dim]description[/dim]    {parent['description']}")
        console.print(f"\n  Split into parts [dim](total: {parent_abs:.2f})[/dim]\n")

        parts = []
        allocated = 0.0
        part_num = 1
        while True:
            remaining = round(parent_abs - allocated, 2)
            if remaining <= 0:
                break
            console.print(f"[bold]--- Part {part_num} ---[/bold]")
            cat_result = _pick("Category", known_categories, color="\033[33m")
            if cat_result is None or cat_result == "" or cat_result is _SKIP:
                if not parts:
                    typer.echo("No parts entered. Aborting split.", err=True)
                    raise typer.Exit(1)
                break
            if cat_result is _BACK:
                if parts:
                    parts.pop()
                    allocated = round(sum(a for _, _, a in parts), 2)
                    part_num -= 1
                    console.print("  [dim]removed last part[/dim]")
                continue
            category = str(cat_result).lower()

            cp_result = _pick("Counterparty", known_counterparties, color="\033[36m", initial=parent_cp)
            if cp_result is None:
                if parts:
                    parts.append((category, parent_cp, round(remaining, 2)))
                    allocated = parent_abs
                break
            if cp_result is _BACK:
                continue
            if cp_result is _SKIP:
                counterparty = parent_cp
            else:
                counterparty = str(cp_result).lower() if cp_result else parent_cp
                if counterparty and counterparty not in known_counterparties:
                    known_counterparties.append(counterparty)

            amt_result = _input_prefilled(
                "Amount",
                "",
                color="\033[36m",
            )
            # hint about remainder
            console.print(f"  [dim](Enter for remainder: {remaining:.2f})[/dim]")

            if amt_result is None:
                if parts:
                    parts.append((category, counterparty, round(remaining, 2)))
                    allocated = parent_abs
                    part_num += 1
                break
            if amt_result is _BACK:
                if parts:
                    parts.pop()
                    allocated = round(sum(a for _, _, a in parts), 2)
                    part_num -= 1
                continue
            if amt_result is _SKIP:
                if not parts:
                    typer.echo("No parts entered. Aborting split.", err=True)
                    raise typer.Exit(1)
                break

            raw_amt = str(amt_result).strip()
            if raw_amt == "":
                # Empty = remainder
                parts.append((category, counterparty, round(remaining, 2)))
                allocated = parent_abs
                part_num += 1
                break
            try:
                if raw_amt.endswith("%"):
                    pct = float(raw_amt[:-1])
                    if not (0 < pct <= 100):
                        console.print(f"  [red]Percentage must be 0–100, got {pct}[/red]")
                        continue
                    amt = round(pct / 100.0 * parent_abs, 2)
                else:
                    amt = round(float(raw_amt), 2)
                if amt <= 0:
                    console.print("  [red]Amount must be > 0[/red]")
                    continue
                if amt > remaining + 0.01:
                    console.print(f"  [red]{amt:.2f} exceeds remaining {remaining:.2f}[/red]")
                    continue
            except ValueError:
                console.print(f"  [red]Invalid amount: {raw_amt!r}[/red]")
                continue

            parts.append((category, counterparty, amt))
            allocated = round(allocated + amt, 2)
            console.print(f"  [dim]Allocated: {allocated:.2f} / {parent_abs:.2f}[/dim]")
            part_num += 1

        if len(parts) < 2:
            typer.echo("Need at least 2 parts to split. Aborting.", err=True)
            raise typer.Exit(1)

        # Fill any gap as remainder into the last part if close enough
        total_parts = round(sum(a for _, _, a in parts), 2)
        if abs(total_parts - parent_abs) > 0.01:
            typer.echo(
                f"Parts total {total_parts:.2f} but parent is {parent_abs:.2f}. Aborting.",
                err=True,
            )
            raise typer.Exit(1)

    # ---------- Preview ----------
    console.print("")
    preview = Table(show_header=True, box=None, padding=(0, 2, 0, 0))
    preview.add_column("ID", style="dim")
    preview.add_column("Category")
    preview.add_column("Counterparty", style="dim")
    preview.add_column("Amount", justify="right")
    preview.add_column("Note", style="dim")
    parent_color = "red" if float(parent["amount"]) < 0 else "green"
    preview.add_row(
        f"#{parent['id']}",
        f"[dim]{parent['category'] or '-'}[/dim]",
        f"[dim]{parent_cp or '-'}[/dim]",
        f"[dim][{parent_color}]{parent['amount']}[/{parent_color}][/dim]",
        "[dim](parent — excluded from insights)[/dim]",
    )
    for cat, cp, amt in parts:
        child_amt = sign * amt
        child_color = "red" if child_amt < 0 else "green"
        preview.add_row(
            f"  [dim]↳[/dim]",
            cat,
            cp or "-",
            f"[{child_color}]{child_amt:.2f}[/{child_color}]",
            "",
        )
    console.print(preview)
    total_parts = sum(a for _, _, a in parts)
    console.print(f"\n  Total: [bold]{total_parts:.2f}[/bold] / {parent_abs:.2f}")

    if not yes:
        typer.confirm("\nConfirm split?", abort=True)

    # ---------- Write children ----------
    start_id = next_id(all_expenses)
    children = []
    for offset, (cat, cp, amt) in enumerate(parts):
        child_amount = sign * amt
        children.append({
            "id": start_id + offset,
            "date": parent["date"],
            "weekday": parent.get("weekday", ""),
            "time": parent.get("time", ""),
            "amount": f"{child_amount:.2f}",
            "direction": "out" if child_amount < 0 else "in",
            "description": parent["description"],
            "iban": parent.get("iban", ""),
            "counterparty": cp,
            "category": cat,
            "note": f"split from #{expense_id}",
            "source_hash": "",
            "split_id": str(expense_id),
        })
    write_expenses_batch(children)
    console.print(f"  [green]✓ {len(children)} sub-expenses created[/green]")


@app.command()
def insights(
    by: str = typer.Option("category", "--by", "-b", help="Group by: category or counterparty"),
    from_date: Optional[str] = typer.Option(None, "--from", "-f", help="Start date (YYYY-MM-DD, inclusive)"),
    to_date: Optional[str] = typer.Option(None, "--to", "-t", help="End date (YYYY-MM-DD, inclusive)"),
    month: Optional[str] = typer.Option(None, "--month", "-m", help="Filter by month: YYYY-MM or 1-12"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Year to use with --month number (default: current year)"),
    direction: Optional[str] = typer.Option(None, "--direction", "-d", help="Filter rows: 'in' or 'out' (default: show all)"),
    category: Optional[str] = typer.Option(None, "--category", help="Filter to a specific category or parent (prefix match: 'food' matches 'food/groceries')"),
    exclude: Optional[str] = typer.Option(None, "--exclude", "-w", help="Exclude values for the active --by dimension (comma-separated)"),
    exclude_category: Optional[str] = typer.Option(None, "--exclude-category", "-C", help="Exclude categories (comma-separated, prefix match)"),
    exclude_counterparty: Optional[str] = typer.Option(None, "--exclude-counterparty", "-P", help="Exclude counterparties (comma-separated)"),
    include: Optional[str] = typer.Option(None, "--include", help="Show only these values for the active --by dimension (comma-separated)"),
    include_category: Optional[str] = typer.Option(None, "--include-category", help="Show only these categories (comma-separated, prefix match)"),
    include_counterparty: Optional[str] = typer.Option(None, "--include-counterparty", help="Show only these counterparties (comma-separated)"),
    chart: bool = typer.Option(False, "--chart", "-c", help="Show a bar chart below the table"),
    trend: bool = typer.Option(False, "--trend", "-T", help="Show monthly pivot table (months × groups)"),
    months: Optional[int] = typer.Option(None, "--months", "-M", help="Number of recent months for --trend (default: 6)"),
    rollup: bool = typer.Option(False, "--rollup", "-R", help="Roll up subcategories to their parent (e.g. food/groceries → food)"),
    detail: bool = typer.Option(True, "--detail/--no-detail", help="Show subcategory breakdown under each parent (default: on)"),
    min_amount: Optional[float] = typer.Option(None, "--min", "-l", help="Hide groups with abs(net) below this value"),
    max_amount: Optional[float] = typer.Option(None, "--max", "-x", help="Hide groups with abs(net) above this value"),
) -> None:
    """Summarize transactions grouped by category or counterparty with a bar chart.

    Shows Out, In, and Net columns so you can see the net effect per group
    (e.g. gifts: what you spent minus what you received from friends).
    A Trend sparkline column is shown automatically when data spans multiple months.
    Use --trend for a full monthly pivot table (months × groups).

    Examples:\n
      expense insights\n
      expense insights --direction out\n
      expense insights --by counterparty\n
      expense insights --month 2026-03\n
      expense insights --by category --from 2026-01-01 --to 2026-03-31\n
      expense insights --exclude food,transport\n
      expense insights --by counterparty --exclude Albert,Shell\n
      expense insights --include food,health\n
      expense insights --by counterparty --include Albert,Shell\n
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
    if category:
        expenses = [e for e in expenses if _category_matches(e["category"], category)]
    if exclude:
        excl_list = [c.strip() for c in exclude.split(",")]
        if by == "category":
            expenses = [e for e in expenses if not any(_category_matches(e["category"], ex) for ex in excl_list)]
        else:
            excl_set = {c.lower() for c in excl_list}
            expenses = [e for e in expenses if e.get("counterparty", "").lower() not in excl_set]
    if exclude_category:
        excl_list = [c.strip() for c in exclude_category.split(",")]
        expenses = [e for e in expenses if not any(_category_matches(e["category"], ex) for ex in excl_list)]
    if exclude_counterparty:
        excluded = {c.strip().lower() for c in exclude_counterparty.split(",")}
        expenses = [e for e in expenses if e.get("counterparty", "").lower() not in excluded]
    if include:
        incl_list = [c.strip() for c in include.split(",")]
        if by == "category":
            expenses = [e for e in expenses if any(_category_matches(e["category"], inc) for inc in incl_list)]
        else:
            incl_set = {c.lower() for c in incl_list}
            expenses = [e for e in expenses if e.get("counterparty", "").lower() in incl_set]
    if include_category:
        incl_list = [c.strip() for c in include_category.split(",")]
        expenses = [e for e in expenses if any(_category_matches(e["category"], inc) for inc in incl_list)]
    if include_counterparty:
        incl_set = {c.strip().lower() for c in include_counterparty.split(",")}
        expenses = [e for e in expenses if e.get("counterparty", "").lower() in incl_set]

    # Exclude split parents — their total is covered by their children
    _split_parent_ids = {e["split_id"] for e in expenses if e.get("split_id")}
    expenses = [e for e in expenses if str(e["id"]) not in _split_parent_ids]

    if not expenses:
        label = "expenses" if direction == "out" else "income" if direction == "in" else "transactions"
        typer.echo(f"No {label} found.")
        return

    # --- Monthly bucketing (shared by sparkline and --trend) ---
    monthly_out: dict[str, dict[str, float]] = {}
    monthly_in: dict[str, dict[str, float]] = {}
    for e in expenses:
        mo = e["date"][:7]
        raw_key = e.get(by) or "(none)"
        key = _category_parent(raw_key) if rollup and by == "category" else raw_key
        amount = float(e["amount"])
        if e.get("direction") == "out":
            if mo not in monthly_out:
                monthly_out[mo] = {}
            monthly_out[mo][key] = monthly_out[mo].get(key, 0.0) + amount
        else:
            if mo not in monthly_in:
                monthly_in[mo] = {}
            monthly_in[mo][key] = monthly_in[mo].get(key, 0.0) + amount

    # Compute net per month/group (out + in, where in amounts are negative)
    all_month_keys = sorted(set(monthly_out) | set(monthly_in))
    monthly_net: dict[str, dict[str, float]] = {}
    for mo in all_month_keys:
        groups = set((monthly_out.get(mo) or {}).keys()) | set((monthly_in.get(mo) or {}).keys())
        monthly_net[mo] = {
            g: monthly_out.get(mo, {}).get(g, 0.0) + monthly_in.get(mo, {}).get(g, 0.0)
            for g in groups
        }

    # Primary monthly dict: net by default, filtered by direction when specified
    if direction == "in":
        monthly_primary = monthly_in
    elif direction == "out":
        monthly_primary = monthly_out
    else:
        monthly_primary = monthly_net
    all_months = all_month_keys

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

        all_groups = {(e.get(by) or "(none)") for e in expenses}
        all_groups_keyed = {
            (_category_parent(g) if rollup and by == "category" else g)
            for g in all_groups
        }

        def _group_total(keys: list[str]) -> float:
            return abs(sum(monthly_primary.get(mo, {}).get(k, 0.0) for mo in show_months for k in keys))

        sorted_groups = sorted(all_groups_keyed, key=lambda g: abs(sum(monthly_primary.get(mo, {}).get(g, 0.0) for mo in show_months)), reverse=True)

        if min_amount is not None:
            sorted_groups = [g for g in sorted_groups if abs(sum(monthly_primary.get(mo, {}).get(g, 0.0) for mo in show_months)) >= min_amount]
        if max_amount is not None:
            sorted_groups = [g for g in sorted_groups if abs(sum(monthly_primary.get(mo, {}).get(g, 0.0) for mo in show_months)) <= max_amount]

        # Build hierarchical groups (same logic as regular table)
        if by == "category":
            pivot_parent_groups: dict[str, list[str]] = {}
            for g in sorted_groups:
                parent = g.split("/", 1)[0]
                pivot_parent_groups.setdefault(parent, []).append(g)
            ordered_pivot_groups: list[tuple[str, list[str]]] = sorted(
                pivot_parent_groups.items(),
                key=lambda item: _group_total(item[1]),
                reverse=True,
            )
        else:
            ordered_pivot_groups = [(g, [g]) for g in sorted_groups]

        col_totals = {mo: sum(monthly_primary.get(mo, {}).get(g, 0.0) for g in sorted_groups) for mo in show_months}
        grand_total = sum(col_totals.values())
        avg_total = grand_total / len(show_months) if show_months else 0.0

        def _pivot_color(v: float, bold: bool, dim: bool) -> str:
            if not v:
                return "[dim]—[/dim]"
            color = "red" if v < 0 else "green"
            style = f"bold {color}" if bold else (f"dim {color}" if dim else color)
            return f"[{style}]{v:.2f}[/{style}]"

        pivot = Table(show_lines=False, box=None, padding=(0, 1), show_footer=True)
        pivot.add_column(by.capitalize(), min_width=16, footer="[dim]Total[/dim]")
        for mo in show_months:
            pivot.add_column(mo, justify="right", footer=_pivot_color(col_totals[mo], bold=False, dim=True))
        pivot.add_column("Avg/mo", justify="right", footer=_pivot_color(avg_total, bold=False, dim=True))
        pivot.add_column("%", justify="right", footer="[dim]100%[/dim]")

        def _pivot_row(label: str, keys: list[str], bold: bool, dim_label: bool) -> None:
            row_vals = [sum(monthly_primary.get(mo, {}).get(k, 0.0) for k in keys) for mo in show_months]
            avg = sum(row_vals) / len(show_months)
            row_total = sum(row_vals)
            pct = abs(row_total) / abs(grand_total) * 100 if grand_total else 0.0
            pct_str = f"[dim]{pct:.1f}%[/dim]" if dim_label else (f"[bold]{pct:.1f}%[/bold]" if bold else f"{pct:.1f}%")
            lbl_prefix = "[bold]" if bold else ("[dim]" if dim_label else "")
            lbl_suffix = "[/bold]" if bold else ("[/dim]" if dim_label else "")
            pivot.add_row(
                f"{lbl_prefix}{label}{lbl_suffix}",
                *[_pivot_color(v, bold=bold, dim=dim_label) for v in row_vals],
                _pivot_color(avg, bold=bold, dim=dim_label),
                pct_str,
            )

        for parent, children in ordered_pivot_groups:
            has_subs = len(children) > 1 or (len(children) == 1 and "/" in children[0])
            if has_subs:
                _pivot_row(parent, children, bold=detail, dim_label=False)
                if detail:
                    for child in sorted(children, key=lambda c: _group_total([c]), reverse=True):
                        sub_label = "  " + (child.split("/", 1)[1] if "/" in child else child)
                        _pivot_row(sub_label, [child], bold=False, dim_label=True)
            else:
                _pivot_row(children[0], children, bold=False, dim_label=False)

        console.print(pivot)
        return

    # --- Regular grouped summary ---
    out_totals: dict[str, float] = {}
    in_totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for e in expenses:
        raw_key = e.get(by) or "(none)"
        key = _category_parent(raw_key) if rollup and by == "category" else raw_key
        amount = float(e["amount"])
        counts[key] = counts.get(key, 0) + 1
        if e.get("direction") == "out":
            out_totals[key] = out_totals.get(key, 0.0) + amount
        else:
            in_totals[key] = in_totals.get(key, 0.0) + amount

    all_keys = set(out_totals) | set(in_totals)
    nets = {k: out_totals.get(k, 0.0) + in_totals.get(k, 0.0) for k in all_keys}
    sorted_keys = sorted(all_keys, key=lambda k: abs(nets[k]), reverse=True)

    if min_amount is not None:
        sorted_keys = [k for k in sorted_keys if abs(nets[k]) >= min_amount]
    if max_amount is not None:
        sorted_keys = [k for k in sorted_keys if abs(nets[k]) <= max_amount]

    total_out = sum(out_totals.get(k, 0.0) for k in sorted_keys)
    total_in = sum(in_totals.get(k, 0.0) for k in sorted_keys)
    total_net = total_out + total_in
    total_count = sum(counts[k] for k in sorted_keys)

    # Sparkline — auto-shown when data spans >= 2 months
    sparkline_months = all_months[-(months or 6):]
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

    # Build hierarchical groups for category display (parent → [children])
    # When by=counterparty or rollup already collapsed keys, all groups are flat (one child each).
    if by == "category":
        parent_groups: dict[str, list[str]] = {}
        for k in sorted_keys:
            parent = k.split("/", 1)[0]
            parent_groups.setdefault(parent, []).append(k)
        ordered_groups: list[tuple[str, list[str]]] = sorted(
            parent_groups.items(),
            key=lambda item: abs(sum(nets.get(c, 0.0) for c in item[1])),
            reverse=True,
        )
    else:
        ordered_groups = [(k, [k]) for k in sorted_keys]

    total_abs_net = sum(abs(nets.get(k, 0.0)) for k in sorted_keys)

    def _add_row(label: str, out_val: float, in_val: float, net_val: float,
                 count: int, has_out: bool, has_in: bool, bold: bool, dim_label: bool,
                 spark_keys: list[str]) -> None:
        pct = abs(net_val) / total_abs_net * 100 if total_abs_net else 0.0
        prefix = "[bold]" if bold else ("[dim]" if dim_label else "")
        suffix = "[/bold]" if bold else ("[/dim]" if dim_label else "")
        row: list[str] = [
            f"{prefix}{label}{suffix}",
            f"{prefix}{out_val:.2f}{suffix}" if has_out else "",
            f"{prefix}{in_val:.2f}{suffix}" if has_in else "",
            f"{prefix}{net_val:.2f}{suffix}",
            f"{pct:.1f}%",
            f"{prefix}{count}{suffix}",
        ]
        if show_sparkline:
            spark_vals = [sum(monthly_primary.get(mo, {}).get(k, 0.0) for k in spark_keys) for mo in sparkline_months]
            row.append(_sparkline(spark_vals))
        table.add_row(*row)

    for parent, children in ordered_groups:
        has_subs = len(children) > 1 or (len(children) == 1 and "/" in children[0])

        if has_subs:
            # Parent summary row
            p_out = sum(out_totals.get(c, 0.0) for c in children)
            p_in = sum(in_totals.get(c, 0.0) for c in children)
            p_net = p_out + p_in
            p_count = sum(counts.get(c, 0) for c in children)
            _add_row(parent, p_out, p_in, p_net, p_count,
                     bool(p_out), bool(p_in), bold=detail, dim_label=False, spark_keys=children)

            # Indented child rows (only when --detail, sorted by abs net descending)
            if detail:
                for child in sorted(children, key=lambda c: abs(nets.get(c, 0.0)), reverse=True):
                    sub_label = "  " + (child.split("/", 1)[1] if "/" in child else child)
                    _add_row(sub_label,
                             out_totals.get(child, 0.0), in_totals.get(child, 0.0), nets.get(child, 0.0),
                             counts.get(child, 0),
                             child in out_totals, child in in_totals,
                             bold=False, dim_label=True, spark_keys=[child])
        else:
            key = children[0]
            _add_row(key,
                     out_totals.get(key, 0.0), in_totals.get(key, 0.0), nets.get(key, 0.0),
                     counts.get(key, 0),
                     key in out_totals, key in in_totals,
                     bold=False, dim_label=False, spark_keys=[key])

    if chart:
        # Chart uses parent-level totals so grouped subcategories show as one bar
        chart_data = [
            (parent, sum(nets.get(c, 0.0) for c in children))
            for parent, children in ordered_groups
        ]
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
def ratios(
    numerator: list[str] = typer.Option(..., "--numerator", "-N", help="Category for the top of the fraction (can repeat to sum multiple)"),
    denominator: list[str] = typer.Option(..., "--denominator", "-D", help="Category for the bottom of the fraction (can repeat to sum multiple)"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Display label for this ratio (default: auto-generated)"),
    from_date: Optional[str] = typer.Option(None, "--from", "-f", help="Start date YYYY-MM-DD"),
    to_date: Optional[str] = typer.Option(None, "--to", "-t", help="End date YYYY-MM-DD"),
    months: int = typer.Option(6, "--months", "-M", help="Number of recent months to show (default: 6)"),
    exclude: list[str] = typer.Option([], "--exclude", "-e", help="Category to exclude from both sides (can repeat)"),
) -> None:
    """Show a monthly trend table for a ratio between two category groups.

    Computes abs(sum of numerator) / abs(sum of denominator) per month.
    Both sides use prefix category matching ('investeren' matches 'investeren/etf').
    Use --exclude to drop specific subcategories from both sides before computing.
    Run repeatedly with different --numerator/--denominator to compare multiple ratios.

    Examples:\n
      expense ratios --numerator investeren --denominator salaris\n
      expense ratios --numerator investeren --denominator salaris --exclude investeren/donation\n
      expense ratios --numerator investeren --numerator pensioen --denominator salaris --months 12\n
      expense ratios --numerator food --denominator salaris --label food_rate --from 2025-01-01
    """
    expenses = read_expenses()
    if from_date:
        expenses = [e for e in expenses if e["date"] >= from_date]
    if to_date:
        expenses = [e for e in expenses if e["date"] <= to_date]

    # Bucket expenses by YYYY-MM
    monthly_buckets: dict[str, list[dict]] = {}
    for e in expenses:
        mo = e["date"][:7]
        monthly_buckets.setdefault(mo, []).append(e)

    # Resolve month range
    if from_date or to_date:
        all_months = sorted(monthly_buckets.keys())
        if from_date and to_date:
            start_mo, end_mo = from_date[:7], to_date[:7]
        elif from_date:
            start_mo = from_date[:7]
            end_mo = all_months[-1] if all_months else start_mo
        else:
            start_mo = all_months[0] if all_months else to_date[:7]  # type: ignore[index]
            end_mo = to_date[:7]  # type: ignore[index]
        show_months = _month_range(start_mo, end_mo)
    else:
        today = date.today()
        end_mo = f"{today.year:04d}-{today.month:02d}"
        y, m = today.year, today.month
        for _ in range(months - 1):
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        start_mo = f"{y:04d}-{m:02d}"
        show_months = _month_range(start_mo, end_mo)

    row_label = label or f"{' + '.join(numerator)} / {' + '.join(denominator)}"

    # Compute ratio per month
    values: list[float | None] = [
        compute_ratio(monthly_buckets.get(mo, []), numerator, denominator, exclude=exclude)
        for mo in show_months
    ]

    table = Table(show_lines=False, box=None, padding=(0, 1), show_footer=False)
    table.add_column("Ratio", min_width=24)
    for mo in show_months:
        table.add_column(mo, justify="right")
    if len(show_months) >= 2:
        table.add_column("Trend", style="dim", no_wrap=True)

    def _fmt_ratio(v: float | None) -> str:
        if v is None:
            return "[dim]—[/dim]"
        pct = v * 100
        color = "yellow" if pct >= 100 else "green"
        return f"[{color}]{pct:.1f}%[/{color}]"

    row: list[str] = [row_label] + [_fmt_ratio(v) for v in values]
    if len(show_months) >= 2:
        sparkline_vals = [v for v in values if v is not None]
        row.append(_sparkline(sparkline_vals))

    table.add_row(*row)
    console.print(table)


@app.command()
def version() -> None:
    """Print the current CLI version."""
    typer.echo(__version__)


def main() -> None:
    app()
