from expense_cli.categorizer import categorize

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
