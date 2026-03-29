from pathlib import Path
from expense_cli.toml_store import read_toml, write_toml_array

COUNTERPARTIES_PATH = Path.home() / ".expense_cli" / "counterparties.toml"

_HEADER = """\
# Counterparty identification rules for expense_cli.
#
# Rules are evaluated in order; the first match wins.
# Each entry has a name and one or both matchers:
#
#   iban                 — exact match on counterparty IBAN (case-insensitive)
#   description_contains — substring match against the transaction description (case-insensitive)"""

_FIELD_ORDER = ["name", "iban", "description_contains"]


def load_counterparties() -> list[dict]:
    return read_toml(COUNTERPARTIES_PATH).get("counterparty", [])


def identify(iban: str, description: str, counterparties: list[dict]) -> str:
    """Return normalized counterparty name, or empty string if no rule matches.

    For each entry, tries iban first then description_contains.
    Entries are evaluated in order; first match wins.
    """
    iban_lower = iban.lower()
    description_lower = description.lower()

    for cp in counterparties:
        if "iban" in cp and iban_lower and cp["iban"].lower() == iban_lower:
            return cp["name"]
        if "description_contains" in cp and cp["description_contains"].lower() in description_lower:
            return cp["name"]

    return ""


def rule_exists(name: str) -> bool:
    """Return True if any counterparty entry with this name exists."""
    return any(cp.get("name", "").lower() == name.lower() for cp in load_counterparties())


def matcher_exists(name: str, matcher_type: str) -> bool:
    """Return True if the entry for *name* already has *matcher_type* set."""
    for cp in load_counterparties():
        if cp.get("name", "").lower() == name.lower() and matcher_type in cp:
            return True
    return False


def save_counterparty_rule(name: str, matcher_type: str, matcher_value: str) -> None:
    """Add a matcher to a counterparty entry.

    - If an entry with *name* already exists, the matcher field is added to it
      (keeping any other matchers intact).
    - If no entry with *name* exists, a new entry is appended.
    """
    entries = load_counterparties()
    name_lower = name.lower()

    for entry in entries:
        if entry.get("name", "").lower() == name_lower:
            entry[matcher_type] = matcher_value
            write_toml_array(COUNTERPARTIES_PATH, "counterparty", entries, _HEADER, _FIELD_ORDER)
            return

    # No existing entry — append a new one
    entries.append({"name": name, matcher_type: matcher_value})
    write_toml_array(COUNTERPARTIES_PATH, "counterparty", entries, _HEADER, _FIELD_ORDER)


def edit_counterparty_rule(
    name: str,
    new_name: str | None = None,
    iban: str | None = None,
    description_contains: str | None = None,
) -> bool:
    """Edit fields on an existing counterparty entry. Returns False if not found."""
    entries = load_counterparties()
    for entry in entries:
        if entry.get("name", "").lower() == name.lower():
            if new_name is not None:
                entry["name"] = new_name
            if iban is not None:
                entry["iban"] = iban
            if description_contains is not None:
                entry["description_contains"] = description_contains
            write_toml_array(COUNTERPARTIES_PATH, "counterparty", entries, _HEADER, _FIELD_ORDER)
            return True
    return False
