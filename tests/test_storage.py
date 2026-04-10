import csv
import pytest
import expense_cli.storage as storage
from expense_cli.storage import (
    read_expenses, write_expense, write_expenses_batch,
    next_id, update_expense, FIELDNAMES,
)


def make_row(id=1, date="2026-01-01", amount="10.00", description="test",
             category="", iban="", counterparty=""):
    return {"id": id, "date": date, "amount": amount, "description": description,
            "category": category, "iban": iban, "counterparty": counterparty}


def test_next_id_empty():
    assert next_id([]) == 1


def test_next_id_takes_max():
    assert next_id([{"id": "1"}, {"id": "5"}, {"id": "3"}]) == 6


def test_read_returns_empty_when_no_file(tmp_storage):
    assert read_expenses() == []


def test_write_and_read_single(tmp_storage):
    write_expense(make_row())
    expenses = read_expenses()
    assert len(expenses) == 1
    assert expenses[0]["amount"] == "10.00"
    assert expenses[0]["description"] == "test"


def test_write_batch(tmp_storage):
    rows = [make_row(id=1, description="a"), make_row(id=2, description="b")]
    write_expenses_batch(rows)
    expenses = read_expenses()
    assert len(expenses) == 2
    assert {e["description"] for e in expenses} == {"a", "b"}


def test_update_expense_single_field(tmp_storage):
    write_expense(make_row())
    assert update_expense(1, {"category": "food"}) is True
    assert read_expenses()[0]["category"] == "food"


def test_update_expense_multiple_fields(tmp_storage):
    write_expense(make_row())
    update_expense(1, {"category": "food", "counterparty": "Albert Heijn"})
    row = read_expenses()[0]
    assert row["category"] == "food"
    assert row["counterparty"] == "Albert Heijn"


def test_update_expense_not_found(tmp_storage):
    write_expense(make_row())
    assert update_expense(99, {"category": "food"}) is False


def test_update_ignores_unknown_fields(tmp_storage):
    write_expense(make_row())
    update_expense(1, {"category": "food", "nonexistent": "value"})
    assert "nonexistent" not in read_expenses()[0]


def test_weekday_and_time_stored_and_read_back(tmp_storage):
    write_expense({**make_row(date="2026-01-05"), "weekday": "Monday", "time": "14:30:00"})
    e = read_expenses()[0]
    assert e["weekday"] == "Monday"
    assert e["time"] == "14:30:00"


def test_migration_adds_weekday_from_date(tmp_storage):
    with storage.CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "date", "amount", "description", "category", "iban", "counterparty"])
        writer.writeheader()
        writer.writerow({"id": "1", "date": "2026-01-05", "amount": "10.00",
                         "description": "test", "category": "", "iban": "", "counterparty": ""})
    e = read_expenses()[0]
    assert e["weekday"] == "Monday"
    assert e["time"] == ""


def test_migration_renames_name_to_counterparty(tmp_storage):
    old_fields = ["id", "date", "amount", "description", "category", "iban", "name"]
    with storage.CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({"id": "1", "date": "2026-01-01", "amount": "10.00",
                         "description": "test", "category": "", "iban": "", "name": "Shop A"})
    expenses = read_expenses()
    assert expenses[0]["counterparty"] == "Shop A"
    assert "name" not in expenses[0]


def test_migration_clears_uncategorized_sentinel(tmp_storage):
    with storage.CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerow({**make_row(), "category": "uncategorized"})
    assert read_expenses()[0]["category"] == ""


def test_migration_backfills_direction_positive(tmp_storage):
    old_fields = [f for f in FIELDNAMES if f != "direction"]
    with storage.CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({f: make_row().get(f, "") for f in old_fields})  # amount=10.00
    assert read_expenses()[0]["direction"] == "in"


def test_migration_backfills_direction_negative(tmp_storage):
    old_fields = [f for f in FIELDNAMES if f != "direction"]
    with storage.CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=old_fields)
        writer.writeheader()
        row = {f: make_row().get(f, "") for f in old_fields}
        row["amount"] = "-10.00"
        writer.writerow(row)
    assert read_expenses()[0]["direction"] == "out"


def test_migration_adds_split_id_column(tmp_storage):
    """CSV written without split_id gets empty string on all rows after migration."""
    old_fields = [f for f in FIELDNAMES if f != "split_id"]
    with storage.CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({f: make_row().get(f, "") for f in old_fields})
    rows = read_expenses()
    assert rows[0]["split_id"] == ""
