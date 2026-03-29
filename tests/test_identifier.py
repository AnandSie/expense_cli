import pytest
from expense_cli.identifier import (
    identify,
    rule_exists,
    matcher_exists,
    save_counterparty_rule,
    edit_counterparty_rule,
    load_counterparties,
)

# ---------------------------------------------------------------------------
# identify — pure logic, no file I/O
# ---------------------------------------------------------------------------

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


def test_entry_with_both_matchers_iban_wins():
    counterparties = [
        {"iban": "NL91ABNA0417164300", "description_contains": "heijn", "name": "Albert Heijn"},
    ]
    assert identify("NL91ABNA0417164300", "some other text", counterparties) == "Albert Heijn"


def test_entry_with_both_matchers_falls_back_to_description():
    counterparties = [
        {"iban": "NL91ABNA0417164300", "description_contains": "heijn", "name": "Albert Heijn"},
    ]
    # No IBAN on the transaction → description_contains match
    assert identify("", "heijn payment", counterparties) == "Albert Heijn"


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


# ---------------------------------------------------------------------------
# rule_exists / matcher_exists — require file I/O via tmp_storage
# ---------------------------------------------------------------------------

def test_rule_exists_false_when_no_file(tmp_storage):
    assert rule_exists("albert heijn") is False


def test_rule_exists_true_after_save(tmp_storage):
    save_counterparty_rule("albert heijn", "description_contains", "heijn")
    assert rule_exists("albert heijn") is True


def test_rule_exists_case_insensitive(tmp_storage):
    save_counterparty_rule("albert heijn", "description_contains", "heijn")
    assert rule_exists("Albert Heijn") is True


def test_matcher_exists_false_when_no_file(tmp_storage):
    assert matcher_exists("albert heijn", "iban") is False


def test_matcher_exists_false_for_different_matcher_type(tmp_storage):
    save_counterparty_rule("albert heijn", "description_contains", "heijn")
    assert matcher_exists("albert heijn", "iban") is False


def test_matcher_exists_true_after_save(tmp_storage):
    save_counterparty_rule("albert heijn", "description_contains", "heijn")
    assert matcher_exists("albert heijn", "description_contains") is True


# ---------------------------------------------------------------------------
# save_counterparty_rule
# ---------------------------------------------------------------------------

def test_save_creates_new_entry(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["name"] == "netflix"
    assert entries[0]["description_contains"] == "netflix"


def test_save_creates_file_if_missing(tmp_storage):
    path = tmp_storage / "counterparties.toml"
    assert not path.exists()
    save_counterparty_rule("netflix", "description_contains", "netflix")
    assert path.exists()


def test_save_adds_matcher_to_existing_entry(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    save_counterparty_rule("netflix", "iban", "NL12345")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["description_contains"] == "netflix"
    assert entries[0]["iban"] == "NL12345"


def test_save_multiple_distinct_entries(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    save_counterparty_rule("spotify", "description_contains", "spotify")
    entries = load_counterparties()
    assert len(entries) == 2
    assert entries[0]["name"] == "netflix"
    assert entries[1]["name"] == "spotify"


def test_save_preserves_other_entries(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    save_counterparty_rule("spotify", "description_contains", "spotify")
    # Adding iban to netflix should not affect spotify
    save_counterparty_rule("netflix", "iban", "NL12345")
    entries = load_counterparties()
    spotify = next(e for e in entries if e["name"] == "spotify")
    assert "iban" not in spotify


# ---------------------------------------------------------------------------
# edit_counterparty_rule
# ---------------------------------------------------------------------------

def test_edit_returns_false_if_not_found(tmp_storage):
    assert edit_counterparty_rule("nobody", new_name="someone") is False


def test_edit_renames_entry(tmp_storage):
    save_counterparty_rule("ns trains", "description_contains", "ns betaling")
    assert edit_counterparty_rule("ns trains", new_name="ns") is True
    entries = load_counterparties()
    assert entries[0]["name"] == "ns"


def test_edit_updates_iban(tmp_storage):
    save_counterparty_rule("landlord", "iban", "NL00OLD")
    edit_counterparty_rule("landlord", iban="NL00NEW")
    entries = load_counterparties()
    assert entries[0]["iban"] == "NL00NEW"


def test_edit_updates_description_contains(tmp_storage):
    save_counterparty_rule("ah", "description_contains", "albert")
    edit_counterparty_rule("ah", description_contains="albert heijn")
    entries = load_counterparties()
    assert entries[0]["description_contains"] == "albert heijn"


def test_edit_case_insensitive_lookup(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    assert edit_counterparty_rule("Netflix", new_name="netflix hd") is True


def test_edit_does_not_affect_other_entries(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    save_counterparty_rule("spotify", "description_contains", "spotify")
    edit_counterparty_rule("netflix", new_name="netflix hd")
    entries = load_counterparties()
    spotify = next(e for e in entries if e["name"] == "spotify")
    assert spotify["description_contains"] == "spotify"
