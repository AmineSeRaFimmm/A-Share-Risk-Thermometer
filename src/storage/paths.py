from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
NORMALIZED = DATA / "normalized"
CALCULATED = DATA / "calculated"
SITE = DATA / "site"
WEB = ROOT / "web"
DOCS = ROOT / "docs"

def ensure_dirs() -> None:
    for path in [
        RAW / "options_daily", RAW / "option_realtime", RAW / "indices",
        RAW / "breadth", RAW / "qvix", RAW / "shibor", RAW / "sectors",
        NORMALIZED, CALCULATED, SITE, DOCS
    ]:
        path.mkdir(parents=True, exist_ok=True)
