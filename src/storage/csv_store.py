from pathlib import Path
import pandas as pd

def read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, **kwargs)

def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)

def append_dedup(df: pd.DataFrame, path: Path, keys: list[str]) -> pd.DataFrame:
    old = read_csv(path)
    out = pd.concat([old, df], ignore_index=True) if not old.empty else df.copy()
    if not out.empty:
        out = out.drop_duplicates(keys, keep="last").sort_values(keys)
    write_csv(out, path)
    return out
