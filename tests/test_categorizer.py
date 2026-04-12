import pytest
from expense_cli.categorizer import (
    categorize,
    category_rule_exists,
    save_category_rule,
    edit_category_rule,
    delete_category_rule,
    clear_category_rules,
    load_rules,
    is_manual_category,
    set_manual_category,
)

# ---------------------------------------------------------------------------
# categorize — pure logic, no file I/O
# Rules now use "name" key (merged counterparty format)
# ---------------------------------------------------------------------------

RULES = [
    {"name": "Landlord BV", "category": "rent"},
    {"name": "Albert Heijn", "category": "groceries"},
    {"name": "Netflix", "category": "subscriptions"},
]


def test_exact_match():
    assert categorize("Albert Heijn", RULES) == "groceries"


def test_case_insensitive():
    assert categorize("albert heijn", RULES) == "groceries"
    assert categorize("ALBERT HEIJN", RULES) == "groceries"


def test_first_match_wins():
    rules = [
        {"name": "Albert Heijn", "category": "groceries"},
        {"name": "Albert Heijn", "category": "food"},
    ]
    assert categorize("Albert Heijn", rules) == "groceries"


def test_no_match_returns_empty_string():
    assert categorize("Unknown Shop", RULES) == ""


def test_empty_counterparty_no_match():
    assert categorize("", RULES) == ""


def test_empty_rules():
    assert categorize("Albert Heijn", []) == ""


def test_partial_name_does_not_match():
    # categorizer does exact match, not substring
    assert categorize("Albert", RULES) == ""


# ---------------------------------------------------------------------------
# category_rule_exists — requires file I/O via tmp_storage
# ---------------------------------------------------------------------------

def test_category_rule_exists_false_when_no_file(tmp_storage):
    assert category_rule_exists("netflix") is False


def test_category_rule_exists_true_after_save(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    assert category_rule_exists("netflix") is True


def test_category_rule_exists_case_insensitive(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    assert category_rule_exists("Netflix") is True


# ---------------------------------------------------------------------------
# save_category_rule
# ---------------------------------------------------------------------------

def test_save_creates_new_rule(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    rules = load_rules()
    assert len(rules) == 1
    assert rules[0]["name"] == "netflix"
    assert rules[0]["category"] == "subscriptions"


def test_save_creates_file_if_missing(tmp_storage):
    # Categories are now stored in counterparties.toml
    path = tmp_storage / "counterparties.toml"
    assert not path.exists()
    save_category_rule("netflix", "subscriptions")
    assert path.exists()


def test_save_updates_existing_rule(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    save_category_rule("netflix", "entertainment")
    rules = load_rules()
    assert len(rules) == 1
    assert rules[0]["category"] == "entertainment"


def test_save_multiple_distinct_rules(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    save_category_rule("rewe", "groceries")
    rules = load_rules()
    assert len(rules) == 2
    assert rules[0]["name"] == "netflix"
    assert rules[1]["name"] == "rewe"


def test_save_preserves_other_rules(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    save_category_rule("rewe", "groceries")
    save_category_rule("netflix", "entertainment")
    rules = load_rules()
    rewe = next(r for r in rules if r["name"] == "rewe")
    assert rewe["category"] == "groceries"


# ---------------------------------------------------------------------------
# edit_category_rule
# ---------------------------------------------------------------------------

def test_edit_returns_false_if_not_found(tmp_storage):
    assert edit_category_rule("nobody", category="food") is False


def test_edit_updates_category(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    assert edit_category_rule("netflix", category="entertainment") is True
    rules = load_rules()
    assert rules[0]["category"] == "entertainment"


def test_edit_renames_counterparty_key(tmp_storage):
    save_category_rule("ns trains", "transport")
    edit_category_rule("ns trains", new_counterparty="ns")
    rules = load_rules()
    assert rules[0]["name"] == "ns"
    assert rules[0]["category"] == "transport"


def test_edit_case_insensitive_lookup(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    assert edit_category_rule("Netflix", category="entertainment") is True


def test_edit_does_not_affect_other_rules(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    save_category_rule("rewe", "groceries")
    edit_category_rule("netflix", category="entertainment")
    rules = load_rules()
    rewe = next(r for r in rules if r["name"] == "rewe")
    assert rewe["category"] == "groceries"


# ---------------------------------------------------------------------------
# delete_category_rule
# ---------------------------------------------------------------------------

def test_delete_returns_false_if_not_found(tmp_storage):
    assert delete_category_rule("nobody") is False


def test_delete_removes_rule(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    assert delete_category_rule("netflix") is True
    assert load_rules() == []


def test_delete_case_insensitive(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    assert delete_category_rule("Netflix") is True
    assert load_rules() == []


def test_delete_leaves_other_rules_intact(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    save_category_rule("rewe", "groceries")
    delete_category_rule("netflix")
    rules = load_rules()
    assert len(rules) == 1
    assert rules[0]["name"] == "rewe"


def test_delete_only_removes_category_field_when_entry_has_matchers(tmp_storage):
    """If the counterparty has an identification matcher, deleting the category
    must NOT remove the entire entry — only the category field."""
    from expense_cli.identifier import save_counterparty_rule, load_counterparties
    save_counterparty_rule("netflix", "description_contains", "netflix", category="subscriptions")
    delete_category_rule("netflix")
    # Entry still exists (for identification) but has no category
    entries = load_counterparties()
    assert len(entries) == 1
    assert "category" not in entries[0]
    assert entries[0]["description_contains"] == "netflix"


# ---------------------------------------------------------------------------
# clear_category_rules
# ---------------------------------------------------------------------------

def test_clear_returns_zero_when_empty(tmp_storage):
    assert clear_category_rules() == 0


def test_clear_removes_all_rules(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    save_category_rule("rewe", "groceries")
    count = clear_category_rules()
    assert count == 2
    assert load_rules() == []


def test_clear_keeps_entries_that_have_matchers(tmp_storage):
    """clear_category_rules must not delete entries that still have identification matchers."""
    from expense_cli.identifier import save_counterparty_rule, load_counterparties
    save_counterparty_rule("netflix", "description_contains", "netflix", category="subscriptions")
    clear_category_rules()
    entries = load_counterparties()
    assert len(entries) == 1
    assert "category" not in entries[0]


# ---------------------------------------------------------------------------
# is_manual_category
# ---------------------------------------------------------------------------

def test_is_manual_false_empty_store(tmp_storage):
    assert is_manual_category("albert heijn") is False


def test_is_manual_false_without_flag(tmp_storage):
    save_category_rule("albert heijn", "groceries")
    assert is_manual_category("albert heijn") is False


def test_is_manual_false_with_matcher_only(tmp_storage):
    from expense_cli.identifier import save_counterparty_rule
    save_counterparty_rule("albert heijn", "description_contains", "heijn")
    assert is_manual_category("albert heijn") is False


def test_is_manual_true_after_set(tmp_storage):
    set_manual_category("albert heijn")
    assert is_manual_category("albert heijn") is True


def test_is_manual_case_insensitive(tmp_storage):
    set_manual_category("albert heijn")
    assert is_manual_category("Albert Heijn") is True


def test_is_manual_false_for_different_counterparty(tmp_storage):
    set_manual_category("albert heijn")
    assert is_manual_category("jumbo") is False
