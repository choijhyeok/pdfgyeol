from collections.abc import Callable
from typing import Any
from pdfminer.layout import LTChar
from .models import BBox, PatternColor, TextFragment, TextStyle

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

def assemble_glyphs(
    glyphs: list[LTChar],
    page_id: str,
    number_on_document: int,
    first_index: int,
    geometry: tuple[float, float, float, float, float],
    constructors: tuple[type[BBox], type[TextStyle], type[TextFragment], type[PatternColor]],
) -> list[TextFragment] | None: ...
