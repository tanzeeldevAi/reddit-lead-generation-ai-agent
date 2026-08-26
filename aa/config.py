"""Config loading + project paths. One config file drives the whole agent."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "authority.db"
DRAFT_DIR = ROOT / "drafts"

load_dotenv(ROOT / ".env")


class Config(dict):
    """Dict with dotted access, so cfg.get_path('limits.max_link_ratio') works."""

    def get_path(self, dotted: str, default=None):
        node = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def load(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.yaml"
    if not path.exists():
        raise SystemExit(f"config not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh) or {})


def env(key: str, required: bool = False) -> str | None:
    val = os.environ.get(key)
    if required and not val:
        raise SystemExit(
            f"missing {key}. Put it in {ROOT / '.env'} -- see SETUP.md"
        )
    return val


def ensure_dirs() -> None:
    (ROOT / "data").mkdir(exist_ok=True)
    DRAFT_DIR.mkdir(exist_ok=True)
    (DRAFT_DIR / "pending").mkdir(exist_ok=True)
    (DRAFT_DIR / "ready").mkdir(exist_ok=True)
