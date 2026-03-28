import pytest


@pytest.fixture
def tmp_storage(tmp_path, monkeypatch):
    """Redirect all config/storage paths to a temp directory."""
    banks_dir = tmp_path / "banks"
    banks_dir.mkdir()

    monkeypatch.setattr("expense_cli.storage.DATA_DIR", tmp_path)
    monkeypatch.setattr("expense_cli.storage.CSV_PATH", tmp_path / "expenses.csv")
    monkeypatch.setattr("expense_cli.importer.BANKS_DIR", banks_dir)
    monkeypatch.setattr("expense_cli.identifier.COUNTERPARTIES_PATH", tmp_path / "counterparties.toml")
    monkeypatch.setattr("expense_cli.categorizer.CATEGORIES_PATH", tmp_path / "categories.toml")
    monkeypatch.setattr("expense_cli.cli.BANKS_DIR", banks_dir)
    monkeypatch.setattr("expense_cli.cli.COUNTERPARTIES_PATH", tmp_path / "counterparties.toml")
    monkeypatch.setattr("expense_cli.cli.CATEGORIES_PATH", tmp_path / "categories.toml")

    return tmp_path
