import csv
import hashlib
import json
import re
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from datetime import datetime
from pathlib import Path

import xlrd

BANKS_DIR = Path.home() / ".expense_cli" / "banks"


# TODO: return type of dict is not sufficient for type level strict
def _hash_raw(raw: dict) -> str:
    """Return a short hash of all raw bank row values for deduplication."""
    serialized = json.dumps(raw, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def load_bank_config(bank_name: str) -> dict:
    path = BANKS_DIR / f"{bank_name}.toml"
    if not path.exists():
        raise FileNotFoundError(
            f"No bank config found at {path}\n"
            f"Create it to define the column mapping for '{bank_name}'."
        )
    with path.open("rb") as f:
        return tomllib.load(f)

# TODO: missing type for field_config and dict
def _extract_field(field_config, raw: dict) -> str:
    """Resolve a flexible mapping config to a string value.

    field_config: str (direct column name) or dict with optional keys:
        column      — read directly from this column (tried first)
        from_column — column to search when column absent/empty
        pattern     — regex applied to from_column; first capture group wins, else full match
    raw: {column_name: string_value} for the current row
    """
    if isinstance(field_config, str):
        return raw.get(field_config, "").strip()

    val = ""
    if "column" in field_config:
        val = raw.get(field_config["column"], "").strip()

    if not val and "pattern" in field_config and "from_column" in field_config:
        source = raw.get(field_config["from_column"], "")
        m = re.search(field_config["pattern"], source)
        if m:
            val = (m.group(1) if m.lastindex else m.group(0)).strip()

    return val


def parse_amount(value: str, decimal_separator: str) -> float:
    """Normalize amount string to a float."""
    value = value.strip()
    if decimal_separator == ",":
        value = value.replace(".", "").replace(",", ".")
    return float(value)


def parse_date(value: str, date_format: str) -> str:
    """Parse date string to ISO format YYYY-MM-DD."""
    return datetime.strptime(value.strip(), date_format).date().isoformat()


def parse_time(value: str, time_format: str) -> str:
    """Parse time string to HH:MM:SS."""
    return datetime.strptime(value.strip(), time_format).strftime("%H:%M:%S")


def _read_xls_file(filepath: str, config: dict) -> list[dict]:
    """Parse an Excel 97-2003 (.xls) file into a list of normalized row dicts."""
    bank_cfg = config.get("bank", {})
    mapping = config["mapping"]
    date_format = bank_cfg.get("date_format", "%Y-%m-%d")
    decimal_separator = bank_cfg.get("decimal_separator", ".")

    wb = xlrd.open_workbook(filepath)
    sheet = wb.sheet_by_index(0)

    headers = [str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)]
    col_index = {name: idx for idx, name in enumerate(headers)}

    rows = []
    for row_idx in range(1, sheet.nrows):
        def cell(col_name):
            return sheet.cell(row_idx, col_index[col_name])

        # Date: use xlrd date conversion for date-typed cells, string parse otherwise
        date_cell = cell(mapping["date"])
        if date_cell.ctype == xlrd.XL_CELL_DATE:
            date_val = xlrd.xldate_as_datetime(date_cell.value, wb.datemode).date().isoformat()
        elif date_cell.ctype == xlrd.XL_CELL_NUMBER:
            date_val = parse_date(str(int(date_cell.value)), date_format)
        else:
            date_val = parse_date(str(date_cell.value).strip(), date_format)

        # Amount: use numeric value directly for number-typed cells
        amount_cell = cell(mapping["amount"])
        if amount_cell.ctype == xlrd.XL_CELL_NUMBER:
            amount_val = f"{amount_cell.value:.2f}"
        else:
            amount_val = f"{parse_amount(str(amount_cell.value), decimal_separator):.2f}"

        time_format = bank_cfg.get("time_format")
        raw = {name: str(sheet.cell(row_idx, col_index[name]).value).strip() for name in col_index}
        # TODO instead of hardcoded fieldnames here, maybe we can define and create an class/record/datatype
        row = {
            "date": date_val,
            "time": parse_time(raw[mapping["time"]], time_format) if "time" in mapping and time_format else "",
            "amount": amount_val,
            "description": str(cell(mapping["description"]).value).strip() if "description" in mapping else "",
            "iban": _extract_field(mapping["iban"], raw) if "iban" in mapping else "",
            "counterparty": _extract_field(mapping["counterparty"], raw) if "counterparty" in mapping else "",
            "source_hash": _hash_raw(raw),
        }
        rows.append(row)
    return rows


def read_bank_file(filepath: str, config: dict) -> list[dict]:
    """Parse a bank statement file (CSV, TAB, or XLS) into a list of normalized row dicts."""
    ext = Path(filepath).suffix.lower()

    if ext == ".xls":
        return _read_xls_file(filepath, config)

    bank_cfg = config.get("bank", {})
    mapping = config["mapping"]
    
    # TODO: maybe we can define a type for the bank_cfg such that we know what is in it and then we don't have to use a get based on a static string
    encoding = bank_cfg.get("encoding", "utf-8")
    date_format = bank_cfg.get("date_format", "%Y-%m-%d")
    time_format = bank_cfg.get("time_format")
    decimal_separator = bank_cfg.get("decimal_separator", ".")
    delimiter = "\t" if ext == ".tab" else bank_cfg.get("delimiter", ",")

    rows = []
    with open(filepath, newline="", encoding=encoding) as f:
        for raw in csv.DictReader(f, delimiter=delimiter):
            row = {
                "date": parse_date(raw[mapping["date"]], date_format),
                "time": parse_time(raw[mapping["time"]], time_format) if "time" in mapping and time_format else "",
                "amount": f"{parse_amount(raw[mapping['amount']], decimal_separator):.2f}",
                "description": raw.get(mapping.get("description", ""), "").strip(),
                "iban": _extract_field(mapping["iban"], raw) if "iban" in mapping else "",
                "counterparty": _extract_field(mapping["counterparty"], raw) if "counterparty" in mapping else "",
                "source_hash": _hash_raw(dict(raw)),
            }
            rows.append(row)
    return rows
