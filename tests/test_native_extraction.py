"""Differential checks for optional native PDF extraction."""

import logging
import subprocess
import sys
from dataclasses import asdict
from importlib.metadata import version
from json import dumps
from pathlib import Path
from zlib import compress

import pdfplumber
import pytest
from pdfminer.layout import LTChar
from pdfminer.pdfinterp import PDFContentParser
from pdfminer.pdftypes import PDFStream
from pdfminer.psparser import KWD, LIT, PSEOF, PSKeyword, keyword_name

from pdfgyeol import extract_pdf
from pdfgyeol.extraction import _NativePage, _extract_page
from test_extraction import pdf_stream, write_pdf


OPERAND_CONTENT = (
    b"% a comment with fake operators BI and ET\r\n"
    b"q [+1 2 .5 -.0 4.] 3 d\n"
    b"/Span << /MCID 17 /ActualText (Visible\\040name\\(v\\)) /Flags [true false] "
    b"/Escaped /F#31 /Nested << /Value <426F 74616E79> >> >> BDC\n"
    b"BT /F#31 12 Tf 1 0 0 1 15.25 140 Tm [(Bo) -30 <74616E79>] TJ "
    b"0 -15 Td (A(B)\\134C) ' 1.5 0.5 (Z) \" ET\n"
    b"EMC 0 0 m 2 3 l 4 7 8 9 10 11 c h B* Q"
)


@pytest.fixture
def native_content_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "native-content.pdf"
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 180] "
    font = b"/Font << /F1 10 0 R >>"
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 100 100] "
    page_content = (
        b"BT /F1 12 Tf 10 150 Td (Before) Tj ET\n"
        b"q 1 0 0 1 20 30 cm /Outer Do Q\n"
        b"BT /F1 12 Tf 10 130 Td (After) Tj ET"
    )
    write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>",
        page + b"/Resources << " + font + b" /XObject << /Outer 11 0 R >> >> /Contents 6 0 R >>",
        page + b"/Resources << " + font + b" >> /Contents [7 0 R 8 0 R] >>",
        page + b"/Resources << " + font + b" >> /Contents 9 0 R >>",
        pdf_stream(compress(page_content), b"/Filter /FlateDecode"),
        pdf_stream(b"BT /F1 12 Tf 10 140 Td [(Multi) -20"),
        pdf_stream(b" (stream)] TJ ET\n0 0 m 40 20 l S"),
        pdf_stream(OPERAND_CONTENT),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        pdf_stream(
            b"BT /F1 12 Tf 10 30 Td (Outer) Tj ET q /Inner Do Q",
            form + b"/Resources << " + font + b" /XObject << /Inner 12 0 R >> >>",
        ),
        pdf_stream(
            b"BT /F1 12 Tf 10 20 Td (Inner) Tj ET\n"
            b"q 12 0 0 10 5 20 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x80\nEI\nQ\n"
            b"BT /F1 12 Tf 10 10 Td (Image) Tj ET",
            form + b"/Resources << " + font + b" >>",
        ),
    ])
    return path


def _stock_batches(content: bytes) -> list[tuple[list, str | None]]:
    parser = PDFContentParser([PDFStream({}, content)])
    batches: list[tuple[list, str | None]] = []
    operands: list = []
    while True:
        try:
            _, value = parser.nextobject()
        except PSEOF:
            if operands:
                batches.append((operands, None))
            return batches
        if isinstance(value, PSKeyword):
            batches.append((operands, keyword_name(value)))
            operands = []
        else:
            operands.append(value)


def _typed(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return type(value), [_typed(item) for item in value]
    if isinstance(value, dict):
        return type(value), {key: _typed(item) for key, item in value.items()}
    if isinstance(value, float):
        return type(value), value.hex()
    return type(value), value


def _stock_page_json(path: Path, document_id: str) -> str:
    with pdfplumber.open(path) as pdf:
        return dumps([asdict(_extract_page(page, document_id)) for page in pdf.pages])


def test_rust_content_operands_match_pdfminer() -> None:
    native = pytest.importorskip("pdfgyeol._native")
    batches = native.parse_content(OPERAND_CONTENT, LIT, KWD)
    assert batches is not None
    assert _typed(batches) == _typed(_stock_batches(OPERAND_CONTENT))


@pytest.mark.parametrize("content", [
    b"(A\\\r\nB) Tj <4F2> Tj",
    # Splitting escaped CRLF across pdfminer's buffer affects its string result.
    b" " * 4092 + b"(A\\\r\nB) Tj",
    b"/Span << /Properties [null /F#ff] >> BDC EMC",
], ids=["continued-literal-and-odd-hex", "continued-literal-at-buffer-boundary", "nested-keyword"])
def test_rust_content_lexical_edges_match_or_defer(content: bytes) -> None:
    native = pytest.importorskip("pdfgyeol._native")
    batches = native.parse_content(content, LIT, KWD)
    if batches is not None:
        assert _typed(batches) == _typed(_stock_batches(content))


def test_native_and_fallback_content_preserve_full_extraction(
    native_content_pdf: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if (version("pdfplumber"), version("pdfminer.six")) != ("0.11.11", "20260107"):
        pytest.skip("native PDF interpreter requires the audited dependencies")
    native = pytest.importorskip("pdfgyeol._native")
    from pdfgyeol import _native_extraction

    attempts: list[tuple[bytes, bool]] = []

    def track(data: bytes, literal, keyword):
        result = native.parse_content(data, literal, keyword)
        attempts.append((data, result is not None))
        return result

    monkeypatch.setattr(_native_extraction, "_parse_content", track)
    document = extract_pdf(native_content_pdf)
    assert dumps([asdict(page) for page in document.pages]) == _stock_page_json(
        native_content_pdf, document.id,
    )
    assert ["".join(fragment.text for fragment in page.fragments) for page in document.pages] == [
        "BeforeOuterInnerImageAfter", "Multistream", "BotanyA(B)\\CZ",
    ]
    assert document.pages[0].images[0].source_size == (1, 1)
    expected_attempts = [
        (b"(Before)", True), (b"(Outer)", True), (b"(Inner)", False), (OPERAND_CONTENT, True),
    ]
    for (data, accepted), (marker, expected) in zip(attempts, expected_attempts, strict=True):
        assert marker in data
        assert accepted is expected


def test_extraction_with_unavailable_extension(native_content_pdf: Path) -> None:
    script = """
from dataclasses import asdict
from json import dumps
import sys

sys.modules['pdfgyeol._native'] = None
from pdfgyeol import extract_pdf

document = extract_pdf(sys.argv[1])
print(document.id)
print(dumps([asdict(page) for page in document.pages]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(native_content_pdf)],
        capture_output=True, text=True, check=True,
    )
    document_id, page_json = result.stdout.splitlines()
    assert page_json == _stock_page_json(native_content_pdf, document_id)


def test_native_horizontal_runs_match_stock_and_fall_back_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    if (version("pdfplumber"), version("pdfminer.six")) != ("0.11.11", "20260107"):
        pytest.skip("native PDF extraction requires the audited dependencies")
    native = pytest.importorskip("pdfgyeol._native")
    from pdfgyeol import _native_extraction

    path = tmp_path / "positioned-text.pdf"
    content = (
        b"BT /F1 12.25 Tf 77.5 Tz -0.5 Tc 1.25 Tw -0.75 Ts 11 TL "
        b"0.5 -0.125 .25 1 14 -0.0 Tm "
        b"/Span << /MCID 4 >> BDC [(A B) -24.125 () 120 (CD)] TJ "
        b"(EF) Tj (GH) ' 2.5 0.25 (I J) \" EMC ET\n"
        b"BT /F1 9 Tf -1 0 0 1 100 100 Tm [(K) /Bad (L)] TJ (M) Tj "
        b"(" + b"N" * 4097 + b") Tj (O) Tj ET\n"
        b"BT /F1 0 Tf 0 1 -1 0 -0.0 -0.0 Tm (P Q) Tj ET\n"
        b"BT /F2 9 Tf 50 130 Td <00410042> Tj ET"
    )
    write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [-10 -20 200 180] /Rotate 90 "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>",
        pdf_stream(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /TestVertical /Encoding /Identity-V "
        b"/DescendantFonts [7 0 R] /ToUnicode /Identity-V >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /TestVertical "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> >>",
    ])
    batches: list[tuple[str, ...]] = []

    def track(*args):
        result = native.horizontal_glyphs(*args)
        if result is not None:
            batches.append(tuple(row[0] for row in result[2]))
        return result

    monkeypatch.setattr(_native_extraction, "_horizontal_glyphs", track)
    document = extract_pdf(path)
    assert dumps([asdict(page) for page in document.pages]) == _stock_page_json(path, document.id)
    assert batches == [tuple(text) for text in ("A BCD", "EF", "GH", "I J", "M", "O", "P Q")]
    assert any("Cannot render horizontal string" in record.message for record in caplog.records)
    assert "".join(char.text for char in document.pages[0].fragments).endswith("OP QAB")
    with pdfplumber.open(path) as pdf:
        stock = pdf.pages[0]
        page = _NativePage(pdf, stock.page_obj, stock.page_number)
        def tags(layout):
            return [(char.mcid, char.tag) for char in layout if isinstance(char, LTChar)]

        assert tags(page.layout) == tags(stock.layout)
        assert tags(page.layout)[0] == (4, "Span")

    batches.clear()
    with caplog.at_level(logging.DEBUG, logger="pdfminer.cmapdb"):
        assert extract_pdf(path) == document
    assert not batches


def test_native_horizontal_geometry_rejects_entire_out_of_range_run() -> None:
    native = pytest.importorskip("pdfgyeol._native")
    glyph = (0.5, "A", False)
    params = ((1, 0, 0, 1, 0, 0), (0, 0), 10, 1, 0, 0, 0, 0.01, -0.25)
    assert native.horizontal_glyphs([glyph], *params) is not None
    assert native.horizontal_glyphs([glyph, 1 << 60], *params) is None
    assert native.horizontal_glyphs([glyph, (1e308, "B", False)], *params) is None
    assert native.horizontal_glyphs([glyph] * 4097, *params) is None
