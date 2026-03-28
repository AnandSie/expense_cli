from expense_cli.identifier import identify

COUNTERPARTIES = [
    {"iban": "NL91ABNA0417164300", "name": "Albert Heijn"},
    {"description_contains": "netflix", "name": "Netflix"},
    {"description_contains": "spotify", "name": "Spotify"},
]


def test_iban_exact_match():
    assert identify("NL91ABNA0417164300", "", COUNTERPARTIES) == "Albert Heijn"


def test_iban_case_insensitive():
    assert identify("nl91abna0417164300", "", COUNTERPARTIES) == "Albert Heijn"


def test_description_contains_match():
    assert identify("", "monthly NETFLIX subscription", COUNTERPARTIES) == "Netflix"


def test_description_contains_case_insensitive():
    assert identify("", "SPOTIFY PAYMENT", COUNTERPARTIES) == "Spotify"


def test_iban_takes_priority_over_description():
    counterparties = [
        {"description_contains": "payment", "name": "Generic"},
        {"iban": "NL91ABNA0417164300", "name": "Albert Heijn"},
    ]
    # iban rule comes second but iban is checked first per entry order
    assert identify("NL91ABNA0417164300", "payment", counterparties) == "Generic"


def test_first_match_wins():
    counterparties = [
        {"description_contains": "netflix", "name": "Netflix"},
        {"description_contains": "netflix", "name": "Streaming"},
    ]
    assert identify("", "netflix", counterparties) == "Netflix"


def test_no_match_returns_empty_string():
    assert identify("", "unknown transaction", COUNTERPARTIES) == ""


def test_empty_iban_skips_iban_rule():
    assert identify("", "", COUNTERPARTIES) == ""


def test_empty_counterparties():
    assert identify("NL91ABNA0417164300", "netflix", []) == ""
