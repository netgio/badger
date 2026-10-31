"""Pure helpers for the home-screen display modes (values in tenths)."""

MODES = ("List", "Bars", "Big numbers", "Scenes")

# (upper bound exclusive in tenths of C, name); last band is open ended.
BANDS = ((0, "Freezing"), (160, "Chilly"), (240, "Comfy"),
         (300, "Warm"), (None, "Scorching"))


def band(temp):
    for index, (limit, _) in enumerate(BANDS):
        if limit is None or temp < limit:
            return index
    return len(BANDS) - 1


def scale(values, pad, min_span, step, floor=None, ceiling=None):
    """Shared axis covering every value with a margin, snapped to step."""
    values = [value for value in values if value is not None]
    if not values:
        return None
    low, high = min(values), max(values)
    margin = max(pad, (high - low) // 5)
    low -= margin
    high += margin
    if high - low < min_span:
        middle = (low + high) // 2
        low = middle - min_span // 2
        high = low + min_span
    low = (low // step) * step
    high = -((-high) // step) * step
    if floor is not None and low < floor:
        high = min(high + floor - low, ceiling) if ceiling is not None else high + floor - low
        low = floor
    if ceiling is not None and high > ceiling:
        low = max(floor, low - (high - ceiling)) if floor is not None else low - (high - ceiling)
        high = ceiling
    return low, high


def fill(value, low, high, length):
    if high <= low:
        return 0
    return max(0, min(length, (value - low) * length // (high - low)))
