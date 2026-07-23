from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
import json
import math
import numbers
import os
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    """Convert pipeline values to strict JSON-compatible primitives."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if value.__class__.__name__ in {"NAType", "NaTType"}:
        return None
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        scalar = item()
        if scalar is not value:
            return json_safe(scalar)
    return value


def dumps_json(data: Any, *, indent: int | None = 2) -> str:
    """Serialize only standards-compliant JSON; non-finite values become null."""
    return json.dumps(json_safe(data), ensure_ascii=False, indent=indent, allow_nan=False)


def write_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(dumps_json(data) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))
