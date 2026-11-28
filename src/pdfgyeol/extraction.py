"""Lossless-to-pdfplumber character extraction, without layout inference or OCR."""

from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unicodedata import normalize

import pdfplumber
from pdfminer.layout import LTChar, LTItem, LTPage
from pdfplumber.page import Page as PDFPage

from .models import (
    BBox, Color, Document, ImageEvidence, Page, PageKind, PathSegment, PatternColor,
    TextFragment, TextStyle, VectorEvidence, VectorKind,
)


class _ExtractionPage(PDFPage):
    """Project the LTChar fields this extractor uses during pdfplumber's layout walk."""

    def process_object(self, obj: LTItem) -> dict[str, Any]:
        if type(obj) is not LTChar or not isinstance(obj.fontname, str):
            return super().process_object(obj)

        media_x, media_top = self.mediabox[:2]
        height = self.height
        text = obj.get_text()
        if self.pdf.unicode_norm is not None:
            text = normalize(self.pdf.unicode_norm, text)
        stroke, fill = obj.graphicstate.scolor, obj.graphicstate.ncolor
        # Match Page.process_object's arithmetic order before _bbox rebases the result.
        return {
            "object_type": "char",
            "text": text,
            "fontname": obj.fontname,
            "size": obj.size,
            "x0": obj.x0 + media_x if media_x != 0 else obj.x0,
            "x1": obj.x1 + media_x if media_x != 0 else obj.x1,
            "top": (height - obj.y1) + media_top,
            "bottom": (height - obj.y0) + media_top,
            "stroking_color": stroke if isinstance(stroke, tuple) else (stroke,),
            "non_stroking_color": fill if isinstance(fill, tuple) else (fill,),
            "matrix": obj.matrix,
            "adv": obj.adv,
            "upright": obj.upright,
        }


class _NativePage(_ExtractionPage):
    @property
    def layout(self) -> LTPage:
        if not hasattr(self, "_layout"):
            # Match pdfplumber's Page.layout with the audited interpreter adapter.
            from pdfplumber.utils.exceptions import PdfminerException

            from ._native_extraction import NativeInterpreter, NativePageAggregator

            device = NativePageAggregator(
                self.pdf.rsrcmgr, pageno=self.page_number, laparams=self.pdf.laparams,
            )
            interpreter = NativeInterpreter(self.pdf.rsrcmgr, device)
            try:
                interpreter.process_page(self.page_obj)
            except Exception as error:
                raise PdfminerException(error)
            self._layout: LTPage = device.get_result()
        return self._layout


def _color(value: Any) -> Color:
    if value is None:
        return None
    if isinstance(value, str):
        return PatternColor(value)

    pattern_name: str | None = None
    if isinstance(value, (tuple, list)):
        # pdfplumber wraps a character's pattern name in a one-element tuple.
        if len(value) == 1 and isinstance(value[0], str):
            return PatternColor(value[0])
        if (len(value) == 2 and isinstance(value[1], str)
                and isinstance(value[0], (int, float, tuple, list))):
            value, pattern_name = value

    components: tuple[float, ...]
    if isinstance(value, (int, float)):
        components = (float(value),)
    else:
        components = tuple(float(component) for component in value)
    return PatternColor(pattern_name, components) if pattern_name is not None else components


def _bbox(obj: dict[str, Any], origin: tuple[float, float]) -> BBox:
    x, y = origin
    return BBox(
        float(obj["x0"]) - x, float(obj["top"]) - y,
        float(obj["x1"]) - x, float(obj["bottom"]) - y,
    )


def _points(points: Any, origin: tuple[float, float]) -> tuple[tuple[float, float], ...]:
    x, y = origin
    return tuple((float(px) - x, float(py) - y) for px, py in points)


def _extract_page(source: PDFPage, document_id: str) -> Page:
    number = source.page_number
    page_id = f"{document_id}:p{number:06d}"
    origin = (source.bbox[0], source.bbox[1])
    fragments = tuple(
        TextFragment(
            id=f"{page_id}:c{index:08d}", page_id=page_id,
            page_number=number, source_index=index, text=char["text"],
            bbox=_bbox(char, origin),
            style=TextStyle(
                font_name=char["fontname"], size=float(char["size"]),
                upright=bool(char["upright"]),
                stroking_color=_color(char.get("stroking_color")),
                non_stroking_color=_color(char.get("non_stroking_color")),
            ),
            source_matrix=tuple(float(value) for value in char["matrix"]),
            advance=float(char["adv"]),
        )
        for index, char in enumerate(source.chars)
    )
    vectors = []
    # Keep native shapes once: page.edges would duplicate rect/curve geometry.
    object_groups: tuple[tuple[VectorKind, list[dict[str, Any]]], ...] = (
        ("line", source.lines), ("rect", source.rects), ("curve", source.curves),
    )
    for kind, objects in object_groups:
        for index, obj in enumerate(objects):
            dash = obj.get("dash")
            vectors.append(VectorEvidence(
                id=f"{page_id}:{kind}{index:08d}", page_id=page_id,
                page_number=number, source_index=index, kind=kind,
                bbox=_bbox(obj, origin), points=_points(obj.get("pts", ()), origin),
                path=tuple(
                    PathSegment(segment[0], _points(segment[1:], origin))
                    for segment in obj.get("path", ())
                ),
                line_width=float(obj.get("linewidth", 0)),
                stroke=bool(obj.get("stroke", False)),
                fill=bool(obj.get("fill", False)),
                even_odd=bool(obj.get("evenodd", False)),
                stroking_color=_color(obj.get("stroking_color")),
                non_stroking_color=_color(obj.get("non_stroking_color")),
                dash=None if dash is None else (tuple(float(n) for n in dash[0]), float(dash[1])),
            ))
    images = tuple(
        ImageEvidence(
            id=f"{page_id}:image{index:08d}", page_id=page_id,
            page_number=number, source_index=index, bbox=_bbox(obj, origin),
            source_size=(int(obj["srcsize"][0]), int(obj["srcsize"][1])),
            bits=obj.get("bits"), image_mask=bool(obj.get("imagemask", False)),
        )
        for index, obj in enumerate(source.images)
    )
    if fragments:
        page_kind = PageKind.TEXT
    elif images:
        page_kind = PageKind.IMAGE_ONLY
    elif vectors:
        page_kind = PageKind.VECTOR_ONLY
    else:
        page_kind = PageKind.BLANK
    return Page(
        id=page_id, page_number=number, width=float(source.width),
        height=float(source.height), rotation=int(source.rotation),
        source_bbox=BBox(*(float(n) for n in source.bbox)), kind=page_kind,
        fragments=fragments, vectors=tuple(vectors), images=images,
    )


def extract_pdf(path: str | Path, *, password: str | None = None) -> Document:
    """Read all pages; propagate I/O/password/parser errors rather than invent blank pages.

    IDs use file SHA-256 + schema namespace + page/type/source index. Native char
    order and original text are retained, without sorting, filtering, deduplication,
    word extraction or Unicode normalization. This order is NOT reading order.
    """
    # Hash and parse the same open file, without loading the whole file into memory.
    with Path(path).open("rb") as stream:
        digest = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        checksum = digest.hexdigest()
        document_id = f"pdfgyeol:v1:{checksum}"
        stream.seek(0)
        plumber_version = version("pdfplumber")
        miner_version = version("pdfminer.six")
        # Use stock pdfplumber object processing until dependency upgrades
        # have been audited for the projected fields and native extraction.
        project_chars = (plumber_version, miner_version) == ("0.11.11", "20260107")
        page_class = _ExtractionPage
        if project_chars:
            from ._native_extraction import native_available

            if native_available():
                page_class = _NativePage
        with pdfplumber.open(stream, password=password) as pdf:
            pages = []
            for source in pdf.pages:
                if project_chars:
                    source = page_class(
                        pdf, source.page_obj, source.page_number, source.initial_doctop,
                    )
                try:
                    pages.append(_extract_page(source, document_id))
                finally:
                    source.close()
    return Document(
        id=document_id, sha256=checksum, schema_version="1",
        extractor_version=f"pdfplumber/{plumber_version};pdfminer.six/{miner_version}",
        pages=tuple(pages),
    )
