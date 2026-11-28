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
from pdfminer import settings
from pdfminer.layout import LTChar
from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfinterp import PDFContentParser, PDFInterpreterError, PDFPageInterpreter, PDFResourceManager
from pdfminer.pdftypes import PDFStream
from pdfminer.psparser import KWD, LIT, PSEOF, PSKeyword, keyword_name
from pdfminer.utils import MATRIX_IDENTITY

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

    attempts: list[tuple[bytes, int | None]] = []
    lexed: list[tuple[bytes, bool]] = []

    def track_plan(data: bytes, *args):
        plan = native.prepare_dispatch(data, *args)
        attempts.append((data, len(plan) if plan is not None else None))
        return plan

    def track(data: bytes, literal, keyword):
        result = native.parse_content(data, literal, keyword)
        lexed.append((data, result is not None))
        return result

    monkeypatch.setattr(_native_extraction, "_prepare_dispatch", track_plan)
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
    for (data, size), (marker, expected) in zip(attempts, expected_attempts, strict=True):
        assert marker in data
        assert (size is not None) is expected
    assert len(lexed) == 1 and b"(Inner)" in lexed[0][0] and not lexed[0][1]


def _interpreter(cls):
    resources = PDFResourceManager()
    device = PDFDevice(resources)
    interpreter = cls(resources, device)
    interpreter.init_resources({})
    interpreter.init_state(MATRIX_IDENTITY)
    return interpreter


def _stream(content: bytes) -> PDFStream:
    stream = PDFStream({}, content)
    stream.set_objid(1, 0)
    return stream


def test_dispatch_materializes_numeric_operands_at_python_handler() -> None:
    native = pytest.importorskip("pdfgyeol._native")

    class Recorder(PDFPageInterpreter):
        def __init__(self, resources, device):
            super().__init__(resources, device)
            self.calls = []

        def do_Inspect(self, a, b, c, d, e, f, g):
            self.calls.append((a, b, c, d, e, f, g))

        def do_Sample(self, label, values, amount):
            self.calls.append((label, values, amount))

        def do_End(self):
            self.calls.append(())

    content = (
        b"-0 -0.0 +.0 9223372036854775807 -9223372036854775808 "
        b"9007199254740993 9007199254740993. Inspect "
        b"/F#31 [1. -.0 true << /Value 2 >>] +.125 Sample End"
    )
    operators = {
        "Inspect": (Recorder.do_Inspect, 7), "Sample": (Recorder.do_Sample, 3),
        "End": (Recorder.do_End, 0),
    }
    actual = _interpreter(Recorder)
    expected = _interpreter(Recorder)
    plan = native.prepare_dispatch(content, LIT, KWD, actual, operators)
    assert plan is not None and len(plan) == 3
    assert actual.calls == []
    expected.execute([_stream(content)])
    plan.run()
    plan.run()
    assert len(plan) == 0
    assert _typed(actual.calls) == _typed(expected.calls)
    assert len(actual.calls) == 3


@pytest.mark.parametrize("number", [
    b"9223372036854775808", b"-9223372036854775809", b"0." + b"1" * 63, b"1e2", b"1.2.3",
], ids=["positive-overflow", "negative-overflow", "over-width", "exponent", "second-period"])
def test_numeric_dispatch_declines_whole_stream_before_call(number: bytes) -> None:
    native = pytest.importorskip("pdfgyeol._native")
    calls = []

    def record(interpreter, amount):
        calls.append(amount)

    # Start with an otherwise runnable command. A rejected operand in the
    # second command must not partially execute the first one.
    content = b"2 w " + number + b" w"
    assert native.prepare_dispatch(content, LIT, KWD, object(), {"w": (record, 1)}) is None
    assert native.parse_content(content, LIT, KWD) is None
    assert calls == []


@pytest.mark.parametrize(("content", "initial", "native_plan"), [
    (b"q 1 0 0 1 -0.0 5 cm BT -1 0 -0.0 1 -0.0 7 Tm [(A) 5 ( B)] TJ ET Q "
     b"BT 1 -0.0 0 1 -0.0 0 Tm ET", [], True),
    (b"q 27 Unknown 2 w Q 4 6", [], False),
    (b"2 w 9223372036854775808 w", [], False),
    (b"1 w 2 3 m 4 l w", [8], None),
    (b"1 0 0 rg .5 scn 2 w [(partial)", [], False),
    (b"/DeviceRGB cs .1 .2 .3 scn /DeviceRGB CS .4 .5 .6 SCN 5 w", [], False),
], ids=["matrix-and-state", "unknown-and-trailing", "integer-overflow", "dirty-and-short",
        "partial-lex", "variable-color"])
def test_dispatch_and_stack_fallback_match_pdfminer(
    content: bytes, initial: list[int], native_plan: bool | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = pytest.importorskip("pdfgyeol._native")
    from pdfgyeol import _native_extraction as ne

    plans = []

    def track(*args):
        result = native.prepare_dispatch(*args)
        plans.append(result is not None)
        return result

    monkeypatch.setattr(ne, "_prepare_dispatch", track)
    actual = _interpreter(ne.NativeInterpreter)
    expected = _interpreter(PDFPageInterpreter)
    for interpreter in (actual, expected):
        interpreter.argstack.extend(initial)
        interpreter.execute([_stream(content)])
    assert plans == ([] if native_plan is None else [native_plan])
    for field in ("ctm", "argstack", "curpath", "stream_ids", "gstack"):
        assert _typed(getattr(actual, field)) == _typed(getattr(expected, field))
    assert _typed(vars(actual.textstate)) == _typed(vars(expected.textstate))
    assert _typed(vars(actual.graphicstate)) == _typed(vars(expected.graphicstate))


def test_dispatch_does_not_retry_handler_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pdfgyeol._native")
    from pdfgyeol import _native_extraction as ne

    def unexpected_lex(*args):
        pytest.fail("already executing an exact-arity stream")

    monkeypatch.setattr(ne, "_parse_content", unexpected_lex)
    interpreter = _interpreter(ne.NativeInterpreter)
    translations = []

    def fail_on_second_transform(ctm):
        translations.append(ctm[4])
        if ctm[4] == 3:
            raise ValueError("device rejected transform")

    interpreter.device.set_ctm = fail_on_second_transform
    with pytest.raises(ValueError, match="device rejected transform"):
        interpreter.execute([_stream(b"1 0 0 1 1 0 cm 1 0 0 1 2 0 cm 1 0 0 1 4 0 cm")])
    assert translations == [1, 3]
    assert interpreter.stream_ids == {1}


def test_dispatch_defers_custom_strict_and_debug_execution(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("pdfgyeol._native")
    from pdfgyeol import _native_extraction as ne

    class Custom(ne.NativeInterpreter):
        def do_Custom(self, value):
            self.push(value)

        def do_w(self, linewidth):
            super().do_w(linewidth + 1)

    def unexpected_dispatch(*args):
        pytest.fail("dispatch attempted despite customized or diagnostic execution")

    monkeypatch.setattr(ne, "_prepare_dispatch", unexpected_dispatch)
    custom = _interpreter(Custom)
    custom.execute([_stream(b"7 Custom w")])
    assert custom.graphicstate.linewidth == 8
    exact = _interpreter(ne.NativeInterpreter)
    with monkeypatch.context() as patch:
        patch.setattr(settings, "STRICT", True)
        with pytest.raises(PDFInterpreterError, match="Unknown operator"):
            exact.execute([_stream(b"Unknown")])
    with caplog.at_level(logging.DEBUG, logger="pdfminer.pdfinterp"):
        exact.execute([_stream(b"5 w")])
    assert any(record.getMessage() == "exec: w [5]" for record in caplog.records)


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


@pytest.mark.parametrize("seq", [
    [b"\x00\x20", b"\x00\x21"],
    [b"\x00\x20", b"\x00"],
    [b"\x00\x20", b"\x00\x20\x00\x20"],
    [b"\x00\x20", b""],
    [b"\x00\x20", True],
    [b"\x00\x20", float("inf")],
    [b"\x00\x20", 1 << 60],
    [b"\x00\x20", type("Bytes", (bytes,), {})(b"\x00\x20")],
    [b"\x00\x20"] * 2049,
    (b"\x00\x20",),
], ids=["miss", "odd", "wide", "empty", "bool", "infinity", "int-range", "bytes-subclass", "budget", "tuple"])
def test_cached_identity_rejects_whole_show(seq) -> None:
    native = pytest.importorskip("pdfgyeol._native")
    cache = {32: (0.5, "A", True)}
    params = ((1, -0.0, 0, 1, -0.0, 0), (0, 0), 10, 1, -0.0, 2, 0, 0.01, -0.25)
    assert native.identity_cached_glyphs(seq, cache, *params) is None
    assert cache == {32: (0.5, "A", True)}
    accepted = native.identity_cached_glyphs([b"\x00\x20", -0.0, 5, b"\x00\x20"], cache, *params)
    expected = native.horizontal_glyphs([cache[32], -0.0, 5, cache[32]], *params)
    assert accepted is not None and _typed(accepted) == _typed(expected)


def _cid_font(unicode_object: int, encoding: bytes = b"Identity-H") -> bytes:
    return (b"<< /Type /Font /Subtype /Type0 /BaseFont /SharedName /Encoding /" + encoding
            + b" /DescendantFonts [9 0 R] /ToUnicode %d 0 R >>" % unicode_object)


def _unicode_map(first: bytes, second: bytes) -> bytes:
    return pdf_stream(b"begincmap\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
                      b"2 beginbfchar\n<0020> <%s>\n<0021> <%s>\nendbfchar\nendcmap" % (first, second))


def test_cached_identity_show_reuses_only_the_current_page_font(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    if (version("pdfplumber"), version("pdfminer.six")) != ("0.11.11", "20260107"):
        pytest.skip("native PDF extraction requires the audited dependencies")
    native = pytest.importorskip("pdfgyeol._native")
    from pdfgyeol import _native_extraction as ne

    path = tmp_path / "identity-forms.pdf"
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
    form = (b"/Type /XObject /Subtype /Form /BBox [0 0 160 160] "
            b"/Resources << /Font << /Same 12 0 R >> /XObject << /Inner 14 0 R >> >>")
    outer_show = b"BT /Same 14 Tf 1 0 -0.0 1 10 220 Tm <0020> Tj <0020> Tj ET"
    write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        page + b"/Resources << /Font << /Same 7 0 R /Vertical 8 0 R /Byte 15 0 R >> "
        b"/XObject << /Form 13 0 R >> >> /Contents 5 0 R >>",
        page + b"/Resources << /Font << /Same 7 0 R >> >> /Contents 6 0 R >>",
        pdf_stream(outer_show + b" q 1 0 0 1 25 20 cm /Form Do Q "
                   b"BT /Same 12 Tf 1 0 0 1 10 100 Tm 2 Tc -1 Tw 89 Tz "
                   b"/Span << /MCID 2 >> BDC [<0020> -0.0 -31.25 <0020> <0021>] TJ "
                   b"[<0020> -0.0 <0021>] TJ [<002000> -3 <00200021>] TJ <0020> Tj EMC ET "
                   b"BT /Vertical 11 Tf 20 55 Td <0020> Tj <0021> Tj ET "
                   b"BT /Byte 11 Tf 20 35 Td <20> Tj <20> Tj ET"),
        pdf_stream(outer_show),
        _cid_font(10),
        _cid_font(10, b"Identity-V"),
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SharedName /W [32 [420 630]] "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> >>",
        _unicode_map(b"0041", b"00660069"),
        _unicode_map(b"03A9", b"0044"),
        _cid_font(11),
        pdf_stream(b"BT /Same 10 Tf 3 45 Td <0020> Tj <0020> Tj ET /Inner Do", form),
        pdf_stream(b"BT /Same 12 Tf 4 25 Td <0020> Tj <0020> Tj ET",
                   b"/Type /XObject /Subtype /Form /BBox [0 0 80 80] /Resources << /Font << /Same 7 0 R >> >>"),
        _cid_font(10, b"OneByteIdentityH"),
    ])
    events = []

    def track(seq, cache, *args):
        plan = native.identity_cached_glyphs(seq, cache, *args)
        events.append((cache[32][1], None if plan is None else "".join(row[0] for row in plan[2])))
        return plan

    monkeypatch.setattr(ne, "_identity_cached_glyphs", track)
    document = extract_pdf(path)
    assert dumps([asdict(page) for page in document.pages]) == _stock_page_json(path, document.id)
    assert events == [
        ("A", "A"), ("Ω", "Ω"), ("A", "A"), ("A", "A"), ("A", None),
        ("A", "Afi"), ("A", None), ("A", "A"), ("A", "A"),
    ]

    events.clear()
    with caplog.at_level(logging.DEBUG, logger="pdfminer.cmapdb"):
        assert extract_pdf(path) == document
    assert not events
    monkeypatch.setattr(ne, "_identity_cached_glyphs", None)
    assert extract_pdf(path) == document
