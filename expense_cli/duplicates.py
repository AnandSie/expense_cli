from __future__ import annotations

from collections import defaultdict

ALLOWED_MATCH_FIELDS = ("date", "amount", "description", "iban", "counterparty", "time")
DEFAULT_MATCH_FIELDS = ("date", "amount")


def resolve_match_fields(fields: list[str] | None) -> list[str]:
    """Return validated match fields, or the default set when none are provided."""
    if not fields:
        return list(DEFAULT_MATCH_FIELDS)

    normalized: list[str] = []
    invalid: list[str] = []
    for field in fields:
        for part in field.split(","):
            name = part.strip().lower()
            if not name:
                continue
            if name not in ALLOWED_MATCH_FIELDS:
                invalid.append(part.strip() or field)
                continue
            if name not in normalized:
                normalized.append(name)

    if invalid:
        allowed = ", ".join(ALLOWED_MATCH_FIELDS)
        bad = ", ".join(invalid)
        raise ValueError(f"Invalid --match-field value(s): {bad}. Allowed: {allowed}")

    return normalized


def duplicate_key(row: dict, fields: list[str]) -> tuple[str, ...]:
    """Return the normalized key tuple for a row over the selected fields."""
    parts: list[str] = []
    for field in fields:
        raw = str(row.get(field, "")).strip()
        if field == "amount":
            try:
                raw = f"{float(raw):.2f}"
            except ValueError:
                pass
        parts.append(raw)
    return tuple(parts)


def format_match_key(fields: list[str], key: tuple[str, ...]) -> str:
    return ", ".join(f"{field}={value or '∅'}" for field, value in zip(fields, key))


def group_possible_duplicates(rows: list[dict], fields: list[str]) -> list[tuple[tuple[str, ...], list[dict]]]:
    grouped: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[duplicate_key(row, fields)].append(row)

    duplicates = [(key, members) for key, members in grouped.items() if len(members) > 1]
    duplicates.sort(key=lambda item: item[0])
    return duplicates
