try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path

CATEGORIES_PATH = Path.home() / ".expense_cli" / "categories.toml"


def load_rules() -> list[dict]:
    if not CATEGORIES_PATH.exists():
        return []
    with CATEGORIES_PATH.open("rb") as f:
        data = tomllib.load(f)
    return data.get("rules", [])


def categorize(counterparty: str, rules: list[dict]) -> str:
    """Return the first matching category, or empty string if no rule matches."""
    counterparty_lower = counterparty.lower()

    for rule in rules:
        if "counterparty" in rule and rule["counterparty"].lower() == counterparty_lower:
            return rule["category"]

    return ""
