"""Pure I/O layer for reading and writing TOML array-of-tables config files.

No domain knowledge lives here — only generic TOML read/write operations.
"""
try:
    import tomllib
except ImportError:
    import tomli as tomllib

from pathlib import Path


def read_toml(path: Path) -> dict:
    """Parse a TOML file, returning an empty dict if the file does not exist."""
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def write_toml_array(
    path: Path,
    section: str,
    entries: list[dict],
    header: str = "",
    field_order: list[str] | None = None,
    sort_key: str | None = None,
) -> None:
    """Rewrite *path* as a TOML file containing a single array-of-tables section.

    Args:
        path:        Destination file (created with parent dirs if needed).
        section:     TOML table name, e.g. ``"counterparty"`` → ``[[counterparty]]``.
        entries:     List of dicts, each representing one table entry.
        header:      Optional comment block written at the top of the file.
        field_order: Keys to write first (in order); remaining keys follow.
        sort_key:    If given, sort entries by this field (case-insensitive) before writing.
    """
    if sort_key is not None:
        entries = sorted(entries, key=lambda e: e.get(sort_key, "").lower())
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if header:
        lines.append(header.rstrip())
        lines.append("")
    for entry in entries:
        lines.append(f"[[{section}]]")
        written: set[str] = set()
        for key in (field_order or []):
            if key in entry:
                lines.append(_fmt(key, entry[key]))
                written.add(key)
        for key, val in entry.items():
            if key not in written:
                lines.append(_fmt(key, val))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_bank_config(path: Path, config: dict) -> None:
    """Write a bank config dict as a TOML file with [bank] and [mapping] sections.

    Mapping values that are dicts are serialized as TOML inline tables.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if "bank" in config:
        lines.append("[bank]")
        for k, v in config["bank"].items():
            lines.append(_fmt(k, v))
        lines.append("")
    if "mapping" in config:
        lines.append("[mapping]")
        for k, v in config["mapping"].items():
            if isinstance(v, dict):
                pairs = ", ".join(f'{dk} = "{dv}"' for dk, dv in v.items())
                lines.append(f"{k} = {{ {pairs} }}")
            else:
                lines.append(_fmt(k, v))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _fmt(key: str, value: object) -> str:
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key} = "{escaped}"'
    return f"{key} = {value}"
