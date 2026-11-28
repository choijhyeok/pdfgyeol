"""Compare public extraction with stock dispatch in fresh processes."""

import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from test_extraction import pdf_stream, write_pdf


@pytest.fixture
def cm_compat_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "cm-compat.pdf"
    page = (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 320] "
        b"/Resources << /Font << /F 7 0 R >> /XObject << /Outer 8 0 R >> >> /Contents "
    )
    content = (
        b"q 1 -0.0 .125 1 -0.0 11 cm .5000000000000001 -.25 0 1 30.125 20 cm "
        b".2 .3 .4 RG 1 2 m 81 19 l S /Outer Do Q q -1 0 0 1 200 29 cm "
        b"/Span << /MCID 2 >> BDC BT /F 14 Tf 3 42 Td (PDF) Tj ET EMC Q"
    )
    form = b"/Type /XObject /Subtype /Form /BBox [0 0 120 120] /Matrix [1 0 .25 1 2 -1] "
    write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        page + b"5 0 R >>", page + b"6 0 R >>",
        pdf_stream(content), pdf_stream(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        pdf_stream(b"q 0 1 -1 0 65 2 cm 0 0 20 10 re f /Inner Do Q",
                   form + b"/Resources << /XObject << /Inner 9 0 R >> >>"),
        pdf_stream(b"q .125 0 0 .75 1 8 cm .3 .6 .1 rg BT /F 8 Tf 1 9 Td (Form) Tj ET Q",
                   form + b"/Resources << /Font << /F 7 0 R >> >>"),
    ])
    return path


_TRACE = r"""
import hashlib
import json
import sys
from dataclasses import asdict
from functools import wraps
from types import FunctionType

import pdfplumber
from pdfminer import pdfinterp
from pdfplumber.utils.exceptions import PdfminerException

mode, path = sys.argv[1:]
cls = pdfinterp.PDFPageInterpreter
handler = cls.do_cm
calls, plans = [], []
namespace = vars(pdfinterp)
namespace.update(cm_trace=calls, cm_offset=7, cm_stop=5 if mode == "early-error" else None,
                 cm_delegate=FunctionType(handler.__code__, namespace))
exec('def cm_override(self, a, b, c, d, e, f):\n'
     '    cm_trace.append((a, b, c, d, e, f))\n'
     '    if len(cm_trace) == cm_stop:\n'
     '        raise RuntimeError("custom cm failure")\n'
     '    return cm_delegate(self, a, b, c, d, e + cm_offset, f)\n', namespace)
override = wraps(handler)(namespace.pop("cm_override"))
if mode in ("early", "early-error"):
    cls.do_cm = override
safe_matrix = pdfinterp.safe_matrix
if mode == "mid-global":
    original_q = cls.do_q

    def adjusted(a, b, c, d, e, f):
        calls.append((a, b, c, d, e, f))
        return safe_matrix(a, b, c, d, e + 7, f)

    def custom_q(self):
        pdfinterp.safe_matrix = adjusted
        return original_q(self)

    cls.do_q = custom_q
assert "pdfgyeol._native_extraction" not in sys.modules

from pdfgyeol import extract_pdf, extraction, _native_extraction as ne
from pdfgyeol import _native

assert not hasattr(_native, "prepare_matrix_dispatch")
prepare = ne._prepare_dispatch
assert prepare is not None

def observe(*args):
    plan = prepare(*args)
    if plan is not None:
        plans.append(len(plan))
    return plan

ne._prepare_dispatch = observe
if mode in ("late-class", "table", "table-tokenizer", "code"):
    extract_pdf(path)
    plans.clear()
    if mode == "late-class":
        cls.do_cm = override
    elif mode in ("table", "table-tokenizer"):
        def competing_class(self, a, b, c, d, e, f):
            raise AssertionError("explicit table handler must take precedence")

        cls.do_cm = competing_class
        ne._OPERATORS["cm"] = (override, 6)
        if mode == "table-tokenizer":
            ne._prepare_dispatch = None
    else:
        handler.__code__ = override.__code__

def snapshot(pages):
    wire = json.dumps([asdict(page) for page in pages], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(wire.encode()).hexdigest()

def trace():
    return [[(type(x).__name__, x.hex() if type(x) is float else x) for x in row] for row in calls]

try:
    document = extract_pdf(path)
    native = snapshot(document.pages)
    doc_id = document.id
except PdfminerException as error:
    native = [type(error).__name__, type(error.__context__).__name__, str(error)]
    doc_id = "exception-fixture"
native_trace = trace()
calls.clear()
if mode in ("table", "table-tokenizer"):
    cls.do_cm = override
if mode == "mid-global":
    pdfinterp.safe_matrix = safe_matrix

try:
    with pdfplumber.open(path) as pdf:
        stock = snapshot(extraction._extract_page(page, doc_id) for page in pdf.pages)
except PdfminerException as error:
    stock = [type(error).__name__, type(error.__context__).__name__, str(error)]
print(json.dumps({"native": native, "stock": stock, "plans": sum(plans), "opt": sys.flags.optimize,
                  "calls": native_trace, "reference_calls": trace()}))
"""


@pytest.mark.parametrize("mode", [
    "normal", "early", "early-error", "mid-global", "late-class", "table", "table-tokenizer", "code",
])
@pytest.mark.parametrize("optimize", [0, 2], ids=["default", "OO"])
def test_cm_compat_in_fresh_process(cm_compat_pdf: Path, mode: str, optimize: int) -> None:
    if (version("pdfplumber"), version("pdfminer.six")) != ("0.11.11", "20260107"):
        pytest.skip("native PDF extraction requires the audited dependencies")
    pytest.importorskip("pdfgyeol._native")
    process = subprocess.run(
        [sys.executable, "-IB", *(["-OO"] if optimize else []), "-c", _TRACE, mode, str(cm_compat_pdf)],
        capture_output=True, text=True,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["opt"] == optimize
    assert result["native"] == result["stock"]
    assert result["calls"] == result["reference_calls"]
    assert len(result["calls"]) == {"normal": 0, "early-error": 5}.get(mode, 10)
    if mode == "early-error":
        assert result["native"] == ["PdfminerException", "RuntimeError", "custom cm failure"]
    assert (result["plans"] > 0) == (mode not in ("late-class", "table-tokenizer"))


def test_cm_missing_before_native_import(tmp_path: Path) -> None:
    if (version("pdfplumber"), version("pdfminer.six")) != ("0.11.11", "20260107"):
        pytest.skip("native PDF extraction requires the audited dependencies")
    pytest.importorskip("pdfgyeol._native")
    path = tmp_path / "text.pdf"
    write_pdf(path, [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] "
        b"/Resources << /Font << /F 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        pdf_stream(b"BT /F 12 Tf 10 50 Td (test) Tj ET"),
    ])
    script = """
import sys
from dataclasses import asdict
from json import dumps

import pdfplumber
from pdfminer.pdfinterp import PDFPageInterpreter

from pdfgyeol import extract_pdf, extraction

assert "pdfgyeol._native_extraction" not in sys.modules
del PDFPageInterpreter.do_cm
document = extract_pdf(sys.argv[1])
actual, = document.pages
assert "pdfgyeol._native_extraction" in sys.modules
assert len(actual.fragments) == 4
with pdfplumber.open(sys.argv[1]) as pdf:
    stock = extraction._extract_page(pdf.pages[0], document.id)
assert dumps(asdict(actual)) == dumps(asdict(stock))
"""
    process = subprocess.run(
        [sys.executable, "-IB", "-c", script, str(path)], capture_output=True, text=True,
    )
    assert process.returncode == 0, process.stderr
