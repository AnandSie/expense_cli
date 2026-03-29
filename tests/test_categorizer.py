import pytest
from expense_cli.categorizer import (
    categorize,
    category_rule_exists,
    save_category_rule,
    edit_category_rule,
    load_rules,
)

# ---------------------------------------------------------------------------
# categorize — pure logic, no file I/O
# ---------------------------------------------------------------------------

RULES = [
    {"counterparty": "Landlord BV", "category": "rent"},
    {"counterparty": "Albert Heijn", "category": "groceries"},
    {"counterparty": "Netflix", "category": "subscriptions"},
]


def test_exact_match():
    assert categorize("Albert Heijn", RULES) == "groceries"


def test_case_insensitive():
    assert categorize("albert heijn", RULES) == "groceries"
    assert categorize("ALBERT HEIJN", RULES) == "groceries"


def test_first_match_wins():
    rules = [
        {"counterparty": "Albert Heijn", "category": "groceries"},
        {"counterparty": "Albert Heijn", "category": "food"},
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
    assert rules[0] == {"counterparty": "netflix", "category": "subscriptions"}


def test_save_creates_file_if_missing(tmp_storage):
    path = tmp_storage / "categories.toml"
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
    assert rules[0]["counterparty"] == "netflix"
    assert rules[1]["counterparty"] == "rewe"


def test_save_preserves_other_rules(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    save_category_rule("rewe", "groceries")
    save_category_rule("netflix", "entertainment")
    rules = load_rules()
    rewe = next(r for r in rules if r["counterparty"] == "rewe")
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
    assert rules[0]["counterparty"] == "ns"
    assert rules[0]["category"] == "transport"


def test_edit_case_insensitive_lookup(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    assert edit_category_rule("Netflix", category="entertainment") is True


def test_edit_does_not_affect_other_rules(tmp_storage):
    save_category_rule("netflix", "subscriptions")
    save_category_rule("rewe", "groceries")
    edit_category_rule("netflix", category="entertainment")
    rules = load_rules()
    rewe = next(r for r in rules if r["counterparty"] == "rewe")
    assert rewe["category"] == "groceries"
