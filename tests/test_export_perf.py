"""Regression checks for faster traversal and export of immutable parse records."""

import copy
import json
import pickle
from dataclasses import asdict, replace

import pytest

from pdfgyeol import (
    BBox, DecisionRecord, InsuranceAnnotations, InsuranceSection, InsuranceSpan, Page,
    PageKind, PageLayout, ParsedDocument, ParsedPage, PathSegment, PatternColor, Region,
    Table, TableCell, TableRow, TextFragment, TextLine, TextStyle, VectorEvidence,
)


@pytest.fixture
def document():
    page_id = "p1"
    box = BBox(0, 0, 20, 20)
    style = TextStyle("Test font", 12, True, PatternColor("무늬", (0.25,)), (0.1, 0.2, 0.3))
    fragments = tuple(
        TextFragment(f"f{i}", page_id, 1, i, text, box, style, (1, 0, 0, 1, 0, 0), 20)
        for i, text in enumerate(("가 Café", "two", "three"))
    )
    vector = VectorEvidence(
        "v1", page_id, 1, 0, "line", box, ((0, 0), (20, 20)),
        (PathSegment("m", ((0, 0),)), PathSegment("l", ((20, 20),))),
        1, True, False, False, (0.0,), None, ((2.0, 1.0), 0.0),
    )
    source = Page(page_id, 1, 20, 20, 0, box, PageKind.TEXT, fragments, (vector,), ())
    lines = tuple(TextLine(f"l{i}", box, (fragment.id,), 12) for i, fragment in enumerate(fragments))
    regions = (
        *(Region(f"r{i}", "text", box, (line.id,)) for i, line in enumerate(lines)),
        Region("outer", "table", box, table_id="t1"),
        Region("inner", "table", box, table_id="t2"),
    )
    outer_cell = TableCell("outer-cell", box, 0, 0, 1, 1, ("inner", "r2"))
    empty_cell = TableCell("empty-cell", box, 0, 0, 1, 1, ())
    inner_cell = TableCell("inner-cell", box, 0, 1, 1, 1, ("r1", "r0"))
    tables = (
        Table("t1", box, (TableRow(0, (outer_cell,)),), (vector.id,)),
        Table("t2", box, (TableRow(0, (empty_cell, inner_cell)),), ()),
    )
    layout = PageLayout(page_id, lines, regions, tables, ("outer",), (), ())
    decision = DecisionRecord("choice", page_id, 0, 1, "applied", "margin", ("col", "row"), (0.1, 0.9))
    heading = InsuranceSpan(page_id, "r1", ("l1",))
    insurance = InsuranceAnnotations((InsuranceSection("article", heading, None, (heading,)),))
    return ParsedDocument("doc", "sha", "source", "extractor", True,
                          (ParsedPage(source, layout, (decision,)),), insurance=insurance)


def _legacy_json(document, indent):
    data = asdict(document)
    if document.insurance is None:
        del data["insurance"]
    for page_data, page in zip(data["pages"], document.pages):
        page_data["text"] = page.text
    return json.dumps(data, ensure_ascii=False, allow_nan=False, indent=indent) + "\n"


@pytest.mark.parametrize("indent", [None, 0, 2, 4, "\t"])
def test_export_matches_legacy_json_bytes(document, indent):
    for annotations in (None, InsuranceAnnotations(()), document.insurance):
        variant = replace(document, insurance=annotations)
        expected = _legacy_json(variant, indent)
        assert variant.to_json(indent=indent).encode("utf-8") == expected.encode("utf-8")
        assert variant.to_dict() == json.loads(expected)


def test_to_dict_returns_independent_plain_json_values(document):
    expected = json.loads(_legacy_json(document, None))
    result = document.to_dict()
    source = result["pages"][0]["source"]
    assert type(source["kind"]) is str
    source["fragments"][0]["style"]["stroking_color"]["base_components"].append(1)
    source["vectors"][0]["dash"][0].clear()
    result["insurance"]["sections"][0]["heading"]["line_ids"].clear()
    result["pages"][0]["layout"]["tables"][0]["rows"].clear()
    assert document.to_dict() == expected
    assert document.to_json() == _legacy_json(document, 2)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_export_rejects_nonfinite_values_in_nested_records(document, value):
    page, = document.pages
    record, = page.decisions
    invalid = replace(document, pages=(replace(page, decisions=(replace(record, scores=(value,)),)),))
    with pytest.raises(ValueError, match="Out of range float values"):
        invalid.to_json()
    with pytest.raises(ValueError, match="Out of range float values"):
        invalid.to_dict()


def test_cell_lookup_keeps_nested_order_and_dataclass_behavior(document):
    page, = document.pages
    original_data, original_hash = asdict(page), hash(page)
    expected_json = _legacy_json(document, 2)
    for _ in range(2):
        assert page.cell_text("outer-cell") == "two\n가 Café\nthree"
        assert page.cell_text("inner-cell") == "two\n가 Café"
        assert page.cell_text("empty-cell") == ""
        with pytest.raises(KeyError) as exc:
            page.cell_text("unknown")
        assert exc.value.args == ("unknown",)

    assert asdict(page) == original_data
    assert hash(page) == original_hash
    assert document.to_json() == expected_json
    for duplicate in (copy.copy(page), copy.deepcopy(page), pickle.loads(pickle.dumps(page))):
        assert duplicate == page
        assert duplicate.cell_text("inner-cell") == "two\n가 Café"

    first, *rest = page.source.fragments
    source = replace(page.source, fragments=(replace(first, text="updated"), *rest))
    assert replace(page, source=source).cell_text("inner-cell") == "two\nupdated"
