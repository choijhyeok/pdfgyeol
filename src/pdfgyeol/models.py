"""Immutable extraction records; all geometry except source_matrix is page-local."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

Point = tuple[float, float]
VectorKind = Literal["line", "rect", "curve"]


@dataclass(frozen=True, slots=True)
class PatternColor:
    """PDF pattern resource name and any base color components exposed by the parser."""

    name: str
    base_components: tuple[float, ...] | None = None


Color = tuple[float, ...] | PatternColor | None


@dataclass(frozen=True, slots=True)
class BBox:
    """PDF points, origin at rotated MediaBox top-left, x right and y down."""

    x0: float
    top: float
    x1: float
    bottom: float


class PageKind(str, Enum):
    TEXT = "text"
    IMAGE_ONLY = "image_only"
    VECTOR_ONLY = "vector_only"
    BLANK = "blank"


@dataclass(frozen=True, slots=True)
class TextStyle:
    font_name: str
    size: float
    upright: bool
    stroking_color: Color
    non_stroking_color: Color


@dataclass(frozen=True, slots=True)
class TextFragment:
    """Exactly one pdfplumber char, including spaces, repeats and multi-codepoint text."""

    id: str
    page_id: str
    page_number: int
    source_index: int
    text: str
    bbox: BBox
    style: TextStyle
    # pdfminer CTM, retained as source evidence; NOT top-left page-local geometry.
    source_matrix: tuple[float, ...]
    advance: float


@dataclass(frozen=True, slots=True)
class PathSegment:
    operator: str
    points: tuple[Point, ...]


@dataclass(frozen=True, slots=True)
class VectorEvidence:
    """Raw ruling/shape evidence, not inferred table cells or duplicated edges."""

    id: str
    page_id: str
    page_number: int
    source_index: int
    kind: VectorKind
    bbox: BBox
    points: tuple[Point, ...]
    path: tuple[PathSegment, ...]
    line_width: float
    stroke: bool
    fill: bool
    even_odd: bool
    stroking_color: Color
    non_stroking_color: Color
    dash: tuple[tuple[float, ...], float] | None


@dataclass(frozen=True, slots=True)
class ImageEvidence:
    """Placement and raster metadata only; no embedded image data."""

    id: str
    page_id: str
    page_number: int
    source_index: int
    bbox: BBox
    source_size: tuple[int, int]
    bits: int | None
    image_mask: bool


@dataclass(frozen=True, slots=True)
class Page:
    id: str
    page_number: int
    width: float
    height: float
    rotation: int
    # pdfplumber's absolute top-left box before translating geometry to (0, 0).
    source_bbox: BBox
    kind: PageKind
    fragments: tuple[TextFragment, ...]
    vectors: tuple[VectorEvidence, ...]
    images: tuple[ImageEvidence, ...]


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    sha256: str
    schema_version: str
    extractor_version: str
    pages: tuple[Page, ...]
