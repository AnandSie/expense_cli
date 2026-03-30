from pathlib import Path
from expense_cli.toml_store import read_toml, write_toml_array

CATEGORIES_PATH = Path.home() / ".expense_cli" / "categories.toml"

_HEADER = """\
# Categorization rules for expense_cli.
#
# Rules are evaluated in order; the first match wins.
# Each rule matches on the normalized counterparty name (set by counterparties.toml)
# and assigns a category."""

_FIELD_ORDER = ["counterparty", "category"]

# TODO: expand the dict type
def load_rules() -> list[dict]:
    return read_toml(CATEGORIES_PATH).get("rules", [])


def categorize(counterparty: str, rules: list[dict]) -> str:
    """Return the first matching category, or empty string if no rule matches."""
    counterparty_lower = counterparty.lower()
    for rule in rules:
        if rule.get("counterparty", "").lower() == counterparty_lower:
            return rule["category"]
    return ""


def category_rule_exists(counterparty: str) -> bool:
    """Return True if a category rule for this counterparty already exists."""
    return any(r.get("counterparty", "").lower() == counterparty.lower() for r in load_rules())


def save_category_rule(counterparty: str, category: str) -> None:
    """Upsert a category rule.

    - If a rule for *counterparty* already exists, its category is updated.
    - Otherwise a new rule is appended.
    """
    rules = load_rules()
    cp_lower = counterparty.lower()

    for rule in rules:
        if rule.get("counterparty", "").lower() == cp_lower:
            rule["category"] = category
            write_toml_array(CATEGORIES_PATH, "rules", rules, _HEADER, _FIELD_ORDER)
            return

    rules.append({"counterparty": counterparty, "category": category})
    write_toml_array(CATEGORIES_PATH, "rules", rules, _HEADER, _FIELD_ORDER)


def edit_category_rule(
    counterparty: str,
    new_counterparty: str | None = None,
    category: str | None = None,
) -> bool:
    """Edit an existing category rule. Returns False if not found."""
    rules = load_rules()
    for rule in rules:
        if rule.get("counterparty", "").lower() == counterparty.lower():
            if new_counterparty is not None:
                rule["counterparty"] = new_counterparty
            if category is not None:
                rule["category"] = category
            write_toml_array(CATEGORIES_PATH, "rules", rules, _HEADER, _FIELD_ORDER)
            return True
    return False
