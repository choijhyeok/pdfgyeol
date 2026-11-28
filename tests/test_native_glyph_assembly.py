"""Compare batched source records against stock pdfplumber."""

from dataclasses import asdict
from importlib.metadata import version
from json import dumps
from pathlib import Path

import pdfplumber
import pytest
from pdfminer.layout import LTChar

from pdfgyeol import extract_pdf
from pdfgyeol import extraction
from test_extraction_perf import projection_pdf
from test_native_extraction import native_content_pdf


@pytest.fixture
def native():
    if (version("pdfplumber"), version("pdfminer.six")) != ("0.11.11", "20260107"):
        pytest.skip("native PDF extraction requires the audited dependencies")
    return pytest.importorskip("pdfgyeol._native")


def _stock_json(path: Path, document_id: str) -> str:
    with pdfplumber.open(path) as pdf:
        return dumps([asdict(extraction._extract_page(page, document_id)) for page in pdf.pages])


def test_batches_preserve_order_without_projected_character_dictionaries(
    native, native_content_pdf: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extraction, "_GLYPH_BATCH", 2)
    assemble = native.assemble_glyphs
    project = extraction._NativePage.process_object
    batches = []

    def track(chars, page_id, number, start, *args):
        records = assemble(chars, page_id, number, start, *args)
        assert records is not None
        batches.append((number, start, len(records)))
        return records

    def noncharacters_only(self, obj):
        assert not isinstance(obj, LTChar)
        return project(self, obj)

    monkeypatch.setattr(native, "assemble_glyphs", track)
    monkeypatch.setattr(extraction._NativePage, "process_object", noncharacters_only)
    doc = extract_pdf(native_content_pdf)
    assert dumps([asdict(page) for page in doc.pages]) == _stock_json(native_content_pdf, doc.id)
    for page in doc.pages:
        offsets = range(0, len(page.fragments), 2)
        assert [(start, count) for num, start, count in batches if num == page.page_number] == [
            (start, min(2, len(page.fragments) - start)) for start in offsets
        ]


@pytest.mark.parametrize("rotation", [90])
def test_late_decline_replays_page_and_unicode_normalization_uses_stock(
    native, projection_pdf: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extraction, "_GLYPH_BATCH", 2)
    assemble = native.assemble_glyphs
    results = []

    def track(*args):
        records = assemble(*args)
        results.append(records is not None)
        return records

    monkeypatch.setattr(native, "assemble_glyphs", track)
    doc = extract_pdf(projection_pdf)
    assert results == [True, True, False]  # Last glyph has a byte-encoded font name.
    assert dumps([asdict(page) for page in doc.pages]) == _stock_json(projection_pdf, doc.id)
    results.clear()
    with pdfplumber.open(projection_pdf, unicode_norm="NFKC") as pdf:
        stock = pdf.pages[0]
        page = extraction._NativePage(pdf, stock.page_obj, stock.page_number)
        normal = extraction._extract_page(page, doc.id)
        assert normal.fragments[0].text == "fi"
        assert dumps(asdict(normal)) == dumps(asdict(extraction._extract_page(stock, doc.id)))
    assert not results


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_accepted_batch_preserves_transformed_patterned_glyphs(native, projection_pdf: Path) -> None:
    with pdfplumber.open(projection_pdf) as pdf:
        stock = pdf.pages[0]
        old = extraction._extract_page(stock, "document")
        glyphs = [item for item in extraction._layout_leaves(stock.layout._objs) if type(item) is LTChar]
        # The first four glyphs cover the patterned text and nested Forms.
        records = native.assemble_glyphs(
            glyphs[:4], old.id, old.page_number, 0, (*stock.mediabox[:2], stock.height, *stock.bbox[:2]),
            (extraction.BBox, extraction.TextStyle, extraction.TextFragment, extraction.PatternColor),
        )
        assert records is not None
        assert dumps([asdict(record) for record in records]) == dumps(
            [asdict(record) for record in old.fragments[:4]],
        )
