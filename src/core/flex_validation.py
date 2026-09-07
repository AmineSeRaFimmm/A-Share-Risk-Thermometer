"""Read-only provenance for Flex research artifacts, not proof of foresight."""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import math
import numbers
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 3
POLICY_ROOTS = (
    "research/backtest_flex_v2.py",
    "research/flex_event_backtest.py",
    "research/backtest_core_plus_sectors.py",
    "src/core/flex_validation.py",
    "src/core/flex_engine.py",
    "src/core/core_tail_policy.py",
    "src/core/sector_etf_map.py",
    "src/core/stage_trade_playbook.py",
    "src/core/risk_temperature.py",
    "scripts/bootstrap_history.py",
)
INPUT_PATHS = (
    "data/calculated/risk_components.csv",
    "data/raw/indices/sh000300.csv",
    "data/normalized/sw_level1_sector_history.csv",
    "data/raw/indices/hstech.csv",
)


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value) if math.isfinite(value) else None
    return value


def fingerprint(value: dict) -> str:
    encoded = json.dumps(
        json_safe(value), sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_record(path: Path) -> dict:
    if not path.is_file():
        return {"status": "MISSING", "sha256": None, "size_bytes": None}
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"status": "PRESENT", "sha256": digest.hexdigest(), "size_bytes": size}


def _local_dependencies(root: Path, seeds: tuple[str, ...]) -> set[str]:
    pending = list(seeds)
    found: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in found:
            continue
        found.add(relative)
        path = root / relative
        if path.suffix != ".py" or not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parents = list(Path(relative).parent.parts)
                    parents = parents[:len(parents) - node.level + 1]
                    base = ".".join(parents + ([base] if base else []))
                modules = [base] + [f"{base}.{alias.name}" for alias in node.names]
            for module in modules:
                parts = module.split(".")
                if not parts or parts[0] not in {"src", "research", "scripts"}:
                    continue
                candidates = [Path(*parts).with_suffix(".py")]
                candidates.extend(Path(*parts[:i]) / "__init__.py" for i in range(1, len(parts) + 1))
                pending.extend(str(p) for p in candidates if (root / p).is_file())
    return found


def build_policy_manifest(*, root: Path = ROOT) -> dict:
    """Hash whole local dependency files, including RT, without importing RT."""
    paths = _local_dependencies(root, POLICY_ROOTS)
    # Configuration loading is dynamic; conservatively freeze every config file.
    paths.update(str(p.relative_to(root)) for p in (root / "config").rglob("*") if p.is_file())
    paths.update(("requirements.txt", "requirements-dev.txt"))
    manifest = {
        "manifest_version": 1,
        "hash_algorithm": "sha256",
        "scope": "whole-file local import closure, RT generation dependencies and all configuration",
        "files": {name: _file_record(root / name) for name in sorted(paths)},
    }
    manifest["complete"] = all(record["status"] == "PRESENT" for record in manifest["files"].values())
    return {**manifest, "policy_fingerprint": fingerprint(manifest)}


def build_input_versions(*, root: Path = ROOT) -> dict:
    files = {name: _file_record(root / name) for name in INPUT_PATHS}
    return {
        "files": files,
        "input_fingerprint": fingerprint(files),
        "point_in_time_archive": False,
        "provenance": "current file snapshots; first availability and historical revisions are unverified",
    }


def _frame_record(frame: pd.DataFrame) -> dict:
    return {
        "rows": len(frame),
        "columns": [{"name": str(name), "dtype": str(dtype)} for name, dtype in frame.dtypes.items()],
        "sha256": hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes()).hexdigest(),
    }


def build_run_manifest(df: pd.DataFrame, meta: dict, *, policy: dict, inputs: dict, root: Path = ROOT) -> dict:
    def git(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    dates = pd.to_datetime(df["trade_date"])
    aligned = {
        "frame": _frame_record(df),
        "sector_panels": {
            panel: {name: _frame_record(pd.DataFrame({"value": values})) for name, values in sorted(meta[panel].items())}
            for panel in ("sector_open", "sector_close")
        },
    }
    versions = {}
    for package in ("numpy", "pandas", "PyYAML"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    status = git("status", "--porcelain")
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(status) if status is not None else None,
        "runtime": {"python": platform.python_version(), "packages": versions},
        "sample": {
            "start": str(dates.min().date()) if len(df) else None,
            "end": str(dates.max().date()) if len(df) else None,
            "days": len(df),
        },
        "policy": policy,
        "inputs": inputs,
        "aligned_inputs": aligned,
        "aligned_input_fingerprint": fingerprint(aligned),
    }
    return {**manifest, "run_id": fingerprint(manifest)}


def blocked_prospective(*, policy_fingerprint: str, scenario: dict) -> dict:
    return {
        "status": "BLOCKED_REQUIRES_POINT_IN_TIME_ARCHIVE",
        "validation_kind": "unverified_provenance",
        "independent_parameter_validation": False,
        "strict_prospective": False,
        "reason_cn": "缺少事前冻结与时点档案；历史回填仅属回顾重放，不是严格前瞻。",
        "protocol": "Requires a new preregistered policy and timestamped append-only inputs and decisions; no inherited freeze date",
        "policy_fingerprint": policy_fingerprint,
        "scenario_fingerprint": fingerprint(scenario),
        "freeze_id": None,
        "frozen_at": None,
        "start": None,
        "sample_days": 0,
        "stats": None,
    }
