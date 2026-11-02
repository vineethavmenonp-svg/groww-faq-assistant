from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "config.yaml"


def repo_root() -> Path:
    return ROOT


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid config at {CONFIG_PATH}")
    return data


def get_env(name: str, required: bool = False) -> str:
    load_dotenv(ROOT / ".env")
    value = os.getenv(name, "")
    if required and not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def resolve_path(relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        return path
    return ROOT / path
