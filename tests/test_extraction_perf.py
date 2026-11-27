"""Differential checks for the character projection against native pdfplumber."""

from dataclasses import asdict
from json import dumps
from pathlib import Path

import pdfplumber
import pytest

from pdfgyeol import PatternColor, extract_pdf
from pdfgyeol import extraction
from test_extraction import pdf_stream, write_pdf


@pytest.fixture
def projection_pdf(tmp_path: Path, rotation: int) -> Path:
    path = tmp_path / "projection.pdf"
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 100 100]"
    pattern = b"/Type /Pattern /PatternType 1 /TilingType 1 /BBox [0 0 2 2] /XStep 2 /YStep 2"
    write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [20.25 -30.1 320.05 230.3] "
         f"/CropBox [30 -20 280 210] /Rotate {rotation} ".encode()
         + b"/Resources << /Font << /F1 4 0 R /FBytes 10 0 R >> "
         b"/XObject << /Outer 6 0 R >> "
         b"/Pattern << /Hatch 8 0 R /Tint 8 0 R >> >> /Contents 5 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 9 0 R >>",
        pdf_stream(
            b"/Pattern CS /Hatch SCN /Pattern cs /Tint scn\n"
            b"BT /F1 12 Tf 2 Tr 1 0.2 0 1 50 80 Tm (A) Tj ET\n"
            b"q 0.75 0 0 1.2 70 80 cm /Outer Do Q\n"
            b"0.4 G 0.1 0.2 0.3 0.4 k BT /FBytes 12 Tf 90 140 Td (Z) Tj ET"
        ),
        pdf_stream(
            b"BT /F1 12 Tf 10 20 Td (B) Tj ET\n"
            b"q 1 0 0 1 20 20 cm /Inner Do Q\n"
            b"BT /F1 14 Tf 10 40 Td (D) Tj ET",
            form + b" /Resources << /Font << /F1 4 0 R >> /XObject << /Inner 7 0 R >> >>",
        ),
        pdf_stream(
            b"BT /F1 12 Tf 10 10 Td (C) Tj ET 10 10 20 20 re S",
            form + b" /Resources << /Font << /F1 4 0 R >> >>",
        ),
        pdf_stream(b"0 G 0 0 m 2 2 l S", pattern + b" /PaintType 1 /Resources << >>"),
        pdf_stream(b"begincmap 1 beginbfchar <41> <fb01> endbfchar endcmap"),
        (b"<< /Type /Font /Subtype /Type1 /BaseFont /ABCDEF+RawFont "
         b"/FontDescriptor << /FontName <4142434445462BCBCECCE5> "
         b"/Ascent 800 /Descent -200 /FontBBox [0 -200 1000 800] >> "
         b"/FirstChar 90 /LastChar 90 /Widths [555] >>"),
    ])
    return path


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_source_projection_matches_native_pdfplumber(projection_pdf: Path) -> None:
    document = extract_pdf(projection_pdf)
    page = document.pages[0]
    with pdfplumber.open(projection_pdf) as pdf:
        native = extraction._extract_page(pdf.pages[0], document.id)
    assert dumps(asdict(page)) == dumps(asdict(native))
    assert "".join(fragment.text for fragment in page.fragments) == "ﬁBCDZ"
    assert page.fragments[0].style.stroking_color == PatternColor("Hatch")
    assert page.fragments[0].style.non_stroking_color == PatternColor("Tint")
    assert page.fragments[-1].style.font_name == "ABCDEF+SimSun,Regular"
    assert page.fragments[-1].style.stroking_color == (0.4,)
    assert page.fragments[-1].style.non_stroking_color == (0.1, 0.2, 0.3, 0.4)
    assert len(page.vectors) == 1


@pytest.mark.parametrize("rotation", [0])
def test_projection_honors_page_unicode_normalization(projection_pdf: Path) -> None:
    with pdfplumber.open(projection_pdf, unicode_norm="NFKC") as pdf:
        native = pdf.pages[0]
        projected = extraction._ExtractionPage(pdf, native.page_obj, native.page_number)
        try:
            assert projected.chars[0]["text"] == "fi"
            assert extraction._extract_page(projected, "doc") == extraction._extract_page(native, "doc")
        finally:
            projected.close()


@pytest.mark.parametrize("rotation", [0])
def test_unaudited_version_uses_native_processing(
    projection_pdf: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_version = extraction.version

    def unaudited_version(name: str) -> str:
        return "0.11.12" if name == "pdfplumber" else original_version(name)

    def unexpected_projection(*args: object, **kwargs: object) -> None:
        pytest.fail("an unaudited version used the character projection")

    monkeypatch.setattr(extraction, "version", unaudited_version)
    monkeypatch.setattr(extraction, "_ExtractionPage", unexpected_projection)
    document = extract_pdf(projection_pdf)
    with pdfplumber.open(projection_pdf) as pdf:
        assert document.pages[0] == extraction._extract_page(pdf.pages[0], document.id)
