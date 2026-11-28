"""Optional Rust extraction for the audited pdfminer interpreter."""

import logging
from collections.abc import Sequence
from typing import Any, Callable, cast

from pdfminer import settings
from pdfminer.layout import LTChar, LTLine
from pdfminer.pdfcolor import PDFColorSpace
from pdfminer.pdfdevice import PDFTextSeq
from pdfminer.pdfexceptions import PDFException
from pdfminer.pdffont import PDFCIDFont, PDFFont, PDFTrueTypeFont, PDFType1Font, PDFUnicodeNotDefined
from pdfminer.pdfinterp import PDFGraphicState, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import stream_value
from pdfminer.psparser import KWD, LIT
from pdfminer.utils import Matrix, PathSegment, Point, Rect
from pdfplumber.page import PDFPageAggregatorWithMarkedContent

_parse_content: Callable[
    [bytes, Callable[[str | bytes], Any], Callable[[bytes], Any]],
    list[tuple[list[Any], str | None]] | None,
] | None
try:
    from ._native import parse_content
except ImportError:
    _parse_content = None
else:
    _parse_content = parse_content

_Glyph = tuple[float, str, bool]
_PositionedGlyph = tuple[str, float, float, float, Rect, float, float]
_TextPlan = tuple[float, bool, list[_PositionedGlyph]]
_horizontal_glyphs: Callable[
    [list[_Glyph | int | float], Matrix, Point, float, float, float, float, float, float, float],
    _TextPlan | None,
] | None
try:
    from ._native import horizontal_glyphs
except ImportError:
    _horizontal_glyphs = None
else:
    _horizontal_glyphs = horizontal_glyphs

_straight_line: Callable[
    [Matrix, tuple[str, float, float], tuple[str, float, float]],
    tuple[Point, Point, Rect, float, float] | None,
] | None
try:
    from ._native import straight_line
except ImportError:
    _straight_line = None
else:
    _straight_line = straight_line


def native_available() -> bool:
    return _parse_content is not None


_LOGGERS = (logging.getLogger("pdfminer.pdfinterp"), logging.getLogger("pdfminer.psparser"))
_TEXT_LOGGERS = (logging.getLogger("pdfminer.cmapdb"), logging.getLogger("pdfminer.converter"))
_HORIZONTAL_FONTS = (PDFCIDFont, PDFTrueTypeFont, PDFType1Font)
_MAX_TEXT_RUN = 4096
_MAX_CACHED_GLYPHS = 4096
_OPERATORS = {
    name.removeprefix("do_").replace("_a", "*").replace("_w", '"').replace("_q", "'"):
        (method, method.__code__.co_argcount - 1)
    for name, method in vars(PDFPageInterpreter).items()
    if name.startswith("do_")
}


class NativeInterpreter(PDFPageInterpreter):
    """Feed batched operands to stock pdfminer operator handlers."""

    def execute(self, streams: Sequence[object]) -> None:
        if (_parse_content is None or len(streams) != 1 or settings.STRICT
                or any(logger.isEnabledFor(logging.DEBUG) for logger in _LOGGERS)):
            super().execute(streams)
            return

        stream = stream_value(streams[0])
        # Preserve upstream tracking and warnings for ineligible stream IDs.
        if stream.objid is None or stream.objid in self.parent_stream_ids:
            super().execute(streams)
            return

        ops = _parse_content(stream.get_data(), LIT, KWD)
        if ops is None:
            super().execute(streams)
            return

        self.stream_ids.clear()
        self.stream_ids.add(stream.objid)
        # Match PDFPageInterpreter.execute's tolerant operator dispatch semantics.
        # In particular, unknown operators leave the existing operand stack intact.
        for operands, name in ops:
            self.argstack.extend(operands)
            if name is None:
                continue
            operator = _OPERATORS.get(name)
            if operator is not None:
                method, arity = operator
                args = self.pop(arity)
                if len(args) == arity:
                    method(self, *args)


class NativePageAggregator(PDFPageAggregatorWithMarkedContent):
    """Apply native geometry to audited horizontal text and straight lines."""

    _font_glyphs: dict[PDFFont, dict[int, _Glyph]]
    _cached_glyph_count: int
    _stock_painting = False

    def paint_path(
        self, gstate: PDFGraphicState, stroke: bool, fill: bool, evenodd: bool,
        path: Sequence[PathSegment],
    ) -> None:
        if self._stock_painting or _straight_line is None or settings.STRICT:
            super().paint_path(gstate, stroke, fill, evenodd, path)
            return
        plan = None
        closed = False
        if type(path) is list:
            length = len(path)
            closed = length == 3 and path[2] == ("h",)
            if length == 2 or closed:
                move, line = path[0], path[1]
                if len(move) == 3 and len(line) == 3 and move[0] == "m" and line[0] == "l":
                    try:
                        plan = _straight_line(self.ctm, move, line)
                    except (TypeError, OverflowError):
                        pass
        if plan is None:
            # Compound paths recursively call this override. Keep their
            # children in stock rendering once the parent is declined.
            self._stock_painting = True
            try:
                super().paint_path(gstate, stroke, fill, evenodd, path)
            finally:
                self._stock_painting = False
            return

        first, last, bbox, width, height = plan
        item = LTLine.__new__(LTLine)
        item.x0, item.y0, item.x1, item.y1 = bbox
        item.width, item.height, item.bbox = width, height, bbox
        item.pts = [first, last]
        item.linewidth = gstate.linewidth
        item.stroke, item.fill, item.evenodd = stroke, fill, evenodd
        item.stroking_color, item.non_stroking_color = gstate.scolor, gstate.ncolor
        # pdfminer stores nested points despite the annotation.
        original_path: list[tuple[str, Point] | tuple[str]] = [("m", first), ("l", last)]
        if closed:
            original_path.append(("h",))
        item.original_path = cast(list[PathSegment], original_path)
        item.dashing_style = gstate.dash
        self.cur_item.add(item)
        self.tag_cur_item()

    def begin_page(self, page: PDFPage, ctm: Matrix) -> None:
        self._font_glyphs = {}
        self._cached_glyph_count = 0
        super().begin_page(page, ctm)

    def _prepare_text(self, seq: PDFTextSeq, font: PDFFont) -> list[_Glyph | int | float] | None:
        if not isinstance(seq, (list, tuple)) or not 0 < len(seq) <= _MAX_TEXT_RUN:
            return None
        # Each supported decoder emits at most one CID per input byte.
        # Check the budget before decoding or creating any glyphs.
        size = 0
        for obj in seq:
            if type(obj) is bytes:
                size += len(obj)
            elif type(obj) in (int, float):
                size += 1
            else:
                return None
            if size > _MAX_TEXT_RUN:
                return None

        glyphs = self._font_glyphs.get(font)
        if glyphs is None:
            glyphs = {}
        items: list[_Glyph | int | float] = []
        for obj in seq:
            if isinstance(obj, bytes):
                for cid in font.decode(obj):
                    glyph = glyphs.get(cid)
                    if glyph is None:
                        try:
                            text = font.to_unichr(cid)
                        except PDFUnicodeNotDefined:
                            text = self.handle_undefined_char(font, cid)
                        if not isinstance(text, str):
                            return None
                        glyph = font.char_width(cid), text, cid == 32
                        if self._cached_glyph_count < _MAX_CACHED_GLYPHS:
                            if not glyphs:
                                self._font_glyphs[font] = glyphs
                            glyphs[cid] = glyph
                            self._cached_glyph_count += 1
                    items.append(glyph)
            else:
                items.append(obj)
        return items

    def render_string_horizontal(
        self, seq: PDFTextSeq, matrix: Matrix, pos: Point, font: PDFFont,
        fontsize: float, scaling: float, charspace: float, wordspace: float,
        rise: float, dxscale: float, ncs: PDFColorSpace, graphicstate: PDFGraphicState,
    ) -> Point:
        plan = None
        if (_horizontal_glyphs is not None and type(font) in _HORIZONTAL_FONTS
                and type(font.fontname) is str
                and not settings.STRICT
                and not any(logger.isEnabledFor(logging.DEBUG) for logger in _TEXT_LOGGERS)):
            try:
                items = self._prepare_text(seq, font)
                if items:
                    plan = _horizontal_glyphs(
                        items, matrix, pos, fontsize, scaling, charspace, wordspace,
                        rise, dxscale, font.get_descent(),
                    )
            except (PDFException, ArithmeticError, LookupError, TypeError, ValueError):
                # Replay the whole show operation to preserve malformed-font
                # errors and their order relative to rendering.
                pass
        if plan is None:
            return super().render_string_horizontal(
                seq, matrix, pos, font, fontsize, scaling, charspace, wordspace,
                rise, dxscale, ncs, graphicstate,
            )

        x, upright, glyphs = plan
        a, b, c, d, _, _ = matrix
        # Populate the same LTChar fields as pdfminer after the complete run
        # has been accepted. Extend the device only after preparing all items.
        new_char = LTChar.__new__
        fontname, mcid, tag = font.fontname, self.cur_mcid, self.cur_tag
        rendered = []
        for text, tx, ty, advance, bbox, width, height in glyphs:
            char = new_char(LTChar)
            char._text = text
            char.matrix = (a, b, c, d, tx, ty)
            char.fontname = fontname
            char.ncs = ncs
            char.graphicstate = graphicstate
            char.adv = advance
            char.upright = upright
            char.x0, char.y0, char.x1, char.y1 = bbox
            char.width, char.height, char.size, char.bbox = width, height, height, bbox
            char.mcid, char.tag = mcid, tag  # type: ignore[attr-defined]
            rendered.append(char)
        self.cur_item.extend(rendered)
        return x, pos[1]
