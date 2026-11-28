"""Differential tests for the optional straight-line paint path."""

from dataclasses import asdict
from importlib.metadata import version
from json import dumps
from pathlib import Path

import pdfplumber
import pytest
from pdfminer import settings
from pdfminer.layout import LTContainer, LTCurve
from pdfminer.utils import apply_matrix_pt, get_bound

from pdfgyeol import extract_pdf
from pdfgyeol.extraction import _NativePage, _extract_page
from test_extraction import pdf_stream, write_pdf


def _vector_items(layout):
    for item in layout:
        if isinstance(item, LTContainer):
            yield from _vector_items(item)
        elif isinstance(item, LTCurve):
            yield (type(item).__name__, item.__dict__)


def _typed(value):
    if type(value) is float:
        return float, value.hex()
    if isinstance(value, (tuple, list)):
        return type(value), [_typed(item) for item in value]
    if isinstance(value, dict):
        return type(value), {key: _typed(item) for key, item in value.items()}
    return type(value), value


def test_straight_line_geometry_matches_stock() -> None:
    native = pytest.importorskip("pdfgyeol._native")
    for matrix, first, last in [
        ((0.5, -0.125, 0.25, 1.0, 14, -0.0), ("m", 6.5, -2.0), ("l", -4.25, 10.0)),
        ((-1.0, -0.0, -0.0, -1.0, -0.0, -0.0), ("m", 0.0, 0.0), ("l", -0.0, -0.0)),
        ((0, 1, -1, 0, 100, -20), ("m", 2.0, 8.0), ("l", 2.0, 8.0)),
    ]:
        points = apply_matrix_pt(matrix, first[1:]), apply_matrix_pt(matrix, last[1:])
        bounds = get_bound(points)
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        assert _typed(native.straight_line(matrix, first, last)) == _typed(
            (*points, bounds, width, height),
        )
    assert native.straight_line((1, 0, 0, 1, 0, 0), ("m", 0.0, 0.0), ("l", 2147483647.0, 0.0)) is None
    assert native.straight_line((1, 0, 0, 1, 0, 0), ("m", 0.0, 0.0), ("l", float("inf"), 0.0)) is None


def test_native_paint_preserves_full_path_and_form_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    if (version("pdfplumber"), version("pdfminer.six")) != ("0.11.11", "20260107"):
        pytest.skip("native PDF extraction requires the audited dependencies")
    native = pytest.importorskip("pdfgyeol._native")
    from pdfgyeol import _native_extraction

    path = tmp_path / "vector-paint.pdf"
    content = (
        b"[3 2] 1 d 0.5 w 0.1 0.2 0.3 RG 0.4 g "
        b"/Span << /MCID 3 >> BDC -0.0 0 m 10 10 l S 0 0 m 10 20 l h S "
        b"q 0.5 -0.125 0.25 1 14 -0.0 cm 1 2 m 11 21 l s "
        b"2 3 m 12 22 l f 3 4 m 13 23 l f* 4 5 m 14 24 l B 5 6 m 15 25 l B* "
        b"6 7 m 16 26 l b 7 8 m 17 27 l b* Q EMC "
        b"/Pattern CS /Hatch SCN /Pattern cs /Tint scn "
        b"8 9 m 18 28 l B* "
        b"0.2 0.4 0.6 /Tint scn 9 10 m 19 29 l B "
        b"10 11 m 20 30 l 11 12 m 21 31 l S "
        b"12 13 m 22 32 l 13 14 l S 1 2 3 4 re S "
        b"12 13 m 24 5 l 12 13 l h S "
        b"14 15 m 21 22 31 32 41 42 c S "
        b"15 16 m 2147489000 34 l S "
        b"/Bad 17 m 24 35 l S "
        b"16 17 m 25 36 l F n 17 18 m 26 37 l W n 17 18 m 26 37 l W* n "
        b"/Form Do 18 19 m 28 38 l S "
        b"/Span << /MCID 12 >> BDC S EMC"
    )
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 100 100] "
    write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [-10 -20 200 180] /Rotate 90 "
        b"/Resources << /XObject << /Form 5 0 R >> /Pattern << /Hatch 6 0 R /Tint 6 0 R >> >> "
        b"/Contents 4 0 R >>",
        pdf_stream(content),
        pdf_stream(b"/Point << /MCID 7 >> BDC 30 40 m 50 60 l S EMC q "
                   b"1 0 0 1 8 6 cm /Inner Do Q 32 42 m 52 62 l S",
                   form + b"/Resources << /XObject << /Inner 7 0 R >> >> "
                   b"/Matrix [1.125 0 0 1.25 12 24]"),
        pdf_stream(b"0 G 0 0 m 1 1 l S",
                   b"/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 "
                   b"/BBox [0 0 2 2] /XStep 2 /YStep 2 /Resources << >>"),
        pdf_stream(b"/Point << /MCID 9 >> BDC 31 41 m 51 61 l B* EMC", form),
    ])
    attempts = []

    def track(matrix, first, last):
        plan = native.straight_line(matrix, first, last)
        attempts.append((first[1], plan is not None))
        return plan

    monkeypatch.setattr(_native_extraction, "_straight_line", track)
    document = extract_pdf(path)
    assert attempts == [(x, x != 15) for x in (-0.0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 30, 31, 32, 18)]
    assert any("Cannot start new subpath" in record.message for record in caplog.records)
    with pdfplumber.open(path) as pdf:
        stock = pdf.pages[0]
        native_page = _NativePage(pdf, stock.page_obj, stock.page_number)
        assert _typed(list(_vector_items(native_page.layout))) == _typed(list(_vector_items(stock.layout)))
        assert dumps(asdict(document.pages[0])) == dumps(asdict(_extract_page(stock, document.id)))

    attempts.clear()
    monkeypatch.setattr(settings, "STRICT", True)
    assert extract_pdf(path) == document
    assert not attempts
