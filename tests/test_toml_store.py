from pathlib import Path
import pytest

from expense_cli.toml_store import read_toml, write_toml_array, write_bank_config


# ---------------------------------------------------------------------------
# read_toml
# ---------------------------------------------------------------------------

def test_read_toml_missing_file_returns_empty_dict(tmp_path):
    assert read_toml(tmp_path / "nonexistent.toml") == {}


def test_read_toml_parses_correctly(tmp_path):
    f = tmp_path / "data.toml"
    f.write_text('[[item]]\nname = "foo"\n', encoding="utf-8")
    result = read_toml(f)
    assert result == {"item": [{"name": "foo"}]}


def test_read_toml_roundtrips_written_array(tmp_path):
    path = tmp_path / "data.toml"
    entries = [{"name": "alice", "iban": "NL01"}, {"name": "bob", "description_contains": "bakery"}]
    write_toml_array(path, "counterparty", entries)
    result = read_toml(path)
    assert result["counterparty"] == entries


# ---------------------------------------------------------------------------
# write_toml_array
# ---------------------------------------------------------------------------

def test_write_toml_array_creates_parent_dirs(tmp_path):
    path = tmp_path / "sub" / "dir" / "out.toml"
    write_toml_array(path, "rules", [{"counterparty": "foo", "category": "bar"}])
    assert path.exists()


def test_write_toml_array_includes_header(tmp_path):
    path = tmp_path / "out.toml"
    write_toml_array(path, "rules", [], header="# my header")
    assert "# my header" in path.read_text(encoding="utf-8")


def test_write_toml_array_respects_field_order(tmp_path):
    path = tmp_path / "out.toml"
    entry = {"category": "groceries", "counterparty": "rewe"}
    write_toml_array(path, "rules", [entry], field_order=["counterparty", "category"])
    content = path.read_text(encoding="utf-8")
    assert content.index("counterparty") < content.index("category")


def test_write_toml_array_escapes_quotes_in_values(tmp_path):
    path = tmp_path / "out.toml"
    write_toml_array(path, "item", [{"name": 'say "hello"'}])
    result = read_toml(path)
    assert result["item"][0]["name"] == 'say "hello"'


def test_write_toml_array_overwrites_existing_file(tmp_path):
    path = tmp_path / "out.toml"
    write_toml_array(path, "item", [{"name": "first"}])
    write_toml_array(path, "item", [{"name": "second"}])
    result = read_toml(path)
    assert len(result["item"]) == 1
    assert result["item"][0]["name"] == "second"


def test_write_toml_array_empty_entries_produces_only_header(tmp_path):
    path = tmp_path / "out.toml"
    write_toml_array(path, "rules", [], header="# header")
    result = read_toml(path)
    assert result.get("rules", []) == []


def test_write_toml_array_sort_key_sorts_entries(tmp_path):
    path = tmp_path / "out.toml"
    entries = [{"name": "zebra"}, {"name": "alpha"}, {"name": "mango"}]
    write_toml_array(path, "item", entries, sort_key="name")
    result = read_toml(path)
    assert [e["name"] for e in result["item"]] == ["alpha", "mango", "zebra"]


def test_write_toml_array_sort_key_is_case_insensitive(tmp_path):
    path = tmp_path / "out.toml"
    entries = [{"name": "Zebra"}, {"name": "alpha"}, {"name": "Mango"}]
    write_toml_array(path, "item", entries, sort_key="name")
    result = read_toml(path)
    assert [e["name"] for e in result["item"]] == ["alpha", "Mango", "Zebra"]


def test_write_toml_array_no_sort_key_preserves_order(tmp_path):
    path = tmp_path / "out.toml"
    entries = [{"name": "zebra"}, {"name": "alpha"}]
    write_toml_array(path, "item", entries)
    result = read_toml(path)
    assert [e["name"] for e in result["item"]] == ["zebra", "alpha"]


# ---------------------------------------------------------------------------
# _fmt boolean serialization
# ---------------------------------------------------------------------------

def test_fmt_writes_bool_true_as_lowercase_true(tmp_path):
    path = tmp_path / "out.toml"
    write_toml_array(path, "counterparty", [{"name": "x", "manual_category": True}])
    result = read_toml(path)
    assert result["counterparty"][0]["manual_category"] is True


def test_fmt_writes_bool_false_as_lowercase_false(tmp_path):
    path = tmp_path / "out.toml"
    write_toml_array(path, "counterparty", [{"name": "x", "manual_category": False}])
    result = read_toml(path)
    assert result["counterparty"][0]["manual_category"] is False


def test_fmt_bool_written_as_lowercase_literal(tmp_path):
    path = tmp_path / "out.toml"
    write_toml_array(path, "counterparty", [{"name": "x", "manual_category": True}])
    content = path.read_text(encoding="utf-8")
    assert "manual_category = true" in content
    assert "True" not in content


# ---------------------------------------------------------------------------
# write_bank_config
# ---------------------------------------------------------------------------

def test_write_bank_config_roundtrips_string_mapping(tmp_path):
    path = tmp_path / "mybank.toml"
    config = {
        "bank": {"encoding": "utf-8", "date_format": "%Y-%m-%d", "decimal_separator": "."},
        "mapping": {"date": "Date", "amount": "Amount", "iban": "IBAN"},
    }
    write_bank_config(path, config)
    result = read_toml(path)
    assert result["mapping"]["iban"] == "IBAN"
    assert result["bank"]["date_format"] == "%Y-%m-%d"


def test_write_bank_config_roundtrips_dict_mapping(tmp_path):
    path = tmp_path / "mybank.toml"
    config = {
        "bank": {"encoding": "utf-8", "date_format": "%Y-%m-%d", "decimal_separator": "."},
        "mapping": {"date": "Date", "amount": "Amount", "iban": {"extract_iban_from": "Description"}},
    }
    write_bank_config(path, config)
    result = read_toml(path)
    assert result["mapping"]["iban"] == {"extract_iban_from": "Description"}


def test_write_bank_config_roundtrips_combined_dict_mapping(tmp_path):
    path = tmp_path / "mybank.toml"
    config = {
        "bank": {"encoding": "utf-8", "date_format": "%Y-%m-%d", "decimal_separator": "."},
        "mapping": {"date": "Date", "amount": "Amount", "iban": {"column": "IBAN", "extract_iban_from": "Desc"}},
    }
    write_bank_config(path, config)
    result = read_toml(path)
    assert result["mapping"]["iban"] == {"column": "IBAN", "extract_iban_from": "Desc"}


def test_write_bank_config_creates_parent_dirs(tmp_path):
    path = tmp_path / "banks" / "mybank.toml"
    write_bank_config(path, {"mapping": {"date": "Date", "amount": "Amount"}})
    assert path.exists()
