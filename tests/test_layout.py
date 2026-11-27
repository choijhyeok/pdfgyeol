"""Generated non-insurance PDFs with explicit expected order and table labels."""

from collections import Counter
from dataclasses import replace

import pytest
from reportlab.pdfgen.canvas import Canvas

from pdfgyeol import LayoutConfig, extract_pdf, infer_page, iter_layout


def pdf_page(tmp_path, draw, *, pagesize=(600, 800)):
    path = tmp_path / "layout.pdf"
    canvas = Canvas(str(path), pagesize=pagesize, invariant=1)
    canvas.setFont("Helvetica", 10)
    draw(canvas)
    canvas.save()
    return extract_pdf(path).pages[0]


def text(page, layout):
    lookup = {f.id: f.text for f in page.fragments}
    return "".join(lookup[i] for i in layout.ordered_fragment_ids())


def region_text(page, layout, ids):
    regions = {r.id: r for r in layout.regions}
    lines = {line.id: line for line in layout.lines}
    fragments = {f.id: f.text for f in page.fragments}
    return "".join(fragments[f] for r in ids for line in regions[r].line_ids for f in lines[line].fragment_ids)


def exactly_once(page, layout):
    assert Counter(layout.ordered_fragment_ids()) == Counter(f.id for f in page.fragments)
    assert len(layout.ordered_fragment_ids()) == len(set(layout.ordered_fragment_ids()))
    assert Counter(f for line in layout.lines for f in line.fragment_ids) == Counter(f.id for f in page.fragments)


def grid(canvas, xs, ys):
    for y in ys:
        canvas.line(xs[0], y, xs[-1], y)
    for x in xs:
        canvas.line(x, ys[0], x, ys[-1])


def test_two_columns_full_width_headings_and_embedded_table(tmp_path):
    def draw(c):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(40, 760, "Field station observations across the valley")
        c.setFont("Helvetica", 10)
        # Emit right column first, deliberately interleave native drawing order.
        for y, label in [(720, "R1"), (700, "R2"), (680, "R3"), (660, "R4")]:
            c.drawString(340, y, label)
        c.drawString(40, 720, "L1")
        c.drawString(40, 700, "L2")
        grid(c, [40, 130, 250], [620, 645, 670])
        for x, y, label in [(45, 653, "Peas"), (135, 653, "12"), (45, 628, "Beans"), (135, 628, "8")]:
            c.drawString(x, y, label)
        c.drawString(40, 600, "L3 after table")
        c.setFont("Helvetica-Bold", 18)
        c.drawString(40, 560, "Evening observations across the whole valley")
        c.setFont("Helvetica", 10)
        for y, left, right in [(530, "L4", "R5"), (510, "L5", "R6"), (490, "L6", "R7"), (470, "L7", "R8")]:
            c.drawString(340, y, right)
            c.drawString(40, y, left)
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert text(page, layout) == (
        "Field station observations across the valleyL1L2Peas12Beans8L3 after tableR1R2R3R4"
        "Evening observations across the whole valleyL4L5L6L7R5R6R7R8"
    )
    assert len(layout.tables) == 1
    table = layout.tables[0]
    assert [[region_text(page, layout, cell.region_ids) for cell in row.cells] for row in table.rows] == [
        ["Peas", "12"], ["Beans", "8"],
    ]
    assert len([r for r in layout.regions if r.kind == "heading"]) == 2
    exactly_once(page, layout)
    assert infer_page(page) == layout


def test_parallel_tables_are_atomic_and_resume_prose(tmp_path):
    def draw(c):
        c.drawString(40, 760, "Before")
        for x, prefix in [(40, "A"), (340, "B")]:
            grid(c, [x, x + 70, x + 200], [610, 650, 690, 730])
            for row in range(3):
                c.drawString(x + 5, 705 - row * 40, prefix + str(row))
                c.drawString(x + 75, 705 - row * 40, "value" + str(row))
        c.drawString(40, 580, "After both parallel tables, resume the station notebook across the page")
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert len(layout.tables) == 2
    assert text(page, layout) == ("BeforeA0value0A1value1A2value2B0value0B1value1B2value2"
                                  "After both parallel tables, resume the station notebook across the page")
    exactly_once(page, layout)


def test_nested_table_has_one_owner_and_cell_hierarchy(tmp_path):
    def draw(c):
        grid(c, [40, 260, 560], [400, 700])
        c.drawString(50, 680, "Outer key")
        c.drawString(280, 680, "Nested measurements")
        grid(c, [280, 380, 540], [450, 520, 590])
        for x, y, label in [(290, 560, "Depth"), (390, 560, "12"), (290, 490, "Width"), (390, 490, "8")]:
            c.drawString(x, y, label)
        c.drawString(280, 420, "Cell tail")
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert len(layout.tables) == 2
    assert text(page, layout) == "Outer keyNested measurementsDepth12Width8Cell tail"
    assert len(layout.reading_order) == 1
    root = next(r for r in layout.regions if r.id == layout.reading_order[0])
    outer = next(t for t in layout.tables if t.id == root.table_id)
    assert len(outer.rows[0].cells[1].region_ids) == 3
    exactly_once(page, layout)


def test_merged_cells_empty_cells_and_wrapped_unruled_rows(tmp_path):
    def draw(c):
        # Header spans both columns; body has separate key/value cells.
        c.rect(40, 400, 210, 330)
        c.line(40, 700, 250, 700)
        c.line(80, 400, 80, 700)
        c.drawString(50, 710, "Plant observations")
        for y, key, label in [(670, "A", "Oak"), (620, "B", "Silver birch"), (550, "C", "Pine")]:
            c.drawString(50, y, key)
            c.drawString(90, y, label)
        c.drawString(90, 605, "with two stems")
        c.drawString(50, 605, " ")  # Blank continuation label stays in prior row.
        # Independent grid with a genuinely empty cell.
        grid(c, [340, 420, 540], [500, 550, 600])
        c.drawString(350, 570, "Empty")
        c.drawString(430, 520, "Kept")
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    left = min(layout.tables, key=lambda t: t.bbox.x0)
    assert left.rows[0].cells[0].column_span == 2
    assert [[region_text(page, layout, cell.region_ids) for cell in row.cells] for row in left.rows] == [
        ["Plant observations"], ["A", "Oak"], ["B ", "Silver birchwith two stems"], ["C", "Pine"],
    ]
    assert all(cell.inferred for row in left.rows[1:] for cell in row.cells)
    assert any(not cell.region_ids for table in layout.tables for row in table.rows for cell in row.cells)
    exactly_once(page, layout)


@pytest.mark.parametrize("jitter", [0, .2])
def test_sparse_ambiguous_region_has_deterministic_permutations(tmp_path, jitter):
    def draw(c):
        for x, y, label in [(40, 700.9, "Seed"), (340, 700.9 + jitter, "Cost"),
                            (40, 670.9, "Peas"), (340, 670.9 + jitter, "12")]:
            c.drawString(x, y, label)
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert len(layout.alternatives) == 1
    choice = layout.alternatives[0]
    assert choice.scope_id == page.id
    assert choice.default_index == 0
    assert choice.candidates[0].region_ids != choice.candidates[1].region_ids
    assert set(choice.candidates[0].region_ids) == set(choice.candidates[1].region_ids)
    assert region_text(page, layout, choice.candidates[0].region_ids) == "SeedPeasCost12"
    assert region_text(page, layout, choice.candidates[1].region_ids) == "SeedCostPeas12"
    assert layout == infer_page(page)
    for candidate in choice.candidates:
        exactly_once(page, replace(layout, reading_order=candidate.region_ids))
    exactly_once(page, layout)


@pytest.mark.parametrize("count", [4, 8])
def test_spaced_measurements_have_a_bounded_row_alternative(tmp_path, count):
    pairs = [
        ("Temperature", "18 C"), ("Humidity", "45 %"), ("Pressure", "1012 hPa"), ("Wind", "3 m/s"),
        ("Rain", "0 mm"), ("Dew point", "9 C"), ("Visibility", "12 km"), ("Elevation", "140 m"),
    ][:count]
    heading = "Measurement log for the North Ridge observation station"
    footer = "End of measurements for the North Ridge observation station"

    def draw(c):
        if count > 4:
            c.setFont("Helvetica-Bold", 13)
            c.drawString(35, 460, heading)
            c.drawString(35, 100, footer)
            c.setFont("Helvetica", 10)
        for j, (label, value) in enumerate(pairs):
            y = 400 - 28 * j
            c.drawString(35, y, label)
            if count == 4:
                c.drawString(250, y, value)
            else:
                c.drawRightString(310, y + .2, value)

    page = pdf_page(tmp_path, draw, pagesize=(400, 500))
    layout = infer_page(page)
    assert len(layout.alternatives) == 1
    choice = layout.alternatives[0]
    assert choice.scope_id == page.id
    assert choice.reason == "aligned_label_value_rows"
    columns, rows = choice.candidates
    assert choice.default_index == 0
    assert columns.name == "columns" and rows.name == "rows"
    assert len(columns.region_ids) == len(rows.region_ids) == count * 2
    assert set(columns.region_ids) == set(rows.region_ids)
    assert columns.region_ids != rows.region_ids
    assert region_text(page, layout, columns.region_ids) == "".join(a for a, _ in pairs) + "".join(b for _, b in pairs)
    assert region_text(page, layout, rows.region_ids) == "".join(a + b for a, b in pairs)
    start = layout.reading_order.index(columns.region_ids[0])
    end = start + count * 2
    assert layout.reading_order[start:end] == columns.region_ids
    reordered = replace(layout, reading_order=layout.reading_order[:start] + rows.region_ids + layout.reading_order[end:])
    expected = "".join(a + b for a, b in pairs)
    assert text(page, reordered) == (heading + expected + footer if count > 4 else expected)
    assert not layout.tables
    assert infer_page(page) == layout
    exactly_once(page, layout)
    exactly_once(page, reordered)


@pytest.mark.parametrize("pitch", [13, 28])
def test_aligned_independent_prose_columns_stay_column_first(tmp_path, pitch):
    left = ["The team followed the river upstream", "and noted several nesting sites along",
            "the bank. Their survey ended where", "the path led past the old stone bridge."]
    right = ["The second group began at the ridge", "and recorded changes in air pressure",
             "before heading down the eastern slope", "to collect the instruments before dusk."]

    def draw(c):
        for j, (a, b) in enumerate(zip(left, right)):
            y = 400 - pitch * j
            c.drawString(35, y, a)
            c.drawString(250, y, b)

    page = pdf_page(tmp_path, draw, pagesize=(440, 500))
    layout = infer_page(page)
    assert not layout.alternatives
    assert text(page, layout) == "".join(left + right)
    exactly_once(page, layout)


@pytest.mark.parametrize(("count", "pitch", "blank_labels"), [(4, 13, False), (17, 22, False), (4, 28, True)])
def test_longer_row_alternatives_require_spaced_nonblank_entries_within_budget(tmp_path, count, pitch, blank_labels):
    labels = [" " if blank_labels else f"Sample {j}" for j in range(count)]

    def draw(c):
        for j, label in enumerate(labels):
            y = 400 - pitch * j
            c.drawString(35, y, label)
            c.drawString(250, y, f"{j} cm")

    page = pdf_page(tmp_path, draw, pagesize=(400, 500))
    layout = infer_page(page)
    assert not layout.alternatives
    assert text(page, layout) == "".join(labels) + "".join(f"{j} cm" for j in range(count))
    exactly_once(page, layout)


def test_overprints_spaces_and_blank_page_are_preserved(tmp_path):
    def draw(c):
        c.drawString(40, 700, "A  B")
        c.drawString(40, 700, "A  B")
        c.drawString(40, 680, "   ")
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert Counter(text(page, layout)) == Counter("A  BA  B   ")
    exactly_once(page, layout)
    blank = replace(page, fragments=(), vectors=(), images=())
    assert infer_page(blank).ordered_fragment_ids() == ()


def test_page_iterator_is_lazy_and_has_no_cross_page_state(tmp_path):
    page = pdf_page(tmp_path, lambda c: c.drawString(40, 700, "Small lab log"))
    requested = []
    def pages():
        for index in range(200):
            requested.append(index)
            yield replace(page, id=f"page:{index}")
    iterator = iter_layout(pages())
    assert requested == []
    assert next(iterator).page_id == "page:0"
    assert requested == [0]
    assert len(list(iterator)) == 199


def test_complexity_budget_is_explicit_without_text_loss(tmp_path):
    def draw(c):
        grid(c, [40, 100, 200], [500, 600, 700])
        c.drawString(50, 650, "Budget")
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page, LayoutConfig(max_table_edges=1))
    assert layout.warnings == ("table_edge_budget_exceeded",)
    exactly_once(page, layout)


def test_filled_hairline_rulings_and_broad_fills(tmp_path):
    def draw(c):
        c.rect(40, 650, 220, 80, stroke=0, fill=1)  # Broad paint isn't a grid.
        for y in (400, 450, 500):
            c.rect(300, y, 250, .4, stroke=0, fill=1)
        for x in (300, 360, 550):
            c.rect(x, 400, .4, 100, stroke=0, fill=1)
        c.drawString(310, 470, "A")
        c.drawString(370, 470, "Maple")
        c.drawString(310, 420, "B")
        c.drawString(370, 420, "Elm")
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert len(layout.tables) == 1
    assert [[region_text(page, layout, c.region_ids) for c in row.cells] for row in layout.tables[0].rows] == [
        ["A", "Maple"], ["B", "Elm"],
    ]
    exactly_once(page, layout)


def test_three_columns_and_paragraph_regions(tmp_path):
    def draw(c):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(40, 760, "Research notes from three observation stations")
        c.setFont("Helvetica", 10)
        for x, prefix in [(420, "C"), (40, "A"), (230, "B")]:
            for row in range(4):
                c.drawString(x, 700 - row * 14, prefix + str(row))
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert text(page, layout) == "Research notes from three observation stationsA0A1A2A3B0B1B2B3C0C1C2C3"
    assert len(layout.reading_order) == 4
    assert [len(r.line_ids) for r in layout.regions if r.kind == "text"] == [4, 4, 4]
    assert not layout.alternatives
    exactly_once(page, layout)


def test_rowspan_cell_is_not_repeated_in_later_row(tmp_path):
    def draw(c):
        c.rect(40, 400, 260, 300)
        c.line(120, 400, 120, 700)
        c.line(40, 600, 300, 600)
        c.line(120, 500, 300, 500)
        for x, y, label in [(50, 650, "Group"), (130, 650, "Result"), (50, 480, "Shared"),
                            (130, 550, "Upper"), (130, 450, "Lower")]:
            c.drawString(x, y, label)
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    table = layout.tables[0]
    assert table.rows[1].cells[0].row_span == 2
    assert len(table.rows[2].cells) == 1
    assert table.rows[2].cells[0].column_index == 1
    assert text(page, layout) == "GroupResultSharedUpperLower"
    exactly_once(page, layout)


def test_non_upright_and_invalid_source_ids_are_explicit(tmp_path):
    def draw(c):
        c.saveState()
        c.translate(100, 400)
        c.rotate(90)
        c.drawString(0, 0, "Vertical")
        c.restoreState()
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert "non_upright_text_geometry_order" in layout.warnings
    exactly_once(page, layout)
    with pytest.raises(ValueError, match="unique"):
        infer_page(replace(page, fragments=page.fragments + page.fragments[:1]))
    with pytest.raises(ValueError, match="positive"):
        infer_page(page, LayoutConfig(line_tolerance=0))


def test_alternative_is_local_to_table_cell_and_preserves_other_cells(tmp_path):
    def draw(c):
        grid(c, [40, 320, 560], [400, 700])
        for x, y, label in [(50, 650, "Seed"), (200, 650, "Cost"),
                            (50, 620, "Peas"), (200, 620, "12"), (340, 650, "Other cell")]:
            c.drawString(x, y, label)
    page = pdf_page(tmp_path, draw)
    layout = infer_page(page)
    assert len(layout.alternatives) == 1
    choice = layout.alternatives[0]
    assert choice.scope_id == layout.tables[0].rows[0].cells[0].id
    for candidate in choice.candidates:
        tables = tuple(replace(table, rows=tuple(
            replace(row, cells=tuple(
                replace(cell, region_ids=candidate.region_ids) if cell.id == choice.scope_id else cell
                for cell in row.cells
            )) for row in table.rows
        )) for table in layout.tables)
        exactly_once(page, replace(layout, tables=tables))
    assert region_text(page, layout, layout.tables[0].rows[0].cells[1].region_ids) == "Other cell"
    with pytest.raises(ValueError):
        infer_page(page, LayoutConfig(line_tolerance=0))
