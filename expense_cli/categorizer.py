from expense_cli.identifier import (
    COUNTERPARTIES_PATH,
    load_counterparties,
    save_counterparty_rule,
    edit_counterparty_rule,
    remove_category,
    clear_categories,
    set_manual_category,
)

# Re-export COUNTERPARTIES_PATH so tests can monkeypatch expense_cli.categorizer.COUNTERPARTIES_PATH
# and have it take effect in load_counterparties calls via the identifier module.
# (conftest monkeypatches both expense_cli.identifier.COUNTERPARTIES_PATH and this one.)
__all__ = ["categorize", "load_rules", "category_rule_exists", "save_category_rule",
           "edit_category_rule", "delete_category_rule", "clear_category_rules",
           "is_manual_category", "set_manual_category", "COUNTERPARTIES_PATH"]


def load_rules() -> list[dict]:
    """Return counterparty entries that have a category field."""
    return [e for e in load_counterparties() if "category" in e]


def categorize(counterparty: str, rules: list[dict]) -> str:
    """Return the first matching category, or empty string if no rule matches."""
    counterparty_lower = counterparty.lower()
    for rule in rules:
        if rule.get("name", "").lower() == counterparty_lower:
            return rule["category"]
    return ""


def category_rule_exists(counterparty: str) -> bool:
    """Return True if a category rule for this counterparty already exists."""
    return any(
        e.get("name", "").lower() == counterparty.lower() and "category" in e
        for e in load_counterparties()
    )


def save_category_rule(counterparty: str, category: str) -> None:
    """Upsert a category on a counterparty entry.

    - If an entry for *counterparty* already exists, its category is set/updated.
    - Otherwise a new name-only entry is created with just name + category.
    """
    save_counterparty_rule(counterparty, category=category)


def edit_category_rule(
    counterparty: str,
    new_counterparty: str | None = None,
    category: str | None = None,
) -> bool:
    """Edit the category (and optionally rename) on an existing counterparty entry.

    Returns False if not found or no category exists for this counterparty.
    """
    entries = load_counterparties()
    for entry in entries:
        if entry.get("name", "").lower() == counterparty.lower():
            if "category" not in entry and category is None:
                return False
            return edit_counterparty_rule(
                name=counterparty,
                new_name=new_counterparty,
                category=category,
            )
    return False


def delete_category_rule(counterparty: str) -> bool:
    """Remove the category from a counterparty entry.

    If the entry has no matchers after removal, the entry is deleted entirely.
    Returns False if not found or no category was set.
    """
    return remove_category(counterparty)


def is_manual_category(counterparty: str) -> bool:
    """Return True if this counterparty is flagged as always-manual (never auto-categorize)."""
    return any(
        e.get("name", "").lower() == counterparty.lower() and e.get("manual_category") is True
        for e in load_counterparties()
    )


def clear_category_rules() -> int:
    """Remove the category field from all counterparty entries.

    Name-only entries (no matchers) are also removed.
    Returns the count of entries that had a category.
    """
    return clear_categories()
