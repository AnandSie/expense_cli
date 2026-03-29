import csv
from datetime import datetime
from pathlib import Path

DATA_DIR = Path.home() / ".expense_cli"
CSV_PATH = DATA_DIR / "expenses.csv"

# TODO: create a type/datatype/record which we can read in, instead of hardcode strings 
FIELDNAMES = ["id", "date", "weekday", "time", "amount", "direction", "description", "iban", "counterparty", "category", "source_hash"]

# TODO: can we maybe create a seperate csv storage next to normal storage for decoupling, next to this generic core stoarge

def _weekday_from_date(date_str: str) -> str:
    # TODO: this strptime 
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")


def _migrate() -> None:
    """Add missing columns and normalise legacy sentinel values in expenses.csv."""
    if not CSV_PATH.exists():
        return
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_fields = reader.fieldnames or []
        rows = list(reader)

    needs_column_migration = not set(FIELDNAMES).issubset(set(existing_fields))
    needs_sentinel_migration = any(r.get("category") == "uncategorized" for r in rows)
    needs_name_rename = "name" in existing_fields and "counterparty" not in existing_fields

    if not needs_column_migration and not needs_sentinel_migration and not needs_name_rename:
        return

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            if "name" in row and "counterparty" not in row:
                row["counterparty"] = row.pop("name")
            full = {field: row.get(field, "") for field in FIELDNAMES}
            if full["category"] == "uncategorized":
                full["category"] = ""
            if not full["weekday"] and full["date"]:
                full["weekday"] = _weekday_from_date(full["date"])
            if not full["direction"] and full["amount"]:
                try:
                    full["direction"] = "out" if float(full["amount"]) < 0 else "in"
                except ValueError:
                    full["direction"] = "out"
            writer.writerow(full)


def read_expenses() -> list[dict]:
    _migrate()
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_expense(row: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _migrate()
    is_new = not CSV_PATH.exists()
    full_row = {field: row.get(field, "") for field in FIELDNAMES}
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerow(full_row)


def write_expenses_batch(rows: list[dict]) -> None:
    """Append multiple rows at once (used by importer)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _migrate()
    is_new = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})


def next_id(expenses: list[dict]) -> int:
    if not expenses:
        return 1
    return max(int(e["id"]) for e in expenses) + 1


def delete_expense(expense_id: int) -> bool:
    """Remove an expense by ID. Returns True if found and deleted."""
    expenses = read_expenses()
    filtered = [e for e in expenses if int(e["id"]) != expense_id]
    if len(filtered) == len(expenses):
        return False
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(filtered)
    return True


def reset_expenses() -> int:
    """Delete the expenses CSV. Returns the number of records that were deleted."""
    if not CSV_PATH.exists():
        return 0
    count = len(read_expenses())
    CSV_PATH.unlink()
    return count


def update_expense(expense_id: int, fields: dict) -> bool:
    """Update fields on an existing expense by ID. Returns True if found."""
    expenses = read_expenses()
    updated = False
    for row in expenses:
        if int(row["id"]) == expense_id:
            row.update({k: v for k, v in fields.items() if k in FIELDNAMES})
            updated = True
            break
    if not updated:
        return False
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(expenses)
    return True
