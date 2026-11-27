"""Geometry inference records. Text/coordinates remain in the source Page."""

from dataclasses import dataclass
from typing import Literal

from .models import BBox


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    line_tolerance: float = 2.0
    ruling_tolerance: float = 2.0
    minimum_column_lines: int = 2
    max_table_edges: int = 1200
    max_intersections: int = 4000


@dataclass(frozen=True, slots=True)
class TextLine:
    id: str
    bbox: BBox
    fragment_ids: tuple[str, ...]
    font_size: float


@dataclass(frozen=True, slots=True)
class Region:
    id: str
    kind: Literal["text", "heading", "table"]
    bbox: BBox
    line_ids: tuple[str, ...] = ()
    table_id: str | None = None
    column_path: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class TableCell:
    id: str
    bbox: BBox
    row_index: int
    column_index: int
    row_span: int
    column_span: int
    region_ids: tuple[str, ...]
    inferred: bool = False


@dataclass(frozen=True, slots=True)
class TableRow:
    index: int
    # A merged cell occurs only in its starting row; row_span covers later rows.
    cells: tuple[TableCell, ...]


@dataclass(frozen=True, slots=True)
class Table:
    id: str
    bbox: BBox
    rows: tuple[TableRow, ...]
    vector_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadingCandidate:
    name: str
    region_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReadingAlternative:
    id: str
    scope_id: str
    bbox: BBox
    reason: str
    candidates: tuple[ReadingCandidate, ...]
    default_index: int = 0


@dataclass(frozen=True, slots=True)
class PageLayout:
    page_id: str
    lines: tuple[TextLine, ...]
    regions: tuple[Region, ...]
    tables: tuple[Table, ...]
    reading_order: tuple[str, ...]
    alternatives: tuple[ReadingAlternative, ...]
    warnings: tuple[str, ...]

    def ordered_line_ids(self, region_ids: tuple[str, ...] | None = None) -> tuple[str, ...]:
        """Walk canonical ownership: roots (or supplied regions) -> cells -> lines."""
        regions = {region.id: region for region in self.regions}
        tables = {table.id: table for table in self.tables}
        result: list[str] = []

        def visit(ids: tuple[str, ...]) -> None:
            for region_id in ids:
                region = regions[region_id]
                if region.table_id is not None:
                    for row in tables[region.table_id].rows:
                        for cell in row.cells:
                            visit(cell.region_ids)
                else:
                    result.extend(region.line_ids)

        visit(self.reading_order if region_ids is None else region_ids)
        return tuple(result)

    def ordered_fragment_ids(self) -> tuple[str, ...]:
        """Return each owned source fragment once in resolved reading order."""
        lines = {line.id: line for line in self.lines}
        return tuple(fragment_id for line_id in self.ordered_line_ids()
                     for fragment_id in lines[line_id].fragment_ids)
