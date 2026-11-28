from collections.abc import Callable
from typing import Any
from pdfminer.layout import LTChar, LTLine
from .models import BBox, PathSegment, PatternColor, TextFragment, TextStyle, VectorEvidence

def parse_content(
    data: bytes,
    literal: Callable[[str | bytes], Any],
    keyword: Callable[[bytes], Any],
) -> list[tuple[list[Any], str | None]] | None: ...

class ContentDispatch:
    def __len__(self) -> int: ...
    @property
    def matrix_ops(self) -> int: ...
    @property
    def applied_matrices(self) -> int: ...
    def run(self) -> None: ...

def prepare_dispatch(
    data: bytes,
    literal: Callable[[str | bytes], Any],
    keyword: Callable[[bytes], Any],
    interpreter: object,
    operators: dict[str, tuple[Callable[..., None], int]],
) -> ContentDispatch | None: ...

def prepare_matrix_dispatch(
    data: bytes,
    literal: Callable[[str | bytes], Any],
    keyword: Callable[[bytes], Any],
    interpreter: object,
    operators: dict[str, tuple[Callable[..., None], int]],
    matrix_handler: Callable[..., None],
) -> ContentDispatch | None: ...

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

def identity_cached_glyphs(
    seq: object,
    cache: dict[int, tuple[float, str, bool]],
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

def assemble_lines(
    lines: list[LTLine],
    page_id: str,
    number_on_document: int,
    first_index: int,
    geometry: tuple[float, float, float, float, float],
    constructors: tuple[type[BBox], type[PathSegment], type[VectorEvidence], type[PatternColor]],
) -> list[VectorEvidence] | None: ...
