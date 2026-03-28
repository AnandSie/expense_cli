try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path

COUNTERPARTIES_PATH = Path.home() / ".expense_cli" / "counterparties.toml"


def load_counterparties() -> list[dict]:
    if not COUNTERPARTIES_PATH.exists():
        return []
    with COUNTERPARTIES_PATH.open("rb") as f:
        data = tomllib.load(f)
    return data.get("counterparty", [])


def identify(iban: str, description: str, counterparties: list[dict]) -> str:
    """Return normalized counterparty name, or empty string if no rule matches.

    Matches in order: exact IBAN first, then description_contains substring.
    """
    iban_lower = iban.lower()
    description_lower = description.lower()

    for cp in counterparties:
        if "iban" in cp and iban_lower and cp["iban"].lower() == iban_lower:
            return cp["name"]
        if "description_contains" in cp and cp["description_contains"].lower() in description_lower:
            return cp["name"]

    return ""
