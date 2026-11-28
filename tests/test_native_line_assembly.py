"""Differential checks for bounded source line assembly."""

from dataclasses import asdict
from importlib.metadata import version
from json import dumps
from pathlib import Path
from unittest.mock import patch

import pdfplumber
import pytest
from pdfminer.layout import LTLine

from pdfgyeol import extract_pdf
from pdfgyeol import extraction
from test_extraction import pdf_stream, write_pdf


@pytest.fixture
def native():
    if (version("pdfplumber"), version("pdfminer.six")) != ("0.11.11", "20260107"):
        pytest.skip("native PDF extraction requires the audited dependencies")
    return pytest.importorskip("pdfgyeol._native")


def _drawing(path: Path, rotation: int = 0, repeated: int = 0, long_dash: bool = False) -> None:
    page = (f"<< /Type /Page /Parent 2 0 R /Rotate {rotation} /MediaBox [-10.25 -20 200 180] "
            "/Resources << /Font << /F1 8 0 R >> /XObject << /Form 7 0 R >> "
            "/ColorSpace << /TintSpace [/Pattern /DeviceRGB] >> "
            "/Pattern << /Ink 9 0 R /Tint 11 0 R >> >> /Contents ").encode()
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 100 100] "
    mixed = (
        b"BT /F1 10 Tf 30 95 Td (Drawing) Tj ET "
        b"q -1.0 -0.0 -0.0 -1.0 -0.0 -0.0 cm 0.0 0.0 m -0.0 -0.0 l S Q "
        b"[3 -0.0 4.25] 1 d .5 w 0.1 0.2 0.3 RG 0.4 g "
        b"/Span << /MCID 3 >> BDC 2 3 m 4 8 l h B* EMC "
        b"10 11 m 20 30 l 11 12 m 21 31 l S "
        b"12 13 m 24 5 l 12 13 l h S "
        b"1 2 3 4 re S 14 15 m 21 22 31 32 41 42 c S "
        b"/Pattern CS /Ink SCN /TintSpace cs 0.2 0.4 0.6 /Tint scn "
        b"18 19 m 28 38 l B /Form Do "
    )
    prefix = b"3 4 m 15 24 l S\n" * repeated
    if long_dash:
        prefix += b"q [" + b"2 " * 33 + b"] 1 d 3 4 m 20 26 l S Q\n"
    write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        page + b"5 0 R >>",
        page + b"6 0 R >>",
        pdf_stream(prefix + mixed),
        pdf_stream(mixed),
        pdf_stream(b"25 30 m 35 48 l S q 1 0 0 1 8 6 cm /Inner Do Q",
                   form + b"/Resources << /XObject << /Inner 10 0 R >> >> "
                   b"/Matrix [1.125 0 0 1.25 12 24]"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        pdf_stream(b"0 G 0 0 m 1 1 l S",
                   b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 "
                   b"/BBox [0 0 2 2] /XStep 2 /YStep 2 /Resources << >>"),
        pdf_stream(b"/Point << /MCID 8 >> BDC 31 41 m 51 61 l f* EMC", form),
        pdf_stream(b"0 0 m 1 1 l S",
                   b"/Type /Pattern /PatternType 1 /PaintType 2 /TilingType 1 "
                   b"/BBox [0 0 2 2] /XStep 2 /YStep 2 /Resources << >>"),
    ])


def _document_json(path: Path) -> str:
    return dumps(asdict(extract_pdf(path)))


def _stock_pages(path: Path, document_id: str) -> str:
    with pdfplumber.open(path) as pdf:
        return dumps([asdict(extraction._extract_page(page, document_id)) for page in pdf.pages])


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_line_batches_match_stock_without_intermediate_line_dicts(
    native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rotation: int,
) -> None:
    path = tmp_path / "drawing.pdf"
    _drawing(path, rotation)
    assemble = native.assemble_lines
    project = extraction._NativePage.process_object
    calls = []

    def track(lines, page_id, page_number, start, *args):
        records = assemble(lines, page_id, page_number, start, *args)
        assert records is not None
        calls.append((page_number, start, len(records)))
        return records

    def remaining_only(self, obj):
        assert type(obj) is not LTLine
        return project(self, obj)

    monkeypatch.setattr(extraction, "_LINE_BATCH", 2)
    monkeypatch.setattr(native, "assemble_lines", track)
    monkeypatch.setattr(extraction._NativePage, "process_object", remaining_only)
    document = extract_pdf(path)
    assert dumps([asdict(page) for page in document.pages]) == _stock_pages(path, document.id)
    for page in document.pages:
        lines = [vector for vector in page.vectors if vector.kind == "line"]
        assert [(start, count) for number, start, count in calls if number == page.page_number] == [
            (i, min(2, len(lines) - i)) for i in range(0, len(lines), 2)
        ]
        assert any(tuple(seg.operator for seg in line.path) == ("m", "l", "l", "h") for line in lines)
        assert tuple(v.kind for v in page.vectors[-2:]) == ("rect", "curve")


@pytest.mark.parametrize("binding_error", [False, True])
def test_late_line_fallback_clears_prior_batches_and_keeps_glyphs(
    native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding_error: bool,
) -> None:
    path = tmp_path / "many-lines.pdf"
    _drawing(path, repeated=2048, long_dash=not binding_error)
    assemble = native.assemble_lines
    results = []

    def track(lines, page_id, page_number, start, *args):
        if binding_error and page_number == 1 and start == 2048:
            results.append((1, start, False))
            raise OverflowError("controlled conversion failure")
        records = assemble(lines, page_id, page_number, start, *args)
        results.append((page_number, start, records is not None))
        return records

    with patch.object(extraction._NativePage, "_source_lines", return_value=None):
        expected = _document_json(path)
    monkeypatch.setattr(native, "assemble_lines", track)
    with patch.object(native, "assemble_glyphs", wraps=native.assemble_glyphs) as glyphs:
        assert _document_json(path) == expected
    assert glyphs.call_count == 2
    assert results == [(1, 0, True), (1, 1024, True), (1, 2048, False), (2, 0, True)]


def test_missing_line_symbol_and_glyph_rejection_keep_original_output(
    native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "drawing.pdf"
    _drawing(path)
    expected = _document_json(path)
    with monkeypatch.context() as context:
        context.delattr(native, "assemble_lines")
        assert _document_json(path) == expected
    monkeypatch.setattr(native, "assemble_glyphs", lambda *_: None)
    with patch.object(native, "assemble_lines", side_effect=AssertionError("line assembly after glyph rejection")):
        assert _document_json(path) == expected


def test_late_unsupported_dash_preserves_public_error(
    native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "drawing.pdf"
    _drawing(path, repeated=2048)
    layout = extraction._NativePage.layout.fget

    def with_injected_dash(self):
        result = layout(self)
        if self.page_number == 1:
            lines = (item for item in extraction._layout_leaves(result._objs) if type(item) is LTLine)
            for index, line in enumerate(lines):
                if index == 2048:
                    line.dashing_style = (None, 0)
                    break
        return result

    monkeypatch.setattr(extraction._NativePage, "layout", property(with_injected_dash))
    with patch.object(extraction._NativePage, "_source_lines", return_value=None):
        with pytest.raises(TypeError) as old:
            extract_pdf(path)
    with patch.object(native, "assemble_lines", wraps=native.assemble_lines) as builder:
        with pytest.raises(TypeError) as new:
            extract_pdf(path)
    assert builder.call_count == 3
    assert type(old.value) is type(new.value) and str(old.value) == str(new.value)
