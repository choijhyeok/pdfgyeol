"""PDF결 local PDF parsing, source extraction and opt-in Decision scoring."""

from .api import PARSE_SCHEMA_VERSION, ParsedDocument, ParsedPage, parse_pdf
from .decision import (
    DecisionClient, DecisionConfig, DecisionProvider, DecisionRecord, DecisionResult, resolve_page,
)
from .extraction import extract_pdf
from .insurance import InsuranceAnnotations, InsuranceSection, InsuranceSpan
from .layout import infer_page, iter_layout
from .layout_models import (
    LayoutConfig, PageLayout, ReadingAlternative, ReadingCandidate, Region,
    Table, TableCell, TableRow, TextLine,
)
from .models import (
    BBox,
    Document,
    ImageEvidence,
    Page,
    PageKind,
    PathSegment,
    TextFragment,
    TextStyle,
    VectorEvidence,
)

__all__ = [
    "PARSE_SCHEMA_VERSION", "ParsedDocument", "ParsedPage", "parse_pdf",
    "BBox", "Document", "ImageEvidence", "Page", "PageKind", "PathSegment",
    "TextFragment", "TextStyle", "VectorEvidence", "extract_pdf",
    "LayoutConfig", "PageLayout", "ReadingAlternative", "ReadingCandidate", "Region",
    "Table", "TableCell", "TableRow", "TextLine", "infer_page", "iter_layout",
    "DecisionClient", "DecisionConfig", "DecisionProvider", "DecisionRecord", "DecisionResult", "resolve_page",
    "InsuranceAnnotations", "InsuranceSection", "InsuranceSpan",
]
