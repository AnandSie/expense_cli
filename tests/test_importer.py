import csv
import pytest
from expense_cli.importer import parse_amount, parse_date, parse_time, _extract_field, read_bank_file


# --- parse_amount ---

def test_parse_amount_dot_decimal():
    assert parse_amount("1234.56", ".") == 1234.56


def test_parse_amount_comma_decimal():
    assert parse_amount("1.234,56", ",") == 1234.56


def test_parse_amount_negative():
    assert parse_amount("-42.50", ".") == -42.50


def test_parse_amount_strips_whitespace():
    assert parse_amount("  10.00  ", ".") == 10.0


# --- parse_date ---

def test_parse_date_iso():
    assert parse_date("2026-01-03", "%Y-%m-%d") == "2026-01-03"


def test_parse_date_compact():
    assert parse_date("20260103", "%Y%m%d") == "2026-01-03"


def test_parse_date_german():
    assert parse_date("03.01.2026", "%d.%m.%Y") == "2026-01-03"


def test_parse_date_abn():
    assert parse_date("20260103", "%Y%m%d") == "2026-01-03"


# --- parse_time ---

def test_parse_time_hhmm():
    assert parse_time("14:30", "%H:%M") == "14:30:00"


def test_parse_time_hhmmss():
    assert parse_time("14:30:45", "%H:%M:%S") == "14:30:45"


def test_parse_time_compact():
    assert parse_time("143045", "%H%M%S") == "14:30:45"


def test_parse_time_midnight():
    assert parse_time("00:00", "%H:%M") == "00:00:00"


# --- _extract_field ---

def test_extract_field_string():
    assert _extract_field("IBAN", {"IBAN": "NL91ABNA0417164300"}) == "NL91ABNA0417164300"


def test_extract_field_string_missing_column():
    assert _extract_field("IBAN", {}) == ""


def test_extract_field_dict_direct_column():
    assert _extract_field({"column": "IBAN"}, {"IBAN": "NL91ABNA0417164300"}) == "NL91ABNA0417164300"


def test_extract_field_dict_pattern_no_capture_group():
    raw = {"Desc": "Payment NL91ABNA0417164300 ref"}
    result = _extract_field({"from_column": "Desc", "pattern": r"[A-Z]{2}\d{2}[A-Z0-9]+"}, raw)
    assert result == "NL91ABNA0417164300"


def test_extract_field_dict_pattern_capture_group():
    raw = {"Desc": "Auftraggeber: Albert Heijn  extra text"}
    result = _extract_field({"from_column": "Desc", "pattern": r"Auftraggeber:\s*(.+?)(?:\s{2,}|$)"}, raw)
    assert result == "Albert Heijn"


def test_extract_field_pattern_no_match():
    raw = {"Desc": "no iban here"}
    assert _extract_field({"from_column": "Desc", "pattern": r"[A-Z]{2}\d{2}[A-Z0-9]+"}, raw) == ""


def test_extract_field_dict_column_fallback_to_pattern(tmp_path):
    # column is empty, should fall back to pattern
    raw = {"IBAN": "", "Desc": "Payment NL91ABNA0417164300 done"}
    result = _extract_field({"column": "IBAN", "from_column": "Desc", "pattern": r"[A-Z]{2}\d{2}[A-Z0-9]+"}, raw)
    assert result == "NL91ABNA0417164300"


# --- _extract_field: extract_iban_from ---

def test_extract_iban_from_single_match():
    raw = {"Desc": "Betaling aan NL91ABNA0417164300 voor huur"}
    result = _extract_field({"extract_iban_from": "Desc"}, raw)
    assert result == "NL91ABNA0417164300"


def test_extract_iban_from_no_match():
    raw = {"Desc": "geen iban in deze tekst"}
    result = _extract_field({"extract_iban_from": "Desc"}, raw)
    assert result == ""


def test_extract_iban_from_multiple_matches_returns_empty():
    raw = {"Desc": "Van NL91ABNA0417164300 naar NL02ABNA0123456789"}
    result = _extract_field({"extract_iban_from": "Desc"}, raw)
    assert result == ""


def test_extract_iban_from_column_wins_when_present():
    # column has a value — extract_iban_from should not be tried
    raw = {"IBAN": "NL91ABNA0417164300", "Desc": "Van NL02ABNA0123456789 betaling"}
    result = _extract_field({"column": "IBAN", "extract_iban_from": "Desc"}, raw)
    assert result == "NL91ABNA0417164300"


def test_extract_iban_from_used_when_column_empty():
    # column is empty — fall back to extract_iban_from
    raw = {"IBAN": "", "Desc": "Betaling NL91ABNA0417164300 ref"}
    result = _extract_field({"column": "IBAN", "extract_iban_from": "Desc"}, raw)
    assert result == "NL91ABNA0417164300"


# --- read_bank_file (CSV) ---

SIMPLE_CONFIG = {
    "bank": {"date_format": "%Y-%m-%d", "decimal_separator": ".", "delimiter": ",", "encoding": "utf-8"},
    "mapping": {
        "date": "Date", "amount": "Amount", "description": "Description",
        "iban": "IBAN", "counterparty": "Counterparty",
    },
}


def test_read_csv_basic(tmp_path):
    f = tmp_path / "bank.csv"
    with f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Date", "Amount", "Description", "IBAN", "Counterparty"])
        w.writeheader()
        w.writerow({"Date": "2026-01-03", "Amount": "42.50", "Description": "Groceries",
                    "IBAN": "NL91ABNA0417164300", "Counterparty": "Albert Heijn"})
    rows = read_bank_file(str(f), SIMPLE_CONFIG)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-01-03"
    assert rows[0]["amount"] == "42.50"
    assert rows[0]["counterparty"] == "Albert Heijn"
    assert rows[0]["iban"] == "NL91ABNA0417164300"


def test_read_csv_multiple_rows(tmp_path):
    f = tmp_path / "bank.csv"
    with f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Date", "Amount", "Description", "IBAN", "Counterparty"])
        w.writeheader()
        w.writerow({"Date": "2026-01-01", "Amount": "10.00", "Description": "A", "IBAN": "", "Counterparty": ""})
        w.writerow({"Date": "2026-01-02", "Amount": "20.00", "Description": "B", "IBAN": "", "Counterparty": ""})
    rows = read_bank_file(str(f), SIMPLE_CONFIG)
    assert len(rows) == 2


def test_read_tab_delimited(tmp_path):
    f = tmp_path / "bank.tab"
    with f.open("w", newline="", encoding="utf-8") as fh:
        fh.write("Date\tAmount\tDescription\n")
        fh.write("2026-01-03\t42.50\tGroceries\n")
    config = {
        "bank": {"date_format": "%Y-%m-%d", "decimal_separator": ".", "encoding": "utf-8"},
        "mapping": {"date": "Date", "amount": "Amount", "description": "Description"},
    }
    rows = read_bank_file(str(f), config)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-01-03"
    assert rows[0]["amount"] == "42.50"


def test_read_csv_with_time(tmp_path):
    f = tmp_path / "bank.csv"
    with f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Date", "Time", "Amount", "Description"])
        w.writeheader()
        w.writerow({"Date": "2026-01-03", "Time": "14:30", "Amount": "10.00", "Description": "Test"})
    config = {
        "bank": {"date_format": "%Y-%m-%d", "time_format": "%H:%M", "decimal_separator": ".", "delimiter": ",", "encoding": "utf-8"},
        "mapping": {"date": "Date", "time": "Time", "amount": "Amount", "description": "Description"},
    }
    rows = read_bank_file(str(f), config)
    assert rows[0]["time"] == "14:30:00"


def test_read_csv_no_time_mapping(tmp_path):
    f = tmp_path / "bank.csv"
    with f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Date", "Amount"])
        w.writeheader()
        w.writerow({"Date": "2026-01-01", "Amount": "5.00"})
    config = {
        "bank": {"date_format": "%Y-%m-%d", "decimal_separator": ".", "delimiter": ",", "encoding": "utf-8"},
        "mapping": {"date": "Date", "amount": "Amount"},
    }
    rows = read_bank_file(str(f), config)
    assert rows[0]["time"] == ""


def test_read_csv_no_optional_mapping(tmp_path):
    f = tmp_path / "bank.csv"
    with f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["Date", "Amount"])
        w.writeheader()
        w.writerow({"Date": "2026-01-01", "Amount": "5.00"})
    config = {
        "bank": {"date_format": "%Y-%m-%d", "decimal_separator": ".", "delimiter": ",", "encoding": "utf-8"},
        "mapping": {"date": "Date", "amount": "Amount"},
    }
    rows = read_bank_file(str(f), config)
    assert rows[0]["iban"] == ""
    assert rows[0]["counterparty"] == ""
