"""Local geometry rules over Feature 1 evidence; no vocabulary or model calls."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from math import floor
from statistics import median
from typing import Any

from pdfplumber.table import cells_to_tables, edges_to_intersections, intersections_to_cells, merge_edges

from .layout_models import (
    LayoutConfig, PageLayout, ReadingAlternative, ReadingCandidate, Region,
    Table, TableCell, TableRow, TextLine,
)
from .models import BBox, Page, TextFragment


def _union(boxes: Iterable[BBox]) -> BBox:
    items = list(boxes)
    return BBox(min(b.x0 for b in items), min(b.top for b in items),
                max(b.x1 for b in items), max(b.bottom for b in items))


def _area(box: BBox) -> float:
    return max(0, box.x1 - box.x0) * max(0, box.bottom - box.top)


def _contains(outer: BBox, inner: BBox, tolerance: float = 0) -> bool:
    return (outer.x0 - tolerance <= inner.x0 and outer.top - tolerance <= inner.top
            and outer.x1 + tolerance >= inner.x1 and outer.bottom + tolerance >= inner.bottom)


def _center_in(box: BBox, fragment: TextFragment) -> bool:
    x = (fragment.bbox.x0 + fragment.bbox.x1) / 2
    y = (fragment.bbox.top + fragment.bbox.bottom) / 2
    return box.x0 <= x <= box.x1 and box.top <= y <= box.bottom


def _line_groups(fragments: Iterable[TextFragment], config: LayoutConfig) -> list[list[TextFragment]]:
    """Bucket baselines, then split wide gaps; no filtering, including whitespace."""
    buckets: dict[int, list[list[TextFragment]]] = defaultdict(list)
    groups: list[list[TextFragment]] = []
    tolerance = config.line_tolerance
    for fragment in sorted(fragments, key=lambda f: (f.bbox.top, f.bbox.x0, f.source_index)):
        center = (fragment.bbox.top + fragment.bbox.bottom) / 2
        key = floor(center / tolerance)
        candidates = [group for k in (key - 1, key, key + 1) for group in buckets[k]
                      if abs((group[0].bbox.top + group[0].bbox.bottom) / 2 - center) <= tolerance
                      and group[0].style.upright == fragment.style.upright]
        if candidates:
            min(candidates, key=lambda g: abs(g[0].bbox.top - fragment.bbox.top)).append(fragment)
        else:
            group = [fragment]
            buckets[key].append(group)
            groups.append(group)
    result = []
    for group in groups:
        group.sort(key=lambda f: (f.bbox.x0, f.source_index))
        gap = max(8.0, median(max(1, f.style.size) for f in group) * 1.5)
        run = [group[0]]
        right = group[0].bbox.x1
        for fragment in group[1:]:
            if fragment.bbox.x0 - right > gap:
                result.append(run)
                run = []
            run.append(fragment)
            right = max(right, fragment.bbox.x1) if len(run) > 1 else fragment.bbox.x1
        result.append(run)
    return sorted(result, key=lambda g: (min(f.bbox.top for f in g), min(f.bbox.x0 for f in g)))


@dataclass
class _Grid:
    id: str
    bbox: BBox
    cells: list[BBox]
    inferred: set[BBox]
    vector_ids: tuple[str, ...]
    parent_cell: tuple[int, int] | None = None


def _edges(page: Page, tolerance: float) -> list[dict[str, Any]]:
    edges = []
    for vector in page.vectors:
        if vector.kind == "rect" and vector.fill and not vector.stroke:
            box = vector.bbox
            width, height = box.x1 - box.x0, box.bottom - box.top
            # Filled hairline strips are common PDF rulings; broad fills are not.
            if 0 < height <= tolerance and width > tolerance * 2:
                y = (box.top + box.bottom) / 2
                edges.append(dict(x0=box.x0, x1=box.x1, top=y, bottom=y,
                                  width=width, height=0, orientation="h"))
            elif 0 < width <= tolerance and height > tolerance * 2:
                x = (box.x0 + box.x1) / 2
                edges.append(dict(x0=x, x1=x, top=box.top, bottom=box.bottom,
                                  width=0, height=height, orientation="v"))
            continue
        if not vector.stroke:
            continue
        if vector.kind == "rect":
            box = vector.bbox
            pairs = [((box.x0, box.top), (box.x1, box.top)),
                     ((box.x1, box.top), (box.x1, box.bottom)),
                     ((box.x1, box.bottom), (box.x0, box.bottom)),
                     ((box.x0, box.bottom), (box.x0, box.top))]
        elif vector.kind == "line":
            pairs = list(zip(vector.points, vector.points[1:]))
        else:
            pairs = []
            previous = None
            start = None
            for segment in vector.path:
                if segment.operator == "m" and segment.points:
                    previous = start = segment.points[0]
                elif segment.operator == "l" and previous is not None and segment.points:
                    pairs.append((previous, segment.points[0]))
                    previous = segment.points[0]
                elif segment.operator == "h" and previous is not None and start is not None:
                    pairs.append((previous, start))
                    previous = start
                elif segment.points:
                    previous = segment.points[-1]  # Never turn a Bézier into a ruling.
        for (x0, y0), (x1, y1) in pairs:
            horizontal = abs(y1 - y0) <= tolerance and abs(x1 - x0) > tolerance * 2
            vertical = abs(x1 - x0) <= tolerance and abs(y1 - y0) > tolerance * 2
            if horizontal:
                y0 = y1 = (y0 + y1) / 2
            elif vertical:
                x0 = x1 = (x0 + x1) / 2
            else:
                continue
            edges.append(dict(x0=min(x0, x1), x1=max(x0, x1), top=min(y0, y1), bottom=max(y0, y1),
                              width=abs(x1 - x0), height=abs(y1 - y0),
                              orientation="h" if horizontal else "v"))
    return edges


def _split_unruled_rows(cells: list[BBox], fragments: tuple[TextFragment, ...],
                       config: LayoutConfig) -> tuple[list[BBox], set[BBox]]:
    """Refine tall side-by-side body cells from a narrow repeated label column."""
    bands: dict[tuple[float, float], list[BBox]] = defaultdict(list)
    for cell in cells:
        bands[(cell.top, cell.bottom)].append(cell)
    result = []
    inferred: set[BBox] = set()
    for band in bands.values():
        band.sort(key=lambda b: b.x0)
        width = sum(b.x1 - b.x0 for b in band)
        label = min(band, key=lambda b: b.x1 - b.x0)
        groups = _line_groups((f for f in fragments if _center_in(label, f)), config)
        # Whitespace is owned later, but blank label lines are not row starts.
        groups = [group for group in groups if any(f.text.strip() for f in group)]
        heights = [f.bbox.bottom - f.bbox.top for group in groups for f in group]
        # Narrow aligned labels plus a much wider text cell, not two prose columns.
        if (len(band) < 2 or label.x1 - label.x0 > width * .3 or len(groups) < 3
                or label.bottom - label.top < median(heights or [1]) * 4):
            result.extend(band)
            continue
        starts = [min(f.bbox.top for f in group) for group in groups]
        # Every label must have neighboring text at the same height.
        neighbors = [f for f in fragments if any(_center_in(b, f) for b in band if b != label)]
        if sum(any(abs(f.bbox.top - y) <= config.line_tolerance for f in neighbors) for y in starts) < len(starts) * .8:
            result.extend(band)
            continue
        cuts = [label.top] + [y - .5 for y in starts[1:]] + [label.bottom]
        for top, bottom in zip(cuts, cuts[1:]):
            for cell in band:
                child = BBox(cell.x0, top, cell.x1, bottom)
                result.append(child)
                inferred.add(child)
    return result, inferred


def _grids(page: Page, config: LayoutConfig, warnings: list[str]) -> list[_Grid]:
    edges = _edges(page, config.ruling_tolerance)
    if len(edges) > config.max_table_edges:
        warnings.append("table_edge_budget_exceeded")
        return []
    tolerance = config.ruling_tolerance
    edges = merge_edges(edges, tolerance, tolerance, tolerance, tolerance)
    orientations = Counter(e["orientation"] for e in edges)
    if orientations["h"] * orientations["v"] > config.max_intersections * 20:
        warnings.append("table_intersection_budget_exceeded")
        return []
    intersections = edges_to_intersections(edges, tolerance, tolerance)
    if len(intersections) > config.max_intersections:
        warnings.append("table_intersection_budget_exceeded")
        return []
    grids: list[_Grid] = []
    for boxes in cells_to_tables(intersections_to_cells(intersections)):
        cells = [BBox(*(float(n) for n in box)) for box in boxes]
        cells, inferred = _split_unruled_rows(cells, page.fragments, config)
        cells.sort(key=lambda b: (b.top, b.x0, b.bottom, b.x1))
        bbox = _union(cells)
        vector_ids = tuple(v.id for v in page.vectors if _contains(bbox, v.bbox, tolerance))
        grids.append(_Grid(f"{page.id}:t{len(grids):05d}", bbox, cells, inferred, vector_ids))
    for index, grid in enumerate(grids):
        parents = [(parent_index, cell_index, cell)
                   for parent_index, parent in enumerate(grids) if parent_index != index
                   for cell_index, cell in enumerate(parent.cells)
                   if _area(cell) > _area(grid.bbox) + 1 and _contains(cell, grid.bbox, tolerance)]
        if parents:
            parent_index, cell_index, _ = min(parents, key=lambda entry: _area(entry[2]))
            grid.parent_cell = (parent_index, cell_index)
    return grids


@dataclass(frozen=True)
class _Item:
    id: str
    bbox: BBox
    size: float = 0
    column_path: tuple[int, ...] = ()
    has_text: bool = False
    fragments: tuple[TextFragment, ...] = ()


def _text_item(id: str, fragments: tuple[TextFragment, ...]) -> _Item:
    return _Item(id, _union(f.bbox for f in fragments), median(f.style.size for f in fragments),
                 has_text=any(f.text.strip() for f in fragments), fragments=fragments)


@dataclass(frozen=True)
class _Choice:
    bbox: BBox
    columns: tuple[str, ...]
    rows: tuple[str, ...]
    reason: str


def _row_order(items: list[_Item], tolerance: float) -> list[_Item]:
    """Cluster nearly aligned rows without quantization-boundary reversals."""
    result: list[_Item] = []
    row: list[_Item] = []
    for item in sorted(items, key=lambda i: (i.bbox.top, i.bbox.x0, i.id)):
        if row and item.bbox.top - row[0].bbox.top > tolerance:
            result.extend(sorted(row, key=lambda i: (i.bbox.x0, i.id)))
            row = []
        row.append(item)
    result.extend(sorted(row, key=lambda i: (i.bbox.x0, i.id)))
    return result


def _aligned_label_value_order(left: list[_Item], right: list[_Item], tolerance: float) -> list[_Item]:
    """Look for a compact, spaced list with one entry per side on every row."""
    if not 4 <= len(left) == len(right) <= 16 or not all(item.has_text for item in left + right):
        return []
    left = sorted(left, key=lambda item: item.bbox.top)
    right = sorted(right, key=lambda item: item.bbox.top)
    if any(abs(a.bbox.top - b.bbox.top) > tolerance for a, b in zip(left, right)):
        return []

    size = median(item.size for item in left + right)
    # Check both row whitespace and horizontal compactness. Shared baselines alone
    # are common in independent prose columns.
    rows = list(zip(left, right))
    for (a, b), (next_a, next_b) in zip(rows, rows[1:]):
        clearance = min(next_a.bbox.top, next_b.bbox.top) - max(a.bbox.bottom, b.bbox.bottom)
        if clearance < max(tolerance, size * .6):
            return []

    left_starts = [item.bbox.x0 for item in left]
    right_starts = [item.bbox.x0 for item in right]
    right_ends = [item.bbox.x1 for item in right]
    anchor_tolerance = max(tolerance, size * .45)
    if (max(left_starts) - min(left_starts) > anchor_tolerance
            or min(max(right_starts) - min(right_starts), max(right_ends) - min(right_ends)) > anchor_tolerance):
        return []

    column_distance = median(right_starts) - median(left_starts)
    if max(item.bbox.x1 - item.bbox.x0 for item in left + right) > column_distance * .65:
        return []
    return [item for pair in rows for item in pair]


def _gutter(items: list[_Item], config: LayoutConfig) -> float | None:
    if len(items) < 2:
        return None
    box = _union(item.bbox for item in items)
    width = box.x1 - box.x0
    if width <= 0:
        return None
    boundaries = sorted({item.bbox.x0 for item in items} | {item.bbox.x1 for item in items})
    cuts = [(a + b) / 2 for a, b in zip(boundaries, boundaries[1:])
            if box.x0 + width * .25 < (a + b) / 2 < box.x1 - width * .25]
    if len(cuts) > 256:
        cuts = cuts[::max(1, len(cuts) // 256)]
    best: tuple[float, float] | None = None
    for x in cuts:
        left = [item for item in items if item.bbox.x1 <= x]
        right = [item for item in items if item.bbox.x0 >= x]
        cross = len(items) - len(left) - len(right)
        support = [sum(3 if item.size == 0 else 1 for item in side) for side in (left, right)]
        if min(support) < config.minimum_column_lines or cross > max(2, len(items) * .15):
            continue
        overlap = min(max(i.bbox.bottom for i in left), max(i.bbox.bottom for i in right)) - max(
            min(i.bbox.top for i in left), min(i.bbox.top for i in right))
        gap = min(i.bbox.x0 for i in right) - max(i.bbox.x1 for i in left)
        size = median([i.size for i in left + right if i.size] or [10])
        if gap < max(8, size) or overlap < size:
            continue
        score = min(support) - cross * 2 + gap / width
        candidate = (score, -abs(x - (box.x0 + box.x1) / 2))
        if best is None or candidate > best:
            best = candidate
            best_x = x
    return None if best is None else best_x


def _split_gutter_line(item: _Item, x: float, left: list[_Item], right: list[_Item],
                       tolerance: float) -> tuple[_Item, _Item] | None:
    """Recover two runs when a narrow gutter was missed by baseline grouping."""
    fragments = item.fragments
    if not fragments or not fragments[0].style.upright:
        return None
    cut = next((i for i, f in enumerate(fragments) if f.bbox.x0 >= x), len(fragments))
    if not 0 < cut < len(fragments):
        return None
    left_end = max(f.bbox.x1 for f in fragments[:cut])
    if left_end > x or fragments[cut].bbox.x0 - left_end < max(8, item.size):
        return None

    # Both runs must continue nearby, similarly sized and aligned column text.
    # A gap alone is also possible in a spanning heading or a justified line.
    parts = (_text_item(f"{item.id}:0", fragments[:cut]), _text_item(f"{item.id}:1", fragments[cut:]))
    for part, side in zip(parts, (left, right)):
        if not part.has_text:
            return None
        offsets = [neighbor.bbox.top - part.bbox.top for neighbor in side
                   if neighbor.has_text and neighbor.fragments[0].style.upright
                   and abs(neighbor.bbox.x0 - part.bbox.x0) <= tolerance
                   and abs(neighbor.size - part.size) <= max(1, part.size * .12)
                   and abs(neighbor.bbox.top - part.bbox.top) <= part.size * 2.5]
        if not offsets or min(offsets) >= -tolerance or max(offsets) <= tolerance:
            return None
    return parts


def _order(items: list[_Item], config: LayoutConfig, choices: list[_Choice],
           path: tuple[int, ...] = ()) -> list[_Item]:
    x = _gutter(items, config)
    if x is None:
        return [replace(item, column_path=path) for item in sorted(items, key=lambda i: (i.bbox.top, i.bbox.x0, i.id))]
    left = [item for item in items if item.bbox.x1 <= x]
    right = [item for item in items if item.bbox.x0 >= x]
    cross = sorted((item for item in items if item.bbox.x0 < x < item.bbox.x1), key=lambda i: (i.bbox.top, i.id))
    # Use established lines as evidence before adding any recovered runs.
    splits = [_split_gutter_line(item, x, left, right, config.line_tolerance) for item in cross]
    separators = []
    for item, parts in zip(cross, splits):
        if parts is None:
            separators.append(item)
        else:
            left.append(parts[0])
            right.append(parts[1])
    result = []

    def band(a: list[_Item], b: list[_Item]) -> list[_Item]:
        child_choices = len(choices)
        ordered = _order(a, config, choices, path + (0,)) + _order(b, config, choices, path + (1,))
        if len(choices) != child_choices:
            return ordered  # Keep nested candidate scopes disjoint.

        row_order: list[_Item] = []
        reason = "aligned_label_value_rows"
        # Sparse, aligned fragments can be independent columns or unruled rows.
        if 2 <= len(a) <= 3 and 2 <= len(b) <= 3 and all(i.size for i in a + b):
            aligned = sum(any(abs(i.bbox.top - j.bbox.top) <= config.line_tolerance for j in b) for i in a)
            if aligned >= 2:
                row_order = _row_order(a + b, config.line_tolerance)
                reason = "sparse_aligned_blocks"
        else:
            row_order = _aligned_label_value_order(a, b, config.line_tolerance)

        if row_order:
            columns = tuple(i.id for i in ordered)
            rows = tuple(i.id for i in row_order)
            if columns != rows:
                choices.append(_Choice(_union(i.bbox for i in a + b), columns, rows, reason))
        return ordered

    for separator in separators:
        before_left = [i for i in left if (i.bbox.top + i.bbox.bottom) / 2 < separator.bbox.top]
        before_right = [i for i in right if (i.bbox.top + i.bbox.bottom) / 2 < separator.bbox.top]
        result.extend(band(before_left, before_right))
        left = [i for i in left if i not in before_left]
        right = [i for i in right if i not in before_right]
        result.append(replace(separator, column_path=path))
    result.extend(band(left, right))
    return result


class _Builder:
    def __init__(self, page: Page, config: LayoutConfig):
        self.page = page
        self.config = config
        self.warnings: list[str] = []
        self.grids = _grids(page, config, self.warnings)
        self.lines: list[TextLine] = []
        self.regions: list[Region] = []
        self.tables: list[Table] = []
        self.alternatives: list[ReadingAlternative] = []
        self.assigned: dict[tuple[int, int] | None, list[TextFragment]] = defaultdict(list)
        # A small vertical index bounds ownership checks to cells on that strip.
        cell_index: dict[int, list[tuple[int, int, BBox]]] = defaultdict(list)
        for table_index, grid in enumerate(self.grids):
            for index, box in enumerate(grid.cells):
                for strip in range(floor(box.top / 32), floor(box.bottom / 32) + 1):
                    cell_index[strip].append((table_index, index, box))
        for fragment in page.fragments:
            strip = floor((fragment.bbox.top + fragment.bbox.bottom) / 64)
            matches = [entry for entry in cell_index[strip] if _center_in(entry[2], fragment)]
            owner = min(matches, key=lambda entry: (_area(entry[2]), entry[0], entry[1])) if matches else None
            self.assigned[None if owner is None else (owner[0], owner[1])].append(fragment)
        if any(not f.style.upright for f in page.fragments):
            self.warnings.append("non_upright_text_geometry_order")

    def content(self, owner: tuple[int, int] | None, scope_id: str) -> tuple[str, ...]:
        groups = _line_groups(self.assigned[owner], self.config)
        items = [_text_item(f"{self.page.id}:l{len(self.lines) + index:06d}", tuple(group))
                 for index, group in enumerate(groups)]
        child_tables = [index for index, grid in enumerate(self.grids) if grid.parent_cell == owner]
        items.extend(_Item(f"{self.grids[index].id}:region", self.grids[index].bbox) for index in child_tables)
        choices: list[_Choice] = []
        ordered = _order(items, self.config, choices)

        local_lines: dict[str, TextLine] = {}
        for item in sorted((item for item in ordered if item.fragments),
                           key=lambda i: (i.bbox.top, i.bbox.x0, i.id)):
            line = TextLine(f"{self.page.id}:l{len(self.lines):06d}", item.bbox,
                            tuple(f.id for f in item.fragments), item.size)
            self.lines.append(line)
            local_lines[item.id] = line
        for index in child_tables:
            table = self.build_table(index)
            self.regions.append(Region(f"{table.id}:region", "table", table.bbox, table_id=table.id))
        separate = {item_id for choice in choices for item_id in choice.columns}
        mapping: dict[str, str] = {}
        result: list[str] = []
        body_size = median([line.font_size for line in local_lines.values()] or [10])
        previous: _Item | None = None
        for item in ordered:
            if item.id not in local_lines:
                result.append(item.id)
                mapping[item.id] = item.id
                previous = None
                # Replace the table region with its inferred column membership.
                for index, region in enumerate(self.regions):
                    if region.id == item.id:
                        self.regions[index] = replace(region, column_path=item.column_path)
                        break
                continue
            line = local_lines[item.id]
            heading = line.font_size >= body_size * 1.25
            join = (previous is not None and previous.id not in separate and item.id not in separate
                    and previous.column_path == item.column_path
                    and abs(previous.size - item.size) <= max(1, item.size * .12)
                    and -.2 * item.size <= item.bbox.top - previous.bbox.bottom <= item.size * .9
                    and abs(item.bbox.x0 - previous.bbox.x0) <= item.size * 2
                    and not heading)
            if join:
                last = self.regions[-1]
                self.regions[-1] = replace(last, bbox=_union((last.bbox, item.bbox)), line_ids=last.line_ids + (line.id,))
                mapping[item.id] = last.id
            else:
                region = Region(f"{self.page.id}:r{len(self.regions):06d}", "heading" if heading else "text",
                                item.bbox, (line.id,), column_path=item.column_path)
                self.regions.append(region)
                result.append(region.id)
                mapping[item.id] = region.id
            previous = item
        for choice in choices:
            self.alternatives.append(ReadingAlternative(
                f"{self.page.id}:a{len(self.alternatives):05d}", scope_id, choice.bbox,
                choice.reason, (
                    ReadingCandidate("columns", tuple(mapping[i] for i in choice.columns)),
                    ReadingCandidate("rows", tuple(mapping[i] for i in choice.rows)),
                ),
            ))
        return tuple(result)

    def build_table(self, index: int) -> Table:
        grid = self.grids[index]
        xs = sorted({b.x0 for b in grid.cells} | {b.x1 for b in grid.cells})
        ys = sorted({b.top for b in grid.cells} | {b.bottom for b in grid.cells})
        rows: dict[int, list[TableCell]] = defaultdict(list)
        for cell_index, box in enumerate(grid.cells):
            row, column = ys.index(box.top), xs.index(box.x0)
            cell_id = f"{grid.id}:cell{cell_index:05d}"
            content = self.content((index, cell_index), cell_id)
            rows[row].append(TableCell(cell_id, box, row, column,
                                       ys.index(box.bottom) - row, xs.index(box.x1) - column,
                                       content, box in grid.inferred))
        table = Table(grid.id, grid.bbox,
                      tuple(TableRow(row, tuple(sorted(cells, key=lambda c: c.column_index)))
                            for row, cells in sorted(rows.items())), grid.vector_ids)
        self.tables.append(table)
        return table


def infer_page(page: Page, config: LayoutConfig | None = None) -> PageLayout:
    """Infer a single page; every source fragment has one canonical owner."""
    config = config or LayoutConfig()
    if config.line_tolerance <= 0 or config.ruling_tolerance <= 0 or config.minimum_column_lines < 1:
        raise ValueError("Layout tolerances and minimum column support must be positive")
    builder = _Builder(page, config)
    reading_order = builder.content(None, page.id)
    result = PageLayout(page.id, tuple(builder.lines), tuple(builder.regions), tuple(builder.tables),
                        reading_order, tuple(builder.alternatives), tuple(builder.warnings))
    actual = result.ordered_fragment_ids()
    expected = [fragment.id for fragment in page.fragments]
    if len(set(expected)) != len(expected):
        raise ValueError("Source fragment IDs must be unique within a page")
    if Counter(actual) != Counter(expected):
        raise RuntimeError("Layout ownership lost or duplicated source fragments")
    return result


def iter_layout(pages: Iterable[Page], config: LayoutConfig | None = None) -> Iterator[PageLayout]:
    """Process/yield one page at a time; no document-wide layout state or cache."""
    for page in pages:
        yield infer_page(page, config)
