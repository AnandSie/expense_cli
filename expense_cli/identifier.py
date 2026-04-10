from pathlib import Path
from expense_cli.toml_store import read_toml, write_toml_array

COUNTERPARTIES_PATH = Path.home() / ".expense_cli" / "counterparties.toml"
_LEGACY_CATEGORIES_PATH = Path.home() / ".expense_cli" / "categories.toml"

_HEADER = """\
# Counterparty identification rules for expense_cli.
#
# Rules are evaluated in order; the first match wins.
# Each entry has a name and optionally one or both matchers and/or a category:
#
#   iban                 — exact match on counterparty IBAN (case-insensitive); can be a single string or a list of strings
#   description_contains — substring match against the transaction description (case-insensitive); can be a single string or a list of strings
#   category             — category assigned to this counterparty (optional)"""

_FIELD_ORDER = ["name", "iban", "description_contains", "category"]


def _migrate_categories() -> None:
    """If a legacy categories.toml exists, merge its rules into counterparties.toml and rename it."""
    if not _LEGACY_CATEGORIES_PATH.exists():
        return
    legacy = read_toml(_LEGACY_CATEGORIES_PATH).get("rules", [])
    if not legacy:
        _LEGACY_CATEGORIES_PATH.unlink()
        return
    entries = _load_raw()
    name_index = {e["name"].lower(): e for e in entries}
    for rule in legacy:
        cp_name = rule.get("counterparty", "")
        cat = rule.get("category", "")
        if not cp_name or not cat:
            continue
        if cp_name.lower() in name_index:
            name_index[cp_name.lower()]["category"] = cat
        else:
            entries.append({"name": cp_name, "category": cat})
    write_toml_array(COUNTERPARTIES_PATH, "counterparty", entries, _HEADER, _FIELD_ORDER, sort_key="name")
    _LEGACY_CATEGORIES_PATH.rename(_LEGACY_CATEGORIES_PATH.with_suffix(".toml.bak"))


def _load_raw() -> list[dict]:
    """Read counterparties.toml without triggering migration (used inside migration itself)."""
    return read_toml(COUNTERPARTIES_PATH).get("counterparty", [])


def load_counterparties() -> list[dict]:
    _migrate_categories()
    return _load_raw()


def identify(iban: str, description: str, counterparties: list[dict]) -> str:
    """Return normalized counterparty name, or empty string if no rule matches.

    For each entry, tries iban first then description_contains.
    Entries are evaluated in order; first match wins.
    """
    iban_lower = iban.lower()
    description_lower = description.lower()

    for cp in counterparties:
        iban_val = cp.get("iban")
        if iban_val is not None and iban_lower:
            ibans = [iban_val] if isinstance(iban_val, str) else iban_val
            if any(i.lower() == iban_lower for i in ibans):
                return cp["name"]
        dc = cp.get("description_contains")
        if dc is not None:
            patterns = [dc] if isinstance(dc, str) else dc
            if any(p.lower() in description_lower for p in patterns):
                return cp["name"]

    return ""


def rule_exists(name: str) -> bool:
    """Return True if any counterparty entry with this name exists."""
    return any(cp.get("name", "").lower() == name.lower() for cp in load_counterparties())


def matcher_exists(name: str, matcher_type: str, value: str | None = None) -> bool:
    """Return True if the entry for *name* already has *matcher_type* set.

    For description_contains, if *value* is given, checks whether that specific
    value is already in the list (to block exact duplicates while allowing new patterns).
    Without *value*, checks for presence only (used for iban).
    """
    for cp in load_counterparties():
        if cp.get("name", "").lower() != name.lower():
            continue
        if value is not None and matcher_type in ("description_contains", "iban"):
            val = cp.get(matcher_type)
            if val is None:
                return False
            patterns = [val] if isinstance(val, str) else val
            return value.lower() in [p.lower() for p in patterns]
        return matcher_type in cp
    return False


def save_counterparty_rule(
    name: str,
    matcher_type: str | None = None,
    matcher_value: str | None = None,
    category: str | None = None,
) -> None:
    """Add a matcher and/or category to a counterparty entry.

    - If an entry with *name* already exists, the provided fields are added/updated.
    - If no entry with *name* exists, a new entry is appended.
    """
    entries = load_counterparties()
    name_lower = name.lower()

    for entry in entries:
        if entry.get("name", "").lower() == name_lower:
            if matcher_type in ("description_contains", "iban"):
                existing = entry.get(matcher_type)
                if existing is None:
                    entry[matcher_type] = matcher_value
                elif isinstance(existing, str):
                    entry[matcher_type] = [existing, matcher_value]
                else:
                    entry[matcher_type] = existing + [matcher_value]
            elif matcher_type is not None:
                entry[matcher_type] = matcher_value
            if category is not None:
                entry["category"] = category
            write_toml_array(COUNTERPARTIES_PATH, "counterparty", entries, _HEADER, _FIELD_ORDER, sort_key="name")
            return

    new_entry: dict = {"name": name}
    if matcher_type is not None:
        new_entry[matcher_type] = matcher_value
    if category is not None:
        new_entry["category"] = category
    entries.append(new_entry)
    write_toml_array(COUNTERPARTIES_PATH, "counterparty", entries, _HEADER, _FIELD_ORDER, sort_key="name")


def edit_counterparty_rule(
    name: str,
    new_name: str | None = None,
    iban: str | None = None,
    description_contains: str | None = None,
    category: str | None = None,
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
            if category is not None:
                entry["category"] = category
            write_toml_array(COUNTERPARTIES_PATH, "counterparty", entries, _HEADER, _FIELD_ORDER, sort_key="name")
            return True
    return False


def delete_counterparty_rule(name: str) -> bool:
    """Remove a counterparty entry by name. Returns False if not found."""
    entries = load_counterparties()
    new_entries = [e for e in entries if e.get("name", "").lower() != name.lower()]
    if len(new_entries) == len(entries):
        return False
    write_toml_array(COUNTERPARTIES_PATH, "counterparty", new_entries, _HEADER, _FIELD_ORDER, sort_key="name")
    return True


def clear_counterparty_rules() -> int:
    """Remove all counterparty entries. Returns the number of entries removed."""
    entries = load_counterparties()
    count = len(entries)
    write_toml_array(COUNTERPARTIES_PATH, "counterparty", [], _HEADER, _FIELD_ORDER, sort_key="name")
    return count


def remove_category(name: str) -> bool:
    """Remove the category field from a counterparty entry.

    If the entry has no matchers after removal, removes the entire entry.
    Returns False if the entry is not found or has no category.
    """
    entries = load_counterparties()
    for entry in entries:
        if entry.get("name", "").lower() == name.lower():
            if "category" not in entry:
                return False
            entry.pop("category")
            has_matchers = "iban" in entry or "description_contains" in entry
            if not has_matchers:
                entries = [e for e in entries if e.get("name", "").lower() != name.lower()]
            write_toml_array(COUNTERPARTIES_PATH, "counterparty", entries, _HEADER, _FIELD_ORDER, sort_key="name")
            return True
    return False


def clear_categories() -> int:
    """Remove the category field from all counterparty entries.

    Entries that become name-only (no matchers) are also removed.
    Returns the count of entries that had a category.
    """
    entries = load_counterparties()
    count = sum(1 for e in entries if "category" in e)
    new_entries = []
    for entry in entries:
        entry.pop("category", None)
        has_matchers = "iban" in entry or "description_contains" in entry
        if has_matchers:
            new_entries.append(entry)
    write_toml_array(COUNTERPARTIES_PATH, "counterparty", new_entries, _HEADER, _FIELD_ORDER, sort_key="name")
    return count
