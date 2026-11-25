"""Project paths and the department list from config/sources.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config" / "sources.toml"

DATA_DIR = ROOT / "data"
RAW_SPEND_DIR = DATA_DIR / "raw" / "spend"
RAW_COMPANIES_DIR = DATA_DIR / "raw" / "companies_house"
DB_PATH = DATA_DIR / "spendsentinel.duckdb"

SQL_DIR = ROOT / "sql"
EXPORTS_DIR = ROOT / "exports"
REPORTS_DIR = ROOT / "reports"


@dataclass(frozen=True)
class Department:
    code: str
    name: str
    collection: str


@dataclass(frozen=True)
class Settings:
    months_per_department: int
    departments: tuple[Department, ...]


def load_settings(path: Path = CONFIG_FILE) -> Settings:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    departments = tuple(Department(**d) for d in raw["departments"])
    return Settings(
        months_per_department=int(raw.get("months_per_department", 12)),
        departments=departments,
    )
