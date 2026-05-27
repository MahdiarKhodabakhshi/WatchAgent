from __future__ import annotations

WMO_LEVELS = {
    "clear": 0,
    "mild_precipitation": 1,
    "moderate": 2,
    "severe": 3,
}

WMO_CATEGORY_BY_CODE = {
    0: "clear",
    1: "clear",
    2: "clear",
    3: "clear",
    45: "mild_precipitation",
    48: "mild_precipitation",
    51: "mild_precipitation",
    53: "mild_precipitation",
    55: "mild_precipitation",
    56: "mild_precipitation",
    57: "mild_precipitation",
    61: "mild_precipitation",
    71: "mild_precipitation",
    73: "mild_precipitation",
    63: "moderate",
    65: "moderate",
    75: "moderate",
    80: "moderate",
    81: "moderate",
    82: "severe",
    85: "severe",
    86: "severe",
    95: "severe",
    96: "severe",
    99: "severe",
}


def wmo_category(code: int | None) -> str | None:
    if code is None:
        return None
    return WMO_CATEGORY_BY_CODE.get(int(code))


def wmo_level(code: int | None) -> int | None:
    category = wmo_category(code)
    if category is None:
        return None
    return WMO_LEVELS[category]
