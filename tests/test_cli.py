import csv
import pytest
from typer.testing import CliRunner
from expense_cli.cli import app, _validate_bank_config, _validate_counterparties_config
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


def test_validate_bank_config_unknown_mapping_key():
    config = {"mapping": {"date": "Date", "amount": "Amount", "merchant": "Merchant"}}
    errors = _validate_bank_config(config)
    assert any("mapping.merchant" in e for e in errors)


# --- _validate_counterparties_config ---

def test_validate_counterparties_valid_iban():
    config = {"counterparty": [{"iban": "NL91ABNA0417164300", "name": "Albert Heijn"}]}
    assert _validate_counterparties_config(config) == []


def test_validate_counterparties_valid_description():
    config = {"counterparty": [{"description_contains": "netflix", "name": "Netflix"}]}
    assert _validate_counterparties_config(config) == []


def test_validate_counterparties_valid_category_only():
    # name + category, no matcher — valid (used for categorization only)
    config = {"counterparty": [{"name": "Mom", "category": "transfers"}]}
    assert _validate_counterparties_config(config) == []


def test_validate_counterparties_valid_with_category():
    config = {"counterparty": [{"name": "AH", "iban": "NL01", "category": "groceries"}]}
    assert _validate_counterparties_config(config) == []


def test_validate_counterparties_empty():
    assert _validate_counterparties_config({"counterparty": []}) == []


def test_validate_counterparties_missing_name():
    errors = _validate_counterparties_config({"counterparty": [{"iban": "NL91ABNA0417164300"}]})
    assert any("name" in e for e in errors)


def test_validate_counterparties_name_only_is_valid():
    # Name-only entry is valid (inert but not an error)
    errors = _validate_counterparties_config({"counterparty": [{"name": "Albert Heijn"}]})
    assert errors == []


def test_validate_counterparties_both_matchers():
    config = {"counterparty": [{"iban": "NL91", "description_contains": "heijn", "name": "Albert Heijn"}]}
    errors = _validate_counterparties_config(config)
    assert any("both" in e for e in errors)


def test_validate_counterparties_unknown_field():
    config = {"counterparty": [{"name": "AH", "foo": "bar"}]}
    errors = _validate_counterparties_config(config)
    assert any("foo" in e for e in errors)


# --- CLI: version ---

def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


# --- CLI: config list (combined view) ---

def test_config_list_empty(tmp_storage):
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    assert "No counterparty" in result.output


def test_config_list_shows_all_fields(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add",
                        "--name", "albert heijn", "--iban", "NL01", "--category", "groceries"])
    runner.invoke(app, ["config", "counterparties", "add",
                        "--name", "netflix", "--contains", "netflix"])
    runner.invoke(app, ["config", "counterparties", "add",
                        "--name", "mom", "--category", "transfers"])
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    assert "albert heijn" in result.output
    assert "NL01" in result.output
    assert "groceries" in result.output
    assert "netflix" in result.output
    assert "mom" in result.output
    assert "transfers" in result.output


def test_config_list_sorts_subcategories_under_parent(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add", "--name", "lidl", "--iban", "NL01", "--category", "food/groceries"])
    runner.invoke(app, ["config", "counterparties", "add", "--name", "mcdonalds", "--contains", "mcd", "--category", "food/restaurant"])
    runner.invoke(app, ["config", "counterparties", "add", "--name", "ns", "--iban", "NL02", "--category", "transport"])
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    food_groceries_pos = result.output.find("food/groceries")
    food_restaurant_pos = result.output.find("food/restaurant")
    transport_pos = result.output.find("transport")
    assert food_groceries_pos < transport_pos
    assert food_restaurant_pos < transport_pos


def test_config_bare_shows_list(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add",
                        "--name", "spotify", "--contains", "spotify", "--category", "subscriptions"])
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "spotify" in result.output


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


def test_config_counterparties_edit_interactive_rename(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add", "--name", "netflix", "--contains", "netflix"])
    # input: new name, empty iban (keep), empty contains (keep), empty category (keep)
    result = runner.invoke(app, ["config", "counterparties", "edit", "--name", "netflix"], input="netflix premium\n\n\n\n")
    assert result.exit_code == 0
    from expense_cli.identifier import load_counterparties
    entries = load_counterparties()
    assert entries[0]["name"] == "netflix premium"
    assert entries[0]["description_contains"] == "netflix"


def test_config_counterparties_edit_interactive_changes_contains(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add", "--name", "netflix", "--contains", "netflix"])
    # input: keep name (Enter), keep iban (Enter), change contains, keep category (Enter)
    result = runner.invoke(app, ["config", "counterparties", "edit", "--name", "netflix"], input="\n\nstreaming\n\n")
    assert result.exit_code == 0
    from expense_cli.identifier import load_counterparties
    entries = load_counterparties()
    assert entries[0]["name"] == "netflix"
    assert entries[0]["description_contains"] == "streaming"


def test_config_counterparties_edit_interactive_no_changes(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add", "--name", "netflix", "--contains", "netflix"])
    result = runner.invoke(app, ["config", "counterparties", "edit", "--name", "netflix"], input="\n\n\n\n")
    assert result.exit_code == 0
    assert "No changes" in result.output


def test_config_counterparties_edit_interactive_not_found(tmp_storage):
    result = runner.invoke(app, ["config", "counterparties", "edit", "--name", "nobody"], input="\n\n\n\n")
    assert result.exit_code == 1


def test_config_categories_edit_interactive_rename(tmp_storage):
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "netflix", "--category", "subscriptions"])
    # input: new counterparty name, new category
    result = runner.invoke(app, ["config", "categories", "edit", "--counterparty", "netflix"], input="netflix premium\nentertainment\n")
    assert result.exit_code == 0
    from expense_cli.categorizer import load_rules
    rules = load_rules()
    assert rules[0]["name"] == "netflix premium"
    assert rules[0]["category"] == "entertainment"


def test_config_categories_edit_interactive_no_changes(tmp_storage):
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "netflix", "--category", "subscriptions"])
    result = runner.invoke(app, ["config", "categories", "edit", "--counterparty", "netflix"], input="\n\n")
    assert result.exit_code == 0
    assert "No changes" in result.output


def test_config_categories_edit_interactive_not_found(tmp_storage):
    result = runner.invoke(app, ["config", "categories", "edit", "--counterparty", "nobody"], input="\n\n")
    assert result.exit_code == 1


def test_config_counterparties_remove(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add", "--name", "netflix", "--contains", "netflix"])
    result = runner.invoke(app, ["config", "counterparties", "remove", "--name", "netflix"])
    assert result.exit_code == 0
    from expense_cli.identifier import load_counterparties
    assert load_counterparties() == []


def test_config_counterparties_remove_not_found(tmp_storage):
    result = runner.invoke(app, ["config", "counterparties", "remove", "--name", "nobody"])
    assert result.exit_code == 1


def test_config_counterparties_remove_all(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add", "--name", "netflix", "--contains", "netflix"])
    runner.invoke(app, ["config", "counterparties", "add", "--name", "spotify", "--contains", "spotify"])
    result = runner.invoke(app, ["config", "counterparties", "remove", "--all"], input="DELETE\n")
    assert result.exit_code == 0
    from expense_cli.identifier import load_counterparties
    assert load_counterparties() == []


def test_config_counterparties_remove_all_aborts_on_wrong_confirmation(tmp_storage):
    runner.invoke(app, ["config", "counterparties", "add", "--name", "netflix", "--contains", "netflix"])
    result = runner.invoke(app, ["config", "counterparties", "remove", "--all"], input="no\n")
    assert result.exit_code == 1
    from expense_cli.identifier import load_counterparties
    assert len(load_counterparties()) == 1


def test_config_counterparties_remove_all_and_name_errors(tmp_storage):
    result = runner.invoke(app, ["config", "counterparties", "remove", "--all", "--name", "netflix"])
    assert result.exit_code == 1


def test_config_counterparties_remove_no_args_errors(tmp_storage):
    result = runner.invoke(app, ["config", "counterparties", "remove"])
    assert result.exit_code == 1


def test_config_categories_remove(tmp_storage):
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "netflix", "--category", "subscriptions"])
    result = runner.invoke(app, ["config", "categories", "remove", "--counterparty", "netflix"])
    assert result.exit_code == 0
    from expense_cli.categorizer import load_rules
    assert load_rules() == []


def test_config_categories_remove_not_found(tmp_storage):
    result = runner.invoke(app, ["config", "categories", "remove", "--counterparty", "nobody"])
    assert result.exit_code == 1


def test_config_categories_remove_all(tmp_storage):
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "netflix", "--category", "subscriptions"])
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "rewe", "--category", "groceries"])
    result = runner.invoke(app, ["config", "categories", "remove", "--all"], input="DELETE\n")
    assert result.exit_code == 0
    from expense_cli.categorizer import load_rules
    assert load_rules() == []


def test_config_categories_remove_all_aborts_on_wrong_confirmation(tmp_storage):
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "netflix", "--category", "subscriptions"])
    result = runner.invoke(app, ["config", "categories", "remove", "--all"], input="no\n")
    assert result.exit_code == 1
    from expense_cli.categorizer import load_rules
    assert len(load_rules()) == 1


def test_config_categories_remove_all_and_counterparty_errors(tmp_storage):
    result = runner.invoke(app, ["config", "categories", "remove", "--all", "--counterparty", "netflix"])
    assert result.exit_code == 1


def test_config_categories_remove_no_args_errors(tmp_storage):
    result = runner.invoke(app, ["config", "categories", "remove"])
    assert result.exit_code == 1


def test_config_counterparties_add_sorts_alphabetically(tmp_storage):
    from expense_cli.identifier import load_counterparties
    runner.invoke(app, ["config", "counterparties", "add", "--name", "zebra", "--contains", "zebra"])
    runner.invoke(app, ["config", "counterparties", "add", "--name", "alpha", "--contains", "alpha"])
    runner.invoke(app, ["config", "counterparties", "add", "--name", "mango", "--contains", "mango"])
    names = [cp["name"] for cp in load_counterparties()]
    assert names == ["alpha", "mango", "zebra"]


def test_config_categories_add_sorts_alphabetically(tmp_storage):
    from expense_cli.categorizer import load_rules
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "zebra", "--category", "z"])
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "alpha", "--category", "a"])
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "mango", "--category", "m"])
    names = [r["name"] for r in load_rules()]
    assert names == ["alpha", "mango", "zebra"]


def test_config_categories_list_empty(tmp_storage):
    result = runner.invoke(app, ["config", "categories"])
    assert result.exit_code == 0
    assert "No category rules found" in result.output


def test_config_categories_list_shows_rules(tmp_storage):
    runner.invoke(app, ["config", "categories", "add", "--counterparty", "netflix", "--category", "subscriptions"])
    result = runner.invoke(app, ["config", "categories"])
    assert result.exit_code == 0
    assert "netflix" in result.output
    assert "subscriptions" in result.output


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


# --- bank-set ---

def _make_bank(tmp_storage) -> None:
    runner.invoke(app, ["config", "bank", "new", "mybank"])


def test_config_bank_set_column(tmp_storage):
    _make_bank(tmp_storage)
    result = runner.invoke(app, ["config", "bank-set", "mybank", "--field", "iban", "--column", "IBAN"])
    assert result.exit_code == 0
    content = (tmp_storage / "banks" / "mybank.toml").read_text(encoding="utf-8")
    assert 'iban = "IBAN"' in content


def test_config_bank_set_extract_iban_from(tmp_storage):
    _make_bank(tmp_storage)
    result = runner.invoke(app, ["config", "bank-set", "mybank", "--field", "iban", "--extract-iban-from", "Description"])
    assert result.exit_code == 0
    content = (tmp_storage / "banks" / "mybank.toml").read_text(encoding="utf-8")
    assert "extract_iban_from" in content
    assert "Description" in content


def test_config_bank_set_column_and_extract_iban_from(tmp_storage):
    _make_bank(tmp_storage)
    result = runner.invoke(app, [
        "config", "bank-set", "mybank",
        "--field", "iban", "--column", "IBAN", "--extract-iban-from", "Description",
    ])
    assert result.exit_code == 0
    content = (tmp_storage / "banks" / "mybank.toml").read_text(encoding="utf-8")
    assert "column" in content
    assert "extract_iban_from" in content


def test_config_bank_set_from_column_and_pattern(tmp_storage):
    _make_bank(tmp_storage)
    result = runner.invoke(app, [
        "config", "bank-set", "mybank",
        "--field", "iban", "--from-column", "Description", "--pattern", r"[A-Z]{2}\d{2}[A-Z0-9]+",
    ])
    assert result.exit_code == 0
    content = (tmp_storage / "banks" / "mybank.toml").read_text(encoding="utf-8")
    assert "from_column" in content
    assert "pattern" in content


def test_config_bank_set_missing_bank(tmp_storage):
    result = runner.invoke(app, ["config", "bank-set", "nobank", "--field", "iban", "--column", "IBAN"])
    assert result.exit_code != 0


def test_config_bank_set_no_options(tmp_storage):
    _make_bank(tmp_storage)
    result = runner.invoke(app, ["config", "bank-set", "mybank", "--field", "iban"])
    assert result.exit_code != 0


def test_config_bank_set_pattern_without_from_column(tmp_storage):
    _make_bank(tmp_storage)
    result = runner.invoke(app, ["config", "bank-set", "mybank", "--field", "iban", "--pattern", "foo"])
    assert result.exit_code != 0


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


def test_add_with_note(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--note", "reimbursed by work"])
    assert read_expenses()[0]["note"] == "reimbursed by work"


def test_add_note_truncated_at_max_length(tmp_storage):
    from expense_cli.cli import NOTE_MAX_LEN
    long_note = "x" * (NOTE_MAX_LEN + 10)
    runner.invoke(app, ["add", "10.00", "Test", "--note", long_note])
    assert read_expenses()[0]["note"] == "x" * NOTE_MAX_LEN


def test_add_without_note_stores_empty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    assert read_expenses()[0]["note"] == ""


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


def test_list_filter_by_parent_category_matches_subcategories(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food/restaurant"])
    runner.invoke(app, ["add", "99.00", "C", "--category", "transport"])
    result = runner.invoke(app, ["list", "--category", "food"])
    assert "10.00" in result.output
    assert "20.00" in result.output
    assert "99.00" not in result.output


def test_list_filter_by_exact_subcategory(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food/restaurant"])
    result = runner.invoke(app, ["list", "--category", "food/groceries"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_list_exclude_category_excludes_subcategories(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["list", "--exclude-category", "food"])
    assert "10.00" not in result.output
    assert "20.00" in result.output


def test_list_filter_by_counterparty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    result = runner.invoke(app, ["list", "--counterparty", "Albert"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_list_filter_by_counterparty_shortflag(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    result = runner.invoke(app, ["list", "-p", "Albert"])
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


def test_list_by_id(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])
    runner.invoke(app, ["add", "20.00", "B"])
    from expense_cli.storage import read_expenses
    first_id = read_expenses()[0]["id"]
    result = runner.invoke(app, ["list", "--id", first_id])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_list_by_id_multiple(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Alpha"])
    runner.invoke(app, ["add", "20.00", "Beta"])
    runner.invoke(app, ["add", "99.00", "Gamma"])
    from expense_cli.storage import read_expenses
    ids = [e["id"] for e in read_expenses()]
    result = runner.invoke(app, ["list", "--id", f"{ids[0]},{ids[1]}"])
    assert "10.00" in result.output
    assert "20.00" in result.output
    assert "99.00" not in result.output


def test_list_by_id_not_found(tmp_storage):
    result = runner.invoke(app, ["list", "--id", "9999"])
    assert result.exit_code != 0


def test_list_by_id_partial_not_found(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])
    from expense_cli.storage import read_expenses
    real_id = read_expenses()[0]["id"]
    result = runner.invoke(app, ["list", "--id", f"{real_id},9999"])
    assert result.exit_code != 0


def test_list_exclude_category_single(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["list", "--exclude-category", "food"])
    assert "20.00" in result.output
    assert "10.00" not in result.output


def test_list_exclude_category_multiple(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    runner.invoke(app, ["add", "30.00", "C", "--category", "health"])
    result = runner.invoke(app, ["list", "--exclude-category", "food,transport"])
    assert "30.00" in result.output
    assert "10.00" not in result.output
    assert "20.00" not in result.output


def test_list_exclude_counterparty_single(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    result = runner.invoke(app, ["list", "--exclude-counterparty", "Albert"])
    assert "20.00" in result.output
    assert "10.00" not in result.output


def test_list_exclude_counterparty_multiple(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    runner.invoke(app, ["add", "30.00", "C", "--counterparty", "Gym"])
    result = runner.invoke(app, ["list", "--exclude-counterparty", "Albert,Shell"])
    assert "30.00" in result.output
    assert "10.00" not in result.output
    assert "20.00" not in result.output


def test_list_include_category_single(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    runner.invoke(app, ["add", "30.00", "C", "--category", "health"])
    result = runner.invoke(app, ["list", "--include-category", "food"])
    assert "10.00" in result.output
    assert "20.00" not in result.output
    assert "30.00" not in result.output


def test_list_include_category_multiple(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    runner.invoke(app, ["add", "30.00", "C", "--category", "health"])
    result = runner.invoke(app, ["list", "--include-category", "food,health"])
    assert "10.00" in result.output
    assert "30.00" in result.output
    assert "20.00" not in result.output


def test_list_include_category_prefix_match(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["list", "--include-category", "food"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_list_include_counterparty_single(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    result = runner.invoke(app, ["list", "--include-counterparty", "Albert"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_list_include_counterparty_multiple(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    runner.invoke(app, ["add", "99.00", "C", "--counterparty", "Gym"])
    result = runner.invoke(app, ["list", "--include-counterparty", "Albert,Shell"])
    assert "Albert" in result.output
    assert "Shell" in result.output
    assert "Gym" not in result.output


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


def test_edit_multi_category(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])
    runner.invoke(app, ["add", "20.00", "B"])
    runner.invoke(app, ["add", "30.00", "C"])
    result = runner.invoke(app, ["edit", "1,2,3", "--category", "food"])
    assert result.exit_code == 0
    expenses = read_expenses()
    assert all(e["category"] == "food" for e in expenses)


def test_edit_multi_partial_not_found(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])
    result = runner.invoke(app, ["edit", "1,99", "--category", "food"])
    # partial success: found ID 1, not 99 — exit 0 with error printed to stderr
    assert read_expenses()[0]["category"] == "food"


def test_edit_multi_all_not_found(tmp_storage):
    result = runner.invoke(app, ["edit", "98,99", "--category", "food"])
    assert result.exit_code == 1


def test_edit_invalid_id_format(tmp_storage):
    result = runner.invoke(app, ["edit", "abc", "--category", "food"])
    assert result.exit_code == 1


def test_edit_note(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test"])
    result = runner.invoke(app, ["edit", "1", "--note", "reimbursed"])
    assert result.exit_code == 0
    assert read_expenses()[0]["note"] == "reimbursed"


def test_edit_note_clear(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--note", "old note"])
    runner.invoke(app, ["edit", "1", "--note", ""])
    assert read_expenses()[0]["note"] == ""


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

def test_review_info_shows_monthly_summary(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Jan expense", "--date", "2024-01-15"])
    runner.invoke(app, ["add", "20.00", "Jan expense 2", "--date", "2024-01-20"])
    runner.invoke(app, ["add", "30.00", "Feb expense", "--date", "2024-02-10"])
    result = runner.invoke(app, ["review", "--info"])
    assert result.exit_code == 0
    assert "2024-01" in result.output
    assert "2024-02" in result.output
    assert "2" in result.output  # 2 in Jan
    assert "1" in result.output  # 1 in Feb


def test_review_info_shows_nothing_when_all_reviewed(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Done", "--category", "food", "--counterparty", "Shop"])
    result = runner.invoke(app, ["review", "--info"])
    assert result.exit_code == 0
    assert "Nothing to review" in result.output


def test_review_info_exits_without_interactive_loop(tmp_storage, monkeypatch):
    pick_called = []
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: pick_called.append(True))
    runner.invoke(app, ["add", "10.00", "Test"])
    runner.invoke(app, ["review", "--info"])
    assert pick_called == []  # --info must not enter the review loop


def test_review_shows_unhandled(tmp_storage, monkeypatch):
    from expense_cli.cli import _SKIP
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: _SKIP)
    runner.invoke(app, ["add", "10.00", "Test"])
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 0
    assert "10.00" in result.output


def test_review_nothing_when_all_handled(tmp_storage):
    runner.invoke(app, ["add", "10.00", "Test", "--category", "food", "--counterparty", "Shop"])
    result = runner.invoke(app, ["review"])
    assert "Nothing to review" in result.output


def test_review_unidentified_filter(tmp_storage, monkeypatch):
    from expense_cli.cli import _SKIP
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: _SKIP)
    runner.invoke(app, ["add", "10.00", "A"])                          # no counterparty
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shop"])  # has counterparty, no category
    result = runner.invoke(app, ["review", "--unidentified"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_review_uncategorized_filter(tmp_storage, monkeypatch):
    from expense_cli.cli import _SKIP
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: _SKIP)
    runner.invoke(app, ["add", "10.00", "A"])                           # no category
    runner.invoke(app, ["add", "20.00", "B", "--category", "food"])     # has category, no counterparty
    result = runner.invoke(app, ["review", "--uncategorized"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_review_interactive_saves_iban_rule(tmp_storage, monkeypatch):
    """IBAN present → confirm y → iban rule created."""
    runner.invoke(app, ["add", "10.00", "Groceries", "--iban", "NL91ABNA0417164300", "--category", "food"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "albert heijn")
    runner.invoke(app, ["review"], input="y\n")
    from expense_cli.identifier import load_counterparties
    rules = load_counterparties()
    assert any(r.get("iban") == "NL91ABNA0417164300" for r in rules)


def test_review_interactive_skips_iban_rule_when_declined(tmp_storage, monkeypatch):
    """IBAN present → confirm n → no rule saved."""
    runner.invoke(app, ["add", "10.00", "Groceries", "--iban", "NL91ABNA0417164300", "--category", "food"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "albert heijn")
    runner.invoke(app, ["review"], input="n\n")
    from expense_cli.identifier import load_counterparties
    assert load_counterparties() == []


def test_review_interactive_non_iban_value_skips_to_description(tmp_storage, monkeypatch):
    """Non-IBAN value in iban field (e.g. 'BS172155') → skip IBAN confirm, go straight to description prompt."""
    runner.invoke(app, ["add", "10.00", "Some payment", "--iban", "BS172155", "--category", "other"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "some party")
    runner.invoke(app, ["review"], input="payment\n")
    from expense_cli.identifier import load_counterparties
    rules = load_counterparties()
    assert any(r.get("description_contains") == "payment" for r in rules)
    assert not any(r.get("iban") for r in rules)


def test_review_interactive_saves_description_rule_accepting_default(tmp_storage, monkeypatch):
    """No IBAN → press Enter to accept pre-filled description → rule saved."""
    runner.invoke(app, ["add", "10.00", "SPOTIFY PREMIUM", "--category", "subscriptions"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "spotify")
    runner.invoke(app, ["review"], input="\n")
    from expense_cli.identifier import load_counterparties
    rules = load_counterparties()
    assert any(r.get("description_contains") == "spotify premium" for r in rules)


def test_review_interactive_saves_custom_keyword(tmp_storage, monkeypatch):
    """No IBAN → user types a shorter keyword → rule saved with that keyword."""
    runner.invoke(app, ["add", "10.00", "SPOTIFY PREMIUM MONTHLY", "--category", "subscriptions"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "spotify")
    runner.invoke(app, ["review"], input="spotify\n")
    from expense_cli.identifier import load_counterparties
    rules = load_counterparties()
    assert any(r.get("description_contains") == "spotify" for r in rules)


def test_review_interactive_keyword_skip_does_not_save_rule(tmp_storage, monkeypatch):
    """^S during keyword prompt → no counterparty rule saved, review completes normally."""
    from expense_cli.cli import _SKIP
    runner.invoke(app, ["add", "10.00", "SPOTIFY PREMIUM MONTHLY", "--category", "subscriptions"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "spotify")
    monkeypatch.setattr("expense_cli.cli._input_prefilled", lambda *a, **kw: _SKIP)
    runner.invoke(app, ["review"])
    from expense_cli.identifier import load_counterparties
    rules = load_counterparties()
    assert not any(r.get("description_contains") for r in rules)


def test_review_interactive_keyword_quit_stops_review(tmp_storage, monkeypatch):
    """^Q during keyword prompt → review session quits; subsequent expenses are not processed."""
    runner.invoke(app, ["add", "10.00", "EXPENSE ONE", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "EXPENSE TWO", "--category", "food"])
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "some party")
    monkeypatch.setattr("expense_cli.cli._input_prefilled", lambda *a, **kw: None)
    runner.invoke(app, ["review"])
    from expense_cli.storage import read_expenses
    expenses = read_expenses()
    without_counterparty = [e for e in expenses if not e.get("counterparty")]
    assert len(without_counterparty) == 1  # second expense was not reviewed


def test_review_interactive_back_goes_to_previous(tmp_storage, monkeypatch):
    """Ctrl+Z on expense 2 → re-visits expense 1 with current value pre-filled; corrected value is saved."""
    from expense_cli.cli import _BACK
    runner.invoke(app, ["add", "10.00", "Expense A"])
    runner.invoke(app, ["add", "20.00", "Expense B"])
    calls: list[tuple[str, str]] = []

    def fake_pick(prompt, options, *a, initial="", **kw):
        calls.append((prompt, initial))
        if len(calls) == 1:
            return "alice"       # expense 1 counterparty
        if len(calls) == 2:
            return "food"        # expense 1 category
        if len(calls) == 3:
            return _BACK         # expense 2 counterparty → go back
        if len(calls) == 4:
            return "alice fixed" # expense 1 counterparty re-prompt (initial should be "alice")
        if len(calls) == 5:
            return "groceries"   # expense 1 category re-prompt
        if len(calls) == 6:
            return "bob"         # expense 2 counterparty (second attempt)
        return "other"

    monkeypatch.setattr("expense_cli.cli._pick", fake_pick)
    monkeypatch.setattr("expense_cli.cli._input_prefilled", lambda *a, **kw: "")
    monkeypatch.setattr("typer.confirm", lambda *a, **kw: False)
    runner.invoke(app, ["review"])

    from expense_cli.storage import read_expenses
    expenses = read_expenses()
    exp_a = next(e for e in expenses if e["description"] == "Expense A")
    assert exp_a["counterparty"] == "alice fixed"
    assert exp_a["category"] == "groceries"
    assert calls[3] == ("Counterparty", "alice")  # pre-filled with prior saved value


def test_review_interactive_back_at_first_expense_redoes_it(tmp_storage, monkeypatch):
    """Ctrl+Z on the very first expense → re-prompts from scratch without crashing."""
    from expense_cli.cli import _BACK
    runner.invoke(app, ["add", "10.00", "Only Expense"])
    calls: list[int] = []

    def fake_pick(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _BACK    # back on first → redo
        if len(calls) == 2:
            return "alice"  # counterparty on redo
        return "food"       # category on redo

    monkeypatch.setattr("expense_cli.cli._pick", fake_pick)
    monkeypatch.setattr("expense_cli.cli._input_prefilled", lambda *a, **kw: "")
    monkeypatch.setattr("typer.confirm", lambda *a, **kw: False)
    runner.invoke(app, ["review"])

    from expense_cli.storage import read_expenses
    expenses = read_expenses()
    assert expenses[0]["counterparty"] == "alice"
    assert expenses[0]["category"] == "food"


def test_review_note_step_sets_note(tmp_storage, monkeypatch):
    """_pick_note returning a value saves it on the expense."""
    runner.invoke(app, ["add", "10.00", "Test"])
    # Return "" from _pick so counterparty/category fields are skipped but transaction is NOT skipped
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: "")
    monkeypatch.setattr("expense_cli.cli._pick_note", lambda *a, **kw: "reimbursed by work")
    runner.invoke(app, ["review"])
    from expense_cli.storage import read_expenses
    assert read_expenses()[0]["note"] == "reimbursed by work"


def test_review_note_step_skipped_when_skip_transaction(tmp_storage, monkeypatch):
    """When a transaction is skipped (^S), the note step is not reached."""
    from expense_cli.cli import _SKIP
    runner.invoke(app, ["add", "10.00", "Test"])
    note_calls: list = []
    monkeypatch.setattr("expense_cli.cli._pick", lambda *a, **kw: _SKIP)
    monkeypatch.setattr("expense_cli.cli._pick_note", lambda *a, **kw: note_calls.append(1) or "x")
    runner.invoke(app, ["review"])
    assert note_calls == []


# --- CLI: import ---

def _write_bank_config(tmp_storage):
    config = (
        '[bank]\nencoding = "utf-8"\ndate_format = "%Y-%m-%d"\n'
        'decimal_separator = "."\ndelimiter = ","\n\n'
        '[mapping]\ndate = "Date"\namount = "Amount"\n'
        'description = "Description"\niban = "IBAN"\ncounterparty = "Counterparty"\n'
    )
    (tmp_storage / "banks" / "test_bank.toml").write_text(config, encoding="utf-8")


def _write_bank_config_with_time_from_description(tmp_storage):
    config = (
        '[bank]\nencoding = "utf-8"\ndate_format = "%Y-%m-%d"\n'
        'decimal_separator = "."\ndelimiter = ","\n\n'
        '[mapping]\ndate = "Date"\namount = "Amount"\n'
        'description = "Description"\niban = "IBAN"\ncounterparty = "Counterparty"\n'
        'time = { from_column = "Description", pattern = \'^(\\d{2}:\\d{2})\' }\n'
    )
    (tmp_storage / "banks" / "test_bank.toml").write_text(config, encoding="utf-8")


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["Date", "Amount", "Description", "IBAN", "Counterparty"])
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _write_expense_fixture(
    expense_id: str,
    *,
    date: str,
    amount: str,
    description: str = "",
    iban: str = "",
    counterparty: str = "",
    category: str = "",
):
    from expense_cli.storage import write_expense
    direction = "out" if float(amount) < 0 else "in"
    write_expense({
        "id": expense_id,
        "date": date,
        "weekday": "",
        "time": "",
        "amount": amount,
        "direction": direction,
        "description": description,
        "iban": iban,
        "counterparty": counterparty,
        "category": category,
        "note": "",
        "source_hash": "",
        "split_id": "",
    })


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


def test_import_rejects_invalid_bank_config_unknown_mapping_field(tmp_storage):
    config = (
        '[bank]\nencoding = "utf-8"\ndate_format = "%Y-%m-%d"\n'
        'decimal_separator = "."\ndelimiter = ","\n\n'
        '[mapping]\ndate = "Date"\namount = "Amount"\nmerchant = "Merchant"\n'
    )
    (tmp_storage / "banks" / "test_bank.toml").write_text(config, encoding="utf-8")
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "Groceries",
         "IBAN": "NL91ABNA0417164300", "Counterparty": "Albert Heijn"},
    ])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert result.exit_code == 1
    assert "Bank config is invalid" in result.output
    assert "mapping.merchant" in result.output


def test_import_deduplication(tmp_storage):
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "Groceries",
         "IBAN": "NL91ABNA0417164300", "Counterparty": "Albert Heijn"},
    ])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert "Skipped 1 duplicate" in result.output
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


def test_import_reports_possible_duplicates_with_default_match_fields(tmp_storage):
    _write_bank_config(tmp_storage)
    runner.invoke(app, ["add", "42.50", "Old desc", "--date", "2026-01-01"])
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "New desc",
         "IBAN": "", "Counterparty": ""},
    ])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert result.exit_code == 0
    assert "Possible duplicates (date, amount)" in result.output
    assert "Old desc" in result.output
    assert "New desc" in result.output
    assert len(read_expenses()) == 2


def test_import_time_can_be_extracted_from_description_regex(tmp_storage):
    _write_bank_config_with_time_from_description(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "12:34 payment",
         "IBAN": "", "Counterparty": ""},
    ])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert result.exit_code == 0
    assert read_expenses()[0]["time"] == "12:34:00"


def test_import_possible_duplicates_respect_custom_match_fields(tmp_storage):
    _write_bank_config(tmp_storage)
    runner.invoke(app, ["add", "42.50", "Old desc", "--date", "2026-01-01", "--iban", "NL01"])
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "New desc",
         "IBAN": "NL02", "Counterparty": ""},
    ])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank",
                                 "--match-field", "date,amount,iban"])
    assert result.exit_code == 0
    assert "Possible duplicates" not in result.output


def test_import_possible_duplicates_can_match_rows_imported_in_same_run(tmp_storage):
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "First",
         "IBAN": "", "Counterparty": ""},
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "Second",
         "IBAN": "", "Counterparty": ""},
    ])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    assert result.exit_code == 0
    assert "Possible duplicates (date, amount)" in result.output
    assert "First" in result.output
    assert "Second" in result.output


def test_import_force_still_reports_possible_duplicates(tmp_storage):
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "Groceries",
         "IBAN": "NL91ABNA0417164300", "Counterparty": "Albert Heijn"},
    ])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    result = runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank", "--force"])
    assert result.exit_code == 0
    assert "Possible duplicates (date, amount)" in result.output
    assert len(read_expenses()) == 2


def test_reimport_time_fills_empty_matching_row(tmp_storage):
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "12:34 payment",
         "IBAN": "", "Counterparty": ""},
    ])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    _write_bank_config_with_time_from_description(tmp_storage)
    result = runner.invoke(app, ["reimport", str(csv_file), "--bank", "test_bank", "--field", "time"])
    assert result.exit_code == 0
    assert "1 updated" in result.output
    assert read_expenses()[0]["time"] == "12:34:00"


def test_reimport_does_not_overwrite_existing_time(tmp_storage):
    _write_bank_config_with_time_from_description(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "12:34 payment",
         "IBAN": "", "Counterparty": ""},
    ])
    runner.invoke(app, ["import", str(csv_file), "--bank", "test_bank"])
    result = runner.invoke(app, ["reimport", str(csv_file), "--bank", "test_bank", "--field", "time"])
    assert result.exit_code == 0
    assert "Nothing to update." in result.output
    assert "1 already had a value" in result.output
    assert read_expenses()[0]["time"] == "12:34:00"


def test_reimport_skips_rows_without_source_hash_match(tmp_storage):
    _write_bank_config_with_time_from_description(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "12:34 payment",
         "IBAN": "", "Counterparty": ""},
    ])
    result = runner.invoke(app, ["reimport", str(csv_file), "--bank", "test_bank", "--field", "time"])
    assert result.exit_code == 0
    assert "Nothing to update." in result.output
    assert "1 no source_hash match" in result.output


def test_reimport_reports_legacy_rows_without_source_hash(tmp_storage):
    _write_bank_config_with_time_from_description(tmp_storage)
    _write_expense_fixture("1", date="2026-01-01", amount="42.50", description="12:34 payment")
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "12:34 payment",
         "IBAN": "", "Counterparty": ""},
    ])
    result = runner.invoke(app, ["reimport", str(csv_file), "--bank", "test_bank", "--field", "time"])
    assert result.exit_code == 0
    assert "1 legacy row(s) without source_hash" in result.output
    assert read_expenses()[0]["time"] == ""


def test_reimport_invalid_field_errors(tmp_storage):
    _write_bank_config(tmp_storage)
    csv_file = tmp_storage / "statement.csv"
    _write_csv(csv_file, [
        {"Date": "2026-01-01", "Amount": "42.50", "Description": "12:34 payment",
         "IBAN": "", "Counterparty": ""},
    ])
    result = runner.invoke(app, ["reimport", str(csv_file), "--bank", "test_bank", "--field", "iban"])
    assert result.exit_code == 1
    assert "--field must be one of: time" in result.output


# --- CLI: duplicates ---


def test_duplicates_finds_default_date_amount_matches(tmp_storage):
    _write_expense_fixture("1", date="2026-01-01", amount="42.50", description="A")
    _write_expense_fixture("2", date="2026-01-01", amount="42.50", description="B")
    result = runner.invoke(app, ["duplicates"])
    assert result.exit_code == 0
    assert "Possible duplicate group 1" in result.output
    assert "A" in result.output
    assert "B" in result.output


def test_duplicates_match_field_iban_narrows_results(tmp_storage):
    _write_expense_fixture("1", date="2026-01-01", amount="42.50", description="A", iban="NL01")
    _write_expense_fixture("2", date="2026-01-01", amount="42.50", description="B", iban="NL02")
    result = runner.invoke(app, ["duplicates", "--match-field", "date,amount,iban"])
    assert result.exit_code == 0
    assert "No possible duplicates found." in result.output


def test_duplicates_description_field_can_distinguish_rows(tmp_storage):
    _write_expense_fixture("1", date="2026-01-01", amount="42.50", description="A")
    _write_expense_fixture("2", date="2026-01-01", amount="42.50", description="B")
    result = runner.invoke(app, ["duplicates", "--match-field", "date,amount,description"])
    assert result.exit_code == 0
    assert "No possible duplicates found." in result.output


def test_duplicates_honors_from_to(tmp_storage):
    _write_expense_fixture("1", date="2026-01-01", amount="42.50", description="Old A")
    _write_expense_fixture("2", date="2026-01-01", amount="42.50", description="Old B")
    _write_expense_fixture("3", date="2026-02-01", amount="42.50", description="New A")
    _write_expense_fixture("4", date="2026-02-01", amount="42.50", description="New B")
    result = runner.invoke(app, ["duplicates", "--from", "2026-02-01", "--to", "2026-02-28"])
    assert result.exit_code == 0
    assert "New A" in result.output
    assert "New B" in result.output
    assert "Old A" not in result.output


def test_duplicates_invalid_match_field_errors(tmp_storage):
    result = runner.invoke(app, ["duplicates", "--match-field", "weekday"])
    assert result.exit_code == 1
    assert "Invalid --match-field value" in result.output


# --- CLI: split ---

def test_split_noninteractive_two_parts(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    result = runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    assert result.exit_code == 0
    expenses = read_expenses()
    children = [e for e in expenses if e.get("split_id") == "1"]
    assert len(children) == 2
    amounts = {e["category"]: float(e["amount"]) for e in children}
    assert amounts["food"] == pytest.approx(-60.0)
    assert amounts["transport"] == pytest.approx(-40.0)
    for c in children:
        assert c["split_id"] == "1"
        assert c["note"] == "split from #1"


def test_split_noninteractive_remainder(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    result = runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport", "--yes"])
    assert result.exit_code == 0
    children = [e for e in read_expenses() if e.get("split_id") == "1"]
    amounts = {e["category"]: float(e["amount"]) for e in children}
    assert amounts["food"] == pytest.approx(-60.0)
    assert amounts["transport"] == pytest.approx(-40.0)


def test_split_noninteractive_percent(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    result = runner.invoke(app, ["split", "1", "--part", "food:60%", "--part", "transport", "--yes"])
    assert result.exit_code == 0
    children = [e for e in read_expenses() if e.get("split_id") == "1"]
    amounts = {e["category"]: float(e["amount"]) for e in children}
    assert amounts["food"] == pytest.approx(-60.0)
    assert amounts["transport"] == pytest.approx(-40.0)


def test_split_amounts_exceed_parent(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    result = runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:50", "--yes"])
    assert result.exit_code == 1
    assert not any(e.get("split_id") for e in read_expenses())


def test_split_no_remainder_and_gap(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    result = runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:30", "--yes"])
    assert result.exit_code == 1
    assert not any(e.get("split_id") for e in read_expenses())


def test_split_expense_not_found(tmp_storage):
    result = runner.invoke(app, ["split", "999", "--part", "food:50", "--part", "transport:50", "--yes"])
    assert result.exit_code == 1


def test_split_already_a_parent(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    result = runner.invoke(app, ["split", "1", "--part", "food:50", "--part", "housing:50", "--yes"])
    assert result.exit_code == 1


def test_split_already_a_child(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    children = [e for e in read_expenses() if e.get("split_id") == "1"]
    child_id = int(children[0]["id"])
    result = runner.invoke(app, ["split", str(child_id), "--part", "food:30", "--part", "transport:30", "--yes"])
    assert result.exit_code == 1


def test_split_children_inherit_parent_fields(tmp_storage):
    runner.invoke(app, ["add", "--counterparty", "ing", "--iban", "NL01INGB0001234567", "--category", "overig", "--", "-100.00", "Betaling"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    children = [e for e in read_expenses() if e.get("split_id") == "1"]
    for c in children:
        assert c["counterparty"] == "ing"
        assert c["iban"] == "NL01INGB0001234567"
        assert c["description"] == "Betaling"
        assert c["date"] == read_expenses()[0]["date"]


def test_insights_excludes_split_parent(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    result = runner.invoke(app, ["insights"])
    assert result.exit_code == 0
    assert "food" in result.output
    assert "transport" in result.output
    assert "overig" not in result.output


def test_insights_split_totals_correct(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    result = runner.invoke(app, ["insights"])
    assert "60.00" in result.output
    assert "40.00" in result.output


def test_insights_trend_excludes_split_parent(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--date", "2026-01-15", "--", "-100.00", "Credit card"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    result = runner.invoke(app, ["insights", "--trend", "--from", "2026-01-01", "--to", "2026-01-31"])
    assert result.exit_code == 0
    assert "food" in result.output
    assert "overig" not in result.output


def test_list_shows_split_marker(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "(split)" in result.output


def test_list_shows_child_arrow(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    result = runner.invoke(app, ["list"])
    assert "↳" in result.output


def test_list_id_shows_children_of_split_parent(tmp_storage):
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    result = runner.invoke(app, ["list", "--id", "1"])
    assert result.exit_code == 0
    assert "(split)" in result.output
    assert "↳" in result.output
    assert "food" in result.output
    assert "transport" in result.output


def test_split_dedup_safety(tmp_storage):
    """Source hash on the parent is preserved after splitting."""
    runner.invoke(app, ["add", "--category", "overig", "--", "-100.00", "Credit card"])
    # Manually set a source_hash on the parent to simulate an imported row
    from expense_cli.storage import update_expense
    update_expense(1, {"source_hash": "abc123"})
    runner.invoke(app, ["split", "1", "--part", "food:60", "--part", "transport:40", "--yes"])
    parent = next(e for e in read_expenses() if int(e["id"]) == 1)
    assert parent["source_hash"] == "abc123"


# --- CLI: insights ---

def test_insights_empty(tmp_storage):
    result = runner.invoke(app, ["insights"])
    assert result.exit_code == 0
    assert "No transactions found" in result.output


def test_insights_groups_by_category(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "groceries"])
    runner.invoke(app, ["add", "15.00", "C", "--category", "transport"])
    result = runner.invoke(app, ["insights"])
    assert result.exit_code == 0
    assert "groceries" in result.output
    assert "transport" in result.output
    assert "30.00" in result.output  # groceries total
    assert "15.00" in result.output  # transport total


def test_insights_shows_percentage(tmp_storage):
    runner.invoke(app, ["add", "75.00", "A", "--category", "groceries"])
    runner.invoke(app, ["add", "25.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["insights"])
    assert "75" in result.output   # 75%
    assert "25" in result.output   # 25%


def test_insights_shows_count(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries"])
    runner.invoke(app, ["add", "10.00", "B", "--category", "groceries"])
    result = runner.invoke(app, ["insights"])
    assert "2" in result.output


def test_insights_sorted_by_amount_descending(tmp_storage):
    runner.invoke(app, ["add", "5.00", "A", "--category", "transport"])
    runner.invoke(app, ["add", "50.00", "B", "--category", "groceries"])
    result = runner.invoke(app, ["insights"])
    assert result.output.index("groceries") < result.output.index("transport")


def test_insights_uncategorized_grouped(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A"])  # no category
    result = runner.invoke(app, ["insights"])
    assert result.exit_code == 0
    assert "10.00" in result.output


def test_insights_filter_by_date(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries", "--date", "2026-01-01"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "groceries", "--date", "2026-03-01"])
    result = runner.invoke(app, ["insights", "--from", "2026-02-01"])
    assert "20.00" in result.output
    assert "10.00" not in result.output


def test_insights_filter_by_month_year_month_format(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "groceries", "--date", "2026-03-10"])
    result = runner.invoke(app, ["insights", "--month", "2026-01"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_insights_filter_by_month_number(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "groceries", "--date", "2026-03-10"])
    result = runner.invoke(app, ["insights", "--month", "1", "--year", "2026"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_insights_month_and_from_are_exclusive(tmp_storage):
    result = runner.invoke(app, ["insights", "--month", "2026-01", "--from", "2026-01-01"])
    assert result.exit_code == 1


def test_list_filter_by_month(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--date", "2026-03-10"])
    result = runner.invoke(app, ["list", "--month", "2026-01"])
    assert "10.00" in result.output
    assert "20.00" not in result.output


def test_insights_by_counterparty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert Heijn"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Albert Heijn"])
    runner.invoke(app, ["add", "15.00", "C", "--counterparty", "NS"])
    result = runner.invoke(app, ["insights", "--by", "counterparty"])
    assert "Albert Heijn" in result.output
    assert "NS" in result.output
    assert "30.00" in result.output
    assert "15.00" in result.output


def _add_expense_row(amount: str, category: str) -> None:
    """Write a raw expense row directly to storage (bypasses CLI argument parsing)."""
    from expense_cli.storage import write_expense
    import datetime
    direction = "out" if float(amount) < 0 else "in"
    write_expense({
        "id": "0",
        "date": "2026-01-01",
        "weekday": "Thursday",
        "time": "",
        "amount": amount,
        "direction": direction,
        "description": f"test {category}",
        "iban": "",
        "counterparty": "",
        "category": category,
        "source_hash": "",
    })


def test_insights_direction_out(tmp_storage):
    """--direction out filters to only expense rows; income rows not shown."""
    _add_expense_row("-50.00", "groceries")
    runner.invoke(app, ["add", "1000.00", "Salary", "--category", "income"])
    result = runner.invoke(app, ["insights", "--direction", "out"])
    assert result.exit_code == 0
    assert "-50.00" in result.output
    assert "1000.00" not in result.output


def test_insights_direction_in(tmp_storage):
    """--direction in filters to only income rows; expense rows not shown."""
    _add_expense_row("-50.00", "groceries")
    runner.invoke(app, ["add", "1000.00", "Salary", "--category", "income"])
    result = runner.invoke(app, ["insights", "--direction", "in"])
    assert result.exit_code == 0
    assert "1000.00" in result.output
    assert "-50.00" not in result.output


def test_insights_out_in_net_columns(tmp_storage):
    """Default shows Out, In, and Net columns in a single table."""
    _add_expense_row("-100.00", "gifts")
    _add_expense_row("30.00", "gifts")
    result = runner.invoke(app, ["insights"])
    assert result.exit_code == 0
    assert "Out" in result.output
    assert "In" in result.output
    assert "Net" in result.output
    assert "-100.00" in result.output
    assert "30.00" in result.output
    assert "-70.00" in result.output  # net effect


def test_insights_direction_out_empty(tmp_storage):
    """--direction out with only income returns no-expenses message."""
    runner.invoke(app, ["add", "1000.00", "Salary", "--category", "income"])
    result = runner.invoke(app, ["insights", "--direction", "out"])
    assert result.exit_code == 0
    assert "No expenses found" in result.output


def test_insights_direction_invalid(tmp_storage):
    result = runner.invoke(app, ["insights", "--direction", "sideways"])
    assert result.exit_code == 1


def test_insights_exclude_category_single(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["insights", "--exclude-category", "food"])
    assert "transport" in result.output
    assert "food" not in result.output


def test_insights_exclude_category_multiple(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    runner.invoke(app, ["add", "30.00", "C", "--category", "health"])
    result = runner.invoke(app, ["insights", "--exclude-category", "food,transport"])
    assert "health" in result.output
    assert "food" not in result.output
    assert "transport" not in result.output


def test_insights_exclude_counterparty_single(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    result = runner.invoke(app, ["insights", "--by", "counterparty", "--exclude-counterparty", "Albert"])
    assert "Shell" in result.output
    assert "Albert" not in result.output


def test_insights_exclude_counterparty_multiple(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    runner.invoke(app, ["add", "30.00", "C", "--counterparty", "Gym"])
    result = runner.invoke(app, ["insights", "--by", "counterparty", "--exclude-counterparty", "Albert,Shell"])
    assert "Gym" in result.output
    assert "Albert" not in result.output
    assert "Shell" not in result.output


def test_insights_exclude_defaults_to_category_when_by_category(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["insights", "--exclude", "food"])
    assert "transport" in result.output
    assert "food" not in result.output


def test_insights_exclude_defaults_to_counterparty_when_by_counterparty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    result = runner.invoke(app, ["insights", "--by", "counterparty", "--exclude", "Albert"])
    assert "Shell" in result.output
    assert "Albert" not in result.output


def test_insights_exclude_and_exclude_counterparty_combined(tmp_storage):
    """--exclude (category) combined with --exclude-counterparty for cross-type filtering."""
    runner.invoke(app, ["add", "10.00", "A", "--category", "food", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport", "--counterparty", "Shell"])
    runner.invoke(app, ["add", "30.00", "C", "--category", "health", "--counterparty", "Shell"])
    result = runner.invoke(app, ["insights", "--exclude", "food", "--exclude-counterparty", "Shell"])
    assert "health" not in result.output
    assert "transport" not in result.output
    assert "food" not in result.output


def test_insights_include_category_single(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    runner.invoke(app, ["add", "30.00", "C", "--category", "health"])
    result = runner.invoke(app, ["insights", "--include-category", "food"])
    assert "food" in result.output
    assert "transport" not in result.output
    assert "health" not in result.output


def test_insights_include_category_multiple(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    runner.invoke(app, ["add", "30.00", "C", "--category", "health"])
    result = runner.invoke(app, ["insights", "--include-category", "food,health"])
    assert "food" in result.output
    assert "health" in result.output
    assert "transport" not in result.output


def test_insights_include_counterparty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    runner.invoke(app, ["add", "30.00", "C", "--counterparty", "Gym"])
    result = runner.invoke(app, ["insights", "--by", "counterparty", "--include-counterparty", "Albert,Shell"])
    assert "Albert" in result.output
    assert "Shell" in result.output
    assert "Gym" not in result.output


def test_insights_include_context_aware_category(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["insights", "--include", "food"])
    assert "food" in result.output
    assert "transport" not in result.output


def test_insights_include_context_aware_counterparty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell"])
    result = runner.invoke(app, ["insights", "--by", "counterparty", "--include", "Albert"])
    assert "Albert" in result.output
    assert "Shell" not in result.output


def test_render_bar_proportional():
    from expense_cli.cli import _render_bar
    assert _render_bar(100.0, 100.0, width=10) == "█" * 10
    assert _render_bar(50.0, 100.0, width=10) == "█" * 5
    assert _render_bar(0.0, 100.0, width=10) == ""


def test_render_bar_zero_max():
    from expense_cli.cli import _render_bar
    assert _render_bar(0.0, 0.0) == ""


def test_insights_no_chart_by_default(tmp_storage):
    runner.invoke(app, ["add", "50.00", "A", "--category", "groceries"])
    result = runner.invoke(app, ["insights"])
    assert result.exit_code == 0
    assert "█" not in result.output


def test_insights_chart_flag_shows_bar(tmp_storage):
    runner.invoke(app, ["add", "50.00", "A", "--category", "groceries"])
    result = runner.invoke(app, ["insights", "--chart"])
    assert result.exit_code == 0
    assert "█" in result.output


def test_insights_bar_chart_proportional(tmp_storage):
    runner.invoke(app, ["add", "80.00", "A", "--category", "groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["insights", "--chart"])
    lines_with_blocks = [l for l in result.output.splitlines() if "█" in l]
    assert len(lines_with_blocks) == 2
    groceries_line = next(l for l in lines_with_blocks if "groceries" in l)
    transport_line = next(l for l in lines_with_blocks if "transport" in l)
    assert groceries_line.count("█") > transport_line.count("█")


# --- CLI: insights sparkline ---

def test_sparkline_empty():
    from expense_cli.cli import _sparkline
    assert _sparkline([]) == ""


def test_sparkline_all_zeros():
    from expense_cli.cli import _sparkline
    result = _sparkline([0.0, 0.0, 0.0])
    assert len(result) == 3
    assert all(c == " " for c in result)


def test_sparkline_uniform():
    from expense_cli.cli import _sparkline
    assert _sparkline([10.0, 10.0, 10.0]) == "███"


def test_sparkline_ascending():
    from expense_cli.cli import _sparkline
    result = _sparkline([0.0, 50.0, 100.0])
    assert len(result) == 3
    assert result[0] <= result[1] <= result[2]


def test_insights_sparkline_shown_when_multi_month(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food", "--date", "2026-02-15"])
    result = runner.invoke(app, ["insights", "--from", "2026-01-01", "--to", "2026-02-28"])
    assert result.exit_code == 0
    assert "Trend" in result.output


def test_insights_no_sparkline_single_month(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food", "--date", "2026-01-10"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food", "--date", "2026-01-20"])
    result = runner.invoke(app, ["insights", "--from", "2026-01-01", "--to", "2026-01-31"])
    assert result.exit_code == 0
    assert "Trend" not in result.output


# --- CLI: insights --trend pivot ---

def test_month_range():
    from expense_cli.cli import _month_range
    assert _month_range("2026-01", "2026-03") == ["2026-01", "2026-02", "2026-03"]
    assert _month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert _month_range("2026-01", "2026-01") == ["2026-01"]
    assert _month_range("", "") == []


def test_insights_trend_shows_months_and_groups(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport", "--date", "2026-02-15"])
    result = runner.invoke(app, ["insights", "--trend", "--from", "2026-01-01", "--to", "2026-02-28"])
    assert result.exit_code == 0
    assert "2026-01" in result.output
    assert "2026-02" in result.output
    assert "food" in result.output
    assert "transport" in result.output


def test_insights_trend_fills_gap_months(tmp_storage):
    """Months with no data in range still appear as columns."""
    runner.invoke(app, ["add", "10.00", "A", "--category", "food", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food", "--date", "2026-03-15"])
    result = runner.invoke(app, ["insights", "--trend", "--from", "2026-01-01", "--to", "2026-03-31"])
    assert "2026-01" in result.output
    assert "2026-02" in result.output  # gap month still shown
    assert "2026-03" in result.output


def test_insights_trend_avg_column(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food", "--date", "2026-02-15"])
    result = runner.invoke(app, ["insights", "--trend", "--from", "2026-01-01", "--to", "2026-02-28"])
    assert "Avg" in result.output


def test_insights_trend_by_counterparty(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--counterparty", "Albert", "--date", "2026-01-15"])
    runner.invoke(app, ["add", "20.00", "B", "--counterparty", "Shell", "--date", "2026-02-15"])
    result = runner.invoke(app, ["insights", "--trend", "--by", "counterparty",
                                  "--from", "2026-01-01", "--to", "2026-02-28"])
    assert result.exit_code == 0
    assert "Albert" in result.output
    assert "Shell" in result.output


def test_insights_subcategories_shown_separately_by_default(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food/restaurant"])
    result = runner.invoke(app, ["insights"])
    # Parent row shown bold, subcategories indented (e.g. "  groceries", "  restaurant")
    assert "food" in result.output
    assert "groceries" in result.output
    assert "restaurant" in result.output


def test_insights_rollup_merges_subcategories_and_sums(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food/restaurant"])
    result = runner.invoke(app, ["insights", "--rollup"])
    assert "food" in result.output
    assert "food/groceries" not in result.output
    assert "food/restaurant" not in result.output
    assert "30.00" in result.output


def test_insights_rollup_plain_categories_unaffected(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["insights", "--rollup"])
    assert "groceries" in result.output
    assert "transport" in result.output


def test_insights_exclude_with_parent_excludes_subcategories(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "transport"])
    result = runner.invoke(app, ["insights", "--exclude", "food"])
    assert "food" not in result.output
    assert "transport" in result.output


def test_insights_category_filter_matches_subcategories(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food/restaurant"])
    runner.invoke(app, ["add", "99.00", "C", "--category", "transport"])
    result = runner.invoke(app, ["insights", "--category", "food"])
    assert "groceries" in result.output
    assert "restaurant" in result.output
    assert "transport" not in result.output


def test_insights_category_filter_exact_subcategory(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food/restaurant"])
    result = runner.invoke(app, ["insights", "--category", "food/groceries"])
    # Only food/groceries matches; shown as parent "food" + child "  groceries"
    assert "groceries" in result.output
    assert "restaurant" not in result.output


def test_insights_category_filter_combined_with_rollup(tmp_storage):
    runner.invoke(app, ["add", "10.00", "A", "--category", "food/groceries"])
    runner.invoke(app, ["add", "20.00", "B", "--category", "food/restaurant"])
    runner.invoke(app, ["add", "99.00", "C", "--category", "transport"])
    result = runner.invoke(app, ["insights", "--category", "food", "--rollup"])
    assert "food" in result.output
    assert "transport" not in result.output
    assert "30.00" in result.output


def test_insights_min_hides_small_groups(tmp_storage):
    _add_expense_row("-5.00", "food")
    _add_expense_row("-200.00", "housing")
    result = runner.invoke(app, ["insights", "--min", "10"])
    assert "housing" in result.output
    assert "food" not in result.output


def test_insights_max_hides_large_groups(tmp_storage):
    _add_expense_row("-5.00", "food")
    _add_expense_row("-200.00", "housing")
    result = runner.invoke(app, ["insights", "--max", "10"])
    assert "food" in result.output
    assert "housing" not in result.output


def test_insights_min_net_effect(tmp_storage):
    # out=100, in=90 → net abs = 10
    _add_expense_row("-100.00", "gifts")
    _add_expense_row("90.00", "gifts")
    result_hidden = runner.invoke(app, ["insights", "--min", "15"])
    assert "gifts" not in result_hidden.output
    result_shown = runner.invoke(app, ["insights", "--min", "5"])
    assert "gifts" in result_shown.output


def test_insights_trend_min_filter(tmp_storage):
    _add_expense_row("-5.00", "food")
    _add_expense_row("-200.00", "housing")
    result = runner.invoke(app, ["insights", "--trend", "--min", "10"])
    assert "housing" in result.output
    assert "food" not in result.output


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


# --- CLI: reapply ---

def test_reapply_identifies_missing_counterparty(tmp_storage):
    (tmp_storage / "counterparties.toml").write_text(
        '[[counterparty]]\ndescription_contains = "spotify"\nname = "Spotify"\n',
        encoding="utf-8",
    )
    runner.invoke(app, ["add", "9.99", "SPOTIFY PREMIUM"])
    result = runner.invoke(app, ["reapply"])
    assert result.exit_code == 0
    assert read_expenses()[0]["counterparty"] == "Spotify"


def test_reapply_categorizes_missing_category(tmp_storage):
    (tmp_storage / "categories.toml").write_text(
        '[[rules]]\ncounterparty = "Spotify"\ncategory = "subscriptions"\n',
        encoding="utf-8",
    )
    runner.invoke(app, ["add", "9.99", "SPOTIFY PREMIUM", "--counterparty", "Spotify"])
    result = runner.invoke(app, ["reapply"])
    assert result.exit_code == 0
    assert read_expenses()[0]["category"] == "subscriptions"


def test_reapply_resolves_both(tmp_storage):
    (tmp_storage / "counterparties.toml").write_text(
        '[[counterparty]]\ndescription_contains = "spotify"\nname = "Spotify"\n',
        encoding="utf-8",
    )
    (tmp_storage / "categories.toml").write_text(
        '[[rules]]\ncounterparty = "Spotify"\ncategory = "subscriptions"\n',
        encoding="utf-8",
    )
    runner.invoke(app, ["add", "9.99", "SPOTIFY PREMIUM"])
    result = runner.invoke(app, ["reapply"])
    assert result.exit_code == 0
    expense = read_expenses()[0]
    assert expense["counterparty"] == "Spotify"
    assert expense["category"] == "subscriptions"


def test_reapply_skips_already_reviewed(tmp_storage):
    (tmp_storage / "counterparties.toml").write_text(
        '[[counterparty]]\ndescription_contains = "spotify"\nname = "Spotify"\n',
        encoding="utf-8",
    )
    runner.invoke(app, ["add", "9.99", "SPOTIFY PREMIUM", "--counterparty", "Spotify", "--category", "subscriptions"])
    runner.invoke(app, ["reapply"])
    expense = read_expenses()[0]
    assert expense["counterparty"] == "Spotify"
    assert expense["category"] == "subscriptions"


def test_reapply_nothing_to_do(tmp_storage):
    runner.invoke(app, ["add", "9.99", "Spotify", "--counterparty", "Spotify", "--category", "subscriptions"])
    result = runner.invoke(app, ["reapply"])
    assert result.exit_code == 0
    assert "Nothing" in result.output or "0" in result.output


def test_reapply_no_rules_match(tmp_storage):
    runner.invoke(app, ["add", "9.99", "UNKNOWN VENDOR"])
    result = runner.invoke(app, ["reapply"])
    assert result.exit_code == 0
    expense = read_expenses()[0]
    assert expense["counterparty"] == ""
    assert expense["category"] == ""


def test_reapply_does_not_overwrite_existing_counterparty(tmp_storage):
    """Expense already has a counterparty — reapply must not overwrite it even if a rule would match."""
    (tmp_storage / "counterparties.toml").write_text(
        '[[counterparty]]\ndescription_contains = "spotify"\nname = "Spotify Auto"\n',
        encoding="utf-8",
    )
    runner.invoke(app, ["add", "9.99", "SPOTIFY PREMIUM", "--counterparty", "Spotify Manual"])
    runner.invoke(app, ["reapply"])
    assert read_expenses()[0]["counterparty"] == "Spotify Manual"


def test_reapply_shows_table_of_updated_expenses(tmp_storage):
    (tmp_storage / "counterparties.toml").write_text(
        '[[counterparty]]\ndescription_contains = "spotify"\nname = "Spotify"\n',
        encoding="utf-8",
    )
    (tmp_storage / "categories.toml").write_text(
        '[[rules]]\ncounterparty = "Spotify"\ncategory = "subscriptions"\n',
        encoding="utf-8",
    )
    runner.invoke(app, ["add", "9.99", "SPOTIFY PREMIUM"])
    result = runner.invoke(app, ["reapply"])
    assert result.exit_code == 0
    assert "SPOTIFY PREMIUM" in result.output
    assert "Spotify" in result.output
    assert "subscriptions" in result.output
    assert "1 expense" in result.output


# --- CLI: ratios ---

def test_ratios_basic(tmp_storage):
    runner.invoke(app, ["add", "1000.00", "salary", "--category", "salaris", "--date", "2025-11-15"])
    runner.invoke(app, ["add", "200.00", "invest", "--category", "investeren", "--date", "2025-11-20"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "20.0%" in result.output


def test_ratios_zero_denominator_shows_dash(tmp_storage):
    runner.invoke(app, ["add", "200.00", "invest", "--category", "investeren", "--date", "2025-11-20"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "—" in result.output


def test_ratios_prefix_match(tmp_storage):
    runner.invoke(app, ["add", "1000.00", "salary", "--category", "salaris/bonus", "--date", "2025-11-15"])
    runner.invoke(app, ["add", "250.00", "etf", "--category", "investeren/etf", "--date", "2025-11-20"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "25.0%" in result.output


def test_ratios_multiple_numerators(tmp_storage):
    runner.invoke(app, ["add", "1000.00", "salary", "--category", "salaris", "--date", "2025-11-15"])
    runner.invoke(app, ["add", "200.00", "etf", "--category", "investeren", "--date", "2025-11-20"])
    runner.invoke(app, ["add", "100.00", "pension", "--category", "pensioen", "--date", "2025-11-22"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--numerator", "pensioen",
                                 "--denominator", "salaris",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "30.0%" in result.output


def test_ratios_direction_independent(tmp_storage):
    # When each side has a consistent sign, the displayed ratio stays positive.
    runner.invoke(app, ["add", "1000.00", "salary", "--category", "salaris", "--date", "2025-11-15"])
    runner.invoke(app, ["add", "200.00", "invest", "--category", "investeren", "--date", "2025-11-20"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "20.0%" in result.output


def test_ratios_sum_signed_amounts_before_abs(tmp_storage):
    runner.invoke(app, ["add", "1000.00", "salary", "--category", "salaris", "--date", "2025-12-15"])
    runner.invoke(app, ["add", "--category", "investeren", "--date", "2025-12-20", "--", "-420.00", "invest out"])
    runner.invoke(app, ["add", "250.00", "invest refund", "--category", "investeren/crypto", "--date", "2025-12-21"])
    runner.invoke(app, ["add", "--category", "investeren/fonds", "--date", "2025-12-22", "--", "-40.00", "funds fee"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--from", "2025-12-01", "--to", "2025-12-31"])
    assert result.exit_code == 0
    assert "21.0%" in result.output


def test_ratios_label_displayed(tmp_storage):
    runner.invoke(app, ["add", "1000.00", "salary", "--category", "salaris", "--date", "2025-11-15"])
    runner.invoke(app, ["add", "200.00", "invest", "--category", "investeren", "--date", "2025-11-20"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--label", "inv_rate",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "inv_rate" in result.output


def test_ratios_from_to(tmp_storage):
    runner.invoke(app, ["add", "1000.00", "salary", "--category", "salaris", "--date", "2025-11-15"])
    runner.invoke(app, ["add", "200.00", "invest", "--category", "investeren", "--date", "2025-11-20"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "2025-11" in result.output
    assert "20.0%" in result.output


def test_ratios_over_100_percent_shown(tmp_storage):
    runner.invoke(app, ["add", "100.00", "salary", "--category", "salaris", "--date", "2025-11-15"])
    runner.invoke(app, ["add", "200.00", "invest", "--category", "investeren", "--date", "2025-11-20"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "200.0%" in result.output


def test_ratios_exclude_subcategory(tmp_storage):
    runner.invoke(app, ["add", "1000.00", "salary",   "--category", "salaris",              "--date", "2025-11-15"])
    runner.invoke(app, ["add",  "200.00", "etf",      "--category", "investeren/etf",        "--date", "2025-11-20"])
    runner.invoke(app, ["add",  "100.00", "donation", "--category", "investeren/donation",   "--date", "2025-11-21"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--exclude", "investeren/donation",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "20.0%" in result.output   # 200/1000, donation excluded


def test_ratios_exclude_does_not_affect_unrelated_categories(tmp_storage):
    runner.invoke(app, ["add", "1000.00", "salary", "--category", "salaris",    "--date", "2025-11-15"])
    runner.invoke(app, ["add",  "200.00", "etf",    "--category", "investeren", "--date", "2025-11-20"])
    result = runner.invoke(app, ["ratios", "--numerator", "investeren", "--denominator", "salaris",
                                 "--exclude", "investeren/donation",
                                 "--from", "2025-11-01", "--to", "2025-11-30"])
    assert result.exit_code == 0
    assert "20.0%" in result.output   # nothing was actually excluded
