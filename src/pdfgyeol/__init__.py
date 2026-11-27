"""PDF결 source extraction, local geometry and opt-in Decision scoring."""

from .extraction import extract_pdf
from .decision import (
    DecisionClient, DecisionConfig, DecisionProvider, DecisionRecord, DecisionResult, resolve_page,
)
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
    "BBox", "Document", "ImageEvidence", "Page", "PageKind", "PathSegment",
    "TextFragment", "TextStyle", "VectorEvidence", "extract_pdf",
    "LayoutConfig", "PageLayout", "ReadingAlternative", "ReadingCandidate", "Region",
    "Table", "TableCell", "TableRow", "TextLine", "infer_page", "iter_layout",
    "DecisionClient", "DecisionConfig", "DecisionProvider", "DecisionRecord", "DecisionResult", "resolve_page",
]
