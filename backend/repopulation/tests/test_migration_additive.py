from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    PROJECT_ROOT / "backend" / "repopulation" / "migrations" / "0001_initial.sql"
)

# Statement-level destructive DDL. Matched against individual SQL statements with
# `--` comments stripped, so prose in comments cannot trip these. FK referential
# actions (`ON DELETE CASCADE` / `ON DELETE SET NULL`) are intentionally NOT matched:
# `DELETE FROM` requires the FROM keyword, and `DROP ...` requires an object keyword.
DESTRUCTIVE_PATTERNS: dict[str, re.Pattern[str]] = {
    "DROP TABLE/SCHEMA/INDEX/COLUMN/CONSTRAINT": re.compile(
        r"\bDROP\s+(TABLE|SCHEMA|INDEX|COLUMN|CONSTRAINT)\b", re.IGNORECASE
    ),
    "TRUNCATE": re.compile(r"\bTRUNCATE\b", re.IGNORECASE),
    "DELETE FROM": re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
    "ALTER ... DROP": re.compile(r"\bALTER\b[\s\S]*?\bDROP\b", re.IGNORECASE),
}


def _read_migration() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _strip_line_comments(sql: str) -> str:
    """Remove `--` line comments (the migration uses no block comments)."""
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _statements(sql: str) -> list[str]:
    """Split comment-stripped SQL into non-empty, semicolon-delimited statements."""
    stripped = _strip_line_comments(sql)
    return [stmt.strip() for stmt in stripped.split(";") if stmt.strip()]


def test_migration_file_exists() -> None:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"


@pytest.mark.parametrize("label", list(DESTRUCTIVE_PATTERNS))
def test_migration_has_no_statement_level_destructive_ddl(label: str) -> None:
    pattern = DESTRUCTIVE_PATTERNS[label]
    offenders = [stmt for stmt in _statements(_read_migration()) if pattern.search(stmt)]
    assert not offenders, f"destructive DDL ({label}) found in statement(s): {offenders}"


def test_fk_referential_actions_are_not_flagged_as_destructive() -> None:
    """Guard against false positives: the migration uses FK referential actions
    that contain the word DELETE, and these must remain allowed."""
    sql = _read_migration()
    assert "ON DELETE CASCADE" in sql
    assert "ON DELETE SET NULL" in sql

    for stmt in _statements(sql):
        if "ON DELETE CASCADE" in stmt or "ON DELETE SET NULL" in stmt:
            for label, pattern in DESTRUCTIVE_PATTERNS.items():
                assert not pattern.search(stmt), (
                    f"referential action statement wrongly flagged as {label}: {stmt}"
                )


def test_every_create_is_guarded_with_if_not_exists() -> None:
    unguarded = [
        stmt
        for stmt in _statements(_read_migration())
        if re.match(r"^\s*CREATE\b", stmt, re.IGNORECASE)
        and not re.search(r"\bIF\s+NOT\s+EXISTS\b", stmt, re.IGNORECASE)
    ]
    assert not unguarded, f"CREATE without IF NOT EXISTS: {unguarded}"


def test_all_objects_live_under_the_repop_schema() -> None:
    """Tables and indexes must target the `repop` schema. CREATE SCHEMA must create
    `repop` itself; CREATE EXTENSION is database-scoped (not schema-bound) and exempt."""
    offenders: list[str] = []
    for stmt in _statements(_read_migration()):
        if not re.match(r"^\s*CREATE\b", stmt, re.IGNORECASE):
            continue
        if re.match(r"^\s*CREATE\s+EXTENSION\b", stmt, re.IGNORECASE):
            continue
        if re.match(r"^\s*CREATE\s+SCHEMA\b", stmt, re.IGNORECASE):
            if not re.search(r"\brepop\b", stmt, re.IGNORECASE):
                offenders.append(stmt)
            continue
        if not re.search(r"\brepop\.", stmt, re.IGNORECASE):
            offenders.append(stmt)
    assert not offenders, f"object(s) not under the repop schema: {offenders}"
