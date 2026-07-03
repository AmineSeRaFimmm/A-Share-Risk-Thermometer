def merge_quality(flags: list[str]) -> str:
    flags = [f for f in flags if f and f != "OK"]
    if any(f.startswith("BAD") for f in flags):
        return next(f for f in flags if f.startswith("BAD"))
    if any(f.startswith("LOW") for f in flags):
        return next(f for f in flags if f.startswith("LOW"))
    if flags:
        return "|".join(sorted(set(flags)))
    return "OK"

def clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))
