"""Display without GTK: how a setting reads."""

from __future__ import annotations



def shown(key: str, value: int) -> str:
    if key == "gamma":
        return f"{value / 100:.2f}"
    if key == "warmth":
        return "Neutral" if value >= 6500 else f"{value}K"
    return f"{value}%"


def check() -> None:
    assert [shown("gamma", 120), shown("warmth", 6500), shown("warmth", 4000), shown("contrast", 90)] == ["1.20", "Neutral", "4000K", "90%"]
