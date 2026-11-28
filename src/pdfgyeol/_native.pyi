from collections.abc import Callable
from typing import Any

def parse_content(
    data: bytes,
    literal: Callable[[str | bytes], Any],
    keyword: Callable[[bytes], Any],
) -> list[tuple[list[Any], str | None]] | None: ...

def horizontal_glyphs(
    items: list[tuple[float, str, bool] | int | float],
    matrix: tuple[float, float, float, float, float, float],
    pos: tuple[float, float],
    fontsize: float,
    scaling: float,
    charspace: float,
    wordspace: float,
    rise: float,
    dxscale: float,
    descent: float,
) -> tuple[
    float, bool,
    list[tuple[str, float, float, float, tuple[float, float, float, float], float, float]],
] | None: ...

def straight_line(
    matrix: tuple[float, float, float, float, float, float],
    start: tuple[str, float, float],
    end: tuple[str, float, float],
) -> tuple[
    tuple[float, float], tuple[float, float],
    tuple[float, float, float, float], float, float,
] | None: ...
