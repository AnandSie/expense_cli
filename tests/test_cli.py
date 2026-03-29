import csv
import pytest
from typer.testing import CliRunner
from expense_cli.cli import app, _validate_bank_config, _validate_categories_config, _validate_counterparties_config
from expense_cli.storage import read_expenses

runner = CliRunner()


# --- _validate_bank_config ---

def test_validate_bank_config_valid():
    config = {"mapping": {"date": "Date", "amount": "Amount"}}
    assert _validate_bank_config(config) == []


def test_validate_bank_config_missing_mapping():
    errors = _validate_bank_config({})
    assert any("mapping" in e for e in errors)


def test_validate_bank_config_missing_amount():
    errors = _validate_bank_config({"mapping": {"date": "Date"}})
    assert any("amount" in e for e in errors)


def test_validate_bank_config_missing_date():
    errors = _validate_bank_config({"mapping": {"amount": "Amount"}})
    assert any("date" in e for e in errors)


def test_validate_bank_config_legacy_name_key():
    config = {"mapping": {"date": "Date", "amount": "Amount", "name": "Name"}}
    errors = _validate_bank_config(config)
    assert any("counterparty" in e for e in errors)


def test_validate_bank_config_dict_field_valid():
    config = {"mapping": {"date": "Date", "amount": "Amount", "iban": {"column": "IBAN"}}}
    assert _validate_bank_config(config) == []


def test_validate_bank_config_dict_field_pattern_without_from_column():
    config = {"mapping": {"date": "Date", "amount": "Amount", "iban": {"pattern": r"\w+"}}}
    errors = _validate_bank_config(config)
    assert any("iban" in e for e in errors)


def test_validate_bank_config_unknown_bank_key():
    config = {"bank": {"unknown_key": "value"}, "mapping": {"date": "Date", "amount": "Amount"}}
    errors = _validate_bank_config(config)
    assert any("unknown_key" in e for e in errors)


# --- _validate_categories_config ---

def test_validate_categories_valid():
    config = {"rules": [{"counterparty": "Albert Heijn", "category": "groceries"}]}
    assert _validate_categories_config(config) == []


def test_validate_categories_empty_rules():
    assert _validate_categories_config({"rules": []}) == []


def test_validate_categories_missing_category():
    errors = _validate_categories_config({"rules": [{"counterparty": "Albert Heijn"}]})
    assert any("category" in e for e in errors)


def test_validate_categories_missing_counterparty():
    errors = _validate_categories_config({"rules": [{"category": "groceries"}]})
    assert any("counterparty" in e for e in errors)


def test_validate_categories_legacy_iban_key():
    config = {"rules": [{"iban": "DE89", "category": "rent"}]}
    errors = _validate_categories_config(config)
    assert any("iban" in e for e in errors)


def test_validate_categories_legacy_name_contains_key():
    config = {"rules": [{"name_contains": "rewe", "category": "groceries"}]}
    errors = _validate_categories_config(config)
    assert any("name_contains" in e for e in errors)


# --- _validate_counterparties_config ---

def test_validate_counterparties_valid_iban():
    config = {"counterparty": [{"iban": "NL91ABNA0417164300", "name": "Albert Heijn"}]}
    assert _validate_counterparties_config(config) == []


def test_validate_counterparties_valid_description():
    config = {"counterparty": [{"description_contains": "netflix", "name": "Netflix"}]}
    assert _validate_counterparties_config(config) == []


def test_validate_counterparties_empty():
    assert _validate_counterparties_config({"counterparty": []}) == []


def test_validate_counterparties_missing_name():
    errors = _validate_counterparties_config({"counterparty": [{"iban": "NL91ABNA0417164300"}]})
    assert any("name" in e for e in errors)


def test_validate_counterparties_missing_matcher():
    errors = _validate_counterparties_config({"counterparty": [{"name": "Albert Heijn"}]})
    assert any("iban" in e or "description_contains" in e for e in errors)


def test_validate_counterparties_both_matchers():
    config = {"counterparty": [{"iban": "NL91", "description_contains": "heijn", "name": "Albert Heijn"}]}
    errors = _validate_counterparties_config(config)
    assert any("both" in e for e in errors)


# --- CLI: version ---

def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


# --- CLI: config bootstrap ---

def test_config_counterparties_missing_offers_template(tmp_storage):
    result = runner.invoke(app, ["config", "counterparties"], input="n\n")
    assert result.exit_code == 0
    assert "[[counterparty]]" in result.output


def test_config_counterparties_missing_creates_file(tmp_storage):
    runner.invoke(app, ["config", "counterparties"], input="y\n")
    assert (tmp_storage / "counterparties.toml").exists()


def test_config_counterparties_existing_shows_content(tmp_storage):
    (tmp_storage / "counterparties.toml").write_text(
        '[[counterparty]]\niban = "NL91"\nname = "Test"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["config", "counterparties"])
    assert result.exit_code == 0
    assert "NL91" in result.output


def test_config_categories_missing_offers_template(tmp_storage):
    result = runner.invoke(app, ["config", "categories"], input="n\n")
    assert result.exit_code == 0
    assert "[[rules]]" in result.output


def test_config_categories_missing_creates_file(tmp_storage):
    runner.invoke(app, ["config", "categories"], input="y\n")
    assert (tmp_storage / "categories.toml").exists()


def test_config_bank_new_creates_template(tmp_storage):
    result = runner.invoke(app, ["config", "bank", "new", "mybank"])
    assert result.exit_code == 0
    assert (tmp_storage / "banks" / "mybank.toml").exists()


def test_config_bank_new_does_not_overwrite_without_confirm(tmp_storage):
    runner.invoke(app, ["config", "bank", "new", "mybank"])
    runner.invoke(app, ["config", "bank", "new", "mybank"], input="n\n")
    # file should still be the original template (not crashed)
    assert (tmp_storage / "banks" / "mybank.toml").exists()


def test_config_bank_missing_suggests_new(tmp_storage):
    result = runner.invoke(app, ["config", "bank", "mybank"])
    assert result.exit_code != 0
    assert "new" in result.output


# --- CLI: add + list ---

def test_add_and_list(tmp_storage):
    result = runner.invoke(app, ["add", "12.50", "Coffee"])
    assert result.exit_code == 0
    assert "Added expense #1" in result.output

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "12.50" in result.output

    result = runner.invoke(app, ["list", "--wide"])
    assert "Coffee" in result.output


def test_add_default_category_is_empty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    assert read_expenses()[0]["category"] == ""


def test_add_positive_amount_stores_direction_in(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Salary"])
    assert read_expenses()[0]["direction"] == "in"


def test_add_negative_amount_stores_direction_out(tmp_storage):
    runner.invoke(app, ["add", "--", "-10.00", "Coffee"])
    assert read_expenses()[0]["direction"] == "out"


def test_add_with_explicit_category(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--category", "food"])
    assert read_expenses()[0]["category"] == "food"


def test_add_stores_weekday(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--date", "2026-01-05"])  # Monday
    assert read_expenses()[0]["weekday"] == "Monday"


def test_add_without_time_stores_empty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    assert read_expenses()[0]["time"] == ""


def test_add_with_time(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--time", "14:30:00"])
    assert read_expenses()[0]["time"] == "14:30:00"


def test_import_stores_weekday(tmp_storage):
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [{"Date": "2026-01-05", "Amount": "10.00", "Description": "Test",
                            "IBAN": "", "Counterparty": ""}])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert read_expenses()[0]["weekday"] == "Monday"


def test_list_empty(tmp_storage):
    result = runner.invoke(app, ["list"])
    assert "No expenses found" in result.output


def test_list_default_hides_description(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Desc"])
    result = runner.invoke(app, ["list"])
    # Description column should not appear in compact mode
    assert "Description" not in result.output


def test_list_default_hides_iban(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--iban", "NL91ABNA0417164300"])
    result = runner.invoke(app, ["list"])
    assert "IBAN" not in result.output


def test_list_wide_shows_description(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Desc"])
    result = runner.invoke(app, ["list", "--wide"])
    assert "Description" in result.output


def test_list_wide_shows_iban(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--iban", "NL91ABNA0417164300"])
    result = runner.invoke(app, ["list", "-w"])
    assert "IBAN" in result.output


def test_list_filter_by_min(tmp_storage):
    runner.invoke(app, ["add", "--", "-3.00", "Small"])
    runner.invoke(app, ["add", "--", "-50.00", "Big"])
    result = runner.invoke(app, ["list", "--min", "10"])
    assert "-50.00" in result.output
    assert "-3.00" not in result.output


def test_list_filter_by_max(tmp_storage):
    runner.invoke(app, ["add", "--", "-3.00", "Small"])
    runner.invoke(app, ["add", "--", "-50.00", "Big"])
    result = runner.invoke(app, ["list", "--max", "10"])
    assert "-3.00" in result.output
    assert "-50.00" not in result.output


def test_list_filter_by_direction_out(tmp_storage):
    runner.invoke(app, ["add", "--", "-42.00", "Expense"])
    runner.invoke(app, ["add", "200.00", "Salary"])
    result = runner.invoke(app, ["list", "--direction", "out"])
    assert "-42.00" in result.output
    assert "200.00" not in result.output


def test_list_filter_by_direction_in(tmp_storage):
    runner.invoke(app, ["add", "--", "-42.00", "Expense"])
    runner.invoke(app, ["add", "200.00", "Salary"])
    result = runner.invoke(app, ["list", "--direction", "in"])
    assert "200.00" in result.output
    assert "-42.00" not in result.output


def test_list_filter_by_category(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["list", "--category", "food"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_list_filter_by_date_range(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--date", "2026-01-01"])
    runner.invoke(app, ["add", "20.00", "B", "--date", "2026-03-01"])
    result = runner.invoke(app, ["list", "--from", "2026-02-01"])
    assert "20.00" in result.output
    assert "10.00" not in result.output


def test_list_unreviewed(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])  # no counterparty or category
    runner.invoke(app, ["add", "20.00", "B", "--category", "food", "--counterparty", "Shop"])
    result = runner.invoke(app, ["list", "--unreviewed"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_list_reviewed(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food", "--counterparty", "Shop"])
    result = runner.invoke(app, ["list", "--reviewed"])
    assert "20.00" in result.output
    assert "10.00" not in result.output


# --- CLI: edit ---

def test_edit_category(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    result = runner.invoke(app, ["edit", "1", "--category", "food"])
    assert result.exit_code == 0
    assert read_expenses()[0]["category"] == "food"


def test_edit_counterparty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    runner.invoke(app, ["edit", "1", "--counterparty", "Albert Heijn"])
    assert read_expenses()[0]["counterparty"] == "Albert Heijn"


def test_edit_nonexistent_id(tmp_storage):
    result = runner.invoke(app, ["edit", "99", "--category", "food"])
    assert result.exit_code == 1


def test_edit_requires_at_least_one_field(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    result = runner.invoke(app, ["edit", "1"])
    assert result.exit_code == 1


# --- CLI: delete ---

def test_delete_expense(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    result = runner.invoke(app, ["delete", "1", "--yes"])
    assert result.exit_code == 0
    assert read_expenses() == []


def test_delete_nonexistent_id(tmp_storage):
    result = runner.invoke(app, ["delete", "99", "--yes"])
    assert result.exit_code == 1


def test_delete_no_args_errors(tmp_storage):
    result = runner.invoke(app, ["delete"])
    assert result.exit_code != 0


def test_delete_all_wipes_everything(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])
    runner.invoke(app, ["add", "20.00", "B"])
    result = runner.invoke(app, ["delete", "--all"], input="DELETE\n")
    assert result.exit_code == 0
    assert read_expenses() == []


def test_delete_all_wrong_confirmation_aborts(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    runner.invoke(app, ["delete", "--all"], input="yes\n")
    assert len(read_expenses()) == 1


def test_delete_all_shows_count(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])
    runner.invoke(app, ["add", "20.00", "B"])
    result = runner.invoke(app, ["delete", "--all"], input="DELETE\n")
    assert "2" in result.output


def test_delete_all_on_empty_store(tmp_storage):
    result = runner.invoke(app, ["delete", "--all"], input="DELETE\n")
    assert result.exit_code == 0


# --- CLI: review ---

def test_review_shows_unhandled(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 0
    assert "10.00" in result.output


def test_review_nothing_when_all_handled(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--category", "food", "--counterparty", "Shop"])
    result = runner.invoke(app, ["review"])
    assert "Nothing to review" in result.output


def test_review_unidentified_filter(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])                          # no counterparty
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shop"])  # has counterparty, no category
    result = runner.invoke(app, ["review", "--unidentified"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_review_uncategorized_filter(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])                           # no category
    runner.invoke(app, ["add", "20.00", "B", "--category", "food"])     # has category, no counterparty
    result = runner.invoke(app, ["review", "--uncategorized"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_review_interactive_saves_iban_rule(tmp_storage, monkeypatch):
    """IBAN present → confirm y → iban rule created."""
    runner.invoke(app, ["add", "10.00", "Groceries", "--iban", "NL91ABNA0417164300", "--category", "food"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "albert heijn")
    runner.invoke(app, ["review", "-i"], input="y\n")
    from expense_cli.identifier import load_counterparties
    rules = load_counterparties()
    assert any(r.get("iban") == "NL91ABNA0417164300" for r in rules)


def test_review_interactive_skips_iban_rule_when_declined(tmp_storage, monkeypatch):
    """IBAN present → confirm n → no rule saved."""
    runner.invoke(app, ["add", "10.00", "Groceries", "--iban", "NL91ABNA0417164300", "--category", "food"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "albert heijn")
    runner.invoke(app, ["review", "-i"], input="n\n")
    from expense_cli.identifier import load_counterparties
    assert load_counterparties() == []


def test_review_interactive_saves_description_rule_accepting_default(tmp_storage, monkeypatch):
    """No IBAN → press Enter to accept pre-filled description → rule saved."""
    runner.invoke(app, ["add", "10.00", "SPOTIFY PREMIUM", "--category", "subscriptions"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "spotify")
    runner.invoke(app, ["review", "-i"], input="\n")
    from expense_cli.identifier import load_counterparties
    rules = load_counterparties()
    assert any(r.get("description_contains") == "spotify premium" for r in rules)


def test_review_interactive_saves_custom_keyword(tmp_storage, monkeypatch):
    """No IBAN → user types a shorter keyword → rule saved with that keyword."""
    runner.invoke(app, ["add", "10.00", "SPOTIFY PREMIUM MONTHLY", "--category", "subscriptions"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "spotify")
    runner.invoke(app, ["review", "-i"], input="spotify\n")
    from expense_cli.identifier import load_counterparties
    rules = load_counterparties()
    assert any(r.get("description_contains") == "spotify" for r in rules)


# --- CLI: import ---

def _write_bank_config(tmp_storage):
    config = (
        '[bank]\nencoding = "utf-8"\ndate_format = "%Y-%m-%d"\n'
        'decimal_separator = "."\ndelimiter = ","\n\n'
        '[mapping]\ndate = "Date"\namount = "Amount"\n'
        'description = "Description"\niban = "IBAN"\ncounterparty = "Counterparty"\n'
    )
    (tmp_storage / "banks" / "test_bank.toml").write_text(config, encoding="utf-8")


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Date", "Amount", "Description", "IBAN", "Counterparty"])
        w.writeheader()
        for row in rows:
            w.writerow(row)


def test_import_basic(tmp_storage):
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "Groceries",
         "IBAN": "NL91ABNA0417164300", "Counterparty": "Albert Heijn"},
    ])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert result.exit_code == 0
    assert "Imported 1" in result.output
    assert read_expenses()[0]["amount"] == "42.50"


def test_import_deduplication(tmp_storage):
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "Groceries",
         "IBAN": "NL91ABNA0417164300", "Counterparty": "Albert Heijn"},
    ])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert "skipped 1 duplicates" in result.output
    assert len(read_expenses()) == 1


def test_import_force_bypasses_deduplication(tmp_storage):
    """--force re-imports even rows that would normally be skipped as duplicates."""
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "Groceries",
         "IBAN": "NL91ABNA0417164300", "Counterparty": "Albert Heijn"},
    ])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank", "--force"])
    assert result.exit_code == 0
    assert len(read_expenses()) == 2


def test_import_same_key_different_raw_not_deduplicated(tmp_storage):
    """Two rows with identical mapped fields but differing extra columns are both imported."""
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    # Write CSV with an extra column (BeginSaldo) that differs between the two rows
    with csv_file.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Date", "Amount", "Description", "IBAN", "Counterparty", "BeginSaldo"])
        w.writeheader()
        w.writerow({"Date": "2026-01-01", "Amount": "100.00", "Description": "Savings",
                    "IBAN": "NL91ABNA0417164300", "Counterparty": "Savings Account", "BeginSaldo": "1000.00"})
        w.writerow({"Date": "2026-01-01", "Amount": "100.00", "Description": "Savings",
                    "IBAN": "NL91ABNA0417164300", "Counterparty": "Savings Account", "BeginSaldo": "900.00"})
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert result.exit_code == 0
    assert len(read_expenses()) == 2


# --- CLI: summary ---

def test_summary_empty(tmp_storage):
    result = runner.invoke(app, ["summary"])
    assert result.exit_code == 0
    assert "No expenses" in result.output


def test_summary_groups_by_category(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "groceries"])
    runner.invoke(app, ["add", "15.00", "C", "--category", "transport"])
    result = runner.invoke(app, ["summary"])
    assert result.exit_code == 0
    assert "groceries" in result.output
    assert "transport" in result.output
    assert "30.00" in result.output  # groceries total
    assert "15.00" in result.output  # transport total


def test_summary_shows_percentage(tmp_storage):
    runner.invoke(app, ["add", "75.00", "A", "--category", "groceries"])
    runner.invoke(app, ["add", "25.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["summary"])
    assert "75" in result.output   # 75%
    assert "25" in result.output   # 25%


def test_summary_shows_count(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries"])
    runner.invoke(app, ["add", "10.00", "B", "--category", "groceries"])
    result = runner.invoke(app, ["summary"])
    assert "2" in result.output


def test_summary_sorted_by_amount_descending(tmp_storage):
    runner.invoke(app, ["add", "5.00", "A", "--category", "transport"])
    runner.invoke(app, ["add", "50.00", "B", "--category", "groceries"])
    result = runner.invoke(app, ["summary"])
    assert result.output.index("groceries") < result.output.index("transport")


def test_summary_uncategorized_grouped(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])  # no category
    result = runner.invoke(app, ["summary"])
    assert result.exit_code == 0
    assert "10.00" in result.output


def test_summary_filter_by_date(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries", "--date", "2026-01-01"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "groceries", "--date", "2026-03-01"])
    result = runner.invoke(app, ["summary", "--from", "2026-02-01"])
    assert "20.00" in result.output
    assert "10.00" not in result.output


def test_summary_filter_by_month_year_month_format(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "groceries", "--date", "2026-03-10"])
    result = runner.invoke(app, ["summary", "--month", "2026-01"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_summary_filter_by_month_number(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "groceries", "--date", "2026-03-10"])
    result = runner.invoke(app, ["summary", "--month", "1", "--year", "2026"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_summary_month_and_from_are_exclusive(tmp_storage):
    result = runner.invoke(app, ["summary", "--month", "2026-01", "--from", "2026-01-01"])
    assert result.exit_code == 1


def test_list_filter_by_month(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--date", "2026-03-10"])
    result = runner.invoke(app, ["list", "--month", "2026-01"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_summary_by_counterparty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert Heijn"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Albert Heijn"])
    runner.invoke(app, ["add", "15.00", "C", "--counterparty", "NS"])
    result = runner.invoke(app, ["summary", "--by", "counterparty"])
    assert "Albert Heijn" in result.output
    assert "NS" in result.output
    assert "30.00" in result.output
    assert "15.00" in result.output


# --- CLI: import ---

def test_import_auto_categorization(tmp_storage):
    _write_bank_config(tmp_storage)
    (tmp_storage / "categories.toml").write_text(
        '[[rules]]\ncounterparty = "Albert Heijn"\ncategory = "groceries"\n',
        encoding="utf-8",
    )
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "10.00", "Description": "Shop",
         "IBAN": "", "Counterparty": "Albert Heijn"},
    ])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert read_expenses()[0]["category"] == "groceries"


def test_import_auto_identification(tmp_storage):
    _write_bank_config(tmp_storage)
    (tmp_storage / "counterparties.toml").write_text(
        '[[counterparty]]\ndescription_contains = "groceries"\nname = "Albert Heijn"\n',
        encoding="utf-8",
    )
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "10.00", "Description": "Groceries payment",
         "IBAN": "", "Counterparty": ""},
    ])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert read_expenses()[0]["counterparty"] == "Albert Heijn"
