import pytest
from expense_cli.identifier import (
    identify,
    rule_exists,
    matcher_exists,
    save_counterparty_rule,
    edit_counterparty_rule,
    delete_counterparty_rule,
    clear_counterparty_rules,
    load_counterparties,
    remove_category,
    clear_categories,
    set_manual_category,
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


# ---------------------------------------------------------------------------
# delete_counterparty_rule
# ---------------------------------------------------------------------------

def test_delete_returns_false_if_not_found(tmp_storage):
    assert delete_counterparty_rule("nobody") is False


def test_delete_removes_entry(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    assert delete_counterparty_rule("netflix") is True
    assert load_counterparties() == []


def test_delete_case_insensitive(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    assert delete_counterparty_rule("Netflix") is True
    assert load_counterparties() == []


def test_delete_leaves_other_entries_intact(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    save_counterparty_rule("spotify", "description_contains", "spotify")
    delete_counterparty_rule("netflix")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["name"] == "spotify"


# ---------------------------------------------------------------------------
# clear_counterparty_rules
# ---------------------------------------------------------------------------

def test_clear_returns_zero_when_empty(tmp_storage):
    assert clear_counterparty_rules() == 0


def test_clear_removes_all_entries(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    save_counterparty_rule("spotify", "description_contains", "spotify")
    count = clear_counterparty_rules()
    assert count == 2
    assert load_counterparties() == []


# ---------------------------------------------------------------------------
# category field support in save / edit
# ---------------------------------------------------------------------------

def test_save_with_category_creates_entry(tmp_storage):
    save_counterparty_rule("mom", category="transfers")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["name"] == "mom"
    assert entries[0]["category"] == "transfers"
    assert "iban" not in entries[0]
    assert "description_contains" not in entries[0]


def test_save_category_on_existing_entry(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    save_counterparty_rule("netflix", category="subscriptions")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["category"] == "subscriptions"
    assert entries[0]["description_contains"] == "netflix"


def test_save_matcher_and_category_together(tmp_storage):
    save_counterparty_rule("ah", "iban", "NL01ABCD", category="groceries")
    entries = load_counterparties()
    assert entries[0]["iban"] == "NL01ABCD"
    assert entries[0]["category"] == "groceries"


def test_edit_sets_category(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    edit_counterparty_rule("netflix", category="subscriptions")
    assert load_counterparties()[0]["category"] == "subscriptions"


# ---------------------------------------------------------------------------
# remove_category / clear_categories
# ---------------------------------------------------------------------------

def test_remove_category_from_name_only_entry_deletes_entry(tmp_storage):
    save_counterparty_rule("mom", category="transfers")
    assert remove_category("mom") is True
    assert load_counterparties() == []


def test_remove_category_from_entry_with_matcher_keeps_entry(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix", category="subscriptions")
    assert remove_category("netflix") is True
    entries = load_counterparties()
    assert len(entries) == 1
    assert "category" not in entries[0]
    assert entries[0]["description_contains"] == "netflix"


def test_remove_category_returns_false_if_no_category(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    assert remove_category("netflix") is False


def test_remove_category_returns_false_if_not_found(tmp_storage):
    assert remove_category("nobody") is False


def test_clear_categories_returns_count(tmp_storage):
    save_counterparty_rule("mom", category="transfers")
    save_counterparty_rule("netflix", "description_contains", "netflix", category="subscriptions")
    count = clear_categories()
    assert count == 2


def test_clear_categories_removes_name_only_entries(tmp_storage):
    save_counterparty_rule("mom", category="transfers")
    clear_categories()
    assert load_counterparties() == []


def test_clear_categories_keeps_entries_with_matchers(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix", category="subscriptions")
    clear_categories()
    entries = load_counterparties()
    assert len(entries) == 1
    assert "category" not in entries[0]


# ---------------------------------------------------------------------------
# _migrate_categories
# ---------------------------------------------------------------------------

def test_migrate_merges_categories_into_counterparties(tmp_storage):
    """Migration reads legacy categories.toml and merges into counterparties.toml."""
    (tmp_storage / "counterparties.toml").write_text(
        '[[counterparty]]\nname = "albert heijn"\niban = "NL01ABCD"\n',
        encoding="utf-8",
    )
    (tmp_storage / "categories.toml").write_text(
        '[[rules]]\ncounterparty = "albert heijn"\ncategory = "groceries"\n',
        encoding="utf-8",
    )
    entries = load_counterparties()  # triggers migration
    assert not (tmp_storage / "categories.toml").exists()
    assert (tmp_storage / "categories.toml.bak").exists()
    ah = next(e for e in entries if e["name"] == "albert heijn")
    assert ah["category"] == "groceries"
    assert ah["iban"] == "NL01ABCD"


def test_migrate_creates_name_only_entry_if_no_counterparty_match(tmp_storage):
    """Category rule with no matching counterparty entry → creates a name-only entry."""
    (tmp_storage / "categories.toml").write_text(
        '[[rules]]\ncounterparty = "mom"\ncategory = "transfers"\n',
        encoding="utf-8",
    )
    entries = load_counterparties()
    assert any(e["name"] == "mom" and e["category"] == "transfers" for e in entries)


def test_migrate_skips_if_no_legacy_file(tmp_storage):
    save_counterparty_rule("netflix", "description_contains", "netflix")
    entries = load_counterparties()
    assert len(entries) == 1  # nothing changed


# ---------------------------------------------------------------------------
# description_contains as a list
# ---------------------------------------------------------------------------

def test_description_contains_list_matches_any_pattern():
    counterparties = [{"description_contains": ["shell", "bp", "tinq"], "name": "tankstation"}]
    assert identify("", "Betaling Shell Hoogvliet", counterparties) == "tankstation"
    assert identify("", "BP tankstation A16", counterparties) == "tankstation"
    assert identify("", "Tinq betaling", counterparties) == "tankstation"


def test_description_contains_list_no_match():
    counterparties = [{"description_contains": ["shell", "bp"], "name": "tankstation"}]
    assert identify("", "esso betaling", counterparties) == ""


def test_description_contains_string_still_works():
    counterparties = [{"description_contains": "netflix", "name": "Netflix"}]
    assert identify("", "monthly netflix subscription", counterparties) == "Netflix"


def test_save_converts_string_to_list_on_second_add(tmp_storage):
    save_counterparty_rule("tankstation", "description_contains", "shell")
    save_counterparty_rule("tankstation", "description_contains", "bp")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["description_contains"] == ["shell", "bp"]


def test_save_appends_to_existing_list(tmp_storage):
    save_counterparty_rule("tankstation", "description_contains", "shell")
    save_counterparty_rule("tankstation", "description_contains", "bp")
    save_counterparty_rule("tankstation", "description_contains", "tinq")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["description_contains"] == ["shell", "bp", "tinq"]


def test_matcher_exists_checks_value_not_presence(tmp_storage):
    save_counterparty_rule("tankstation", "description_contains", "shell")
    # Different value → not a duplicate, should be addable
    assert matcher_exists("tankstation", "description_contains", "bp") is False
    # Same value → duplicate, should be blocked
    assert matcher_exists("tankstation", "description_contains", "shell") is True


def test_matcher_exists_no_value_checks_presence(tmp_storage):
    save_counterparty_rule("tankstation", "description_contains", "shell")
    # No value argument → old-style presence check (used for iban)
    assert matcher_exists("tankstation", "description_contains") is True
    assert matcher_exists("tankstation", "iban") is False


def test_edit_replaces_list_with_single_value(tmp_storage):
    save_counterparty_rule("tankstation", "description_contains", "shell")
    save_counterparty_rule("tankstation", "description_contains", "bp")
    edit_counterparty_rule("tankstation", description_contains="pompstation")
    entries = load_counterparties()
    assert entries[0]["description_contains"] == "pompstation"


def test_identify_with_list_and_iban_both_present():
    counterparties = [
        {"iban": "NL01TEST", "description_contains": ["shell", "bp"], "name": "tankstation"},
    ]
    assert identify("NL01TEST", "other", counterparties) == "tankstation"
    assert identify("", "bp betaling", counterparties) == "tankstation"


def test_identify_iban_list_first_match():
    counterparties = [
        {"iban": ["NL01AAAA0000000001", "NL02BBBB0000000002"], "name": "landlord"},
    ]
    assert identify("NL01AAAA0000000001", "", counterparties) == "landlord"
    assert identify("NL02BBBB0000000002", "", counterparties) == "landlord"
    assert identify("NL03CCCC0000000003", "", counterparties) == ""


def test_identify_iban_list_case_insensitive():
    counterparties = [
        {"iban": ["NL01AAAA0000000001", "NL02BBBB0000000002"], "name": "landlord"},
    ]
    assert identify("nl01aaaa0000000001", "", counterparties) == "landlord"


def test_save_multiple_ibans(tmp_storage):
    save_counterparty_rule("landlord", "iban", "NL01OLD")
    save_counterparty_rule("landlord", "iban", "NL02NEW")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["iban"] == ["NL01OLD", "NL02NEW"]


def test_matcher_exists_iban_specific_value(tmp_storage):
    save_counterparty_rule("landlord", "iban", "NL01OLD")
    save_counterparty_rule("landlord", "iban", "NL02NEW")
    assert matcher_exists("landlord", "iban", "NL01OLD") is True
    assert matcher_exists("landlord", "iban", "NL02NEW") is True
    assert matcher_exists("landlord", "iban", "NL03OTHER") is False


def test_matcher_exists_iban_presence_check(tmp_storage):
    save_counterparty_rule("landlord", "iban", "NL01OLD")
    assert matcher_exists("landlord", "iban") is True
    assert matcher_exists("landlord", "description_contains") is False


# ---------------------------------------------------------------------------
# set_manual_category
# ---------------------------------------------------------------------------

def test_set_manual_creates_new_entry(tmp_storage):
    set_manual_category("albert heijn")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["name"] == "albert heijn"
    assert entries[0]["manual_category"] is True


def test_set_manual_updates_existing_preserves_matchers(tmp_storage):
    save_counterparty_rule("albert heijn", "description_contains", "heijn")
    set_manual_category("albert heijn")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["description_contains"] == "heijn"
    assert entries[0]["manual_category"] is True


def test_set_manual_with_existing_category(tmp_storage):
    save_counterparty_rule("albert heijn", category="groceries")
    set_manual_category("albert heijn")
    entries = load_counterparties()
    assert entries[0]["category"] == "groceries"
    assert entries[0]["manual_category"] is True


def test_set_manual_idempotent(tmp_storage):
    set_manual_category("albert heijn")
    set_manual_category("albert heijn")
    assert len(load_counterparties()) == 1


def test_set_manual_case_insensitive(tmp_storage):
    save_counterparty_rule("albert heijn", "description_contains", "heijn")
    set_manual_category("Albert Heijn")
    entries = load_counterparties()
    assert len(entries) == 1
    assert entries[0]["manual_category"] is True


def test_set_manual_written_as_lowercase_true(tmp_storage):
    set_manual_category("albert heijn")
    from expense_cli.identifier import COUNTERPARTIES_PATH
    content = COUNTERPARTIES_PATH.read_text(encoding="utf-8")
    assert "manual_category = true" in content
    assert "True" not in content
