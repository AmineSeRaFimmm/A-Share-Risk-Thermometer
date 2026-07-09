#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys


ALLOWED_PATHS = [
    "data/raw/option_realtime",
    "data/normalized/realtime_option_chain.csv",
    "data/calculated/avix_realtime_mid.csv",
    "data/calculated/risk_temperature_nowcast.csv",
    "data/site/latest.json",
    "data/site/components.json",
    "data/site/audit.json",
    "data/site/nowcast_history.json",
    "docs/data/latest.json",
    "docs/data/components.json",
    "docs/data/audit.json",
    "docs/data/nowcast_history.json",
    "docs/data/downloads/risk_temperature_nowcast.csv",
]

IGNORED_PATHS = {
    "docs/data/build_info.json",
}


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def _status_lines() -> list[str]:
    out = _run(["git", "status", "--porcelain"], check=True).stdout
    return [line for line in out.splitlines() if line]


def _path_from_status(line: str) -> str:
    path = line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path


def main() -> None:
    _run(["git", "add", *ALLOWED_PATHS], check=True)
    if IGNORED_PATHS:
        _run(["git", "restore", "--", *sorted(IGNORED_PATHS)], check=False)

    unexpected = []
    for line in _status_lines():
        path = _path_from_status(line)
        if any(path == allowed or path.startswith(f"{allowed}/") for allowed in ALLOWED_PATHS):
            continue
        if path in IGNORED_PATHS:
            continue
        if path == "research" or path.startswith("research/"):
            continue
        unexpected.append(line)

    if unexpected:
        print("Unexpected realtime update outputs; refusing to rebase/push with a dirty tree.", file=sys.stderr)
        for line in unexpected:
            print(line, file=sys.stderr)
        sys.exit(1)

    staged = _run(["git", "diff", "--cached", "--name-only"], check=True).stdout.strip()
    print("Realtime output staging complete.")
    if staged:
        print(staged)
    else:
        print("No allowed realtime outputs changed.")


if __name__ == "__main__":
    main()
